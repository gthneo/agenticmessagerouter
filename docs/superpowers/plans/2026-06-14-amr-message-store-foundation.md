# AMR Message-Store Foundation (Sub-project A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the v0.5 `jl` SQLite package into a multi-account message store —
accounts/conversations/messages/media tables with FTS5 search, an ingestion adapter
interface, derived last-interaction (replacing the standalone `interactions` table),
and an HITL-gated 复位 (reset) operation.

**Architecture:** Add four tables to the single-source-of-truth `schema.sql`; add CRUD
+ search + reset helpers to `db.py`; add a pure `ingest.py` (adapter ABC, dataclasses,
`msg_key`/dedup, `blob_path`); refactor `weighting` consumers (the CLI sweep/detail)
to read last-interaction from `messages` instead of writing per-channel snapshots.
No network calls — adapters are interface-only in A. Sending/ignite/poller are B+.

**Tech Stack:** Python 3.10+ stdlib (`sqlite3` with FTS5 trigram, `hashlib`,
`dataclasses`, `abc`), pytest, in-memory SQLite for tests.

**Spec:** `docs/superpowers/specs/2026-06-14-amr-message-store-foundation-design.md`

---

## File Structure

- `src/jl/schema.sql` — **modify**: add `accounts`, `conversations`, `messages`,
  `media` tables + `messages_fts` virtual table + 3 sync triggers; drop the
  `interactions` table block.
- `src/jl/db.py` — **modify**: add account/conversation/message/media CRUD,
  `search_messages`, `derive_last_interactions`, `reset_store`; remove
  `record_interaction`/`latest_interaction`.
- `src/jl/ingest.py` — **create**: `ConvRecord`/`MsgRecord` dataclasses, `IngestAdapter`
  ABC, `msg_key`, `content_hash`, `blob_path` (all pure).
- `src/jl/cli.py` — **modify**: feed `weighting` from `derive_last_interactions`; add
  `reset` command with dry-run→confirm HITL gate; route `reset`.
- `tests/test_db.py` — **modify**: drop interactions-table tests; keep the schema test
  (now asserts new tables).
- `tests/test_ingest.py` — **create**: `msg_key`/`content_hash`/`blob_path` tests.
- `tests/test_store.py` — **create**: accounts/conversations/messages/media CRUD, FTS
  search, derive_last_interactions, reset.
- `tests/test_cli.py` — **modify**: update sweep/detail wiring to the messages-derived
  path; add reset route + gate tests.

**Note on TDD discipline:** every code step is preceded by a failing test and a run
that confirms the failure. Do not write implementation before seeing red.

---

## Task 1: Schema — add the four store tables + FTS

**Files:**
- Modify: `src/jl/schema.sql`
- Test: `tests/test_store.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
"""Message-store tests — accounts/conversations/messages/media + FTS, reset.

Synthetic fixtures only (张三/李四/王五, wxid_test_*, +8613000000000 range).
"""
import pytest

from jl import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_init_db_creates_store_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {"accounts", "conversations", "messages", "media"} <= names


def test_init_db_creates_fts_table(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='messages_fts'"
    ).fetchall()
    assert len(rows) == 1


def test_interactions_table_is_gone(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='interactions'"
    ).fetchall()
    assert rows == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: FAIL — `test_init_db_creates_store_tables` (tables don't exist) and
`test_interactions_table_is_gone` (interactions still present).

- [ ] **Step 3: Edit `src/jl/schema.sql`**

Remove the entire `interactions` table block (the `CREATE TABLE IF NOT EXISTS
interactions (...)` and its `idx_interactions_channel_ts` index). Then append the new
tables after the `channels` table (before `events`):

```sql
-- the user's own login identities (inboxes we ingest FROM); 8-bit id space
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY CHECK (account_id BETWEEN 0 AND 255),
    platform   TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    self_id    TEXT NOT NULL DEFAULT '',
    host       TEXT NOT NULL DEFAULT '',
    cred_ref   TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE (platform, self_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    platform      TEXT NOT NULL,
    chat_id       TEXT NOT NULL,
    type          TEXT NOT NULL DEFAULT 'private',
    name          TEXT NOT NULL DEFAULT '',
    person_id     TEXT REFERENCES persons(id),
    muted         INTEGER NOT NULL DEFAULT 0,
    unread        INTEGER NOT NULL DEFAULT 0,
    last_activity_at INTEGER,
    backfill_done   INTEGER NOT NULL DEFAULT 0,
    backfill_cursor TEXT NOT NULL DEFAULT '',
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    UNIQUE (account_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_person ON conversations(person_id);
CREATE INDEX IF NOT EXISTS idx_conv_activity ON conversations(last_activity_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL,
    platform     TEXT NOT NULL,
    msg_key      TEXT NOT NULL,
    ts           INTEGER NOT NULL,
    sender       TEXT NOT NULL DEFAULT '',
    sender_id    TEXT NOT NULL DEFAULT '',
    direction    TEXT NOT NULL DEFAULT 'in',
    type         TEXT NOT NULL DEFAULT 'text',
    content      TEXT NOT NULL DEFAULT '',
    media_ref    TEXT NOT NULL DEFAULT '',
    is_mentioned INTEGER NOT NULL DEFAULT 0,
    embedding_id INTEGER,
    raw          TEXT NOT NULL DEFAULT '{}',
    recorded_at  INTEGER NOT NULL,
    UNIQUE (conversation_id, msg_key)
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conversation_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts DESC);

CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT,
    message_id  INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT '',
    mime        TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    ext         TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    source_ref  TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    transcript  TEXT NOT NULL DEFAULT '',
    fetched_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_media_message ON media(message_id);
CREATE INDEX IF NOT EXISTS idx_media_sha ON media(sha256);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, sender,
    content='messages', content_rowid='id',
    tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
    INSERT INTO messages_fts(rowid, content, sender) VALUES (new.id, new.content, new.sender);
END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, sender) VALUES('delete', old.id, old.content, old.sender);
END;
CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
    INSERT INTO messages_fts(messages_fts, rowid, content, sender) VALUES('delete', old.id, old.content, old.sender);
    INSERT INTO messages_fts(rowid, content, sender) VALUES (new.id, new.content, new.sender);
END;
```

- [ ] **Step 4: Run test to verify the schema tests pass**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: 3 passed. (Other suites will break — fixed in Tasks 5–7.)

- [ ] **Step 5: Commit**

```bash
git add src/jl/schema.sql tests/test_store.py
git commit -m "feat(store): add accounts/conversations/messages/media + FTS schema"
```

---

## Task 2: `ingest.py` — pure dataclasses, adapter ABC, msg_key, blob_path

**Files:**
- Create: `src/jl/ingest.py`
- Test: `tests/test_ingest.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_ingest.py`:

```python
"""Pure ingestion helpers — msg_key, content_hash, blob_path, dataclasses."""
from jl import ingest


def test_msg_key_uses_stable_id_when_present():
    assert ingest.msg_key(source="fullwx", stable_id="12345") == "fullwx:12345"


def test_msg_key_falls_back_to_content_hash():
    k = ingest.msg_key(source="powerdata", stable_id=None,
                       ts=1000, sender="张三", content="明天见")
    assert k.startswith("h:")
    assert len(k) == 2 + 16  # "h:" + 16 hex chars


def test_msg_key_content_hash_is_stable_for_same_inputs():
    a = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    b = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    assert a == b


def test_msg_key_content_hash_differs_on_different_content():
    a = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="hi")
    b = ingest.msg_key(source="x", stable_id=None, ts=1000, sender="张三", content="yo")
    assert a != b


def test_content_hash_minute_collision_is_intentional():
    # same minute + sender + content is treated as the same message
    h1 = ingest.content_hash(ts=1000, sender="张三", content="hi")
    h2 = ingest.content_hash(ts=1000, sender="张三", content="hi")
    assert h1 == h2


def test_blob_path_is_sharded_by_hash_prefix():
    sha = "ab" + "0" * 62
    assert ingest.blob_path(sha) == "blobs/ab/" + sha


def test_blob_path_with_root():
    sha = "cd" + "1" * 62
    assert ingest.blob_path(sha, root="/data") == "/data/blobs/cd/" + sha


def test_msgrecord_defaults():
    m = ingest.MsgRecord(msg_key="x:1", ts=10, content="hi")
    assert m.direction == "in"
    assert m.type == "text"
    assert m.sender == ""


def test_convrecord_defaults():
    c = ingest.ConvRecord(chat_id="c1", name="张三")
    assert c.type == "private"
    assert c.muted is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -q`
Expected: FAIL — `cannot import name 'ingest'`.

- [ ] **Step 3: Create `src/jl/ingest.py`**

```python
"""Pure ingestion contracts shared by all channel adapters (B implements them).

No I/O here — dataclasses, the adapter ABC, dedup-key helpers, and the
content-addressed blob path. Adapters in sub-project B import these so dedup and
storage layout are uniform across platforms.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field


@dataclass
class ConvRecord:
    """Normalized conversation as an adapter reports it."""
    chat_id: str
    name: str = ""
    type: str = "private"            # private | group
    muted: bool = False
    unread: int = 0
    last_activity_at: int | None = None


@dataclass
class MsgRecord:
    """Normalized message as an adapter reports it."""
    msg_key: str
    ts: int
    content: str = ""
    sender: str = ""
    sender_id: str = ""
    direction: str = "in"            # in | out
    type: str = "text"
    media_ref: str = ""
    is_mentioned: bool = False
    raw: dict = field(default_factory=dict)


def content_hash(*, ts: int, sender: str, content: str) -> str:
    """16-hex digest of (minute-resolution ts, sender, content).

    Same minute + sender + content collides on purpose — that is a true duplicate
    for text backends that lack a stable message id.
    """
    minute = ts - (ts % 60)
    h = hashlib.sha1(f"{minute}|{sender}|{content}".encode("utf-8"))
    return h.hexdigest()[:16]


def msg_key(*, source: str, stable_id: str | None,
            ts: int = 0, sender: str = "", content: str = "") -> str:
    """Stable dedup key. Prefer the platform id; else a content hash."""
    if stable_id:
        return f"{source}:{stable_id}"
    return "h:" + content_hash(ts=ts, sender=sender, content=content)


def blob_path(sha256: str, root: str = "") -> str:
    """Content-addressed path, sharded by the first two hex chars."""
    rel = f"blobs/{sha256[:2]}/{sha256}"
    return f"{root.rstrip('/')}/{rel}" if root else rel


class IngestAdapter(abc.ABC):
    """Contract every channel adapter (B) implements."""
    platform: str = ""

    @abc.abstractmethod
    def list_conversations(self, account) -> list[ConvRecord]: ...

    @abc.abstractmethod
    def backfill(self, account, conv, cursor: str) -> tuple[list[MsgRecord], str]:
        """Return (messages, next_cursor); '' next_cursor means done."""

    @abc.abstractmethod
    def pull_new(self, account) -> list[MsgRecord]:
        """Incremental since last call (server cursor or max-ts)."""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_ingest.py -q`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add src/jl/ingest.py tests/test_ingest.py
git commit -m "feat(ingest): pure adapter ABC, dataclasses, msg_key, blob_path"
```

---

## Task 3: Account + conversation CRUD

**Files:**
- Modify: `src/jl/db.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_store.py`)

```python
def test_upsert_account_idempotent_and_8bit(conn):
    db.upsert_account(conn, account_id=1, platform="wechat",
                      label="personal #1", self_id="wxid_self_a")
    db.upsert_account(conn, account_id=1, platform="wechat",
                      label="renamed", self_id="wxid_self_a")
    accts = db.get_accounts(conn)
    assert len(accts) == 1
    assert accts[0]["label"] == "renamed"


def test_account_id_rejects_out_of_8bit_range(conn):
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_account(conn, account_id=256, platform="wechat", self_id="x")


def test_upsert_conversation_idempotent_on_account_chat(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_a")
    cid1 = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                  chat_id="c1", name="张三", type="private")
    cid2 = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                  chat_id="c1", name="张三改", type="private")
    assert cid1 == cid2
    convs = db.get_conversations(conn)
    assert len(convs) == 1
    assert convs[0]["name"] == "张三改"


def test_set_muted_and_filter(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_a")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="g1", name="噪音群", type="group")
    db.set_muted(conn, cid, True)
    assert db.get_conversations(conn, muted=False) == []
    muted = db.get_conversations(conn, muted=True)
    assert len(muted) == 1


def test_link_person_sets_person_id(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_a")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三", type="private")
    db.link_person(conn, cid, "u1")
    convs = db.get_conversations(conn, person_id="u1")
    assert len(convs) == 1 and convs[0]["person_id"] == "u1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -k "account or conversation or muted or link_person" -q`
Expected: FAIL — `module 'jl.db' has no attribute 'upsert_account'`.

- [ ] **Step 3: Add to `src/jl/db.py`** (after the channels section, before events)

```python
# ----- accounts -------------------------------------------------------------

def upsert_account(conn, *, account_id, platform, label="", self_id="",
                   host="", cred_ref=""):
    conn.execute(
        """
        INSERT INTO accounts (account_id, platform, label, self_id, host, cred_ref,
                              created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            platform=excluded.platform, label=excluded.label,
            self_id=excluded.self_id, host=excluded.host, cred_ref=excluded.cred_ref
        """,
        (account_id, platform, label, self_id, host, cred_ref, _now()),
    )
    conn.commit()
    return account_id


def get_accounts(conn):
    rows = conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
    return [dict(r) for r in rows]


# ----- conversations --------------------------------------------------------

def upsert_conversation(conn, *, account_id, platform, chat_id, name="",
                        type="private", unread=0, last_activity_at=None):
    now = _now()
    conn.execute(
        """
        INSERT INTO conversations (account_id, platform, chat_id, name, type,
                                   unread, last_activity_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id, chat_id) DO UPDATE SET
            name=excluded.name, type=excluded.type, unread=excluded.unread,
            last_activity_at=COALESCE(excluded.last_activity_at,
                                      conversations.last_activity_at),
            updated_at=excluded.updated_at
        """,
        (account_id, platform, chat_id, name, type, unread, last_activity_at,
         now, now),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM conversations WHERE account_id=? AND chat_id=?",
        (account_id, chat_id),
    ).fetchone()
    return row["id"]


def get_conversations(conn, *, muted=None, person_id="__all__", account_id=None):
    sql = "SELECT * FROM conversations WHERE 1=1"
    args = []
    if muted is not None:
        sql += " AND muted=?"
        args.append(1 if muted else 0)
    if person_id != "__all__":
        sql += " AND person_id IS ?" if person_id is None else " AND person_id=?"
        if person_id is not None:
            args.append(person_id)
    if account_id is not None:
        sql += " AND account_id=?"
        args.append(account_id)
    sql += " ORDER BY last_activity_at DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def set_muted(conn, conversation_id, muted):
    conn.execute("UPDATE conversations SET muted=?, updated_at=? WHERE id=?",
                 (1 if muted else 0, _now(), conversation_id))
    conn.commit()


def link_person(conn, conversation_id, person_id):
    conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                 (person_id, _now(), conversation_id))
    conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_store.py -k "account or conversation or muted or link_person" -q`
Expected: all selected pass.

- [ ] **Step 5: Commit**

```bash
git add src/jl/db.py tests/test_store.py
git commit -m "feat(store): account + conversation CRUD (8-bit, mute, person link)"
```

---

## Task 4: Message insert with dedup + FTS, and search

**Files:**
- Modify: `src/jl/db.py`
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_store.py`)

```python
from jl import ingest


def _seed_conv(conn, account_id=1, chat_id="c1"):
    db.upsert_account(conn, account_id=account_id, platform="wechat",
                      self_id=f"wxid_self_{account_id}")
    return db.upsert_conversation(conn, account_id=account_id, platform="wechat",
                                  chat_id=chat_id, name="张三", type="private")


def test_insert_messages_dedups_on_msg_key(conn):
    cid = _seed_conv(conn)
    recs = [ingest.MsgRecord(msg_key="fullwx:1", ts=1000, content="明天见", sender="张三")]
    assert db.insert_messages(conn, cid, recs) == 1
    assert db.insert_messages(conn, cid, recs) == 0   # same key ignored
    n = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE conversation_id=?",
                     (cid,)).fetchone()["n"]
    assert n == 1


def test_insert_messages_updates_last_activity(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=5000, content="hi")])
    conv = db.get_conversations(conn)[0]
    assert conv["last_activity_at"] == 5000


def test_search_messages_finds_cjk_substring(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="x:1", ts=1000, content="记得带合同来", sender="张三"),
        ingest.MsgRecord(msg_key="x:2", ts=2000, content="今天天气不错", sender="李四"),
    ])
    hits = db.search_messages(conn, "合同")
    assert len(hits) == 1
    assert hits[0]["content"] == "记得带合同来"


def test_search_messages_account_filter(conn):
    c1 = _seed_conv(conn, account_id=1, chat_id="c1")
    c2 = _seed_conv(conn, account_id=2, chat_id="c2")
    db.insert_messages(conn, c1, [ingest.MsgRecord(msg_key="x:1", ts=1, content="合同A")])
    db.insert_messages(conn, c2, [ingest.MsgRecord(msg_key="x:2", ts=2, content="合同B")])
    hits = db.search_messages(conn, "合同", account_id=1)
    assert [h["content"] for h in hits] == ["合同A"]


def test_delete_message_removes_from_fts(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="合同")])
    conn.execute("DELETE FROM messages")
    conn.commit()
    assert db.search_messages(conn, "合同") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -k "insert_messages or search" -q`
Expected: FAIL — `module 'jl.db' has no attribute 'insert_messages'`.

- [ ] **Step 3: Add to `src/jl/db.py`** (after the conversations section)

```python
# ----- messages -------------------------------------------------------------

def insert_messages(conn, conversation_id, records):
    """Insert MsgRecords with dedup on (conversation_id, msg_key). Returns count
    inserted and bumps the conversation's last_activity_at to the newest ts."""
    conv = conn.execute(
        "SELECT account_id, platform FROM conversations WHERE id=?",
        (conversation_id,),
    ).fetchone()
    if conv is None:
        raise ValueError(f"no conversation {conversation_id}")
    inserted = 0
    max_ts = 0
    now = _now()
    for r in records:
        cur = conn.execute(
            """INSERT OR IGNORE INTO messages
                   (conversation_id, account_id, platform, msg_key, ts, sender,
                    sender_id, direction, type, content, media_ref, is_mentioned,
                    raw, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conversation_id, conv["account_id"], conv["platform"], r.msg_key, r.ts,
             r.sender, r.sender_id, r.direction, r.type, r.content, r.media_ref,
             1 if r.is_mentioned else 0,
             json.dumps(r.raw, ensure_ascii=False), now),
        )
        inserted += cur.rowcount
        if r.ts > max_ts:
            max_ts = r.ts
    if max_ts:
        conn.execute(
            """UPDATE conversations
               SET last_activity_at = MAX(COALESCE(last_activity_at, 0), ?),
                   updated_at=?
               WHERE id=?""",
            (max_ts, now, conversation_id),
        )
    conn.commit()
    return inserted


def search_messages(conn, query, *, limit=50, account_id=None):
    """FTS5 keyword search over message content, newest-relevant first."""
    sql = """
        SELECT m.* FROM messages_fts f
        JOIN messages m ON m.id = f.rowid
        WHERE messages_fts MATCH ?
    """
    args = [query]
    if account_id is not None:
        sql += " AND m.account_id=?"
        args.append(account_id)
    sql += " ORDER BY bm25(messages_fts), m.ts DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_store.py -k "insert_messages or search or delete_message" -q`
Expected: all selected pass.

- [ ] **Step 5: Commit**

```bash
git add src/jl/db.py tests/test_store.py
git commit -m "feat(store): message insert with dedup + FTS5 trigram search"
```

---

## Task 5: Derived last-interaction (replace `interactions`)

**Files:**
- Modify: `src/jl/db.py` (remove `record_interaction`/`latest_interaction`, add
  `derive_last_interactions`)
- Modify: `tests/test_db.py` (remove the two interactions tests)
- Test: `tests/test_store.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_store.py`)

```python
def test_derive_last_interactions_latest_per_platform(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz",
                     threshold_days=3, aliases=[])
    # wechat conversation linked to u1
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    wc = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                chat_id="c1", name="张三", type="private")
    db.link_person(conn, wc, "u1")
    db.insert_messages(conn, wc, [
        ingest.MsgRecord(msg_key="w:1", ts=1000, content="早", sender="张三"),
        ingest.MsgRecord(msg_key="w:2", ts=3000, content="晚", sender="张三"),
    ])
    # phone conversation linked to u1
    db.upsert_account(conn, account_id=2, platform="phone", self_id="me_phone")
    pc = db.upsert_conversation(conn, account_id=2, platform="phone",
                                chat_id="+8613000000001", type="private")
    db.link_person(conn, pc, "u1")
    db.insert_messages(conn, pc, [
        ingest.MsgRecord(msg_key="p:1", ts=2000, content="call", sender="张三"),
    ])

    out = db.derive_last_interactions(conn, "u1")
    assert out["wechat"]["ts"] == 3000
    assert out["wechat"]["summary"] == "晚"
    assert out["phone"]["ts"] == 2000


def test_derive_last_interactions_empty_for_unlinked(conn):
    db.upsert_person(conn, id="u9", name="无会话", category="x",
                     threshold_days=3, aliases=[])
    assert db.derive_last_interactions(conn, "u9") == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -k derive -q`
Expected: FAIL — `module 'jl.db' has no attribute 'derive_last_interactions'`.

- [ ] **Step 3: Edit `src/jl/db.py`**

Delete the `record_interaction` and `latest_interaction` functions (the
`# ----- interactions` block). Add in their place:

```python
# ----- derived last-interaction (replaces the interactions table) -----------

def derive_last_interactions(conn, person_id):
    """Latest message per platform across all conversations linked to a person.
    Returns {platform: {"ts": int, "summary": str}}."""
    rows = conn.execute(
        """
        SELECT m.platform, m.ts, m.sender, m.content
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE c.person_id = ?
        ORDER BY m.platform, m.ts DESC
        """,
        (person_id,),
    ).fetchall()
    out = {}
    for r in rows:
        if r["platform"] in out:
            continue  # rows are ts-desc within platform → first seen is newest
        summary = r["content"] or ""
        out[r["platform"]] = {"ts": r["ts"], "summary": summary}
    return out
```

- [ ] **Step 4: Edit `tests/test_db.py`**

Delete `test_record_interaction_keeps_latest_per_channel` and
`test_record_interaction_dedups_same_channel_and_ts` (they tested the removed
table). In `test_init_db_creates_five_tables`, drop `"interactions"` from the asserted
set so it reads:

```python
    assert {"persons", "channels", "events", "tokens"} <= names
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_store.py tests/test_db.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/jl/db.py tests/test_store.py tests/test_db.py
git commit -m "feat(store): derive last-interaction from messages; drop interactions table"
```

---

## Task 6: Wire CLI sweep/detail to the messages-derived path

**Files:**
- Modify: `src/jl/cli.py`
- Test: `tests/test_cli.py`

The v0.5 CLI gathered per-channel snapshots live and wrote `interactions`. Now sweep/
detail read freshness from `derive_last_interactions` (data the poller/ingest fills).
A keeps the live-adapter `_gather` for `detail`'s on-demand reach but stops writing the
removed table; sweep reads derived data.

- [ ] **Step 1: Write the failing test** (replace the seeded-fixture wiring tests in
  `tests/test_cli.py`)

Replace `test_sweep_persists_interaction_event_and_tokens` and
`test_detail_writes_audit_trace` with:

```python
def test_sweep_reads_derived_interactions(monkeypatch, capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz",
                     threshold_days=3, aliases=["老张"])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三", type="private")
    db.link_person(conn, cid, "u1")
    from jl import ingest
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="w:1", ts=1_000_000, content="hi", sender="张三")])
    cli.cmd_sweep(conn, {})
    out = capsys.readouterr().out
    assert "张三" in out
    assert "sweep" in [e["kind"] for e in db.get_events(conn)]
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k derived -q`
Expected: FAIL — `cmd_sweep` still calls the removed `record_interaction` /
per-channel gather path (AttributeError or wrong output).

- [ ] **Step 3: Edit `src/jl/cli.py`**

Replace `cmd_sweep` so it builds weighting signals from `derive_last_interactions`:

```python
def cmd_sweep(conn, ctx):
    persons = db.get_persons(conn)
    print(f"\n🟢🟡🔴 关系账户健康度 — {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"{'姓名':<18} {'类别':<14} {'综合(天)':<10} {'渠道':<8} {'状态'}")
    print("─" * 70)
    red = []
    for p in persons:
        last = db.derive_last_interactions(conn, p["id"])
        signals = [{"kind": plat, "ts": d["ts"]} for plat, d in last.items()]
        chosen = weighting.combine(signals)
        comb_d = chosen["days"] if chosen else None
        col = weighting.color(comb_d, p["threshold_days"])
        via = chosen["kind"] if chosen else "-"
        print(f"{p['name']:<18} {p['category']:<14} {days_str(comb_d):<10} {via:<8} {col}")
        if col == "🔴":
            red.append((p, comb_d))
    if red:
        print("\n🔴 红色清单 (建议主动联络, 发不发你决定):")
        for p, d in red:
            d_s = f"{d:.1f} 天" if d is not None else "全渠道空"
            print(f"  • {p['name']:<14} 距上次互动 {d_s} (阈值 {p['threshold_days']} 天)")
    db.log_event(conn, kind="sweep", actor=_actor(),
                 detail={"persons": len(persons), "red": len(red)})
```

Replace `cmd_detail`'s body that used `_gather`/`record_interaction` with a derived
read (keep the person-resolve and audit):

```python
def cmd_detail(conn, ctx, name):
    p = _find_person(conn, name)
    if not p:
        names = ", ".join(x["name"] for x in db.get_persons(conn))
        print(f"❌ 找不到 {name}. 可选: {names}")
        return
    print(f"\n=== {p['name']} ({p['category']}) ===")
    print(f"别名: {', '.join(p['aliases']) or '-'}")
    print(f"阈值: {p['threshold_days']} 天")
    last = db.derive_last_interactions(conn, p["id"])
    if not last:
        print("(无消息记录)")
    signals = []
    for plat, d in last.items():
        signals.append({"kind": plat, "ts": d["ts"]})
        days = weighting.days_since(d["ts"])
        print(f"  {plat:<8} last: {d['summary']} ({days:.1f} 天前)")
    chosen = weighting.combine(signals)
    if chosen:
        col = weighting.color(chosen["days"], p["threshold_days"])
        print(f"\n综合: {col} {chosen['days']:.1f} 天 (via {chosen['kind']})")
    db.log_event(conn, kind="detail", person_id=p["id"], actor=_actor(), detail={})
```

Remove the now-unused `_gather` function and the `_ADAPTERS` dict and the
`record_tokens` sweep/detail calls and the wechat/phone adapter imports **only if
nothing else references them** — verify with `grep -n "_gather\|_ADAPTERS\|record_tokens\|channels" src/jl/cli.py` and delete dead lines. Keep `cmd_tokens` and `record_tokens` in `db.py` (used elsewhere). The `ctx`/`wx_url` plumbing in `main()` for sweep/detail can be simplified to `ctx = {}`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass (the old per-channel sweep tests are replaced; `test_channels.py`
parse/match tests stay green since the adapters still exist for B).

- [ ] **Step 5: Commit**

```bash
git add src/jl/cli.py tests/test_cli.py
git commit -m "refactor(cli): sweep/detail read freshness from derived messages"
```

---

## Task 7: 复位 reset — HITL-gated wipe

**Files:**
- Modify: `src/jl/db.py` (add `reset_store`)
- Modify: `src/jl/cli.py` (add `cmd_reset` + route)
- Test: `tests/test_store.py`, `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_store.py`)

```python
def test_reset_store_dry_run_counts_without_deleting(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    counts = db.reset_store(conn, dry_run=True)
    assert counts["messages"] == 1
    assert counts["conversations"] == 1
    # nothing deleted
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 1


def test_reset_store_confirm_wipes_messages_and_conversations(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    db.reset_store(conn, dry_run=False)
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM conversations").fetchone()["n"] == 0


def test_reset_store_keeps_persons(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz",
                     threshold_days=3, aliases=[])
    _seed_conv(conn)
    db.reset_store(conn, dry_run=False)
    assert len(db.get_persons(conn)) == 1


def test_reset_store_all_clears_accounts(conn):
    _seed_conv(conn)
    db.reset_store(conn, dry_run=False, include_accounts=True)
    assert db.get_accounts(conn) == []


def test_reset_store_channel_scope(conn):
    c1 = _seed_conv(conn, account_id=1, chat_id="c1")  # wechat
    db.upsert_account(conn, account_id=2, platform="phone", self_id="me_phone")
    c2 = db.upsert_conversation(conn, account_id=2, platform="phone",
                                chat_id="+8613000000001", type="private")
    db.insert_messages(conn, c1, [ingest.MsgRecord(msg_key="x:1", ts=1, content="a")])
    db.insert_messages(conn, c2, [ingest.MsgRecord(msg_key="y:1", ts=1, content="b")])
    db.reset_store(conn, dry_run=False, platform="wechat")
    plats = [c["platform"] for c in db.get_conversations(conn)]
    assert plats == ["phone"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_store.py -k reset -q`
Expected: FAIL — `module 'jl.db' has no attribute 'reset_store'`.

- [ ] **Step 3: Add `reset_store` to `src/jl/db.py`**

```python
# ----- reset (destructive; HITL-gated at the CLI layer) ---------------------

def reset_store(conn, *, dry_run=True, platform=None, include_accounts=False):
    """Count (dry_run) or wipe ingested store data. Never touches persons.
    Returns a dict of affected-row counts. CASCADE handles media via messages."""
    where = ""
    args = []
    if platform is not None:
        where = " WHERE platform=?"
        args = [platform]
    counts = {
        "messages": conn.execute(
            f"SELECT COUNT(*) AS n FROM messages{where}", args).fetchone()["n"],
        "conversations": conn.execute(
            f"SELECT COUNT(*) AS n FROM conversations{where}", args).fetchone()["n"],
        "media": conn.execute(
            """SELECT COUNT(*) AS n FROM media WHERE message_id IN
               (SELECT id FROM messages%s)""" % where, args).fetchone()["n"],
    }
    if include_accounts:
        counts["accounts"] = conn.execute(
            f"SELECT COUNT(*) AS n FROM accounts{where}", args).fetchone()["n"]
    if dry_run:
        return counts
    # delete conversations first → CASCADE removes their messages + media + fts
    conn.execute(f"DELETE FROM conversations{where}", args)
    # belt-and-suspenders for any orphan messages
    conn.execute(f"DELETE FROM messages{where}", args)
    if include_accounts:
        conn.execute(f"DELETE FROM accounts{where}", args)
    conn.commit()
    return counts
```

Note: CASCADE requires `PRAGMA foreign_keys=ON` (already set in `connect`).

- [ ] **Step 4: Run store tests**

Run: `.venv/bin/python -m pytest tests/test_store.py -k reset -q`
Expected: all reset tests pass.

- [ ] **Step 5: Write the failing CLI test** (append to `tests/test_cli.py`)

```python
def test_route_reset():
    assert cli.route(["reset"]) == ("reset", {"confirm": False, "platform": None,
                                              "include_accounts": False})


def test_route_reset_confirm_all():
    cmd, params = cli.route(["reset", "--confirm", "--all"])
    assert cmd == "reset"
    assert params["confirm"] is True
    assert params["include_accounts"] is True


def test_cmd_reset_dry_run_does_not_delete(capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三")
    from jl import ingest
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    cli.cmd_reset(conn, {"confirm": False, "platform": None, "include_accounts": False})
    out = capsys.readouterr().out
    assert "dry-run" in out.lower() or "确认" in out
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 1
    conn.close()


def test_cmd_reset_confirm_wipes_and_audits(capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三")
    from jl import ingest
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    cli.cmd_reset(conn, {"confirm": True, "platform": None, "include_accounts": False})
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 0
    assert "reset" in [e["kind"] for e in db.get_events(conn)]
    conn.close()
```

- [ ] **Step 6: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_cli.py -k reset -q`
Expected: FAIL — `route` returns `("detail", {"name": "reset"})` and `cmd_reset`
doesn't exist.

- [ ] **Step 7: Edit `src/jl/cli.py`**

In `route`, before the final `return ("detail", ...)`:

```python
    if a == "reset":
        return ("reset", {
            "confirm": "--confirm" in args,
            "platform": _opt_value(args, "--channel"),
            "include_accounts": "--all" in args,
        })
```

Add the helper near `route`:

```python
def _opt_value(args, flag):
    """Return the value following `flag` in args, or None."""
    if flag in args:
        i = args.index(flag)
        if i + 1 < len(args):
            return args[i + 1]
    return None
```

Add the command:

```python
def cmd_reset(conn, params):
    counts = db.reset_store(conn, dry_run=True,
                            platform=params["platform"],
                            include_accounts=params["include_accounts"])
    scope = params["platform"] or ("ALL channels" + (" + accounts"
                                                     if params["include_accounts"] else ""))
    print(f"\n⚠️ 复位 reset — 影响范围: {scope}")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    if not params["confirm"]:
        print("\n这是 dry-run。确认无误后加 --confirm 真正清除 (persons 不受影响)。")
        return
    # audit BEFORE the wipe so the trace survives it
    db.log_event(conn, kind="reset", actor=_actor(),
                 detail={"scope": scope, "counts": counts})
    db.reset_store(conn, dry_run=False, platform=params["platform"],
                   include_accounts=params["include_accounts"])
    print("\n✅ 已清除。可重新点火 (jl ignite — B 阶段) 灌入。")
```

Register in `_DISPATCH`:

```python
    "reset": cmd_reset,
```

And in `main`, the dispatch for non-detail commands already passes the params dict for
`detail` only; ensure `reset` gets its params. Update `main`:

```python
    if command == "detail":
        cmd_detail(conn, ctx, params["name"])
    elif command == "reset":
        cmd_reset(conn, params)
    else:
        _DISPATCH[command](conn, ctx)
```

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add src/jl/db.py src/jl/cli.py tests/test_store.py tests/test_cli.py
git commit -m "feat(reset): HITL-gated 复位 wipe (dry-run -> confirm), audit-logged"
```

---

## Task 8: End-to-end verification + docs

**Files:**
- Modify: `README.md` (note the message-store tables + reset command)
- Test: manual end-to-end

- [ ] **Step 1: Recreate the local db with the new schema**

The dogfood `~/.config/jl/jl.db` has the old schema. Recreate it:

```bash
rm -f ~/.config/jl/jl.db
.venv/bin/jl --migrate
```
Expected: `✅ migration 完成: N 人 → SQLite`.

- [ ] **Step 2: Verify reset dry-run is safe**

```bash
.venv/bin/jl reset
```
Expected: prints scope + counts (all 0 on a fresh store) and the dry-run notice; deletes nothing.

- [ ] **Step 3: Run the full suite once more**

Run: `.venv/bin/python -m pytest -q`
Expected: all green.

- [ ] **Step 4: Secrets scan**

Run: `./scripts/secrets-scan.sh --all`
Expected: `clean` (exit 0). No real data introduced.

- [ ] **Step 5: Update README**

Add to the architecture/commands section: the four store tables
(accounts/conversations/messages/media), FTS5 search, and the `jl reset` command
(dry-run → `--confirm`). Add a one-line roadmap check that A (message-store
foundation) is done and B (ingestion + 5-min poller + ignite) is next.

- [ ] **Step 6: Commit and push**

```bash
git add README.md
git commit -m "docs: message-store foundation (A) + reset; B is next"
git push
```

---

## Self-Review Notes

- **Spec coverage**: accounts/conversations/messages/media + FTS (Tasks 1,3,4) ✓;
  8-bit CHECK (Task 3) ✓; dedup via msg_key (Tasks 2,4) ✓; media/blob_path (Task 2) ✓;
  derive last-interaction replacing interactions (Task 5) ✓; ingestion adapter ABC
  (Task 2) ✓; 复位 reset HITL-gated (Task 7) ✓; weighting refactor (Task 6) ✓;
  regression of existing tests (Tasks 5,6,8) ✓. 点火 ignite is explicitly B (noted in
  Task 7 message + README) — not implemented here, by design.
- **Out of scope (correctly absent)**: live adapters, poller, API, UI, sending,
  embeddings, blob-file GC, byte fetch, ASR.
- **Type consistency**: `MsgRecord`/`ConvRecord` fields used in Tasks 4–7 match the
  Task 2 definitions; `insert_messages(conn, conv_id, [MsgRecord])`,
  `derive_last_interactions(conn, person_id) -> {platform: {ts, summary}}`,
  `reset_store(conn, *, dry_run, platform, include_accounts)` are consistent across
  db + cli + tests.
- **Known dependency**: Task 6 deletes `_gather`/`_ADAPTERS` from cli; the wechat/phone
  adapter modules remain (B uses them) and `test_channels.py` keeps testing their pure
  parsers.
