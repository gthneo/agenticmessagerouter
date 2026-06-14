# AMR Sub-project B (ingestion) + Web Inbox MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use `- [ ]` checkboxes.

**Goal:** Ingest WeChat conversations/messages from the fullwechat backend (@.178) into the AMR store, and serve a read-only browser unified-inbox (list / search / read) — both runnable locally now, deployable to .178 later.

**Architecture:** B adds a `fullwechat` IngestAdapter (HTTP → `:6174`) + a store-write glue `db.ingest_records` + `jl ignite` (one-shot recent pull, groups default-muted) + `jl poll` (5-min loop). Web adds `src/jl/web.py` (stdlib `http.server`, pure data handlers over the existing `db` queries) + an embedded vanilla HTML/JS inbox + `jl web` launcher. Zero third-party runtime deps. Read-only (no send — that's sub-project E).

**Tech stack:** Python 3.10+ stdlib (`urllib`, `http.server`, `json`, `sqlite3`), pytest. Tests use in-memory SQLite + sample JSON fixtures; live HTTP paths verified manually against .178.

**Spec basis:** `docs/superpowers/specs/2026-06-14-amr-message-store-foundation-design.md` (the adapter interface, ConvRecord/MsgRecord, ingest, mute, recent-first are defined there). This plan implements B + the C/D MVP.

## Verified facts (probed 2026-06-14)
- fullwechat `GET /api/chats?limit=&offset=` → `[{id, name, isGroup, lastActivityAt, unreadCount, lastMsgLepocalId, lastMessagePreview, ...}]`. Top entries are folders/official accounts (`gh_*`, `brandsessionholder`, `placeholder*`, `_`-prefixed) — these return no per-chat messages; skip them.
- `GET /api/messages/{chatId}?limit=&offset=` → `[{localId, serverId, chatId, sender, senderName, type, content, timestamp, isMentioned?, reply?}]`, oldest→newest. `timestamp` is ISO-8601. `type` int (1=text).
- Token: `~/.config/agent-wechat/token`; base `http://192.168.31.178:6174`.

## File structure
- `src/jl/channels/fullwechat.py` — **create**: `FullWechatAdapter(IngestAdapter)` + pure `map_chat`/`map_message` + HTTP `_get`.
- `src/jl/db.py` — **modify**: add `ingest_records(conn, account_id, platform, conv, msgs)` glue + `get_conversation(conn, id)`.
- `src/jl/ingest_run.py` — **create**: `ignite(conn, adapter, account, *, recent_limit, mute_groups)` + `poll_once(...)` (orchestration, no I/O of its own beyond the adapter).
- `src/jl/cli.py` — **modify**: route + `cmd_ignite`, `cmd_poll`, `cmd_web`.
- `src/jl/web.py` — **create**: pure handlers `api_conversations`/`api_messages`/`api_search` + `make_server` (http.server) + embedded `INDEX_HTML`.
- `tests/test_fullwechat.py`, `tests/test_ingest_run.py`, `tests/test_web.py` — **create**.

---

## Task 1: store-write glue `db.ingest_records` + `get_conversation`

**Files:** Modify `src/jl/db.py`; Test `tests/test_store.py` (append).

- [ ] **Step 1: failing test** — append to `tests/test_store.py`:

```python
def test_get_conversation_by_id(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="c1", name="张三")
    got = db.get_conversation(conn, cid)
    assert got["chat_id"] == "c1" and got["name"] == "张三"
    assert db.get_conversation(conn, 99999) is None


def test_ingest_records_upserts_conv_and_inserts_msgs(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    conv = ingest.ConvRecord(chat_id="c1", name="张三", type="private",
                             unread=2, last_activity_at=5000)
    msgs = [ingest.MsgRecord(msg_key="fullwx:1", ts=4000, content="早", sender="张三"),
            ingest.MsgRecord(msg_key="fullwx:2", ts=5000, content="晚", sender="张三")]
    cid, n = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=msgs)
    assert n == 2
    got = db.get_conversation(conn, cid)
    assert got["name"] == "张三" and got["unread"] == 2
    # re-ingest same msgs is idempotent
    cid2, n2 = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=msgs)
    assert cid2 == cid and n2 == 0


def test_ingest_records_mutes_when_conv_muted_true(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    conv = ingest.ConvRecord(chat_id="g1", name="群", type="group", muted=True)
    cid, _ = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=[])
    assert db.get_conversation(conn, cid)["muted"] == 1
```

- [ ] **Step 2: run, see red** — `.venv/bin/python -m pytest tests/test_store.py -k "get_conversation or ingest_records" -q` → FAIL (no `ingest_records`).

- [ ] **Step 3: implement** — add to `src/jl/db.py` (after the conversations section):

```python
def get_conversation(conn, conversation_id):
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return dict(row) if row else None
```

and after the messages section:

```python
def ingest_records(conn, *, account_id, platform, conv, msgs):
    """Upsert a ConvRecord + insert its MsgRecords (dedup). Returns (conv_id, inserted).
    Honors conv.muted (a new group can arrive muted); never un-mutes an existing conv."""
    cid = upsert_conversation(conn, account_id=account_id, platform=platform,
                              chat_id=conv.chat_id, name=conv.name, type=conv.type,
                              unread=conv.unread, last_activity_at=conv.last_activity_at)
    if conv.muted:
        set_muted(conn, cid, True)
    inserted = insert_messages(conn, cid, msgs) if msgs else 0
    return cid, inserted
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_store.py -q` → all pass.
- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(store): ingest_records glue + get_conversation"`

---

## Task 2: fullwechat ingestion adapter

**Files:** Create `src/jl/channels/fullwechat.py`, `tests/test_fullwechat.py`.

- [ ] **Step 1: failing test** — `tests/test_fullwechat.py`:

```python
"""fullwechat adapter — pure field mapping (live HTTP verified manually)."""
from jl.channels import fullwechat as fw
from jl import ingest


def test_map_message_to_msgrecord():
    raw = {"localId": 17, "serverId": 99, "chatId": "m1", "sender": "wxid_a",
           "senderName": "张三", "type": 1, "content": "你好",
           "timestamp": "2026-06-14T08:37:05+00:00", "isMentioned": False}
    m = fw.map_message(raw)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "fullwx:17"
    assert m.sender == "张三" and m.sender_id == "wxid_a"
    assert m.content == "你好"
    assert m.ts == 1749890225           # 2026-06-14T08:37:05Z in unix seconds
    assert m.is_mentioned is False


def test_map_message_falls_back_to_serverid_when_no_localid():
    raw = {"localId": 0, "serverId": 12345, "chatId": "m1", "sender": "x",
           "senderName": "Y", "type": 1, "content": "hi",
           "timestamp": "2026-06-14T08:37:05+00:00"}
    assert fw.map_message(raw).msg_key == "fullwx:s12345"


def test_map_chat_personal_not_muted():
    raw = {"id": "m1", "name": "张三", "isGroup": False,
           "lastActivityAt": "2026-06-14T08:37:05+00:00", "unreadCount": 3}
    c = fw.map_chat(raw)
    assert c.chat_id == "m1" and c.type == "private" and c.muted is False
    assert c.unread == 3 and c.last_activity_at == 1749890225


def test_map_chat_group_default_muted():
    raw = {"id": "g1@chatroom", "name": "群", "isGroup": True,
           "lastActivityAt": "2026-06-14T08:37:05+00:00", "unreadCount": 999}
    c = fw.map_chat(raw)
    assert c.type == "group" and c.muted is True


def test_is_ingestable_skips_folders_and_official():
    assert fw.is_ingestable({"id": "m135", "isGroup": False}) is True
    for bad in ("brandsessionholder", "gh_abc", "placeholder_x", "_sys"):
        assert fw.is_ingestable({"id": bad, "isGroup": False}) is False
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_fullwechat.py -q` → FAIL (no module).

- [ ] **Step 3: implement** — `src/jl/channels/fullwechat.py`:

```python
"""fullwechat ingestion adapter — HTTP client for the fullwechat REST backend.

Pure mappers (map_chat / map_message / is_ingestable) are unit-tested; the live
list_conversations / backfill / pull_new methods hit the REST API and are verified
by integration runs against the running backend.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone

from .. import ingest

DEFAULT_URL = os.environ.get("AGENT_WECHAT_URL", "http://192.168.31.178:6174")
SOURCE = "fullwx"

# top-level entries that are folders / official-account buckets, not real chats
_SKIP_PREFIXES = ("gh_", "placeholder", "_")
_SKIP_IDS = {"brandsessionholder"}


def _token():
    t = os.environ.get("AGENT_WECHAT_TOKEN")
    if t:
        return t
    path = os.path.expanduser("~/.config/agent-wechat/token")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _ts(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except ValueError:
        return 0


def is_ingestable(chat):
    cid = chat.get("id", "")
    if cid in _SKIP_IDS:
        return False
    return not any(cid.startswith(p) for p in _SKIP_PREFIXES)


def map_chat(chat):
    is_group = bool(chat.get("isGroup"))
    return ingest.ConvRecord(
        chat_id=chat["id"],
        name=chat.get("name", ""),
        type="group" if is_group else "private",
        muted=is_group,                       # groups arrive muted (noise control)
        unread=chat.get("unreadCount", 0) or 0,
        last_activity_at=_ts(chat.get("lastActivityAt")),
    )


def map_message(msg):
    local = msg.get("localId") or 0
    stable = str(local) if local else "s" + str(msg.get("serverId") or "")
    return ingest.MsgRecord(
        msg_key=ingest.msg_key(source=SOURCE, stable_id=stable),
        ts=_ts(msg.get("timestamp")),
        content=msg.get("content", "") or "",
        sender=msg.get("senderName", "") or "",
        sender_id=msg.get("sender", "") or "",
        direction="in",
        type="text" if msg.get("type") == 1 else str(msg.get("type")),
        is_mentioned=bool(msg.get("isMentioned")),
        raw=msg,
    )


class FullWechatAdapter(ingest.IngestAdapter):
    platform = "wechat"

    def __init__(self, url=DEFAULT_URL, token=None):
        self.url = url.rstrip("/")
        self.token = token or _token()

    def _get(self, path):
        req = urllib.request.Request(self.url + path,
                                     headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def list_conversations(self, account, limit=50, offset=0):
        chats = self._get(f"/api/chats?limit={limit}&offset={offset}")
        return [map_chat(c) for c in chats if is_ingestable(c)]

    def _messages(self, chat_id, limit, offset):
        from urllib.parse import quote
        raw = self._get(f"/api/messages/{quote(chat_id, safe='')}?limit={limit}&offset={offset}")
        return [map_message(m) for m in raw]

    def backfill(self, account, conv, cursor):
        # cursor = offset string; pull a page, advance offset, '' when a short page returns
        offset = int(cursor or "0")
        page = self._messages(conv.chat_id, 200, offset)
        nxt = "" if len(page) < 200 else str(offset + len(page))
        return page, nxt

    def pull_new(self, account, recent_limit=30):
        # MVP incremental: re-pull the most recent N per active conversation; dedup handles overlap
        out = []
        for conv in self.list_conversations(account, limit=50):
            out.append((conv, self._messages(conv.chat_id, recent_limit, 0)))
        return out
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_fullwechat.py -q` → all pass. (Verify the `_ts` expected unix value in the test by computing it; if the literal differs, fix the TEST's expected number to match `_ts` output, not the other way.)
- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(channels): fullwechat ingestion adapter (mappers + REST client)"`

---

## Task 3: ignite + poll orchestration

**Files:** Create `src/jl/ingest_run.py`, `tests/test_ingest_run.py`.

- [ ] **Step 1: failing test** — `tests/test_ingest_run.py`:

```python
"""ignite/poll orchestration with a fake adapter (no network)."""
from jl import db, ingest, ingest_run


class FakeAdapter(ingest.IngestAdapter):
    platform = "wechat"
    def __init__(self, convs):
        self._convs = convs  # list of (ConvRecord, [MsgRecord])
    def list_conversations(self, account, **kw):
        return [c for c, _ in self._convs]
    def backfill(self, account, conv, cursor):
        return [], ""
    def pull_new(self, account, **kw):
        return self._convs


def _db():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="self")
    return c


def test_ignite_ingests_all_conversations():
    conn = _db()
    convs = [
        (ingest.ConvRecord(chat_id="m1", name="张三", type="private"),
         [ingest.MsgRecord(msg_key="fullwx:1", ts=100, content="hi", sender="张三")]),
        (ingest.ConvRecord(chat_id="g1", name="群", type="group", muted=True),
         [ingest.MsgRecord(msg_key="fullwx:2", ts=200, content="yo", sender="李四")]),
    ]
    n = ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    assert n == 2
    assert len(db.get_conversations(conn)) == 2            # both stored
    assert len(db.get_conversations(conn, muted=False)) == 1  # group muted out of active
    assert "ignite" in [e["kind"] for e in db.get_events(conn)]


def test_ignite_is_idempotent():
    conn = _db()
    convs = [(ingest.ConvRecord(chat_id="m1", name="张三", type="private"),
              [ingest.MsgRecord(msg_key="fullwx:1", ts=100, content="hi")])]
    ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    n2 = ingest_run.ignite(conn, FakeAdapter(convs), account_id=1)
    assert n2 == 0  # nothing new on second run
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_ingest_run.py -q` → FAIL.

- [ ] **Step 3: implement** — `src/jl/ingest_run.py`:

```python
"""Orchestration: drive an IngestAdapter to fill the store (ignite / poll)."""
from __future__ import annotations

from . import db


def ignite(conn, adapter, *, account_id, recent_limit=30, actor="cli"):
    """One-shot recent pull: ingest the recent messages of every active conversation.
    Groups arrive muted (the adapter sets ConvRecord.muted). Returns messages inserted."""
    inserted = 0
    convs = 0
    for conv, msgs in adapter.pull_new(account_for(conn, account_id), recent_limit=recent_limit):
        _, n = db.ingest_records(conn, account_id=account_id, platform=adapter.platform,
                                 conv=conv, msgs=msgs)
        inserted += n
        convs += 1
    db.log_event(conn, kind="ignite", actor=actor,
                 detail={"account_id": account_id, "conversations": convs,
                         "inserted": inserted})
    return inserted


def account_for(conn, account_id):
    for a in db.get_accounts(conn):
        if a["account_id"] == account_id:
            return a
    return {"account_id": account_id}
```

(Note: `pull_new` on the fake/real adapter takes the account object + `recent_limit`. `account_for` resolves it; the fake ignores it.)

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_ingest_run.py -q` → pass.
- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(ingest): ignite orchestration (recent-first, groups muted, audited)"`

---

## Task 4: CLI — ignite / poll / web routes + commands

**Files:** Modify `src/jl/cli.py`; Test `tests/test_cli.py` (append route tests).

- [ ] **Step 1: failing test** — append to `tests/test_cli.py`:

```python
def test_route_ignite():
    assert cli.route(["ignite"]) == ("ignite", {})


def test_route_poll():
    cmd, params = cli.route(["poll"])
    assert cmd == "poll"


def test_route_web():
    cmd, params = cli.route(["web"])
    assert cmd == "web"
    assert "port" in params
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_cli.py -k "ignite or poll or web" -q` → FAIL (returns detail).

- [ ] **Step 3: implement** — in `src/jl/cli.py`:

In `route`, before the final detail return:
```python
    if a == "ignite":
        return ("ignite", {})
    if a == "poll":
        return ("poll", {"interval": int(_opt_value(args, "--interval") or 300)})
    if a == "web":
        return ("web", {"port": int(_opt_value(args, "--port") or 8088),
                        "host": _opt_value(args, "--host") or "0.0.0.0"})
```

Add commands (the fullwechat account is account_id 1 by convention; ensure it exists):
```python
def _ensure_wechat_account(conn):
    from .channels import fullwechat
    accts = {a["account_id"] for a in db.get_accounts(conn)}
    if 1 not in accts:
        db.upsert_account(conn, account_id=1, platform="wechat",
                          label="fullwechat #1", host=fullwechat.DEFAULT_URL)
    return 1


def cmd_ignite(conn, ctx):
    from .channels.fullwechat import FullWechatAdapter
    from . import ingest_run
    aid = _ensure_wechat_account(conn)
    n = ingest_run.ignite(conn, FullWechatAdapter(), account_id=aid, actor=_actor())
    print(f"✅ 点火完成: 新增 {n} 条消息入库 (account #{aid})")


def cmd_poll(conn, ctx):
    import time as _t
    from .channels.fullwechat import FullWechatAdapter
    from . import ingest_run
    aid = _ensure_wechat_account(conn)
    interval = ctx.get("interval", 300)
    print(f"🔁 poll 每 {interval}s 拉新 (Ctrl-C 停)")
    while True:
        n = ingest_run.ignite(conn, FullWechatAdapter(), account_id=aid, actor="poll")
        print(f"  [{_t.strftime('%H:%M:%S')}] +{n}")
        _t.sleep(interval)


def cmd_web(conn, ctx):
    from . import web
    web.serve(conn_path=db.DEFAULT_DB, host=ctx.get("host", "0.0.0.0"),
              port=ctx.get("port", 8088))
```

Wire dispatch in `main` (ignite/poll use ctx, web needs host/port via ctx):
```python
    if command == "detail":
        cmd_detail(conn, ctx, params["name"])
    elif command == "reset":
        cmd_reset(conn, params)
    elif command == "ignite":
        cmd_ignite(conn, ctx)
    elif command == "poll":
        ctx["interval"] = params["interval"]; cmd_poll(conn, ctx)
    elif command == "web":
        ctx.update(params); cmd_web(conn, ctx)
    else:
        _DISPATCH[command](conn, ctx)
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_cli.py -q` → all pass.
- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(cli): ignite / poll / web commands + routes"`

---

## Task 5: web data handlers (pure) + server + UI

**Files:** Create `src/jl/web.py`, `tests/test_web.py`.

- [ ] **Step 1: failing test** — `tests/test_web.py`:

```python
"""Web data handlers — pure, over an in-memory store (no socket)."""
from jl import db, ingest, web


def _seed():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    m = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="m1",
                               name="张三", type="private")
    db.insert_messages(c, m, [ingest.MsgRecord(msg_key="x:1", ts=100, content="带合同来", sender="张三")])
    g = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="g1",
                               name="群", type="group")
    db.set_muted(c, g, True)
    return c


def test_api_conversations_excludes_muted_by_default():
    c = _seed()
    rows = web.api_conversations(c, {})
    names = [r["name"] for r in rows]
    assert "张三" in names and "群" not in names


def test_api_conversations_include_muted():
    c = _seed()
    names = [r["name"] for r in web.api_conversations(c, {"muted": "1"})]
    assert "群" in names


def test_api_messages_returns_conversation_messages():
    c = _seed()
    conv = web.api_conversations(c, {})[0]
    msgs = web.api_messages(c, conv["id"])
    assert msgs[0]["content"] == "带合同来"


def test_api_search_hits_content():
    c = _seed()
    hits = web.api_search(c, "合同")
    assert len(hits) == 1 and hits[0]["content"] == "带合同来"


def test_api_search_empty_query_returns_empty():
    c = _seed()
    assert web.api_search(c, "") == []
```

- [ ] **Step 2: red** — `.venv/bin/python -m pytest tests/test_web.py -q` → FAIL.

- [ ] **Step 3: implement** — `src/jl/web.py`:

```python
"""Read-only web inbox: stdlib http.server over the AMR store.

Pure data handlers (api_*) are unit-tested; serve() wires them to BaseHTTPRequestHandler.
Read-only by design — replying/sending is sub-project E (human-in-the-loop outbox).
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import db


def api_conversations(conn, params):
    muted = params.get("muted") == "1"
    rows = db.get_conversations(conn, muted=(True if muted else None))
    # when not include-muted, get_conversations(muted=None) returns ALL — filter to unmuted
    if not muted:
        rows = [r for r in rows if not r["muted"]]
    return rows


def api_messages(conn, conversation_id, limit=200):
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY ts ASC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def api_search(conn, query, limit=50):
    q = (query or "").strip()
    if not q:
        return []
    return db.search_messages(conn, q, limit=limit)


def _auth_ok(headers, params):
    want = os.environ.get("JL_WEB_TOKEN")
    if not want:
        return True
    got = params.get("token") or (headers.get("Authorization", "").replace("Bearer ", ""))
    return got == want


def make_handler(db_path):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path == "/" or u.path == "/index.html":
                return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
            if not _auth_ok(self.headers, params):
                return self._send(401, {"error": "unauthorized"})
            conn = db.connect(db_path)
            try:
                if u.path == "/api/conversations":
                    return self._send(200, api_conversations(conn, params))
                if u.path.startswith("/api/conversations/") and u.path.endswith("/messages"):
                    cid = int(u.path.split("/")[3])
                    return self._send(200, api_messages(conn, cid))
                if u.path == "/api/search":
                    return self._send(200, api_search(conn, params.get("q", "")))
                return self._send(404, {"error": "not found"})
            finally:
                conn.close()

        def log_message(self, *a):
            pass  # quiet
    return H


def serve(conn_path=None, host="0.0.0.0", port=8088):
    db_path = conn_path or db.DEFAULT_DB
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"🌐 AMR inbox: http://{host}:{port}  (db={db_path})")
    httpd.serve_forever()


INDEX_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AMR 收件箱</title><style>
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,system-ui,sans-serif;display:flex;height:100vh}
#side{width:300px;border-right:1px solid #ddd;overflow:auto}#main{flex:1;display:flex;flex-direction:column}
.conv{padding:8px 12px;border-bottom:1px solid #eee;cursor:pointer}.conv:hover{background:#f5f5f5}
.conv .n{font-weight:600}.conv .p{color:#888;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#hdr{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:8px;align-items:center}
#msgs{flex:1;overflow:auto;padding:12px}.m{margin:6px 0}.m .s{font-weight:600;color:#333}.m .t{color:#aaa;font-size:11px;margin-left:6px}
input{padding:6px 8px;border:1px solid #ccc;border-radius:6px;width:100%}
</style></head><body>
<div id=side><div style=padding:8px><input id=q placeholder="🔍 搜索消息 (回车)"></div><div id=list></div></div>
<div id=main><div id=hdr><b id=title>选择会话</b></div><div id=msgs></div></div>
<script>
const E=(s,p='')=>fetch('/api'+s+(p?'?'+p:'')).then(r=>r.json());
function fmt(ts){return new Date(ts*1000).toLocaleString('zh-CN')}
async function loadConvs(){const c=await E('/conversations');document.getElementById('list').innerHTML=
 c.map(x=>`<div class=conv onclick="openConv(${x.id},'${(x.name||'').replace(/'/g,"")}')">
 <div class=n>${x.name||x.chat_id}</div><div class=p>${x.platform} · ${fmt(x.last_activity_at||0)}</div></div>`).join('')}
async function openConv(id,name){document.getElementById('title').textContent=name;
 const m=await E('/conversations/'+id+'/messages');document.getElementById('msgs').innerHTML=
 m.map(x=>`<div class=m><span class=s>${x.sender||''}</span><span class=t>${fmt(x.ts)}</span>
 <div>${(x.content||'').replace(/</g,'&lt;')}</div></div>`).join('')}
document.getElementById('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
 const h=await E('/search','q='+encodeURIComponent(e.target.value));
 document.getElementById('msgs').innerHTML='<h3>搜索结果</h3>'+h.map(x=>`<div class=m>
 <span class=s>${x.sender||''}</span><span class=t>${fmt(x.ts)}</span>
 <div>${(x.content||'').replace(/</g,'&lt;')}</div></div>`).join('')})
loadConvs()
</script></body></html>"""
```

- [ ] **Step 4: green** — `.venv/bin/python -m pytest tests/test_web.py -q` → all pass.
- [ ] **Step 5: commit** — `git add -A && git commit -m "feat(web): read-only stdlib inbox (conversations/messages/search) + UI"`

---

## Task 6: end-to-end against .178 + docs

- [ ] **Step 1: full suite** — `.venv/bin/python -m pytest -q` → all pass; report count.
- [ ] **Step 2: secrets scan** — `./scripts/secrets-scan.sh --all` → clean.
- [ ] **Step 3: live ignite (recent)** — `rm -f ~/.config/jl/jl.db && .venv/bin/jl --migrate && .venv/bin/jl ignite` → prints `点火完成: 新增 N 条` with N>0 (real .178 data). Then `.venv/bin/jl` sweep shows non-⚪ for matched persons; `.venv/bin/jl reset` dry-run shows the counts.
- [ ] **Step 4: web smoke** — launch `.venv/bin/jl web --port 8088 &`, `curl -s localhost:8088/api/conversations | head -c 200`, confirm JSON; `curl -s 'localhost:8088/api/search?q=<a real 3-char substring>'`; kill the server.
- [ ] **Step 5: README** — add an "## 摄取与 Web 收件箱" section: `jl ignite` / `jl poll [--interval]` / `jl web [--port] [--host]`, the optional `JL_WEB_TOKEN`, and that it's read-only (reply = sub-project E). Mark roadmap B + web-MVP done.
- [ ] **Step 6: commit + push** — `git add -A && git commit -m "docs: ingest + web inbox MVP" && git push`

---

## Self-review notes
- **Scope:** B = fullwechat adapter + ingest glue + ignite + poll (recent-first, groups muted, audited). Web MVP = read-only inbox (list/search/read) via stdlib http.server. Out of scope (later): lark/wecom/phone adapters, full backfill driving, media byte-fetch/ASR, reply/send (E), .156 vector, auth beyond a shared token.
- **Types consistent:** adapter emits `ingest.ConvRecord`/`MsgRecord`; `db.ingest_records(account_id, platform, conv, msgs)` consumes them; `ignite` calls `adapter.pull_new(account, recent_limit=)` → `[(conv, msgs)]`; web `api_*` return list[dict]. `msg_key` via `ingest.msg_key(source="fullwx", ...)`.
- **HITL preserved:** read-only web; ignite/poll only ingest (no send); events logged for ignite. groups default-muted to prevent overload.
- **Deploy:** all stdlib, runs on .178's python3.10 unchanged; `jl web --host 0.0.0.0 --port 8088` + a background `jl poll`. Set `JL_WEB_TOKEN` on .178 since it exposes private messages on the LAN.
- **Known follow-up:** `pull_new` re-pulls recent N per conv each cycle (dedup absorbs overlap) — fine for MVP; a true server-cursor incremental is a B+ refinement. Full historical backfill uses the adapter's `backfill()` (implemented, not yet driven by a command).
