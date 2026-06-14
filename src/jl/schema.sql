-- jl relationship-account router — v0.5 schema (5 tables)
-- Source of truth for contact identity + cross-channel interaction snapshots,
-- plus a human-in-the-loop audit trail and token-usage accounting.

CREATE TABLE IF NOT EXISTS persons (
    id             TEXT PRIMARY KEY,           -- stable slug, e.g. "lixiangquan"
    name           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',   -- GC0001 / 家人4x / 伴侣5x ...
    threshold_days REAL NOT NULL DEFAULT 7,    -- red-alert threshold
    aliases        TEXT NOT NULL DEFAULT '[]', -- JSON array
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS channels (
    id         INTEGER PRIMARY KEY,
    person_id  TEXT NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,                  -- wechat/phone/feishu/imsg/gmail/whatsapp/wecom
    identifier TEXT NOT NULL DEFAULT '',       -- wxid / phone / open_id / email ...
    label      TEXT NOT NULL DEFAULT '',       -- human display (chat_name etc.)
    meta       TEXT NOT NULL DEFAULT '{}',     -- JSON, channel-specific extras
    UNIQUE (person_id, kind, identifier)
);

CREATE TABLE IF NOT EXISTS interactions (
    id         INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    ts         INTEGER NOT NULL,               -- unix seconds of the interaction
    direction  TEXT NOT NULL DEFAULT '',       -- in / out
    summary    TEXT NOT NULL DEFAULT '',       -- one-line last message/call
    recorded_at INTEGER NOT NULL,
    UNIQUE (channel_id, ts)                     -- repeated sweeps don't pile up dups
);
CREATE INDEX IF NOT EXISTS idx_interactions_channel_ts
    ON interactions(channel_id, ts DESC);

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
