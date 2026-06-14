"""SQLite data layer for jl — the relationship-account router.

Single source of truth (replaces the v0.4 persons.json runtime read). Holds
contact identity, per-channel interaction snapshots, an audit trail, and token
accounting. All times are unix seconds.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

DEFAULT_DB = os.path.expanduser("~/.config/jl/jl.db")
_SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def _now() -> int:
    return int(time.time())


def connect(path: str = DEFAULT_DB) -> sqlite3.Connection:
    if path != ":memory:":
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


# ----- persons --------------------------------------------------------------

def upsert_person(conn, *, id, name, category="", threshold_days=7, aliases=None):
    now = _now()
    conn.execute(
        """
        INSERT INTO persons (id, name, category, threshold_days, aliases,
                             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            name=excluded.name,
            category=excluded.category,
            threshold_days=excluded.threshold_days,
            aliases=excluded.aliases,
            updated_at=excluded.updated_at
        """,
        (id, name, category, threshold_days,
         json.dumps(aliases or [], ensure_ascii=False), now, now),
    )
    conn.commit()
    return id


def _person_row(row):
    d = dict(row)
    d["aliases"] = json.loads(d.get("aliases") or "[]")
    return d


def get_persons(conn):
    rows = conn.execute("SELECT * FROM persons ORDER BY name").fetchall()
    return [_person_row(r) for r in rows]


def get_person(conn, person_id):
    row = conn.execute("SELECT * FROM persons WHERE id=?", (person_id,)).fetchone()
    return _person_row(row) if row else None


# ----- channels -------------------------------------------------------------

def upsert_channel(conn, *, person_id, kind, identifier="", label="", meta=None):
    conn.execute(
        """
        INSERT INTO channels (person_id, kind, identifier, label, meta)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(person_id, kind, identifier) DO UPDATE SET
            label=excluded.label,
            meta=excluded.meta
        """,
        (person_id, kind, identifier, label,
         json.dumps(meta or {}, ensure_ascii=False)),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM channels WHERE person_id=? AND kind=? AND identifier=?",
        (person_id, kind, identifier),
    ).fetchone()
    return row["id"]


def _channel_row(row):
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return d


def get_channels(conn, person_id):
    rows = conn.execute(
        "SELECT * FROM channels WHERE person_id=? ORDER BY kind", (person_id,)
    ).fetchall()
    return [_channel_row(r) for r in rows]


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
        if person_id is None:
            sql += " AND person_id IS ?"
        else:
            sql += " AND person_id=?"
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
    """Keyword search over message content, newest-relevant first.

    The FTS5 trigram tokenizer only matches queries of >= 3 characters, but
    2-char words dominate Chinese (合同/发票/明天). So for queries shorter than 3
    chars we fall back to a LIKE substring scan; >= 3 chars use FTS5 + bm25 rank.
    """
    q = (query or "").strip()
    if len(q) < 3:
        # full-table scan; acceptable for the short-query fallback at single-user scale
        sql = "SELECT m.* FROM messages m WHERE m.content LIKE ?"
        args = [f"%{q}%"]
        if account_id is not None:
            sql += " AND m.account_id=?"
            args.append(account_id)
        sql += " ORDER BY m.ts DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    sql = """
        SELECT m.* FROM messages_fts f
        JOIN messages m ON m.id = f.rowid
        WHERE messages_fts MATCH ?
    """
    # treat the whole query as a literal phrase so FTS5 operators
    # (OR/AND/NEAR/parens/quotes) in user input don't raise syntax errors
    args = ['"' + q.replace('"', '""') + '"']
    if account_id is not None:
        sql += " AND m.account_id=?"
        args.append(account_id)
    sql += " ORDER BY bm25(messages_fts), m.ts DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


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


# ----- events (audit trail) -------------------------------------------------

def log_event(conn, *, kind, person_id=None, actor="", detail=None):
    conn.execute(
        "INSERT INTO events (ts, kind, person_id, actor, detail) VALUES (?, ?, ?, ?, ?)",
        (_now(), kind, person_id, actor,
         json.dumps(detail or {}, ensure_ascii=False)),
    )
    conn.commit()


def get_events(conn, limit=50):
    rows = conn.execute(
        "SELECT * FROM events ORDER BY ts DESC, id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["detail"] = json.loads(d.get("detail") or "{}")
        out.append(d)
    return out


# ----- tokens ---------------------------------------------------------------

def record_tokens(conn, *, channel_kind="", op="", reach_count=0,
                  tokens_in=0, tokens_out=0):
    conn.execute(
        """INSERT INTO tokens (ts, channel_kind, op, reach_count, tokens_in, tokens_out)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (_now(), channel_kind, op, reach_count, tokens_in, tokens_out),
    )
    conn.commit()


def token_summary(conn):
    row = conn.execute(
        """SELECT COALESCE(SUM(reach_count),0) AS reach_count,
                  COALESCE(SUM(tokens_in),0)   AS tokens_in,
                  COALESCE(SUM(tokens_out),0)  AS tokens_out
           FROM tokens"""
    ).fetchone()
    return dict(row)
