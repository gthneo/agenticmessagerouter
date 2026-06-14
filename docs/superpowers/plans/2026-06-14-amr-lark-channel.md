# AMR Lark (Feishu) Channel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Ingest Feishu group/topic conversations + messages via `lark-cli` (running on .178) into the AMR store, surfaced in the inbox like wechat/phone. account_id=3 (wechat=1, phone=2, feishu=3).

**Architecture:** `lark-cli` (npm `@larksuite/cli`, pure node) is installed on .178; AMR's `LarkAdapter` shells out to it (subprocess), runs **on .178**, ingests into the local DB via `db.ingest_records` driven by `jl ignite lark` — same .178-local pattern as fullwechat, no push. Pure mappers unit-tested; subprocess + live pull verified after the user's `lark-cli auth login --as user` on .178.

**Tech stack:** Python 3.10+ stdlib (`subprocess`, `json`, `datetime`), pytest.

**Decisions (王总 2026-06-14):** lark deploys on .178 (CLI is cross-platform node); reuse OpenClaw's app `cli_<app>`; build now against Mac-probed shapes, user auths .178 later.

## Probed shapes (lark-cli --format json, user identity)
- `im +chat-list --page-all` → `{data:{chats:[{chat_id:"oc_…", chat_mode:"group|topic", name, external, chat_status}], has_more, page_token}}`. **Groups/topics only** — feishu has no list-my-P2P endpoint (P2P = a follow-up needing contact enumeration).
- `im +chat-messages-list --chat-id oc_… --page-all` → `{data:{messages:[{message_id:"om_…", msg_type:"text|post|interactive|image|…", create_time:"YYYY-MM-DD HH:MM", sender:{id, id_type}, content:"<json string>", chat_id, deleted}], has_more, page_token, total}}`, oldest→newest.
- text `content` = JSON `{"text":"…"}`; post/interactive/others vary → extract text or fall back to `[msg_type]`.

## Scope
**In:** group/topic chats + their recent messages, mapped + ingested; `jl ignite lark`; groups default-muted (overload control, consistent with wechat). **Out (follow-ups):** P2P/DM enumeration (needs contact resolution), sender display-name resolution (store sender open_id for now), outbound-direction detection (always "in" this slice — needs self open_id), media download.

## File structure
- `src/jl/channels/lark.py` — `map_chat`/`map_message`/`extract_text` (pure) + `LarkAdapter(IngestAdapter)` (subprocess `lark-cli`).
- `src/jl/cli.py` — extend `ignite` to take an optional channel arg (`jl ignite lark`).
- tests: `tests/test_lark.py` (new), `tests/test_cli.py`.

---

## Task 1: lark adapter

**Files:** create `src/jl/channels/lark.py`, `tests/test_lark.py`.

- [ ] **Step 1: failing test** — `tests/test_lark.py`:

```python
"""lark (feishu) adapter — pure mapping (live lark-cli verified manually)."""
from jl.channels import lark
from jl import ingest


def test_extract_text_plain():
    assert lark.extract_text("text", '{"text":"你好"}') == "你好"


def test_extract_text_post_falls_back_to_title_or_type():
    # post/interactive: best-effort title, else a typed placeholder
    assert lark.extract_text("interactive", '{"x":1}') == "[interactive]"
    assert lark.extract_text("post", '{"title":"周报","content":[]}') == "周报"


def test_extract_text_bad_json():
    assert lark.extract_text("text", "not json") == ""


def test_map_message():
    raw = {"message_id": "om_abc", "msg_type": "text", "create_time": "2026-06-14 16:00",
           "sender": {"id": "ou_sender", "id_type": "open_id"},
           "content": '{"text":"见附件"}', "chat_id": "oc_1", "deleted": False}
    m = lark.map_message(raw)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "lark:om_abc"
    assert m.content == "见附件"
    assert m.sender_id == "ou_sender"
    assert m.type == "text"
    assert m.ts == lark._ts("2026-06-14 16:00") and m.ts > 0


def test_map_message_skips_deleted_via_none():
    raw = {"message_id": "om_x", "msg_type": "text", "create_time": "2026-06-14 16:00",
           "sender": {"id": "ou_s"}, "content": '{"text":"x"}', "deleted": True}
    assert lark.map_message(raw) is None   # deleted -> skip


def test_map_chat_group_muted():
    c = lark.map_chat({"chat_id": "oc_1", "name": "项目群", "chat_mode": "group"})
    assert c.chat_id == "oc_1" and c.type == "group" and c.muted is True
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_lark.py -q` → FAIL.

- [ ] **Step 3: implement** `src/jl/channels/lark.py`:

```python
"""Lark/Feishu ingestion adapter via the lark-cli (@larksuite/cli) subprocess.

Pure mappers unit-tested; live methods shell out to `lark-cli` (user identity) and
are verified by integration runs on the host where lark-cli is authenticated.
Scope: group/topic chats (chat-list). P2P/DM enumeration is a follow-up.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .. import ingest

SOURCE = "lark"
LARK_BIN = "lark-cli"


def _ts(s):
    """'YYYY-MM-DD HH:MM' (lark-cli local-formatted) -> unix seconds. Best-effort."""
    if not s:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def extract_text(msg_type, content):
    """Best-effort plain text from a lark message content JSON string."""
    try:
        c = json.loads(content) if content else {}
    except (ValueError, TypeError):
        return ""
    if not isinstance(c, dict):
        return ""
    if "text" in c and isinstance(c["text"], str):
        return c["text"]
    if "title" in c and isinstance(c["title"], str) and c["title"]:
        return c["title"]
    return f"[{msg_type}]"


def map_chat(chat):
    return ingest.ConvRecord(
        chat_id=chat["chat_id"],
        name=chat.get("name", ""),
        type="group",                 # chat-list returns group/topic only
        muted=True,                   # groups arrive muted (overload control)
    )


def map_message(msg):
    if msg.get("deleted"):
        return None
    sender = msg.get("sender") or {}
    return ingest.MsgRecord(
        msg_key=ingest.msg_key(source=SOURCE, stable_id=msg["message_id"]),
        ts=_ts(msg.get("create_time")),
        content=extract_text(msg.get("msg_type", ""), msg.get("content", "")),
        sender="",                    # display-name resolution is a follow-up
        sender_id=sender.get("id", ""),
        direction="in",               # outbound detection needs self open_id (follow-up)
        type=msg.get("msg_type", "text"),
        raw=msg,
    )


class LarkAdapter(ingest.IngestAdapter):
    platform = "feishu"

    def __init__(self, bin=LARK_BIN):
        self.bin = bin

    def _run(self, args):
        out = subprocess.run([self.bin, *args, "--format", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"lark-cli {args} failed: {out.stderr[:200]}")
        return json.loads(out.stdout or "{}")

    def all_conversations(self, account):
        d = self._run(["im", "+chat-list", "--as", "user", "--page-all"])
        chats = (d.get("data") or {}).get("chats") or []
        return [map_chat(c) for c in chats]

    def _messages(self, chat_id):
        d = self._run(["im", "+chat-messages-list", "--as", "user",
                       "--chat-id", chat_id, "--page-all"])
        msgs = (d.get("data") or {}).get("messages") or []
        return [m for m in (map_message(x) for x in msgs) if m is not None]

    def backfill(self, account, conv, cursor):
        return self._messages(conv.chat_id), ""

    def pull_new(self, account, recent_limit=30):
        out = []
        for conv in self.all_conversations(account):
            out.append((conv, self._messages(conv.chat_id)))
        return out
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_lark.py -q` → all pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/channels/lark.py tests/test_lark.py && git commit -m "feat(channels): lark/feishu ingestion adapter (groups; pure mappers + lark-cli subprocess)"`

---

## Task 2: wire `jl ignite lark`

**Files:** modify `src/jl/cli.py`; test `tests/test_cli.py`.

The current `cmd_ignite` hardcodes the wechat (fullwechat) adapter at account 1. Generalize to accept an optional channel: `jl ignite` (default wechat) / `jl ignite lark`.

- [ ] **Step 1: failing test** — append to `tests/test_cli.py`:

```python
def test_route_ignite_default_channel():
    assert cli.route(["ignite"]) == ("ignite", {"channel": "wechat"})


def test_route_ignite_lark():
    assert cli.route(["ignite", "lark"]) == ("ignite", {"channel": "lark"})
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_cli.py -k route_ignite -q` → FAIL (route currently returns `("ignite", {})`).

- [ ] **Step 3: implement** in `src/jl/cli.py`:
Change the ignite route to capture the channel:
```python
    if a == "ignite":
        ch = args[1] if len(args) > 1 and not args[1].startswith("--") else "wechat"
        return ("ignite", {"channel": ch})
```
Update `cmd_ignite` to dispatch by channel (account_id: wechat=1, feishu=3):
```python
def cmd_ignite(conn, ctx):
    from . import ingest_run
    ch = ctx.get("channel", "wechat")
    if ch == "wechat":
        from .channels.fullwechat import FullWechatAdapter
        adapter, aid = FullWechatAdapter(), _ensure_account(conn, 1, "wechat", "fullwechat #1")
    elif ch == "lark":
        from .channels.lark import LarkAdapter
        adapter, aid = LarkAdapter(), _ensure_account(conn, 3, "feishu", "feishu #1")
    else:
        print(f"❌ 未知渠道: {ch} (支持: wechat, lark)")
        return
    n = ingest_run.ignite(conn, adapter, account_id=aid, actor=_actor())
    print(f"✅ 点火完成 [{ch}]: 新增 {n} 条消息入库 (account #{aid})")
```
Replace the old `_ensure_wechat_account` with a generic helper (keep backward behavior):
```python
def _ensure_account(conn, account_id, platform, label):
    if account_id not in {a["account_id"] for a in db.get_accounts(conn)}:
        db.upsert_account(conn, account_id=account_id, platform=platform, label=label)
    return account_id
```
(Update `cmd_poll` to call `_ensure_account(conn, 1, "wechat", "fullwechat #1")` instead of the removed `_ensure_wechat_account`, keeping poll on wechat.)
Update `main` dispatch: `elif command == "ignite": ctx["channel"] = params["channel"]; cmd_ignite(conn, ctx)`.

- [ ] **Step 4: green** — `.venv/bin/python -m pytest -q` → all pass (confirm `cmd_poll` still references a valid helper; grep `_ensure_wechat_account` → none).
- [ ] **Step 5: commit** — `git add src/jl/cli.py tests/test_cli.py && git commit -m "feat(cli): jl ignite <channel> (wechat default, lark)"`

---

## Task 3: e2e + deploy + docs

- [ ] **Step 1:** full suite + secrets scan clean.
- [ ] **Step 2:** redeploy code to .178 (`rsync` + restart). lark-cli already installed on .178.
- [ ] **Step 3 (needs user):** user runs on .178: `lark-cli auth login --as user` (OAuth, reuses app cli_<app>). Then `cd ~/amr && PYTHONPATH=src python3 -m jl.cli ignite lark` → prints inserted count.
- [ ] **Step 4:** verify on .178: `curl /api/conversations?muted=1&token=…` shows feishu group convs; `/api/search` finds a feishu substring. (Groups are muted, so they appear under `?muted=1`.)
- [ ] **Step 5:** README: add feishu to the channel matrix; `jl ignite lark`; note P2P + display-name + direction follow-ups; lark-cli on .178 with user OAuth.
- [ ] **Step 6:** commit + push; merge to master.

## Self-review notes
- Scope: feishu group/topic ingestion via lark-cli on .178, account 3. Out: P2P enumeration, sender-name resolution, outbound detection, media — all documented follow-ups.
- HITL/LLM-free: deterministic; groups default-muted; ingest = self-data collection (no send), consistent with prior channels and the LLM-optional-core rule.
- Types: `LarkAdapter.pull_new()->[(ConvRecord,[MsgRecord])]` matches `ingest_run.ignite`; `map_message` returns None for deleted (filtered). msg_key `lark:om_…` stable.
- Dependency: live ingest blocked on the user's `lark-cli auth login` on .178 (their feishu account) — Steps 1–2 + code land without it; Step 3 is the user's gate.
