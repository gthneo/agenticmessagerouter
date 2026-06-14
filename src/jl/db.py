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


# ----- interactions ---------------------------------------------------------

def record_interaction(conn, *, channel_id, ts, direction="", summary=""):
    conn.execute(
        """INSERT OR IGNORE INTO interactions
               (channel_id, ts, direction, summary, recorded_at)
           VALUES (?, ?, ?, ?, ?)""",
        (channel_id, ts, direction, summary, _now()),
    )
    conn.commit()


def latest_interaction(conn, channel_id):
    row = conn.execute(
        "SELECT * FROM interactions WHERE channel_id=? ORDER BY ts DESC LIMIT 1",
        (channel_id,),
    ).fetchone()
    return dict(row) if row else None


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
