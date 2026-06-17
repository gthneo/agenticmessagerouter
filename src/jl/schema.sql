-- jl relationship-account router / AMR message store — v0.6 schema (8 tables + FTS)
-- persons / channels / accounts / conversations / messages / media / events / tokens,
-- plus messages_fts (FTS5 trigram). Source of truth for contact identity, the
-- multi-account message store, a human-in-the-loop audit trail, and token accounting.

CREATE TABLE IF NOT EXISTS persons (
    id             TEXT PRIMARY KEY,           -- stable slug, e.g. "lixiangquan"
    name           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',   -- GC0001 / 家人4x / 伴侣5x ...
    threshold_days REAL NOT NULL DEFAULT 7,    -- red-alert threshold
    aliases        TEXT NOT NULL DEFAULT '[]', -- JSON array
    watch          INTEGER NOT NULL DEFAULT 0, -- 关注: enter the proactive queue regardless of color
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY,
    person_id  TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                  -- wechat/phone/feishu/imsg/gmail/whatsapp/wecom
    pinned     INTEGER NOT NULL DEFAULT 0,     -- 人钦点的首选发送端点
    identifier TEXT NOT NULL DEFAULT '',       -- wxid / phone / open_id / email ...
    label      TEXT NOT NULL DEFAULT '',       -- human display (chat_name etc.)
    meta       TEXT NOT NULL DEFAULT '{}',     -- JSON, channel-specific extras
    UNIQUE (person_id, kind, identifier)
);

-- the user's own login identities (inboxes we ingest FROM); 8-bit id space
CREATE TABLE IF NOT EXISTS accounts (
    account_id INTEGER PRIMARY KEY CHECK (account_id BETWEEN 0 AND 255),
    platform   TEXT NOT NULL,
    label      TEXT NOT NULL DEFAULT '',
    self_id    TEXT NOT NULL DEFAULT '',
    host       TEXT NOT NULL DEFAULT '',
    cred_ref   TEXT NOT NULL DEFAULT '',
    tool       TEXT NOT NULL DEFAULT '',      -- which access tool serves this account (fullwechat/powerdata/lark-cli/callhistory)
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

-- outbound drafts (human-in-the-loop: queued -> confirmed -> sent). Send only on confirm.
CREATE TABLE IF NOT EXISTS outbox (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    account_id      INTEGER NOT NULL,
    platform        TEXT NOT NULL,
    chat_id         TEXT NOT NULL,
    body            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      INTEGER NOT NULL,
    created_by      TEXT NOT NULL DEFAULT '',
    sent_at         INTEGER,
    error           TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_outbox_status ON outbox(status, created_at);

-- AI reply-draft candidates (separate from outbox; outbox = human-committed only)
CREATE TABLE IF NOT EXISTS suggestions (
    id              INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    version_idx     INTEGER NOT NULL DEFAULT 0,
    stance          TEXT NOT NULL DEFAULT '',
    body            TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'reply',      -- reply|opener (proactive)
    llm_provider    TEXT NOT NULL DEFAULT '',
    llm_model       TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'suggested',  -- suggested|used|dismissed
    created_at      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suggestions_conv ON suggestions(conversation_id, status);

-- 事(matters): first-class units of "what's being handled", M:N with persons + conversations.
-- A matter's lifecycle (事卡) = T4诊断 → T5话术 → T6人审发 → T10承诺/T9跟进.
CREATE TABLE IF NOT EXISTS matters (
    id          INTEGER PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',           -- 跟进/危机/承诺/会议/边界...
    status      TEXT NOT NULL DEFAULT 'open',        -- open|handled|dropped
    diagnosis   TEXT NOT NULL DEFAULT '{}',          -- structured T4 (filled in Block 3)
    surface_on  TEXT NOT NULL DEFAULT '',            -- T9 定时调出 date(s)
    created_at  INTEGER NOT NULL,
    updated_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS matter_persons (
    matter_id INTEGER NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    person_id TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    UNIQUE (matter_id, person_id)
);
CREATE TABLE IF NOT EXISTS matter_conversations (
    matter_id       INTEGER NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    UNIQUE (matter_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_matter_persons ON matter_persons(person_id);
CREATE INDEX IF NOT EXISTS idx_matter_convs ON matter_conversations(conversation_id);

-- 承诺台账 (T10): commitments tracked per matter.
CREATE TABLE IF NOT EXISTS commitments (
    id         INTEGER PRIMARY KEY,
    matter_id  INTEGER NOT NULL REFERENCES matters(id) ON DELETE CASCADE,
    text       TEXT NOT NULL DEFAULT '',
    due        TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'open',          -- open|kept|broken
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_commitments_matter ON commitments(matter_id);

-- 分层运维日志 (for 运维工程师 + 运维Agent): level (DEBUG<INFO<WARN<ERROR) × component.
-- Distinct from `events` (HITL audit trail) — this is operational/diagnostic logging.
CREATE TABLE IF NOT EXISTS logs (
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,
    level     TEXT NOT NULL DEFAULT 'INFO',   -- DEBUG/INFO/WARN/ERROR
    component TEXT NOT NULL DEFAULT '',        -- ingest/route/llm/send/self/归一/diagnose/web...
    msg       TEXT NOT NULL DEFAULT '',
    detail    TEXT NOT NULL DEFAULT '{}'       -- JSON
);
CREATE INDEX IF NOT EXISTS idx_logs_ts ON logs(ts DESC);
CREATE INDEX IF NOT EXISTS idx_logs_comp ON logs(component, ts DESC);

-- SELF(自我): the user's OWN identities across channels (中心节点, NOT a contact).
-- Multi-channel × multi-identity, flat, each tagged with a persona 面具.
CREATE TABLE IF NOT EXISTS self_identities (
    id         INTEGER PRIMARY KEY,
    kind       TEXT NOT NULL,                  -- wechat/phone/feishu/xhs/wecom...
    identifier TEXT NOT NULL,                  -- wxid / phone(canon) / open_id ...
    persona    TEXT NOT NULL DEFAULT '自我',    -- 自我 / AI分身 / 经营 (configurable)
    label      TEXT NOT NULL DEFAULT '',
    created_at INTEGER NOT NULL,
    UNIQUE (kind, identifier)
);

-- full-text search (trigram = CJK substring; NOTE: only matches queries >= 3 chars,
-- so the search layer uses a LIKE fallback for shorter queries)
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

-- Human-in-the-loop audit trail: who / when / why for every decision & intervention.
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,
    kind      TEXT NOT NULL,                   -- sweep/reach/auto_add/intervention/migration...
    person_id TEXT,                            -- nullable; not a hard FK (events outlive persons)
    actor     TEXT NOT NULL DEFAULT '',        -- user / agent / cron ...
    detail    TEXT NOT NULL DEFAULT '{}'       -- JSON
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS tokens (
    id           INTEGER PRIMARY KEY,
    ts           INTEGER NOT NULL,
    channel_kind TEXT NOT NULL DEFAULT '',
    op           TEXT NOT NULL DEFAULT '',
    reach_count  INTEGER NOT NULL DEFAULT 0,
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0
);
