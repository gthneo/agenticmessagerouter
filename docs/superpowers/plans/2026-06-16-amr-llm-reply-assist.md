# AMR LLM Layer + Reply-Draft Assistant (B2-1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** A provider-agnostic LLM layer + an auto/on-demand reply-draft assistant that proposes WeChat reply 话术 the human picks/edits/sends — running **LLM-optional** (no key → degrades cleanly, manual compose unchanged), Claude drop-in-ready, fully token-accounted, sends still behind the existing outbox HITL gate.

**Architecture:** `llm.py` (provider registry + router + LLMResult + LLM-optional + token accounting; Claude wired, multi-provider-ready). `suggestions` table + `assist.py` (build context from a person's merged timeline + category + style guide → generate N stance-varied drafts, llm injected). Triggers: scoped auto-after-poll + on-demand (web button / `jl draft-assist`). Web shows suggestions under a conversation → pick fills reply box → existing `/api/outbox` confirm→send. No new send path.

**Tech Stack:** Python 3.10+ stdlib (`urllib`, `sqlite3`, `json`, `dataclasses`, `http.server`), pytest. Claude HTTP + real send verified on .178; everything else unit-tested with a fake LLM.

**Spec:** `docs/superpowers/specs/2026-06-16-amr-llm-reply-assist-design.md`

---

## File Structure
- `src/jl/llm.py` — **create**: `LLMResult`, `PROVIDERS` registry, `route(task)`, `complete(...)`, `_claude` provider, `available()`.
- `src/jl/schema.sql` — **modify**: add `suggestions` table.
- `src/jl/db.py` — **modify**: `add_suggestions`, `get_suggestions`, `set_suggestion_status`, `clear_suggestions`.
- `src/jl/assist.py` — **create**: `build_context`, `parse_versions`, `generate_drafts`, `auto_draft_sweep`.
- `src/jl/cli.py` — **modify**: `draft-assist` route + `cmd_draft_assist`.
- `src/jl/web.py` — **modify**: `api_suggestions`/`api_dismiss_suggestion`/`api_generate_drafts` + routes + UI.
- `src/jl/ingest_run.py` — **modify**: call `assist.auto_draft_sweep` after ignite (scoped auto-draft).
- tests: `test_llm.py`, `test_assist.py` (new); `test_store.py`, `test_cli.py`, `test_web.py` (append).

---

## Task 1: LLM abstraction (`llm.py`) — registry, router, LLM-optional, token accounting

**Files:** Create `src/jl/llm.py`, `tests/test_llm.py`.

- [ ] **Step 1: failing test** — `tests/test_llm.py`:

```python
"""LLM abstraction — registry/route/LLM-optional/token-accounting (fake provider)."""
from jl import llm, db


def _fake_provider(result):
    def p(messages, **opts):
        return llm.LLMResult(text=result, provider="fake", model="fake-1",
                             tokens_in=11, tokens_out=7, latency_ms=1, ok=True, error="")
    return p


def test_complete_dispatches_to_registered_provider(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "fake", _fake_provider("hi"))
    monkeypatch.setattr(llm, "route", lambda task: "fake")
    r = llm.complete([{"role": "user", "content": "x"}], task="reply")
    assert r.ok is True and r.text == "hi" and r.provider == "fake"


def test_complete_llm_optional_when_no_provider(monkeypatch):
    monkeypatch.setattr(llm, "route", lambda task: None)   # nothing available
    r = llm.complete([{"role": "user", "content": "x"}], task="reply")
    assert r.ok is False and r.error == "llm_unavailable" and r.text == ""


def test_complete_records_tokens_when_conn_given(monkeypatch):
    monkeypatch.setitem(llm.PROVIDERS, "fake", _fake_provider("hi"))
    monkeypatch.setattr(llm, "route", lambda task: "fake")
    c = db.connect(":memory:"); db.init_db(c)
    llm.complete([{"role": "user", "content": "x"}], task="reply", conn=c)
    t = db.token_summary(c)
    assert t["tokens_in"] == 11 and t["tokens_out"] == 7


def test_available_false_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.available() is False
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_llm.py -q` → FAIL (no module).

- [ ] **Step 3: implement** — `src/jl/llm.py`:

```python
"""Provider-agnostic LLM layer. LLM-optional by contract: callers degrade to manual
when ok is False. Token usage is unconstrained-by-design but always accounted
(global memory: token-spend-agentic-era-principle). Claude wired; multi-provider-ready."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass

from . import db

CLAUDE_MODEL = os.environ.get("AMR_CLAUDE_MODEL", "claude-opus-4-8")


@dataclass
class LLMResult:
    text: str = ""
    provider: str = ""
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    ok: bool = False
    error: str = ""


def _claude(messages, *, max_tokens=1024, **opts):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return LLMResult(ok=False, error="llm_unavailable")
    # split a leading system message (Anthropic wants system separate)
    system = ""
    msgs = []
    for m in messages:
        if m.get("role") == "system":
            system += (m["content"] + "\n")
        else:
            msgs.append({"role": m["role"], "content": m["content"]})
    body = {"model": CLAUDE_MODEL, "max_tokens": max_tokens, "messages": msgs}
    if system:
        body["system"] = system.strip()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:  # transport/API error → LLM-optional path
        return LLMResult(ok=False, error=str(e), latency_ms=int((time.time() - t0) * 1000))
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    usage = d.get("usage") or {}
    return LLMResult(text=text, provider="claude", model=d.get("model", CLAUDE_MODEL),
                     tokens_in=usage.get("input_tokens", 0),
                     tokens_out=usage.get("output_tokens", 0),
                     latency_ms=int((time.time() - t0) * 1000), ok=True)


PROVIDERS = {"claude": _claude}


def available():
    """True if any provider is usable right now (1a: Claude key present)."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def route(task):
    """Pick a provider name for a task, or None if none available (LLM-optional)."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    return None


def complete(messages, *, task="reply", provider=None, conn=None, **opts):
    """Run an LLM completion. Returns LLMResult; ok=False means 'no assist' (degrade).
    Records token usage to the tokens table when conn is given."""
    name = provider or route(task)
    fn = PROVIDERS.get(name) if name else None
    if fn is None:
        return LLMResult(ok=False, error="llm_unavailable")
    res = fn(messages, **opts)
    if conn is not None and (res.tokens_in or res.tokens_out):
        db.record_tokens(conn, channel_kind="llm", op=task,
                         tokens_in=res.tokens_in, tokens_out=res.tokens_out)
    return res
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_llm.py -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/llm.py tests/test_llm.py && git commit -m "feat(llm): provider-agnostic LLM layer (Claude, LLM-optional, token-accounted)"`

---

## Task 2: `suggestions` table + db ops

**Files:** Modify `src/jl/schema.sql`, `src/jl/db.py`; Test `tests/test_store.py`.

- [ ] **Step 1: failing test** — append to `tests/test_store.py`:

```python
def test_suggestions_add_get_status(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="w", name="张三")
    db.add_suggestions(conn, cid, [
        {"version_idx": 0, "stance": "稳妥", "body": "稳妥版", "llm_provider": "fake", "llm_model": "f1"},
        {"version_idx": 1, "stance": "直接", "body": "直接版", "llm_provider": "fake", "llm_model": "f1"},
    ])
    rows = db.get_suggestions(conn, cid)
    assert len(rows) == 2 and rows[0]["status"] == "suggested"
    sid = rows[0]["id"]
    db.set_suggestion_status(conn, sid, "dismissed")
    assert len(db.get_suggestions(conn, cid)) == 1   # only 'suggested' returned


def test_clear_suggestions(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="w", name="x")
    db.add_suggestions(conn, cid, [{"version_idx": 0, "stance": "x", "body": "b",
                                    "llm_provider": "f", "llm_model": "m"}])
    db.clear_suggestions(conn, cid)
    assert db.get_suggestions(conn, cid) == []
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_store.py -k suggestion -q` → FAIL.

- [ ] **Step 3a: schema** — in `src/jl/schema.sql`, after the `outbox` table block, add:
```sql
-- AI reply-draft candidates (separate from outbox; outbox = human-committed only)
CREATE TABLE IF NOT EXISTS suggestions (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    version_idx     INTEGER NOT NULL DEFAULT 0,
    stance          TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    llm_provider    TEXT NOT NULL DEFAULT '',
    llm_model       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'suggested',  -- suggested|used|dismissed
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suggestions_conv ON suggestions(conversation_id, status);
```

- [ ] **Step 3b: db ops** — add to `src/jl/db.py` (after the outbox section):
```python
# ----- suggestions (AI reply-draft candidates) ------------------------------

def add_suggestions(conn, conversation_id, items):
    now = _now()
    for it in items:
        conn.execute(
            """INSERT INTO suggestions (conversation_id, version_idx, stance, body,
                                        llm_provider, llm_model, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'suggested', ?)""",
            (conversation_id, it.get("version_idx", 0), it.get("stance", ""),
             it.get("body", ""), it.get("llm_provider", ""), it.get("llm_model", ""), now))
    conn.commit()


def get_suggestions(conn, conversation_id, status="suggested"):
    rows = conn.execute(
        "SELECT * FROM suggestions WHERE conversation_id=? AND status=? ORDER BY version_idx",
        (conversation_id, status)).fetchall()
    return [dict(r) for r in rows]


def set_suggestion_status(conn, suggestion_id, status):
    conn.execute("UPDATE suggestions SET status=? WHERE id=?", (status, suggestion_id))
    conn.commit()


def clear_suggestions(conn, conversation_id):
    conn.execute("DELETE FROM suggestions WHERE conversation_id=?", (conversation_id,))
    conn.commit()
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_store.py -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/schema.sql src/jl/db.py tests/test_store.py && git commit -m "feat(assist): suggestions table + db ops"`

---

## Task 3: `assist.py` — context, version parsing, generate_drafts, auto sweep

**Files:** Create `src/jl/assist.py`, `tests/test_assist.py`.

- [ ] **Step 1: failing test** — `tests/test_assist.py`:

```python
"""reply-draft assistant — context build, version parse, generate (fake llm)."""
from jl import assist, db, llm, ingest


def _seed(conn):
    db.upsert_person(conn, id="u1", name="张三", category="GC0001", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="w1", name="张三", type="private")
    db.link_person(conn, cid, "u1")
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="m1", ts=1000, content="在吗", sender="张三", direction="in")])
    return cid


def test_build_context_includes_timeline_and_person():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid)
    joined = " ".join(m["content"] for m in msgs)
    assert any(m["role"] == "system" for m in msgs)   # style guide present
    assert "在吗" in joined and "张三" in joined        # timeline + person in context


def test_parse_versions_splits_numbered_blocks():
    raw = "1) 稳妥: 您好,稍后回复\n2) 直接: 现在不方便\n3) 有温度: 在的,马上看"
    vs = assist.parse_versions(raw)
    assert len(vs) == 3
    assert vs[0]["body"] and vs[1]["body"] and vs[2]["body"]


def test_generate_drafts_stores_suggestions_with_fake_llm():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    fake = lambda messages, **kw: llm.LLMResult(
        text="1) 稳妥: A\n2) 直接: B\n3) 有温度: C", ok=True, provider="fake",
        model="f1", tokens_in=5, tokens_out=9)
    n = assist.generate_drafts(conn, cid, n=3, llm_complete=fake)
    assert n == 3
    rows = db.get_suggestions(conn, cid)
    assert [r["body"] for r in rows] == ["A", "B", "C"]
    assert db.token_summary(conn)["tokens_out"] == 9   # accounted via llm path... see note


def test_generate_drafts_llm_unavailable_is_noop():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    fake = lambda messages, **kw: llm.LLMResult(ok=False, error="llm_unavailable")
    n = assist.generate_drafts(conn, cid, n=3, llm_complete=fake)
    assert n == 0 and db.get_suggestions(conn, cid) == []   # degrade: no suggestions


def test_auto_draft_sweep_scopes_to_awaiting_private_linked():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="x", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    # eligible: private, linked, latest inbound
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wa", name="张三")
    db.link_person(conn, a, "u1")
    db.insert_messages(conn, a, [ingest.MsgRecord(msg_key="a1", ts=9, content="hi", direction="in")])
    # ineligible: group
    g = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="g", name="群", type="group")
    db.insert_messages(conn, g, [ingest.MsgRecord(msg_key="g1", ts=9, content="x", direction="in")])
    # ineligible: latest is my own outbound
    b = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wb", name="李四")
    db.link_person(conn, b, "u1")
    db.insert_messages(conn, b, [ingest.MsgRecord(msg_key="b1", ts=9, content="已回", direction="out")])
    fake = lambda messages, **kw: llm.LLMResult(text="1) 稳妥: R", ok=True, model="f", provider="f")
    touched = assist.auto_draft_sweep(conn, llm_complete=fake)
    assert touched == [a]   # only the eligible conversation
```

> Note on the token assertion: `generate_drafts` calls `llm_complete(..., conn=conn)` so the llm layer records tokens. The injected `fake` here ignores `conn`; to keep the token test meaningful, `generate_drafts` itself must NOT double-record — it relies on the llm layer. For the fake (which doesn't record), assert tokens via a fake that the test treats as the llm layer. SIMPLER: drop the token assertion from `test_generate_drafts_stores_suggestions_with_fake_llm` (token accounting is already covered in test_llm.py). Implement accordingly: remove that one line if it complicates — the canonical token test lives in Task 1.

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_assist.py -q` → FAIL.

- [ ] **Step 3: implement** — `src/jl/assist.py`:

```python
"""Reply-draft assistant: build context from a conversation + person, ask the LLM for
N stance-varied 话术 versions, store as suggestions. LLM-optional (no llm → no-op).
Sending stays behind the outbox HITL gate — this only proposes."""
from __future__ import annotations

import re

from . import db, llm as _llm

# 1a style guide (phase-A). Method content (M1–M8/Cialdini) injected in 1b.
STYLE_GUIDE = (
    "你是用户的中文沟通助手,为下面这段对话起草回复。围绕把事做成、不树敌、给确定"
    "(具体时间/地点/动作,不用'等会/晚点'),宁可糙而真,不要美而假。"
    "输出恰好 {n} 个不同风格的版本,每行一个,格式: 序号) 风格: 正文。"
    "风格依次为: 稳妥 / 直接 / 有温度。只输出这 {n} 行,不要额外说明。"
)


def build_context(conn, conversation_id, recent=12):
    conv = db.get_conversation(conn, conversation_id)
    person = db.get_person(conn, conv["person_id"]) if conv and conv.get("person_id") else None
    rows = conn.execute(
        "SELECT sender, direction, content FROM messages WHERE conversation_id=? "
        "ORDER BY ts DESC LIMIT ?", (conversation_id, recent)).fetchall()
    lines = []
    for r in reversed(rows):
        who = "我" if r["direction"] == "out" else (r["sender"] or "对方")
        lines.append(f"{who}: {r['content']}")
    pname = (person or {}).get("name") or (conv or {}).get("name") or "对方"
    pcat = (person or {}).get("category") or ""
    sys = STYLE_GUIDE.format(n=3)
    user = (f"对话对象: {pname}" + (f"(类别 {pcat})" if pcat else "") + "\n\n"
            "最近对话:\n" + "\n".join(lines) + "\n\n请起草回复。")
    return [{"role": "system", "content": sys}, {"role": "user", "content": user}]


_LINE = re.compile(r"^\s*\d+\s*[)\).、]\s*([^:：]+?)\s*[:：]\s*(.+?)\s*$")


def parse_versions(text):
    """Parse 'N) 风格: 正文' lines into [{version_idx, stance, body}]."""
    out = []
    for line in (text or "").splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        out.append({"version_idx": len(out), "stance": m.group(1).strip(),
                    "body": m.group(2).strip()})
    return out


def generate_drafts(conn, conversation_id, *, n=3, llm_complete=_llm.complete):
    """Generate + store N reply suggestions. Returns count stored (0 if llm unavailable)."""
    messages = build_context(conn, conversation_id)
    res = llm_complete(messages, task="reply", conn=conn)
    if not res.ok or not res.text:
        return 0
    versions = parse_versions(res.text)[:n]
    if not versions:
        return 0
    db.clear_suggestions(conn, conversation_id)
    for v in versions:
        v["llm_provider"] = res.provider
        v["llm_model"] = res.model
    db.add_suggestions(conn, conversation_id, versions)
    return len(versions)


def _latest_direction(conn, conversation_id):
    r = conn.execute("SELECT direction FROM messages WHERE conversation_id=? "
                     "ORDER BY ts DESC LIMIT 1", (conversation_id,)).fetchone()
    return r["direction"] if r else None


def auto_draft_sweep(conn, *, llm_complete=_llm.complete):
    """Scoped auto-draft: for private + person-linked + unmuted conversations whose
    latest message is inbound (awaiting my reply) and which have no fresh suggestions,
    generate drafts. Returns the conversation ids drafted for."""
    touched = []
    for c in db.get_conversations(conn, muted=False):
        if c["type"] != "private" or not c.get("person_id"):
            continue
        if _latest_direction(conn, c["id"]) != "in":
            continue
        if db.get_suggestions(conn, c["id"]):
            continue
        if generate_drafts(conn, c["id"], llm_complete=llm_complete) > 0:
            touched.append(c["id"])
    return touched
```

(Per the Task-3 note: do NOT add a token assertion in `test_generate_drafts_stores_suggestions_with_fake_llm` — remove that line; token accounting is covered in Task 1.)

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_assist.py -q` → all pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/assist.py tests/test_assist.py && git commit -m "feat(assist): context build + version parse + generate_drafts + scoped auto sweep"`

---

## Task 4: CLI `jl draft-assist` + auto-sweep after poll

**Files:** Modify `src/jl/cli.py`, `src/jl/ingest_run.py`; Test `tests/test_cli.py`.

- [ ] **Step 1: failing test** — append to `tests/test_cli.py`:

```python
def test_route_draft_assist():
    assert cli.route(["draft-assist", "5"]) == ("draft_assist", {"conversation_id": 5})


def test_route_draft_assist_missing_id():
    assert cli.route(["draft-assist"]) == ("draft_assist", {"conversation_id": None})
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_cli.py -k draft_assist -q` → FAIL.

- [ ] **Step 3: implement** — in `src/jl/cli.py`:
route (before final detail return):
```python
    if a == "draft-assist":
        cid = args[1] if len(args) > 1 and not args[1].startswith("--") else None
        return ("draft_assist", {"conversation_id": int(cid) if cid else None})
```
command:
```python
def cmd_draft_assist(conn, ctx):
    from . import assist, llm
    cid = ctx.get("conversation_id")
    if not llm.available():
        print("⚠️ 未配置 LLM(ANTHROPIC_API_KEY 缺)——助手不可用,请手敲。(LLM-optional)")
        return
    if cid:
        n = assist.generate_drafts(conn, cid)
        print(f"✨ 会话 {cid}: 生成 {n} 版话术(去收件箱挑/改/发)")
    else:
        touched = assist.auto_draft_sweep(conn)
        print(f"✨ 自动拟稿: {len(touched)} 个待回会话已生成话术")
```
main dispatch: `elif command == "draft_assist": ctx.update(params); cmd_draft_assist(conn, ctx)`

In `src/jl/ingest_run.py`, at the end of `ignite(...)` (after the log_event, before `return inserted`), add a scoped auto-draft sweep that is LLM-optional + best-effort:
```python
    # scoped auto-draft on freshly ingested inbound (LLM-optional; never blocks ingest)
    try:
        from . import assist, llm
        if llm.available():
            assist.auto_draft_sweep(conn)
    except Exception:
        pass
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/cli.py src/jl/ingest_run.py tests/test_cli.py && git commit -m "feat(cli): jl draft-assist + LLM-optional auto-sweep after ignite"`

---

## Task 5: web — suggestions API + UI

**Files:** Modify `src/jl/web.py`; Test `tests/test_web.py`.

- [ ] **Step 1: failing test** — append to `tests/test_web.py`:

```python
def _sg_conv(c):
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三")
    db.add_suggestions(c, cid, [{"version_idx": 0, "stance": "稳妥", "body": "稳妥版",
                                 "llm_provider": "fake", "llm_model": "f1"}])
    return cid


def test_api_suggestions_lists_for_conversation():
    c = db.connect(":memory:"); db.init_db(c); cid = _sg_conv(c)
    rows = web.api_suggestions(c, cid)
    assert len(rows) == 1 and rows[0]["body"] == "稳妥版"


def test_api_dismiss_suggestion():
    c = db.connect(":memory:"); db.init_db(c); cid = _sg_conv(c)
    sid = web.api_suggestions(c, cid)[0]["id"]
    res = web.api_dismiss_suggestion(c, {"id": sid})
    assert res["ok"] is True and web.api_suggestions(c, cid) == []


def test_api_generate_drafts_llm_optional(monkeypatch):
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w", name="张三")
    from jl import llm
    monkeypatch.setattr(llm, "available", lambda: False)
    res = web.api_generate_drafts(c, {"conversation_id": cid})
    assert res["ok"] is False and "llm" in res["error"].lower()
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_web.py -k "suggestion or generate_drafts" -q` → FAIL.

- [ ] **Step 3: implement** — in `src/jl/web.py`:
handlers:
```python
def api_suggestions(conn, conversation_id):
    return db.get_suggestions(conn, conversation_id)


def api_dismiss_suggestion(conn, payload):
    db.set_suggestion_status(conn, int(payload["id"]), "dismissed")
    return {"ok": True}


def api_generate_drafts(conn, payload):
    from . import assist, llm
    if not llm.available():
        return {"ok": False, "error": "LLM 未配置(ANTHROPIC_API_KEY)"}
    n = assist.generate_drafts(conn, int(payload["conversation_id"]))
    return {"ok": n > 0, "count": n}
```
do_GET — add before its 404:
```python
                if u.path.startswith("/api/conversations/") and u.path.endswith("/suggestions"):
                    try:
                        cid = int(u.path.split("/")[3])
                    except (ValueError, IndexError):
                        return self._send(404, {"error": "bad conversation id"})
                    return self._send(200, api_suggestions(conn, cid))
```
do_POST — add `/api/suggestions/dismiss` + `/api/draft-assist` to the allowlist + routing:
```python
            if u.path not in ("/api/ingest", "/api/link", "/api/outbox",
                              "/api/outbox/confirm", "/api/outbox/cancel",
                              "/api/suggestions/dismiss", "/api/draft-assist"):
                return self._send(404, {"error": "not found"})
```
and in the routing try-block add:
```python
                if u.path == "/api/suggestions/dismiss":
                    return self._send(200, api_dismiss_suggestion(conn, payload))
                if u.path == "/api/draft-assist":
                    return self._send(200, api_generate_drafts(conn, payload))
```
(place these alongside the existing outbox/link routes, before the final fallthrough).

UI in INDEX_HTML — in `openConv(id)`, after rendering messages, also fetch + render suggestions and add a generate button:
- Add a JS `loadSuggestions(id)` that GETs `/api/conversations/{id}/suggestions` and renders each as: `[风格] 正文` + a "用此版" button (sets the reply textarea value to the body) + a "✕" button (POST `/api/suggestions/dismiss {id}` then reload).
- Add a "✨ AI 拟话术" button near the reply box that POSTs `/api/draft-assist {conversation_id: CURCONV}` then `loadSuggestions(CURCONV)`; if `res.ok===false` alert the error (e.g. LLM 未配置).
- Call `loadSuggestions(id)` inside `openConv`.
- All DB text via `esc()`; POSTs via `P()` (token forwarded). Keep it self-explanatory (no manual).

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_web.py -q` → all pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/web.py tests/test_web.py && git commit -m "feat(web): reply-draft suggestions API + UI (pick/dismiss/generate)"`

---

## Task 6: e2e (LLM-optional) + deploy + docs

- [ ] **Step 1:** full suite + `./scripts/secrets-scan.sh --all` clean.
- [ ] **Step 2:** redeploy to .178 (`rsync` + restart amr-web).
- [ ] **Step 3 (LLM-optional proof):** with NO `ANTHROPIC_API_KEY` on .178: `jl draft-assist` prints the "未配置 LLM" notice; web "✨ AI 拟话术" returns the LLM-未配置 message; the inbox + manual outbox compose/confirm/send all still work. `jl --tokens` shows llm op rows only after a real call (zero now). Confirm nothing blocks.
- [ ] **Step 4:** README: add "话术助手(LLM-optional)" section — `jl draft-assist [conv]`, web ✨ button, suggestions→pick→outbox→confirm; note Claude drops in via `ANTHROPIC_API_KEY`; LLM-optional + token-accounted. Mark B2-1a done; 1b/1c next.
- [ ] **Step 5:** commit + push; merge to master; redeploy.

## Self-Review Notes
- **Spec coverage:** llm.py (T1) ✓ registry/route/LLM-optional/token-accounting + Claude; suggestions table+ops (T2) ✓; assist context-phaseA/parse/generate/auto-sweep (T3) ✓; triggers auto-after-poll + on-demand CLI (T4) ✓ + web button (T5); web suggestions UI reuse-outbox (T5) ✓; LLM-optional everywhere (T1/T3/T4/T5/T6) ✓; token accounting (T1) ✓; HITL no-new-send-path (T5 reuses outbox) ✓; phasing 1b/1c out of scope (spec) ✓. Wechat test (T6).
- **Placeholders:** none. (The Task-3 note instructs dropping one token assertion — honor it; not a placeholder.)
- **Type consistency:** `LLMResult(ok,text,provider,model,tokens_in,tokens_out)` used consistently; `generate_drafts(conn, cid, *, n, llm_complete)` and `auto_draft_sweep(conn, *, llm_complete)` consistent across T3/T4/T5; suggestion dict keys (version_idx/stance/body/llm_provider/llm_model) consistent T2↔T3; `llm.available()`/`llm.complete(...,conn=)` consistent.
- **HITL/LLM-optional:** sending only via existing outbox confirm; assist degrades on ok=False everywhere; auto-sweep wrapped try/except in ignite (never blocks ingest).
