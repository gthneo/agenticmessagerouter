# AMR Multi-host Ingest + Phone Channel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Let Mac-side edge collectors feed the central .178 store: add an authed `POST /api/ingest` to the AMR web server, a phone (CallHistory) ingestion adapter, and a `jl push <remote>` collector that normalizes + pushes. First proves the multi-host pattern with phone; lark/wecom reuse it.

**Architecture:** `.178` gains an ingest-write endpoint (auth = `JL_WEB_TOKEN`; ingest-only, not user-send → HITL-clean). Mac runs `jl push http://192.168.31.178:8088` which builds normalized `{account, conversations:[{conv,msgs}]}` from a local adapter and POSTs it; the server calls `db.upsert_account` + `db.ingest_records`. Phone account = `account_id 2` (wechat is 1).

**Tech stack:** Python 3.10+ stdlib (`urllib`, `sqlite3`, `json`, `http.server`), pytest. Pure mappers/handlers unit-tested; live CallHistory + HTTP push verified manually.

**Decisions (王总 2026-06-14):** Mac-collector-push topology; phone first.

## File structure
- `src/jl/web.py` — **modify**: add pure `api_ingest(conn, payload)` + a `do_POST` route for `/api/ingest` (authed).
- `src/jl/channels/phone.py` — **modify**: add `map_call`/`conversations_from_calls` pure mappers + `PhoneAdapter(IngestAdapter)` reading CallHistory.
- `src/jl/push.py` — **create**: `build_payload(adapter, account_id)` (pure) + `push(remote, token, payload)` (urllib POST).
- `src/jl/cli.py` — **modify**: `push` route + `cmd_push`.
- tests: `test_web.py`, `test_phone_adapter.py` (new), `test_push.py` (new), `test_cli.py`.

---

## Task 1: `POST /api/ingest` on the server

**Files:** modify `src/jl/web.py`; test `tests/test_web.py`.

- [ ] **Step 1: failing test** — append to `tests/test_web.py`:

```python
def test_api_ingest_creates_account_conv_and_messages():
    c = db.connect(":memory:"); db.init_db(c)
    payload = {
        "account": {"account_id": 2, "platform": "phone", "label": "iPhone"},
        "conversations": [
            {"conv": {"chat_id": "+8613000000001", "name": "张三", "type": "private",
                      "unread": 0, "last_activity_at": 100, "muted": False},
             "msgs": [{"msg_key": "phone:1", "ts": 100, "content": "[通话] 30s",
                       "sender": "张三", "direction": "in", "type": "call"}]},
        ],
    }
    res = db_ingest_via_web(c, payload)  # helper below calls web.api_ingest
    assert res["accounts"] == 1 and res["conversations"] == 1 and res["messages"] == 1
    convs = db.get_conversations(c)
    assert convs[0]["chat_id"] == "+8613000000001"
    assert convs[0]["platform"] == "phone"


def db_ingest_via_web(conn, payload):
    from jl import web
    return web.api_ingest(conn, payload)


def test_api_ingest_is_idempotent():
    from jl import web
    c = db.connect(":memory:"); db.init_db(c)
    payload = {
        "account": {"account_id": 2, "platform": "phone"},
        "conversations": [
            {"conv": {"chat_id": "p1", "name": "X", "type": "private"},
             "msgs": [{"msg_key": "phone:1", "ts": 1, "content": "hi"}]},
        ],
    }
    web.api_ingest(c, payload)
    r2 = web.api_ingest(c, payload)
    assert r2["messages"] == 0   # dedup on re-push
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_web.py -k ingest -q` → FAIL (no api_ingest).

- [ ] **Step 3: implement** in `src/jl/web.py`:

Add the imports at top (already has `from . import db`): also `from . import ingest`.

Pure handler (place near the other api_*):
```python
def api_ingest(conn, payload):
    """Ingest a pushed batch from an edge collector. Idempotent (dedup on msg_key).
    payload = {"account": {account_id, platform, label?, self_id?, host?},
               "conversations": [{"conv": {ConvRecord fields}, "msgs": [{MsgRecord fields}]}]}"""
    acct = payload["account"]
    db.upsert_account(conn, account_id=acct["account_id"], platform=acct["platform"],
                      label=acct.get("label", ""), self_id=acct.get("self_id", ""),
                      host=acct.get("host", ""))
    n_conv = n_msg = 0
    for item in payload.get("conversations", []):
        cv = item["conv"]
        conv = ingest.ConvRecord(
            chat_id=cv["chat_id"], name=cv.get("name", ""),
            type=cv.get("type", "private"), muted=cv.get("muted", False),
            unread=cv.get("unread", 0), last_activity_at=cv.get("last_activity_at"))
        msgs = [ingest.MsgRecord(
            msg_key=m["msg_key"], ts=m["ts"], content=m.get("content", ""),
            sender=m.get("sender", ""), sender_id=m.get("sender_id", ""),
            direction=m.get("direction", "in"), type=m.get("type", "text"),
            is_mentioned=m.get("is_mentioned", False), raw=m.get("raw", {}))
            for m in item.get("msgs", [])]
        _, ins = db.ingest_records(conn, account_id=acct["account_id"],
                                   platform=acct["platform"], conv=conv, msgs=msgs)
        n_conv += 1
        n_msg += ins
    return {"accounts": 1, "conversations": n_conv, "messages": n_msg}
```

Add `do_POST` to the handler class in `make_handler` (after `do_GET`):
```python
        def do_POST(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not _auth_ok(self.headers, params):
                return self._send(401, {"error": "unauthorized"})
            if u.path != "/api/ingest":
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            conn = db.connect(db_path)
            try:
                return self._send(200, api_ingest(conn, payload))
            except (KeyError, TypeError) as e:
                return self._send(400, {"error": f"bad payload: {e}"})
            finally:
                conn.close()
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_web.py -q` → all pass.
- [ ] **Step 5: commit** — `git add src/jl/web.py tests/test_web.py && git commit -m "feat(web): authed POST /api/ingest for edge-collector push"`

---

## Task 2: phone (CallHistory) ingestion adapter

**Files:** modify `src/jl/channels/phone.py`; test `tests/test_phone_adapter.py` (new).

Existing phone.py has `norm_phone`, `tail_match`, `last`, `resolve_contact`, `CALLDB`, `APPLE_OFFSET`. Reuse them. CallHistory `ZCALLRECORD` columns: `Z_PK` (rowid, stable), `ZDATE` (Apple epoch), `ZADDRESS` (number), `ZDURATION` (seconds), `ZORIGINATED` (1=outgoing).

- [ ] **Step 1: failing test** — `tests/test_phone_adapter.py`:

```python
"""phone adapter — pure mapping from CallHistory rows (live DB read verified manually)."""
from jl.channels import phone
from jl import ingest


def test_map_call_outgoing():
    row = {"Z_PK": 7, "ZADDRESS": "+8613000000001", "ZDATE": 100,
           "ZDURATION": 42.0, "ZORIGINATED": 1}
    m = phone.map_call(row)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "phone:7"
    assert m.ts == 100 + phone.APPLE_OFFSET
    assert m.direction == "out"
    assert "42" in m.content and m.type == "call"


def test_map_call_incoming_missed():
    row = {"Z_PK": 8, "ZADDRESS": "13000000002", "ZDATE": 200,
           "ZDURATION": 0.0, "ZORIGINATED": 0}
    m = phone.map_call(row)
    assert m.direction == "in"
    assert "未接" in m.content or "miss" in m.content.lower()


def test_conversations_from_calls_groups_by_number():
    rows = [
        {"Z_PK": 1, "ZADDRESS": "+8613000000001", "ZDATE": 100, "ZDURATION": 10, "ZORIGINATED": 1},
        {"Z_PK": 2, "ZADDRESS": "+8613000000001", "ZDATE": 300, "ZDURATION": 0, "ZORIGINATED": 0},
        {"Z_PK": 3, "ZADDRESS": "13000000002", "ZDATE": 200, "ZDURATION": 5, "ZORIGINATED": 1},
    ]
    convs = phone.conversations_from_calls(rows, name_resolver=lambda n: "")
    by_id = {c.chat_id: (c, msgs) for c, msgs in convs}
    assert set(by_id) == {"+8613000000001", "13000000002"}
    conv1, msgs1 = by_id["+8613000000001"]
    assert conv1.type == "private" and conv1.last_activity_at == 300 + phone.APPLE_OFFSET
    assert len(msgs1) == 2


def test_conversations_from_calls_uses_name_resolver():
    rows = [{"Z_PK": 1, "ZADDRESS": "+8613000000001", "ZDATE": 100, "ZDURATION": 10, "ZORIGINATED": 1}]
    convs = phone.conversations_from_calls(rows, name_resolver=lambda n: "张三")
    conv, _ = convs[0]
    assert conv.name == "张三"
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_phone_adapter.py -q` → FAIL.

- [ ] **Step 3: implement** — add to `src/jl/channels/phone.py`:

```python
from .. import ingest


def map_call(row):
    """One CallHistory row -> MsgRecord."""
    ts = int(row["ZDATE"]) + APPLE_OFFSET
    out = bool(row.get("ZORIGINATED"))
    dur = int(row.get("ZDURATION") or 0)
    if dur >= 1:
        body = f"[通话] {dur}s {'拨出' if out else '接听'}"
    else:
        body = "[通话] 未接" if not out else "[通话] 未接通"
    return ingest.MsgRecord(
        msg_key=f"phone:{row['Z_PK']}",
        ts=ts,
        content=body,
        sender="me" if out else (row.get("ZADDRESS") or ""),
        sender_id=row.get("ZADDRESS") or "",
        direction="out" if out else "in",
        type="call",
        raw={k: row.get(k) for k in ("Z_PK", "ZADDRESS", "ZDATE", "ZDURATION", "ZORIGINATED")},
    )


def conversations_from_calls(rows, name_resolver=resolve_contact):
    """Group call rows by normalized number into [(ConvRecord, [MsgRecord])]."""
    groups = {}
    for r in rows:
        num = r.get("ZADDRESS") or ""
        groups.setdefault(num, []).append(r)
    out = []
    for num, grp in groups.items():
        msgs = [map_call(r) for r in grp]
        last_ts = max(m.ts for m in msgs) if msgs else None
        name = name_resolver(num) or ""
        conv = ingest.ConvRecord(chat_id=num, name=name, type="private",
                                 last_activity_at=last_ts)
        out.append((conv, msgs))
    return out


class PhoneAdapter(ingest.IngestAdapter):
    platform = "phone"

    def _rows(self, limit):
        conn = sqlite3.connect(f"file:{CALLDB}?mode=ro", uri=True, timeout=3)
        try:
            cur = conn.execute(
                """SELECT Z_PK, ZADDRESS, ZDATE, ZDURATION, ZORIGINATED
                   FROM ZCALLRECORD ORDER BY ZDATE DESC LIMIT ?""", (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_conversations(self, account, **kw):
        return [c for c, _ in conversations_from_calls(self._rows(2000))]

    def backfill(self, account, conv, cursor):
        return [], ""

    def pull_new(self, account, recent_limit=500):
        return conversations_from_calls(self._rows(recent_limit))
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_phone_adapter.py -q` → pass; full suite green.
- [ ] **Step 5: commit** — `git add src/jl/channels/phone.py tests/test_phone_adapter.py && git commit -m "feat(channels): phone CallHistory ingestion adapter"`

---

## Task 3: `jl push <remote>` collector

**Files:** create `src/jl/push.py`; modify `src/jl/cli.py`; tests `tests/test_push.py` (new), `tests/test_cli.py`.

- [ ] **Step 1: failing test** — `tests/test_push.py`:

```python
"""push payload building (pure) with a fake poster."""
from jl import push, ingest


class FakeAdapter:
    platform = "phone"
    def pull_new(self, account, recent_limit=500):
        return [(ingest.ConvRecord(chat_id="p1", name="张三", last_activity_at=5),
                 [ingest.MsgRecord(msg_key="phone:1", ts=5, content="[通话] 10s")])]


def test_build_payload_shape():
    p = push.build_payload(FakeAdapter(), account_id=2, label="iPhone")
    assert p["account"] == {"account_id": 2, "platform": "phone", "label": "iPhone"}
    assert len(p["conversations"]) == 1
    item = p["conversations"][0]
    assert item["conv"]["chat_id"] == "p1" and item["conv"]["name"] == "张三"
    assert item["msgs"][0]["msg_key"] == "phone:1"
    assert item["msgs"][0]["content"] == "[通话] 10s"
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_push.py -q` → FAIL.

- [ ] **Step 3: implement** `src/jl/push.py`:

```python
"""Edge-collector push: run a local adapter, normalize, POST to a remote AMR /api/ingest."""
from __future__ import annotations

import json
import urllib.request
from dataclasses import asdict


def build_payload(adapter, *, account_id, label="", self_id=""):
    """Pure: adapter.pull_new() -> the /api/ingest JSON payload."""
    convs = []
    for conv, msgs in adapter.pull_new(None):
        convs.append({"conv": asdict(conv), "msgs": [asdict(m) for m in msgs]})
    return {"account": {"account_id": account_id, "platform": adapter.platform,
                        "label": label} | ({"self_id": self_id} if self_id else {}),
            "conversations": convs}


def push(remote, token, payload, timeout=60):
    """POST payload to <remote>/api/ingest. Returns the parsed JSON response."""
    url = remote.rstrip("/") + "/api/ingest"
    if token:
        url += "?token=" + token
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_push.py -q` → pass.

- [ ] **Step 5: CLI route test** — append to `tests/test_cli.py`:

```python
def test_route_push():
    cmd, params = cli.route(["push", "phone", "--remote", "http://x:8088", "--token", "t"])
    assert cmd == "push"
    assert params["channel"] == "phone"
    assert params["remote"] == "http://x:8088"
    assert params["token"] == "t"
```

- [ ] **Step 6: red** — `.venv/bin/python -m pytest tests/test_cli.py -k route_push -q` → FAIL.

- [ ] **Step 7: implement** in `src/jl/cli.py` route (before final detail return):
```python
    if a == "push":
        return ("push", {"channel": args[1] if len(args) > 1 and not args[1].startswith("--") else "phone",
                         "remote": _opt_value(args, "--remote") or "http://192.168.31.178:8088",
                         "token": _opt_value(args, "--token") or ""})
```
Add command:
```python
def cmd_push(conn, ctx):
    from . import push as push_mod
    ch = ctx.get("channel", "phone")
    if ch == "phone":
        from .channels.phone import PhoneAdapter
        adapter, account_id, label = PhoneAdapter(), 2, "phone"
    else:
        print(f"❌ 未知渠道: {ch} (当前支持: phone)"); return
    payload = push_mod.build_payload(adapter, account_id=account_id, label=label)
    nconv = len(payload["conversations"])
    res = push_mod.push(ctx["remote"], ctx.get("token", ""), payload)
    print(f"✅ push {ch}: {nconv} 会话 → {ctx['remote']}  入库 {res.get('messages')} 条新消息")
```
Dispatch in `main` (add branch):
```python
    elif command == "push":
        ctx.update(params); cmd_push(conn, ctx)
```

- [ ] **Step 8: green** — `.venv/bin/python -m pytest -q` → all pass.
- [ ] **Step 9: commit** — `git add -A && git commit -m "feat(push): jl push edge-collector → remote /api/ingest (phone)"`

---

## Task 4: e2e + deploy + docs

- [ ] **Step 1:** full suite + secrets scan clean.
- [ ] **Step 2:** redeploy server to .178 (`rsync` + restart amr-web) so it has `/api/ingest`.
- [ ] **Step 3:** live push from Mac: `.venv/bin/jl push phone --remote http://192.168.31.178:8088 --token <web_token>` → prints conversations + inserted count. Then verify on .178: `curl /api/conversations?token=...` now includes phone conversations.
- [ ] **Step 4:** README: document `jl push <channel> --remote --token` + `POST /api/ingest` + that phone runs as a Mac edge collector (cron/launchd later). Note lark/wecom reuse the same path.
- [ ] **Step 5:** commit + push; merge to master.

## Self-review notes
- Scope: ingest endpoint + phone adapter + push collector. Out: lark/wecom adapters (next slices, same pattern), scheduling the Mac collector (cron/launchd later), full backfill.
- HITL: `/api/ingest` is ingest-write (collecting the user's own data), authed by JL_WEB_TOKEN; not user-facing send (that's E). Read-only browsing unchanged.
- Types: adapter → `pull_new()->[(ConvRecord,[MsgRecord])]` → `build_payload` (asdict) → JSON → `api_ingest` rebuilds ConvRecord/MsgRecord → `db.ingest_records`. Consistent.
- account_id: wechat=1, phone=2 (8-bit registry).
