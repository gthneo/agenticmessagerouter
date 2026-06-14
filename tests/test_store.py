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
