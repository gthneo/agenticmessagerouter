"""SQLite data layer for jl — the relationship-account router.

Single source of truth (replaces the v0.4 persons.json runtime read). Holds
contact identity, per-channel interaction snapshots, an audit trail, and token
accounting. All times are unix seconds.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
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


# Columns added after the initial release. CREATE TABLE IF NOT EXISTS only creates
# them on a fresh DB, so existing DBs (e.g. live .178) need an idempotent ALTER.
_ADDED_COLUMNS = {
    "persons": [("watch", "INTEGER NOT NULL DEFAULT 0")],
    "suggestions": [("kind", "TEXT NOT NULL DEFAULT 'reply'")],
    "channels": [("pinned", "INTEGER NOT NULL DEFAULT 0")],
    "accounts": [("tool", "TEXT NOT NULL DEFAULT ''")],
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    conn.commit()


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    _ensure_columns(conn)
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


def set_watch(conn, person_id, on=True):
    """Toggle the 关注 flag — watched persons enter the proactive queue regardless
    of color. Caller logs the intervention to the events trail (who/when/why)."""
    conn.execute("UPDATE persons SET watch=?, updated_at=? WHERE id=?",
                 (1 if on else 0, _now(), person_id))
    conn.commit()


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
                   host="", cred_ref="", tool=""):
    conn.execute(
        """
        INSERT INTO accounts (account_id, platform, label, self_id, host, cred_ref,
                              tool, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(account_id) DO UPDATE SET
            platform=excluded.platform, label=excluded.label,
            self_id=excluded.self_id, host=excluded.host, cred_ref=excluded.cred_ref,
            tool=excluded.tool
        """,
        (account_id, platform, label, self_id, host, cred_ref, tool, _now()),
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


def _canon_identifier(kind, identifier):
    """Canonical comparable id for an endpoint (phone → canon_phone; else as-is)."""
    if kind == "phone":
        from .channels.phone import canon_phone
        return canon_phone(identifier)
    return identifier


def dedup_channels(conn):
    """Fold endpoint rows sharing a canonical identifier (e.g. phone +86 variants) and
    canonicalize survivors. Distinct identifiers stay separate. Returns count folded."""
    rows = conn.execute("SELECT id, person_id, kind, identifier FROM channels").fetchall()
    groups = {}
    for r in rows:
        key = (r["person_id"], r["kind"], _canon_identifier(r["kind"], r["identifier"]))
        groups.setdefault(key, []).append(dict(r))
    folded = 0
    for key, rs in groups.items():
        survivor = rs[0]
        for dup in rs[1:]:            # delete duplicates first → survivor canon can't collide
            conn.execute("DELETE FROM channels WHERE id=?", (dup["id"],))
            folded += 1
        if key[2] and key[2] != survivor["identifier"]:
            conn.execute("UPDATE channels SET identifier=? WHERE id=?", (key[2], survivor["id"]))
    conn.commit()
    return folded


def endpoints_with_recency(conn, person_id):
    """One row per (kind, canonical identifier) reachable for a person, each with its OWN
    last-interaction ts + the conversation defining it. Endpoint-level (not platform-
    collapsed) so a person fresh on one number and cold on another is seen."""
    out = {}
    for c in get_conversations(conn, person_id=person_id):
        kind = c["platform"]
        ident = _canon_identifier(kind, c["chat_id"])
        if is_self(conn, kind, ident):
            continue   # never route a contact to one of the user's OWN identities
        n, last_ts = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(ts), 0) FROM messages WHERE conversation_id=?",
            (c["id"],)).fetchone()
        key = (kind, ident)
        prev = out.get(key)
        if prev is None or last_ts > prev["last_ts"]:
            out[key] = {"kind": kind, "identifier": ident, "last_ts": last_ts,
                        "msgs": n, "conversation_id": c["id"], "chat_id": c["chat_id"]}
    return list(out.values())


def set_endpoint_pin(conn, person_id, kind, identifier, on=True):
    """Pin (or unpin) one endpoint as the human-chosen primary send target."""
    conn.execute("UPDATE channels SET pinned=? WHERE person_id=? AND kind=? AND identifier=?",
                 (1 if on else 0, person_id, kind, identifier))
    conn.commit()


def dedup_phone_conversations(conn):
    """Merge phone conversations that are format-variants of the SAME number (e.g.
    '13686472775' and '+8613686472775') into one canonical conversation. Distinct
    numbers under a person are preserved. Returns the count of conversations folded.
    Messages move with INSERT OR IGNORE on (conversation_id, msg_key) to absorb overlap."""
    from .channels.phone import canon_phone
    rows = conn.execute(
        "SELECT id, chat_id, account_id, person_id, "
        "(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=conversations.id) n "
        "FROM conversations WHERE platform='phone'").fetchall()
    groups = {}
    for r in rows:
        groups.setdefault((r["account_id"], canon_phone(r["chat_id"])), []).append(dict(r))
    folded = 0
    for (account_id, canon), convs in groups.items():
        if len(convs) < 2:
            # still canonicalize a lone conv's chat_id so future ingests align
            c = convs[0]
            if c["chat_id"] != canon and canon:
                conn.execute("UPDATE conversations SET chat_id=? WHERE id=?", (canon, c["id"]))
            continue
        convs.sort(key=lambda c: (-c["n"], c["id"]))   # keep the richest (then oldest)
        keep = convs[0]
        for dup in convs[1:]:
            conn.execute(
                "UPDATE OR IGNORE messages SET conversation_id=? WHERE conversation_id=?",
                (keep["id"], dup["id"]))
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (dup["id"],))
            conn.execute("DELETE FROM conversations WHERE id=?", (dup["id"],))
            folded += 1
        if keep["chat_id"] != canon and canon:
            conn.execute("UPDATE conversations SET chat_id=? WHERE id=?", (canon, keep["id"]))
    conn.commit()
    return folded


def link_person(conn, conversation_id, person_id):
    conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                 (person_id, _now(), conversation_id))
    conn.commit()


def merge_persons(conn, keep_id, drop_id):
    """Merge drop person INTO keep (HITL: human confirmed same person, e.g. one李夏宁 with
    two wxids). Moves channels + conversations + matter links to keep, deletes drop. ok/False."""
    if keep_id == drop_id or not get_person(conn, keep_id) or not get_person(conn, drop_id):
        return False
    for ch in get_channels(conn, drop_id):
        upsert_channel(conn, person_id=keep_id, kind=ch["kind"],
                       identifier=ch["identifier"], label=ch.get("label", ""))
    conn.execute("UPDATE conversations SET person_id=? WHERE person_id=?", (keep_id, drop_id))
    conn.execute("UPDATE OR IGNORE matter_persons SET person_id=? WHERE person_id=?",
                 (keep_id, drop_id))
    conn.execute("DELETE FROM channels WHERE person_id=?", (drop_id,))
    conn.execute("DELETE FROM persons WHERE id=?", (drop_id,))
    conn.commit()
    return True


def purge_orphan_persons(conn):
    """Delete persons that ended up with NO conversations (e.g. cleaned-up official-account
    noise). Returns count removed."""
    n = 0
    for p in get_persons(conn):
        if not get_conversations(conn, person_id=p["id"]):
            conn.execute("DELETE FROM persons WHERE id=?", (p["id"],))
            n += 1
    conn.commit()
    return n


def unify_by_wxid(conn):
    """Cross-account/tool 联系人归一: for a wxid that ALREADY belongs to a person (via a
    channel or a linked conversation), link that person's OTHER conversations with the same
    wxid (across accounts/tools) to it. **Never auto-creates a person** — unlinked wxids
    stay unlinked (→ HITL suggest_merges), so 公众号/服务号 noise isn't personified. Self
    skipped. Returns {linked}."""
    self_ids = {s["identifier"] for s in get_self_identities(conn) if s["kind"] == "wechat"}
    groups = {}
    for c in conn.execute(
            "SELECT id, chat_id, name, person_id FROM conversations "
            "WHERE platform='wechat' AND type='private'"):
        wxid = c["chat_id"]
        if not wxid or wxid in self_ids:
            continue
        groups.setdefault(wxid, []).append(dict(c))
    linked = 0
    for wxid, convs in groups.items():
        row = conn.execute("SELECT person_id FROM channels WHERE kind='wechat' AND identifier=?",
                           (wxid,)).fetchone()
        pid = (row["person_id"] if row else None) or next(
            (c["person_id"] for c in convs if c["person_id"]), None)
        if not pid:
            continue   # no existing person for this wxid → leave to HITL, don't auto-create
        upsert_channel(conn, person_id=pid, kind="wechat", identifier=wxid)
        for c in convs:
            if c["person_id"] != pid:
                conn.execute("UPDATE conversations SET person_id=? WHERE id=?", (pid, c["id"]))
                linked += 1
    conn.commit()
    return {"linked": linked}


def set_self_persona(conn, kind, identifier, persona):
    """Set the persona (工作/生活/学习…) on a self identity. Manual, per the 自然人为锚 rule."""
    conn.execute("UPDATE self_identities SET persona=? WHERE kind=? AND identifier=?",
                 (persona, kind, _canon_identifier(kind, identifier)))
    conn.commit()


def unlink_conversation(conn, conversation_id):
    """Split a wrongly-merged conversation off a person (HITL fix for bad 归一): clear its
    person link AND drop the matching endpoint row. Returns the freed person_id or None."""
    row = conn.execute("SELECT person_id, platform, chat_id FROM conversations WHERE id=?",
                       (conversation_id,)).fetchone()
    if not row or not row["person_id"]:
        return None
    pid = row["person_id"]
    conn.execute("UPDATE conversations SET person_id=NULL, updated_at=? WHERE id=?",
                 (_now(), conversation_id))
    conn.execute("DELETE FROM channels WHERE person_id=? AND kind=? AND identifier=?",
                 (pid, row["platform"], _canon_identifier(row["platform"], row["chat_id"])))
    conn.commit()
    return pid


def get_conversation(conn, conversation_id):
    row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    return dict(row) if row else None


# ----- person linking (zero-LLM deterministic + HITL) -----------------------

def _match_person(conn, platform, chat_id):
    """[person_id] whose channels match this conversation's peer id.
    phone -> tail_match (country-code tolerant); others -> exact identifier."""
    rows = conn.execute("SELECT person_id, kind, identifier FROM channels").fetchall()
    hits = set()
    if platform == "phone":
        from .channels.phone import tail_match
        for r in rows:
            if r["kind"] == "phone" and tail_match(r["identifier"], chat_id):
                hits.add(r["person_id"])
    else:
        for r in rows:
            if r["kind"] == platform and r["identifier"] == chat_id:
                hits.add(r["person_id"])
    return sorted(hits)


def link_conversations(conn):
    """Auto-link unlinked conversations to persons by exact/tail channel match.
    Skips ambiguous (>1 candidate) — those go to the human. Returns count linked."""
    n = 0
    for c in conn.execute(
            "SELECT id, platform, chat_id FROM conversations WHERE person_id IS NULL").fetchall():
        cands = _match_person(conn, c["platform"], c["chat_id"])
        if len(cands) == 1:
            conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                         (cands[0], _now(), c["id"]))
            n += 1
    conn.commit()
    return n


def set_conversation_person(conn, conversation_id, person_id):
    """Human-confirmed link. LEARNS the conversation's peer id as a channel on the
    person so future auto-links stick. Logs a 'link' event."""
    row = conn.execute("SELECT platform, chat_id FROM conversations WHERE id=?",
                        (conversation_id,)).fetchone()
    if row is None:
        raise ValueError(f"no conversation {conversation_id}")
    upsert_channel(conn, person_id=person_id, kind=row["platform"], identifier=row["chat_id"])
    conn.execute("UPDATE conversations SET person_id=?, updated_at=? WHERE id=?",
                 (person_id, _now(), conversation_id))
    conn.commit()
    log_event(conn, kind="link", person_id=person_id, actor="user",
              detail={"conversation_id": conversation_id, "learned": row["chat_id"]})


_GENERIC_NAMES = {"我", "self", "me", "本人"}
_NAME_SPLIT = re.compile(r"[/／、,，|+&]+")


def _name_tokens(name):
    """Split a multi-name contact label ('Roy/我/Connie') into real name tokens —
    drops generic/self tokens and single chars so they don't blind-match everyone."""
    out = []
    for t in _NAME_SPLIT.split(name or ""):
        t = t.strip()
        if len(t) > 1 and t not in _GENERIC_NAMES:
            out.append(t)
    return out


def suggest_merges(conn, limit=50):
    """HITL merge candidates for unlinked conversations, ranked by signal strength with
    visible evidence (so the human verifies — no blind name merge). Strength: 3=strong
    (exact channel id / phone tail), 2=name token exact, 1=name substring. Each candidate
    carries its channels (phone/wxid) so the human can cross-check & 查漏补缺.
    Returns [{conversation_id, name, platform, peer, candidates:[person+strength+evidence+channels]}]."""
    from .channels.phone import tail_match
    persons = get_persons(conn)
    pchans = {p["id"]: get_channels(conn, p["id"]) for p in persons}
    out = []
    rows = conn.execute(
        "SELECT id, platform, name, chat_id FROM conversations WHERE person_id IS NULL "
        "ORDER BY last_activity_at DESC LIMIT ?", (limit,)).fetchall()
    for c in rows:
        kind, peer = c["platform"], c["chat_id"]
        cands = {}

        def add(p, strength, ev):
            cur = cands.get(p["id"])
            if cur is None:
                cands[p["id"]] = {**p, "strength": strength, "evidence": [ev],
                                  "channels": [{"kind": ch["kind"], "identifier": ch["identifier"]}
                                               for ch in pchans[p["id"]]]}
            else:
                cur["strength"] = max(cur["strength"], strength)
                if ev not in cur["evidence"]:
                    cur["evidence"].append(ev)

        for p in persons:                                  # strong: channel match
            for ch in pchans[p["id"]]:
                if kind == "phone" and ch["kind"] == "phone" and tail_match(ch["identifier"], peer):
                    add(p, 3, f"📱尾号{peer[-4:]}")
                elif ch["kind"] == kind and ch["identifier"] == peer:
                    add(p, 3, f"{kind} id 精确")
        for t in _name_tokens(c["name"]):                  # name: exact token=2, substring=1
            for p in persons:
                keys = [k for k in ([p["name"]] + list(p.get("aliases", []))) if k and len(k) > 1]
                if t in keys:
                    add(p, 2, f"名「{t}」")
                elif any(t in k or k in t for k in keys):
                    add(p, 1, f"名~「{t}」")
        if cands:
            ranked = sorted(cands.values(), key=lambda x: -x["strength"])
            out.append({"conversation_id": c["id"], "name": c["name"],
                        "platform": kind, "peer": peer, "candidates": ranked})
    return out


def persons_overview(conn):
    """Each person that HAS linked conversations, with channel set + latest activity."""
    out = []
    for p in get_persons(conn):
        convs = get_conversations(conn, person_id=p["id"])
        if not convs:
            continue
        last = max((c["last_activity_at"] or 0 for c in convs), default=0)
        out.append({"id": p["id"], "name": p["name"], "category": p["category"],
                    "channels": sorted({c["platform"] for c in convs}),
                    "conversations": len(convs), "last_activity_at": last})
    out.sort(key=lambda x: x["last_activity_at"], reverse=True)
    return out


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


def get_voice_samples(conn, conversation_id=None, limit=6):
    """口吻沉淀：messages the user actually SENT (outbox status='sent') = their real voice
    corpus. Prefer this conversation's sends (voice WITH this person), pad with global
    recent sends. Distinct, newest first. Grows as the user sends (越用越懂你)."""
    out, seen = [], set()
    queries = []
    if conversation_id is not None:
        queries.append(("SELECT body FROM outbox WHERE status='sent' AND conversation_id=? "
                        "ORDER BY id DESC LIMIT ?", (conversation_id, limit)))
    queries.append(("SELECT body FROM outbox WHERE status='sent' ORDER BY id DESC LIMIT ?",
                    (limit,)))
    for sql, args in queries:
        for r in conn.execute(sql, args):
            b = (r[0] or "").strip()
            if b and b not in seen:
                seen.add(b); out.append(b)
            if len(out) >= limit:
                return out
    return out


# ----- suggestions (AI reply-draft candidates) ------------------------------

def add_suggestions(conn, conversation_id, items, *, kind="reply"):
    now = _now()
    for it in items:
        conn.execute(
            """INSERT INTO suggestions (conversation_id, version_idx, stance, body,
                                        kind, llm_provider, llm_model, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'suggested', ?)""",
            (conversation_id, it.get("version_idx", 0), it.get("stance", ""),
             it.get("body", ""), kind, it.get("llm_provider", ""),
             it.get("llm_model", ""), now))
    conn.commit()


def get_suggestions(conn, conversation_id, status="suggested", kind=None):
    sql = "SELECT * FROM suggestions WHERE conversation_id=? AND status=?"
    params = [conversation_id, status]
    if kind is not None:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY version_idx"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def set_suggestion_status(conn, suggestion_id, status):
    conn.execute("UPDATE suggestions SET status=? WHERE id=?", (status, suggestion_id))
    conn.commit()


def clear_suggestions(conn, conversation_id):
    conn.execute("DELETE FROM suggestions WHERE conversation_id=?", (conversation_id,))
    conn.commit()


# ----- 事 (matters: M:N persons + conversations) ----------------------------

def create_matter(conn, *, title, kind="", status="open", surface_on="",
                  person_ids=None, conversation_ids=None, diagnosis=None):
    now = _now()
    cur = conn.execute(
        """INSERT INTO matters (title, kind, status, diagnosis, surface_on, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (title, kind, status, json.dumps(diagnosis or {}, ensure_ascii=False),
         surface_on, now, now))
    mid = cur.lastrowid
    for pid in (person_ids or []):
        link_matter_person(conn, mid, pid)
    for cid in (conversation_ids or []):
        link_matter_conversation(conn, mid, cid)
    conn.commit()
    return mid


def link_matter_person(conn, matter_id, person_id):
    conn.execute("INSERT OR IGNORE INTO matter_persons (matter_id, person_id) VALUES (?, ?)",
                 (matter_id, person_id))
    conn.commit()


def link_matter_conversation(conn, matter_id, conversation_id):
    conn.execute("INSERT OR IGNORE INTO matter_conversations (matter_id, conversation_id) "
                 "VALUES (?, ?)", (matter_id, conversation_id))
    conn.commit()


def _matter_row(conn, row):
    d = dict(row)
    d["diagnosis"] = json.loads(d.get("diagnosis") or "{}")
    d["person_ids"] = [r[0] for r in conn.execute(
        "SELECT person_id FROM matter_persons WHERE matter_id=?", (d["id"],))]
    d["conversation_ids"] = [r[0] for r in conn.execute(
        "SELECT conversation_id FROM matter_conversations WHERE matter_id=?", (d["id"],))]
    d["commitments"] = get_commitments(conn, d["id"])
    return d


def get_matters(conn, *, person_id=None, conversation_id=None, status=None):
    """Matters, optionally filtered by a linked person / conversation / status.
    Returns each with person_ids, conversation_ids, commitments resolved."""
    sql = "SELECT DISTINCT m.* FROM matters m"
    where, args = [], []
    if person_id is not None:
        sql += " JOIN matter_persons mp ON mp.matter_id=m.id"
        where.append("mp.person_id=?"); args.append(person_id)
    if conversation_id is not None:
        sql += " JOIN matter_conversations mc ON mc.matter_id=m.id"
        where.append("mc.conversation_id=?"); args.append(conversation_id)
    if status is not None:
        where.append("m.status=?"); args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY m.updated_at DESC"
    return [_matter_row(conn, r) for r in conn.execute(sql, args).fetchall()]


def set_matter_status(conn, matter_id, status):
    conn.execute("UPDATE matters SET status=?, updated_at=? WHERE id=?",
                 (status, _now(), matter_id))
    conn.commit()


def set_matter_diagnosis(conn, matter_id, diagnosis):
    conn.execute("UPDATE matters SET diagnosis=?, updated_at=? WHERE id=?",
                 (json.dumps(diagnosis or {}, ensure_ascii=False), _now(), matter_id))
    conn.commit()


def add_commitment(conn, matter_id, text, due="", status="open"):
    cur = conn.execute(
        "INSERT INTO commitments (matter_id, text, due, status, created_at) VALUES (?, ?, ?, ?, ?)",
        (matter_id, text, due, status, _now()))
    conn.commit()
    return cur.lastrowid


def get_commitments(conn, matter_id):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM commitments WHERE matter_id=? ORDER BY id", (matter_id,))]


def set_commitment_status(conn, commitment_id, status):
    conn.execute("UPDATE commitments SET status=? WHERE id=?", (status, commitment_id))
    conn.commit()


# ----- SELF(自我) identity registry -----------------------------------------

def add_self_identity(conn, kind, identifier, persona="自我", label=""):
    """Declare one of the user's OWN identities (HITL). phone stored canonical."""
    ident = _canon_identifier(kind, identifier)
    conn.execute(
        """INSERT INTO self_identities (kind, identifier, persona, label, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(kind, identifier) DO UPDATE SET persona=excluded.persona,
                                                       label=excluded.label""",
        (kind, ident, persona, label, _now()))
    conn.commit()


def get_self_identities(conn):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM self_identities ORDER BY kind, identifier")]


def remove_self_identity(conn, kind, identifier):
    conn.execute("DELETE FROM self_identities WHERE kind=? AND identifier=?",
                 (kind, _canon_identifier(kind, identifier)))
    conn.commit()


def is_self(conn, kind, identifier):
    """True if (kind, identifier) is one of the user's own identities (phone via canon)."""
    ident = _canon_identifier(kind, identifier)
    return conn.execute("SELECT 1 FROM self_identities WHERE kind=? AND identifier=?",
                        (kind, ident)).fetchone() is not None


def seed_self_from_accounts(conn):
    """Auto-register each account's self_id as a SELF identity (persona 自我). Returns count."""
    n = 0
    for a in get_accounts(conn):
        sid = (a.get("self_id") or "").strip()
        if sid:
            before = is_self(conn, a["platform"], sid)
            add_self_identity(conn, a["platform"], sid, persona="自我", label=a.get("label", ""))
            if not before:
                n += 1
    return n


def suggest_self_identities(conn):
    """Auto-SUGGEST the user's own identities for HITL confirm (never auto-add). ONLY a
    high-confidence signal: each account's own self_id (the login identity we ingest FROM).
    Name-based guessing was removed — it surfaced garbage (文件传输助手, multi-name contacts)
    and missed the real selves. Declare the rest via mark_person_self / explicit add."""
    seen = {(s["kind"], s["identifier"]) for s in get_self_identities(conn)}
    out = []
    for a in get_accounts(conn):
        sid = (a.get("self_id") or "").strip()
        key = (a["platform"], _canon_identifier(a["platform"], sid))
        if sid and key not in seen:
            seen.add(key)
            out.append({"kind": a["platform"], "identifier": sid,
                        "name": a.get("label", ""), "reason": "账号 self_id"})
    return out


def mark_person_self(conn, person_id, persona="自我"):
    """Declare a wrongly-contacted person as actually the USER: their channel ids + their
    conversations' peer ids become SELF identities, their conversations are unlinked, and the
    person record is removed. HITL — fixes 'my own account got listed as a contact'."""
    p = get_person(conn, person_id)
    if not p:
        return 0
    for ch in get_channels(conn, person_id):
        add_self_identity(conn, ch["kind"], ch["identifier"], persona=persona, label=p["name"])
    for c in get_conversations(conn, person_id=person_id):
        add_self_identity(conn, c["platform"], c["chat_id"], persona=persona, label=p["name"])
        conn.execute("UPDATE conversations SET person_id=NULL WHERE id=?", (c["id"],))
    conn.execute("DELETE FROM persons WHERE id=?", (person_id,))
    conn.commit()
    return len(get_self_identities(conn))


def conversation_is_self(conn, conversation_id):
    """True if a conversation's peer is one of the user's own identities (self-chat)."""
    r = conn.execute("SELECT platform, chat_id FROM conversations WHERE id=?",
                     (conversation_id,)).fetchone()
    return bool(r) and is_self(conn, r["platform"], r["chat_id"])


def apply_self_directions(conn):
    """出站识别: mark messages whose sender is a SELF identity as direction='out' (我).
    Resolves 'who said what' once self is declared. Idempotent. Returns rows updated."""
    selfs = get_self_identities(conn)
    ids = [s["identifier"] for s in selfs]
    if not ids:
        return 0
    q = ",".join("?" for _ in ids)
    cur = conn.execute(
        f"UPDATE messages SET direction='out' WHERE direction!='out' AND sender_id IN ({q})",
        ids)
    conn.commit()
    return cur.rowcount


def reunify(conn, *, reset=False):
    """启动/复位归一. reset=True first clears AUTO-linked conversations (keeps human-confirmed
    links — never destroys human work), then re-links by strong signal. Returns stats."""
    if reset:
        # auto-links have no 'link' event; human-confirmed ones do → keep those.
        confirmed = {e["detail"].get("conversation_id") for e in get_events(conn, limit=100000)
                     if e["kind"] == "link" and isinstance(e.get("detail"), dict)}
        for c in get_conversations(conn):
            if c.get("person_id") and c["id"] not in confirmed:
                conn.execute("UPDATE conversations SET person_id=NULL WHERE id=?", (c["id"],))
        conn.commit()
    linked = link_conversations(conn)
    merged = unify_by_wxid(conn)["linked"]   # cross-account merge of already-known persons
    apply_self_directions(conn)
    return {"linked": linked, "merged": merged, "candidates": len(suggest_merges(conn))}


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
