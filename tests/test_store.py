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


def test_search_messages_finds_short_cjk_substring_via_like(conn):
    # 2-char query — below the FTS5 trigram floor, served by the LIKE fallback
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="x:1", ts=1000, content="记得带合同来", sender="张三"),
        ingest.MsgRecord(msg_key="x:2", ts=2000, content="今天天气不错", sender="李四"),
    ])
    hits = db.search_messages(conn, "合同")
    assert len(hits) == 1
    assert hits[0]["content"] == "记得带合同来"


def test_search_messages_finds_long_cjk_substring_via_fts(conn):
    # 3+-char query — served by the FTS5 trigram index
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="x:1", ts=1000, content="请尽快确认合同条款", sender="张三"),
        ingest.MsgRecord(msg_key="x:2", ts=2000, content="今天天气不错", sender="李四"),
    ])
    hits = db.search_messages(conn, "确认合同")
    assert len(hits) == 1
    assert hits[0]["content"] == "请尽快确认合同条款"


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


def test_search_messages_query_with_fts_operators_does_not_crash(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="x:1", ts=1, content="请确认合同 OR 发票", sender="张三"),
    ])
    # bare 'OR' is an FTS5 operator; must be treated as literal text, not crash
    hits = db.search_messages(conn, "合同 OR 发票")
    assert len(hits) == 1


def test_search_messages_query_with_quote_does_not_crash(conn):
    cid = _seed_conv(conn)
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="x:1", ts=1, content='他说"明天签约"了', sender="张三"),
    ])
    hits = db.search_messages(conn, '"明天签约"')
    assert len(hits) == 1


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


def test_get_conversation_by_id(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="c1", name="张三")
    got = db.get_conversation(conn, cid)
    assert got["chat_id"] == "c1" and got["name"] == "张三"
    assert db.get_conversation(conn, 99999) is None


def test_ingest_records_upserts_conv_and_inserts_msgs(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    conv = ingest.ConvRecord(chat_id="c1", name="张三", type="private",
                             unread=2, last_activity_at=5000)
    msgs = [ingest.MsgRecord(msg_key="fullwx:1", ts=4000, content="早", sender="张三"),
            ingest.MsgRecord(msg_key="fullwx:2", ts=5000, content="晚", sender="张三")]
    cid, n = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=msgs)
    assert n == 2
    got = db.get_conversation(conn, cid)
    assert got["name"] == "张三" and got["unread"] == 2
    cid2, n2 = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=msgs)
    assert cid2 == cid and n2 == 0


def test_ingest_records_mutes_when_conv_muted_true(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    conv = ingest.ConvRecord(chat_id="g1", name="群", type="group", muted=True)
    cid, _ = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=[])
    assert db.get_conversation(conn, cid)["muted"] == 1
