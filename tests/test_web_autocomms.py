"""Unit tests for web.py autocomms endpoints (Task 3 — read-only, propose-only)."""
import sqlite3

from jl import db, ingest, web


def _conn():
    """In-memory DB with full schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    with open("src/jl/schema.sql", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def _make_conv(conn):
    """Create one account + conversation; return conversation id."""
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="me")
    conv = ingest.ConvRecord(chat_id="wxid_test_abc", name="张三", type="private")
    cid, _ = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=[])
    return cid


def test_api_auto_replies_returns_list_on_empty_db():
    conn = _conn()
    result = web.api_auto_replies(conn)
    assert isinstance(result, list)
    conn.close()


def test_api_auto_replies_empty_when_killswitch_on():
    conn = _conn()
    db.set_killswitch(conn, True)
    result = web.api_auto_replies(conn)
    assert result == []
    conn.close()


def test_api_set_autonomy_valid_mode():
    conn = _conn()
    cid = _make_conv(conn)
    r = web.api_set_autonomy(conn, {"conversation_id": cid, "mode": "observe"})
    assert r["ok"] is True
    assert r["error"] == ""
    conn.close()


def test_api_set_autonomy_rejects_autonomous():
    conn = _conn()
    cid = _make_conv(conn)
    r = web.api_set_autonomy(conn, {"conversation_id": cid, "mode": "autonomous"})
    assert r["ok"] is False
    assert r["error"] != ""
    conn.close()


def test_api_killswitch_on():
    conn = _conn()
    r = web.api_killswitch(conn, {"on": True})
    assert r["ok"] is True
    assert r["on"] is True
    assert db.killswitch_on(conn) is True
    conn.close()


def test_api_killswitch_off():
    conn = _conn()
    db.set_killswitch(conn, True)
    r = web.api_killswitch(conn, {"on": False})
    assert r["ok"] is True
    assert r["on"] is False
    assert db.killswitch_on(conn) is False
    conn.close()
