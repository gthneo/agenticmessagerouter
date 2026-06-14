# Sub-project A — AMR Message-Store Foundation

**Date:** 2026-06-14
**Status:** approved design, ready for implementation plan
**Product:** Agentic Messages Router (AMR) — `jl` 关系账户 router 演进为统一消息库
**Scope:** Sub-project A of A→B→C→D→E decomposition (this doc covers **A only**)

## Context

v0.5 delivered a SQLite-backed relationship-account audit (`persons`/`channels`/
`interactions`/`events`/`tokens`) with a CLI. The product target (王总 2026-06-14)
is bigger: a **unified inbox** — ingest *all* conversations/messages from every
connected account (multiple WeChat / WeCom / Feishu per user, plus phone), store
them centrally (DB on `.156`), let the human read everything in a **browser UI**
and reply back. Sending is gated by human-in-the-loop (approval + partial
whitelist), per the project's highest rule.

That full product is decomposed:

| Sub-project | Content | Depends |
|---|---|---|
| **A message-store foundation** | schema (accounts/conversations/messages + FTS5 + 8-bit multi-account), ingestion interface, dedup | — |
| B ingestion + 5-min poller | 4 channels backfill+incremental (fullwechat/lark-cli/wecom-cli/CallHistory), mute, edge-push | A |
| C backend API | `.156` REST/WS: list/search/conversations/reply-queue | A |
| D browser unified inbox UI | read all + reply (reply → approval outbox) | C |
| E sending | per-channel send + outbox approval + whitelist | B, D |

**This spec = A.** It extends the existing v0.5 `jl` package (does not replace it).

## Decisions locked (王总 2026-06-14)

1. **Search: FTS5 first** (keyword). Vector/semantic (sqlite-vec + Ollama on `.156`,
   handoff N8) comes later — `messages` reserves an embedding linkage but A does not
   compute embeddings.
2. **Build locally first, migrate to `.156` later.** A runs against the local
   `~/.config/jl/jl.db`; nothing in A hard-codes a host, so the same code runs on `.156`.
3. **Order A→B→C→D→E approved.**
4. **A extends v0.5**: add `accounts`/`conversations`/`messages` (+FTS), keep
   `persons`/`channels`/`events`/`tokens`, and **derive last-interaction from
   `messages`** instead of the standalone `interactions` table.

## First-batch channels (defined here, implemented in B)

WeChat (fullwechat REST @`.178`, structured) · Feishu (`lark-cli`) ·
WeCom 企业微信 (`wecom-cli`) · Phone (local CallHistory). Email/iMessage later.

## Goals / Non-goals

**A delivers:**
- Schema for multi-account conversation + message storage with dedup and FTS.
- `media` table + content-addressed `blob_path` helper (reference + storage design;
  byte fetch / ASR / PDF-extraction are B).
- `db.py` CRUD + FTS search + derived last-interaction query.
- A platform-agnostic **ingestion adapter interface** (ABC) + pure `msg_key`/dedup
  helpers — so B can drop in fullwechat/lark/wecom/phone adapters.
- Refactor `weighting` consumers to read last-interaction from `messages`.
- **复位 (reset)**: store-level destructive wipe of ingested data, HITL-gated
  (dry-run → confirm → wipe) and audit-logged. See Operations below.

**A does NOT include** (later sub-projects): the actual platform adapters, the
`jl ignite` live pull, and the 5-min poller (B); the backend API/WS (C); the browser
UI (D); sending/outbox (E); embeddings (N8). No network calls are made in A —
adapters are interface-only.

## Architecture (target; A is the bottom box)

```
        Browser unified inbox (read all + reply)            [D]
                     ↓ HTTP/WS
        AMR backend API (.156)                              [C]
        └── SQLite: messages + FTS5 (+ sqlite-vec later)    [A]  ← THIS SPEC
                     ↑ ingest (push)
   edge collectors / adapters (run where the data lives)    [B]
   fullwechat@.178 REST · lark-cli · wecom-cli · CallHistory(Mac-local)
```

## Data model

8-bit multi-account: one human owns many login identities (multiple WeChat / WeCom /
Feishu). Each is an `account` (id 0–255). Conversations and messages carry
`account_id` so we always know *which of my inboxes* a message arrived through. The
same external contact across accounts is reconciled at the `persons` layer.

### New tables (added to `schema.sql`)

```sql
-- the user's own login identities (inboxes we ingest FROM)
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY CHECK (account_id BETWEEN 0 AND 255), -- 8-bit
    platform   TEXT NOT NULL,            -- wechat / wecom / feishu / phone
    label      TEXT NOT NULL DEFAULT '', -- human label e.g. "personal WeChat #1"
    self_id    TEXT NOT NULL DEFAULT '', -- e.g. wxid_<account>
    host       TEXT NOT NULL DEFAULT '', -- where it's served e.g. 192.168.31.178:6174
    cred_ref   TEXT NOT NULL DEFAULT '', -- pointer to credential (NOT the secret)
    created_at INTEGER NOT NULL,
    UNIQUE (platform, self_id)
);

-- every chat (private + group), per account
CREATE TABLE IF NOT EXISTS conversations (
    id            INTEGER PRIMARY KEY,
    account_id    INTEGER NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    platform      TEXT NOT NULL,
    chat_id       TEXT NOT NULL,          -- platform's chat/username id
    type          TEXT NOT NULL DEFAULT 'private',  -- private | group
    name          TEXT NOT NULL DEFAULT '',
    person_id     TEXT REFERENCES persons(id),      -- nullable: set when a private chat maps to a tracked person
    muted         INTEGER NOT NULL DEFAULT 0,        -- 1 = ingested but suppressed from active feed
    unread        INTEGER NOT NULL DEFAULT 0,
    last_activity_at INTEGER,
    backfill_done   INTEGER NOT NULL DEFAULT 0,      -- full-history backfill complete
    backfill_cursor TEXT NOT NULL DEFAULT '',        -- resumable offset/cursor for B
    created_at    INTEGER NOT NULL,
    updated_at    INTEGER NOT NULL,
    UNIQUE (account_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_conv_person ON conversations(person_id);
CREATE INDEX IF NOT EXISTS idx_conv_activity ON conversations(last_activity_at DESC);

-- full message history
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL,        -- denormalized for fast per-account queries
    platform     TEXT NOT NULL,
    msg_key      TEXT NOT NULL,           -- stable platform id, else content-hash (see below)
    ts           INTEGER NOT NULL,        -- unix seconds
    sender       TEXT NOT NULL DEFAULT '',-- display name
    sender_id    TEXT NOT NULL DEFAULT '',-- wxid/open_id when available
    direction    TEXT NOT NULL DEFAULT 'in',  -- in | out
    type         TEXT NOT NULL DEFAULT 'text',
    content      TEXT NOT NULL DEFAULT '',
    media_ref    TEXT NOT NULL DEFAULT '', -- localId/hash/url for lazy media fetch
    is_mentioned INTEGER NOT NULL DEFAULT 0,
    embedding_id INTEGER,                  -- reserved for N8 (sqlite-vec), null in A
    raw          TEXT NOT NULL DEFAULT '{}',
    recorded_at  INTEGER NOT NULL,
    UNIQUE (conversation_id, msg_key)
);
CREATE INDEX IF NOT EXISTS idx_msg_conv_ts ON messages(conversation_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(ts DESC);

-- media / file attachments: DB holds a reference only, bytes live in a
-- content-addressed blob store (filesystem). Same file sent by many people = one blob.
CREATE TABLE IF NOT EXISTS media (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT,                     -- content hash = blob id; null until fetched
    message_id  INTEGER REFERENCES messages(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL DEFAULT '', -- image | voice | video | file
    mime        TEXT NOT NULL DEFAULT '',
    filename    TEXT NOT NULL DEFAULT '',
    ext         TEXT NOT NULL DEFAULT '',
    size        INTEGER NOT NULL DEFAULT 0,
    source_ref  TEXT NOT NULL DEFAULT '', -- platform fetch handle (localId/url/hash)
    status      TEXT NOT NULL DEFAULT 'pending', -- pending | fetched | unsupported
    transcript  TEXT NOT NULL DEFAULT '', -- ASR/extracted text (voice/video/PDF), B fills it
    fetched_at  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_media_message ON media(message_id);
CREATE INDEX IF NOT EXISTS idx_media_sha ON media(sha256);

-- full-text search over message content (trigram = CJK substring friendly)
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    content, sender,
    content='messages', content_rowid='id',
    tokenize='trigram'   -- substring search; queries < 3 chars fall back to LIKE
);
-- triggers keep FTS in sync with messages
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

### `interactions` table

Becomes **derived**: the `interactions` table and its write-path
(`db.record_interaction`) are **dropped** to avoid two overlapping stores. A adds a
query that computes, per person, the latest message across their linked
conversations grouped by platform; `weighting.combine` is fed by that derived query
instead of the v0.5 per-channel snapshot insert.

### Cross-channel person aggregation (identity reconciliation)

Three layers, two orthogonal dimensions:

- **`persons`** = the real human (one row). **`channels`** = that human's identifier
  set across platforms (wxid / open_id / phone / wecom userid — many rows allowed).
  **`conversations`** = a chat with that human on *one of my own accounts*
  (`account_id`), linked back via `person_id`.
- **Linking rule**: a conversation's counterpart id (`chat_id` / peer wxid) is matched
  against `channels`; an exact match auto-sets `conversations.person_id`. Ambiguous or
  fuzzy matches are **handed to the human to confirm (HITL — never silently merge)**.
- **Two orthogonal dimensions**: `account_id` = "which of *my* inboxes it came
  through"; `person_id` = "who the counterpart is". So "me via WeCom #3 ↔ contact X" and
  "me via personal WeChat #1 ↔ contact X" both roll up to the one contact-X `persons` row,
  while each message still records its `account_id` provenance.
- **Unified view**: person → all channels → all conversations (across my accounts) →
  a merged, time-ordered timeline of every message, plus weighted coloring. This is
  the cross-channel "relationship account".

A delivers: exact-match `link_person` + `derive_last_interactions`. Fuzzy/assisted
matching and the merged-timeline API are B/C.

## Media / blob storage (architecture)

Large bytes (PDF/file attachments, voice, video, full-size images) **never go in
SQLite** — that bloats the db and makes backup/migration painful. Instead:

- **Content-addressed blob store** on the filesystem (`.156` in production, local in
  A): `blobs/<sha256[:2]>/<sha256>`. The hash is the id → identical files (same PDF
  forwarded by many people, across accounts/channels) are stored once.
- **`media` table** holds a *reference* per attachment: `kind`, `mime`, `filename`,
  `ext`, `size`, `source_ref` (platform fetch handle), `sha256` (blob id, null until
  fetched), `status` (pending/fetched/unsupported), `transcript`.
- **Lazy fetch**: ingestion records the reference (the file may be `pending` — WeChat
  hasn't downloaded it yet, fullwechat returns `pending()`). Bytes are pulled on
  demand (UI open, or a background top-up) via the platform media API
  (fullwechat `get_message_media`, lark/wecom download), hashed, written to the blob
  store, and the `media` row flipped to `fetched` with its `sha256`.
- **Transcription/extraction is a B-layer step, schema-reserved in A**: fullwechat
  returns voice/video **bytes only** (microWeChat voice is `silk`, needs transcode).
  AMR runs ASR (local whisper, or the existing 妙记/SuperWhisper pipeline) on
  voice/video and PDF→text extraction; results land in `media.transcript` **and** are
  mirrored into the owning `messages.content` so FTS (and later vector) can search
  them. A defines the table + the pure blob-path helper; A does **not** fetch bytes or
  run ASR.

A ships: the `media` table, and a pure `blob_path(sha256)` helper (tested). Fetching,
hashing, ASR, and PDF extraction are B.

## Operations: 点火 (ignite) / 复位 (reset)

Two product operations bracket the store's lifecycle: ignite fills it, reset empties it.

### 点火 ignite — bootstrap pull from edge collectors (B implements; A enables)

`jl ignite [--channel X] [--account N]` kicks off the **initial full-history pull**
from the edge collectors/adapters into the store: for each enabled account, run the
adapter's `list_conversations()` then `backfill()` (paginated, resumable via
`conversations.backfill_cursor` / `backfill_done`), `ingest()` each batch with dedup,
and stamp progress. After ignition the 5-min poller (B) keeps it incremental.

- **A's part**: the adapter interface, `ingest()`, and the resumable-cursor columns
  that make ignition restartable. A ships **no live ignition** (adapters are
  interface-only in A).
- **B's part**: the `jl ignite` command wiring the real adapters + progress/throttle.
- Ignition is read-only ingestion (no sending); it logs an `ignite` event with
  per-channel counts.

### 复位 reset — one-button wipe of all channel data (A delivers)

`jl reset [--channel X] [--all] [--confirm]` clears ingested data so the store can be
re-ignited clean. **Destructive → mandatory human-in-the-loop gate**, per the
project's highest rule:

1. **dry-run (default)**: prints exactly what would be deleted — counts of messages /
   conversations / media blobs, broken down by account + channel — and stops.
2. **confirm**: only with explicit `--confirm` (or interactive y/N) does it delete.
3. **audit**: writes a `reset` event (actor / when / scope / deleted counts) **before**
   wiping, so the trace survives the wipe.

Scope:
- default: wipe `messages` + `conversations` + `media` rows (the harvested content);
  blob files are orphaned-GC'd separately (a `--gc-blobs` pass).
- `--channel X`: limit to one platform/account.
- `--all`: additionally clear the `accounts` registry. **Never** touches `persons`
  (relationship definitions are hand-curated, not harvested) unless explicitly asked.

A delivers `reset_store(scope, dry_run)` (pure-countable + tested) and the CLI gate;
blob-file GC is wired in B (where the blob store lives).

## `msg_key` / dedup (pure, unit-tested)

Each message needs a stable identity for `UNIQUE(conversation_id, msg_key)`:

- **Stable id available** (fullwechat `localId`/`serverId`, lark `message_id`,
  phone CallHistory `Z_PK`): `msg_key = "<source>:<id>"`.
- **No stable id** (text-only backends): `msg_key = "h:" + sha1(ts|sender|content)[:16]`.
  Same minute + same sender + same content is treated as the same message (true dup);
  acceptable collision risk.

`msg_key()` and the content-hash helper live in the new pure module `ingest.py`
(alongside the adapter interface and dataclasses below), with tests; adapters in B
import it so dedup is uniform.

## Ingestion adapter interface (defined in A, implemented in B)

```python
class IngestAdapter(Protocol):
    platform: str
    def list_conversations(self, account) -> list[ConvRecord]: ...
    def backfill(self, account, conv, cursor: str) -> tuple[list[MsgRecord], str]:
        """Return (messages, next_cursor); '' cursor means done."""
    def pull_new(self, account) -> list[MsgRecord]:
        """Incremental since last call (server cursor or max-ts)."""
```

`ConvRecord`/`MsgRecord` are plain dataclasses (the normalized shape the store
ingests), living in `ingest.py` with the interface and `msg_key` helpers. A ships
the interface + dataclasses + `msg_key`/dedup helpers; the store-side upsert/insert
lives in `db.py`. No live adapter in A.

## `db.py` additions (A)

- `upsert_account`, `get_accounts`
- `upsert_conversation` (idempotent on `(account_id, chat_id)`), `get_conversations`
  (filterable by account/muted/person), `set_muted`, `link_person`
- `insert_messages(conv_id, [MsgRecord])` → dedup via `INSERT OR IGNORE`, returns
  inserted count; updates `conversations.last_activity_at`/`unread`
- `search_messages(query, limit, account=None)` → FTS5 MATCH + BM25 rank
- `derive_last_interactions(person_id)` → `{platform: {ts, summary}}` for weighting
- `log_event`/`record_tokens` unchanged (audit + accounting reused)

## Testing (TDD, all pure or in-memory sqlite)

- schema: 4 new tables (accounts/conversations/messages/media) + FTS table + triggers created
- media: reference row inserts as `pending`; `blob_path(sha256)` deterministic + sharded
- accounts: 8-bit CHECK rejects 256; upsert idempotent
- conversations: upsert idempotent on (account_id, chat_id); mute toggle; person link
- messages: insert dedups on msg_key; FTS triggers populate; multi-account isolation
- `msg_key`: stable-id form vs content-hash form; same (ts,sender,content) collides
- search: FTS5 MATCH finds CJK substring (trigram); BM25 ordering; account filter
- derive_last_interactions: picks latest per platform; feeds weighting.combine
- reset: dry-run reports correct counts and deletes nothing; confirm wipes
  messages/conversations/media; `--all` clears accounts; persons untouched; a
  `reset` event is written before the wipe
- regression: existing 37 tests still green after interactions→derived refactor

## Migration / deployment note

- Local `jl.db` gets the new tables via `CREATE TABLE IF NOT EXISTS`. The
  `interactions` removal + new tables warrant a clean re-create of the dogfood db
  (ephemeral data). A `schema_version` row (in a tiny `meta` use of `events` or a
  pragma) is optional; for now: drop & re-migrate locally.
- Nothing hard-codes `.156`; deploying = copy package + db path env. Edge-vs-central
  split (phone adapter on Mac pushing to `.156`) is a B/C concern, not A.

## Open items (not blocking A)

- CJK FTS quality: trigram is robust but larger index; revisit if size matters.
- Person↔conversation auto-linking heuristic (name/id match) — basic in A
  (`link_person` manual + exact self_id match), smarter matching in B.
- Account credential storage (`cred_ref`) — A stores a pointer only; real secret
  handling decided in B/C.
