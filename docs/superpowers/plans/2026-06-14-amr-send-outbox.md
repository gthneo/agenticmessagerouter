# AMR Send / Outbox (sub-project E slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Close the first full loop — reply to a conversation and **send the message out**, on WeChat (fullwechat), strictly **human-in-the-loop**: compose → queue to outbox (dry-run preview) → explicit confirm → send → audit. No auto-send / whitelist this slice (approval-only).

**Architecture:** New `outbox` table + db ops; a `send` dispatch module (registry by platform; `wechat`→fullwechat `POST /api/messages/send`); web reply box → `POST /api/outbox` (queue, status=pending) → outbox panel → `POST /api/outbox/confirm` (THE only place that performs the outward send) → mark sent + `send` audit event. Cancel supported. Runs on .178.

**Tech stack:** Python 3.10+ stdlib (`urllib`, `sqlite3`, `json`, `http.server`), pytest. Send HTTP mocked in tests; real send verified on .178 to a user-named target with the user's confirm.

**Decisions (王总 2026-06-14):** first send channel = wechat/fullwechat; approval-only (no whitelist); first real send to a user-specified contact (named at test time).

**HIGHEST RULE (project):** send is outward + irreversible → dry-run (queued preview) → confirm → act; every send leaves a who/when/what audit trace; the human can cancel. The confirm endpoint is the ONLY code path that emits a message.

## File structure
- `src/jl/schema.sql` — add `outbox` table.
- `src/jl/db.py` — `queue_outbox`, `get_outbox`, `get_outbox_row`, `mark_outbox`.
- `src/jl/send.py` — `send_message(platform, chat_id, body)` registry + dispatch.
- `src/jl/channels/fullwechat.py` — add `send(chat_id, text)`.
- `src/jl/web.py` — `api_queue_outbox` / `api_list_outbox` / `api_confirm_outbox` / `api_cancel_outbox` + routes + UI reply box & outbox panel.
- `src/jl/cli.py` — `jl outbox` (list pending) [optional].
- tests: test_store.py, test_send.py (new), test_web.py.

---

## Task 1: outbox schema + db ops

**Files:** modify `src/jl/schema.sql`, `src/jl/db.py`; test `tests/test_store.py`.

- [ ] **Step 1: failing test** — append to `tests/test_store.py`:

```python
def test_queue_outbox_creates_pending_with_target(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_t", name="张三")
    oid = db.queue_outbox(conn, conversation_id=cid, body="你好", actor="user")
    rows = db.get_outbox(conn, status="pending")
    assert len(rows) == 1
    r = rows[0]
    assert r["id"] == oid and r["body"] == "你好"
    assert r["platform"] == "wechat" and r["chat_id"] == "wxid_t"
    assert r["status"] == "pending"


def test_queue_outbox_unknown_conversation_raises(conn):
    import pytest
    with pytest.raises(ValueError):
        db.queue_outbox(conn, conversation_id=999, body="x", actor="user")


def test_mark_outbox_sent_and_failed(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="w", name="x")
    oid = db.queue_outbox(conn, conversation_id=cid, body="hi", actor="user")
    db.mark_outbox(conn, oid, "sent")
    assert db.get_outbox_row(conn, oid)["status"] == "sent"
    assert db.get_outbox_row(conn, oid)["sent_at"] is not None
    assert db.get_outbox(conn, status="pending") == []
    oid2 = db.queue_outbox(conn, conversation_id=cid, body="hi2", actor="user")
    db.mark_outbox(conn, oid2, "failed", error="boom")
    assert db.get_outbox_row(conn, oid2)["error"] == "boom"
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_store.py -k outbox -q` → FAIL.

- [ ] **Step 3a: schema** — in `src/jl/schema.sql`, after the `media` table (before events), add:
```sql
-- outbound drafts (human-in-the-loop: queued -> confirmed -> sent). Send only on confirm.
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    account_id      INTEGER NOT NULL,
    platform        TEXT NOT NULL,
    chat_id         TEXT NOT NULL,          -- denormalized send target
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|sent|failed|canceled
    created_at      INTEGER NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',
    sent_at         INTEGER,
    error           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, created_at);
```

- [ ] **Step 3b: db ops** — add to `src/jl/db.py` (after the messages section):
```python
# ----- outbox (human-in-the-loop send queue) --------------------------------

def queue_outbox(conn, *, conversation_id, body, actor=""):
    """Queue a draft reply (status=pending). Resolves the send target from the
    conversation. Does NOT send — confirmation happens separately. Returns id."""
    conv = conn.execute(
        "SELECT account_id, platform, chat_id FROM conversations WHERE id=?",
        (conversation_id,)).fetchone()
    if conv is None:
        raise ValueError(f"no conversation {conversation_id}")
    cur = conn.execute(
        """INSERT INTO outbox (conversation_id, account_id, platform, chat_id, body,
                               status, created_at, created_by)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (conversation_id, conv["account_id"], conv["platform"], conv["chat_id"],
         body, _now(), actor))
    conn.commit()
    oid = cur.lastrowid
    log_event(conn, kind="outbox_queue", actor=actor,
              detail={"outbox_id": oid, "platform": conv["platform"],
                      "chat_id": conv["chat_id"]})
    return oid


def get_outbox(conn, status="pending", limit=100):
    rows = conn.execute(
        "SELECT * FROM outbox WHERE status=? ORDER BY created_at DESC LIMIT ?",
        (status, limit)).fetchall()
    return [dict(r) for r in rows]


def get_outbox_row(conn, outbox_id):
    r = conn.execute("SELECT * FROM outbox WHERE id=?", (outbox_id,)).fetchone()
    return dict(r) if r else None


def mark_outbox(conn, outbox_id, status, error=""):
    sent_at = _now() if status == "sent" else None
    conn.execute("UPDATE outbox SET status=?, error=?, sent_at=? WHERE id=?",
                 (status, error, sent_at, outbox_id))
    conn.commit()
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_store.py -q` → all pass. (Note: the schema test that counts tables — if any asserts an exact table set, update to include `outbox`; most use `<=` subset so fine.)
- [ ] **Step 5: commit** — `git add src/jl/schema.sql src/jl/db.py tests/test_store.py && git commit -m "feat(send): outbox table + queue/list/mark db ops (HITL send queue)"`

---

## Task 2: send dispatch + fullwechat send

**Files:** create `src/jl/send.py`; modify `src/jl/channels/fullwechat.py`; test `tests/test_send.py`.

- [ ] **Step 1: failing test** — `tests/test_send.py`:

```python
"""send dispatch (pure registry) — channel send mocked."""
import pytest
from jl import send


def test_send_message_dispatches_by_platform(monkeypatch):
    calls = {}
    monkeypatch.setitem(send.SENDERS, "wechat",
                        lambda chat_id, body: calls.setdefault("c", (chat_id, body)) or (True, ""))
    ok, err = send.send_message("wechat", "wxid_t", "你好")
    assert ok is True and err == ""
    assert calls["c"] == ("wxid_t", "你好")


def test_send_message_unknown_platform():
    ok, err = send.send_message("nope", "x", "y")
    assert ok is False and "unsupported" in err.lower()
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_send.py -q` → FAIL.

- [ ] **Step 3a: fullwechat send** — add to `src/jl/channels/fullwechat.py` (method on `FullWechatAdapter`, plus a module-level helper):
```python
    def send(self, chat_id, text):
        """Send a text message via fullwechat. Returns (ok, error)."""
        body = json.dumps({"chatId": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/messages/send", data=body,
                                     method="POST",
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # noqa: BLE001 - surface any transport error to the human
            return False, str(e)
        return bool(res.get("success")), res.get("error", "") or ""


def send_text(chat_id, text):
    """Module-level convenience: send via a default FullWechatAdapter."""
    return FullWechatAdapter().send(chat_id, text)
```

- [ ] **Step 3b: dispatch** — `src/jl/send.py`:
```python
"""Outbound send dispatch — provider-agnostic registry keyed by platform.

The ONLY module that emits messages outward. Callers (the confirm endpoint) invoke
send_message AFTER the human-in-the-loop confirmation gate. Approval-only this slice.
"""
from __future__ import annotations


def _wechat(chat_id, body):
    from .channels.fullwechat import send_text
    return send_text(chat_id, body)


SENDERS = {
    "wechat": _wechat,
    # "feishu": _feishu,  # next slice
}


def send_message(platform, chat_id, body):
    """Dispatch a send to the platform's sender. Returns (ok, error)."""
    fn = SENDERS.get(platform)
    if fn is None:
        return False, f"unsupported platform: {platform}"
    return fn(chat_id, body)
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_send.py -q` → pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/send.py src/jl/channels/fullwechat.py tests/test_send.py && git commit -m "feat(send): platform send dispatch + fullwechat text send"`

---

## Task 3: web outbox — queue / list / confirm / cancel + UI

**Files:** modify `src/jl/web.py`; test `tests/test_web.py`.

- [ ] **Step 1: failing test** — append to `tests/test_web.py`:

```python
def _conv(c):
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    return db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_t", name="张三")


def test_api_queue_outbox_creates_pending_preview():
    c = db.connect(":memory:"); db.init_db(c); cid = _conv(c)
    res = web.api_queue_outbox(c, {"conversation_id": cid, "body": "你好"})
    assert res["status"] == "pending" and res["body"] == "你好" and res["id"]
    assert db.get_outbox(c, status="pending")[0]["chat_id"] == "wxid_t"


def test_api_confirm_outbox_sends_and_marks(monkeypatch):
    from jl import send
    c = db.connect(":memory:"); db.init_db(c); cid = _conv(c)
    sent = {}
    monkeypatch.setitem(send.SENDERS, "wechat",
                        lambda chat_id, body: sent.setdefault("c", (chat_id, body)) or (True, ""))
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is True
    assert sent["c"] == ("wxid_t", "hi")
    assert db.get_outbox_row(c, oid)["status"] == "sent"
    assert "send" in [e["kind"] for e in db.get_events(c)]


def test_api_confirm_outbox_send_failure_marks_failed(monkeypatch):
    from jl import send
    c = db.connect(":memory:"); db.init_db(c); cid = _conv(c)
    monkeypatch.setitem(send.SENDERS, "wechat", lambda chat_id, body: (False, "offline"))
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    res = web.api_confirm_outbox(c, {"id": oid})
    assert res["ok"] is False
    assert db.get_outbox_row(c, oid)["status"] == "failed"


def test_api_cancel_outbox():
    c = db.connect(":memory:"); db.init_db(c); cid = _conv(c)
    oid = web.api_queue_outbox(c, {"conversation_id": cid, "body": "hi"})["id"]
    web.api_cancel_outbox(c, {"id": oid})
    assert db.get_outbox_row(c, oid)["status"] == "canceled"
    assert db.get_outbox(c, status="pending") == []
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_web.py -k outbox -q` → FAIL.

- [ ] **Step 3: implement** in `src/jl/web.py`:
```python
def api_queue_outbox(conn, payload):
    oid = db.queue_outbox(conn, conversation_id=int(payload["conversation_id"]),
                          body=payload["body"], actor=payload.get("actor", "user"))
    return db.get_outbox_row(conn, oid)


def api_list_outbox(conn):
    return db.get_outbox(conn, status="pending")


def api_confirm_outbox(conn, payload):
    from . import send
    row = db.get_outbox_row(conn, int(payload["id"]))
    if row is None or row["status"] != "pending":
        return {"ok": False, "error": "not a pending outbox item"}
    ok, err = send.send_message(row["platform"], row["chat_id"], row["body"])
    db.mark_outbox(conn, row["id"], "sent" if ok else "failed", error=err)
    db.log_event(conn, kind="send", actor=payload.get("actor", "user"),
                 detail={"outbox_id": row["id"], "platform": row["platform"],
                         "chat_id": row["chat_id"], "ok": ok, "error": err})
    return {"ok": ok, "error": err}


def api_cancel_outbox(conn, payload):
    db.mark_outbox(conn, int(payload["id"]), "canceled")
    return {"ok": True}
```
Add GET route `/api/outbox` → api_list_outbox (in do_GET). Add POST routes in do_POST (after existing auth+parse, route by path): `/api/outbox`→api_queue_outbox, `/api/outbox/confirm`→api_confirm_outbox, `/api/outbox/cancel`→api_cancel_outbox (alongside /api/ingest, /api/link). All already behind the shared `_auth_ok`.

UI (INDEX_HTML): when a conversation is open (`openConv`), show a **reply textarea + "草拟回复 → outbox" button** that POSTs `/api/outbox {conversation_id, body}`. Add an "📤 待发送 outbox" section in the sidebar from `/api/outbox` listing pending drafts (target name + body) each with **"✅ 确认发送"** (POST `/api/outbox/confirm {id}`, then refresh) and **"✕ 取消"** (POST `/api/outbox/cancel {id}`). HTML-escape everything; forward TOK on POSTs (reuse `P`). Make `openConv(id)` remember the current conversation id so the reply box knows the target.

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_web.py -q` → all pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/web.py tests/test_web.py && git commit -m "feat(web): outbox reply -> confirm -> send (HITL gate) + outbox UI"`

---

## Task 4: e2e on .178 + docs (real send needs the user)

- [ ] **Step 1:** full suite + secrets scan clean.
- [ ] **Step 2:** redeploy to .178 (`rsync` + restart amr-web).
- [ ] **Step 3 (with user):** the user names a safe target contact. In the web inbox: open that conversation → draft a reply → it appears in the outbox → the USER clicks ✅ 确认发送 → confirm it actually arrives in WeChat. (Or via curl with the web token to the same endpoints, the user confirming the confirm step.)
- [ ] **Step 4:** verify the `send` audit event recorded; the outbox row is `sent`.
- [ ] **Step 5:** README: add "发送 / outbox(人在回路)" section — queue→confirm→send, approval-only, audit; `POST /api/outbox` + `/confirm` + `/cancel`. Mark E slice 1 done; note feishu/wecom send + whitelist auto-send as follow-ups.
- [ ] **Step 6:** commit + push; merge to master.

## Self-review notes
- HITL: the confirm endpoint is the ONLY send path; queue is a non-sending dry-run; cancel supported; `outbox_queue` + `send` audit events (who/when/what/ok). Approval-only — no auto-send.
- LLM-free: composing + sending is fully manual/deterministic (LLM draft-assist is a later optional layer per LLM-optional-core).
- Types: `queue_outbox(conn,*,conversation_id,body,actor)->id`; `send.send_message(platform,chat_id,body)->(ok,err)`; web `api_*` return dicts. fullwechat `send(chat_id,text)->(ok,err)`.
- Safety: real send tested first to a user-named safe target with the user's own confirm click; send failures mark `failed` (re-confirmable), never silently drop.
