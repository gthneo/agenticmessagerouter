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


def test_get_conversations_person_id_none_returns_unlinked(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_a")
    linked = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                    chat_id="c1", name="张三", type="private")
    db.link_person(conn, linked, "u1")
    db.upsert_conversation(conn, account_id=1, platform="wechat",
                           chat_id="g1", name="未关联群", type="group")
    unlinked = db.get_conversations(conn, person_id=None)
    assert len(unlinked) == 1
    assert unlinked[0]["chat_id"] == "g1"
