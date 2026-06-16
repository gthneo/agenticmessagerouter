"""Data-layer tests — schema + CRUD over an in-memory SQLite db.

Fixtures use synthetic data only (张三/李四 placeholders, wxid_test_*,
+8613000000000-range numbers) — never real contacts. See CLAUDE.md.
"""
import time

import pytest

from jl import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_init_db_creates_five_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {"persons", "channels", "events", "tokens"} <= names


def test_upsert_person_is_idempotent(conn):
    db.upsert_person(conn, id="u1", name="张三", category="family",
                     threshold_days=14, aliases=["小三"])
    db.upsert_person(conn, id="u1", name="张三改", category="family",
                     threshold_days=10, aliases=["小三", "阿三"])
    persons = db.get_persons(conn)
    assert len(persons) == 1
    p = persons[0]
    assert p["name"] == "张三改"               # updated, not duplicated
    assert p["threshold_days"] == 10
    assert p["aliases"] == ["小三", "阿三"]     # JSON round-trips to a list


def test_channels_belong_to_person(conn):
    db.upsert_person(conn, id="u2", name="李四", category="partner",
                     threshold_days=3, aliases=[])
    db.upsert_channel(conn, person_id="u2", kind="wechat",
                      identifier="wxid_test_002", label="测试会话~标签")
    db.upsert_channel(conn, person_id="u2", kind="phone",
                      identifier="+8613000000002", label="")
    chans = db.get_channels(conn, "u2")
    kinds = sorted(c["kind"] for c in chans)
    assert kinds == ["phone", "wechat"]


def test_upsert_channel_idempotent_on_person_kind_identifier(conn):
    db.upsert_person(conn, id="u3", name="王五", category="biz",
                     threshold_days=3, aliases=[])
    db.upsert_channel(conn, person_id="u3", kind="wechat",
                      identifier="wxid_test_003", label="王五会话")
    db.upsert_channel(conn, person_id="u3", kind="wechat",
                      identifier="wxid_test_003", label="王五备注")
    chans = db.get_channels(conn, "u3")
    assert len(chans) == 1
    assert chans[0]["label"] == "王五备注"


def test_set_watch_toggles_flag(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    assert db.get_person(conn, "u1")["watch"] == 0      # default off
    db.set_watch(conn, "u1", True)
    assert db.get_person(conn, "u1")["watch"] == 1
    db.set_watch(conn, "u1", False)
    assert db.get_person(conn, "u1")["watch"] == 0


def test_suggestions_kind_round_trips_and_filters(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="w1", name="张三")
    db.add_suggestions(conn, cid, [{"stance": "稳妥", "body": "回复A"}], kind="reply")
    db.add_suggestions(conn, cid, [{"stance": "稳妥", "body": "开场B"}], kind="opener")
    assert len(db.get_suggestions(conn, cid)) == 2                       # kind=None → all
    openers = db.get_suggestions(conn, cid, kind="opener")
    assert len(openers) == 1 and openers[0]["body"] == "开场B"
    assert openers[0]["kind"] == "opener"


def test_account_tool_round_trips(conn):
    db.upsert_account(conn, account_id=4, platform="wechat", self_id="wxid_test_renxiong",
                      label="仁兄号", tool="powerdata")
    accts = {a["account_id"]: a for a in db.get_accounts(conn)}
    assert accts[4]["tool"] == "powerdata"
    # default stays "" when tool not given (backward compat)
    db.upsert_account(conn, account_id=5, platform="wechat", self_id="wxid_test_bandi")
    assert {a["account_id"]: a for a in db.get_accounts(conn)}[5]["tool"] == ""
    # upsert updates tool in place
    db.upsert_account(conn, account_id=4, platform="wechat", self_id="wxid_test_renxiong",
                      tool="fullwechat")
    assert {a["account_id"]: a for a in db.get_accounts(conn)}[4]["tool"] == "fullwechat"


def test_ensure_columns_adds_tool_on_old_accounts_db():
    # simulate an OLD db whose accounts table predates the tool column → re-init adds it
    c = db.connect(":memory:")
    c.executescript(
        "CREATE TABLE accounts (account_id INTEGER PRIMARY KEY, platform TEXT NOT NULL, "
        "label TEXT DEFAULT '', self_id TEXT DEFAULT '', host TEXT DEFAULT '', "
        "cred_ref TEXT DEFAULT '', created_at INTEGER NOT NULL);")
    assert "tool" not in {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
    db.init_db(c)   # schema (no-op for accounts) + _ensure_columns adds the column
    assert "tool" in {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
    c.close()


def test_ensure_columns_adds_missing_on_old_db():
    # simulate an OLD db created without watch/kind, then re-init → columns added
    c = db.connect(":memory:")
    c.executescript(
        "CREATE TABLE persons (id TEXT PRIMARY KEY, name TEXT NOT NULL, "
        "category TEXT DEFAULT '', threshold_days REAL DEFAULT 7, aliases TEXT DEFAULT '[]', "
        "created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);")
    assert "watch" not in {r[1] for r in c.execute("PRAGMA table_info(persons)")}
    db.init_db(c)   # runs schema (IF NOT EXISTS, no-op for persons) + _ensure_columns
    cols = {r[1] for r in c.execute("PRAGMA table_info(persons)")}
    assert "watch" in cols
    c.close()


def test_unlink_conversation_splits_bad_merge(conn):
    db.upsert_person(conn, id="u1", name="李四", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_other", name="同名")
    db.link_person(conn, cid, "u1")
    db.upsert_channel(conn, person_id="u1", kind="wechat", identifier="wxid_other")
    freed = db.unlink_conversation(conn, cid)
    assert freed == "u1"
    assert db.get_conversation(conn, cid)["person_id"] is None
    # the endpoint row is gone too (no longer a reachable endpoint for u1)
    assert ("wechat", "wxid_other") not in [(c["kind"], c["identifier"]) for c in db.get_channels(conn, "u1")]


def test_dedup_channels_folds_phone_format_variants(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="13686472775")
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="+8613686472775")  # same canon
    db.upsert_channel(conn, person_id="u1", kind="phone", identifier="13760177688")      # distinct
    db.upsert_channel(conn, person_id="u1", kind="wechat", identifier="wxid_a")
    folded = db.dedup_channels(conn)
    ids = sorted((c["kind"], c["identifier"]) for c in db.get_channels(conn, "u1"))
    assert folded == 1
    assert ids == [("phone", "13686472775"), ("phone", "13760177688"), ("wechat", "wxid_a")]


def test_endpoints_with_recency_per_identifier(conn):
    from jl import ingest
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    db.upsert_account(conn, account_id=2, platform="phone", self_id="s")
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_a", name="张三")
    b = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="13686472775", name="张三")
    for cid in (a, b):
        db.link_person(conn, cid, "u1")
    db.insert_messages(conn, a, [ingest.MsgRecord(msg_key="a1", ts=100, content="x", direction="in")])
    db.insert_messages(conn, b, [ingest.MsgRecord(msg_key="b1", ts=200, content="y", direction="in")])
    by = {(e["kind"], e["identifier"]): e for e in db.endpoints_with_recency(conn, "u1")}
    assert by[("wechat", "wxid_a")]["last_ts"] == 100      # each endpoint keeps its own recency
    assert by[("phone", "13686472775")]["last_ts"] == 200  # not collapsed one-per-platform
    assert by[("wechat", "wxid_a")]["conversation_id"] == a


def test_set_endpoint_pin_marks_one_endpoint(conn):
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="u1", kind="wechat", identifier="wxid_a")
    db.set_endpoint_pin(conn, "u1", "wechat", "wxid_a", True)
    chans = {(c["kind"], c["identifier"]): c for c in db.get_channels(conn, "u1")}
    assert chans[("wechat", "wxid_a")]["pinned"] == 1


def test_dedup_phone_conversations_merges_format_variants(conn):
    from jl import ingest
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=2, platform="phone", self_id="me")
    # same number, two formats → two conversations (the bug)
    a = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="13686472775", name="张三")
    b = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="+8613686472775", name="张三")
    # a genuinely DIFFERENT number under the same person — must stay separate
    d = db.upsert_conversation(conn, account_id=2, platform="phone", chat_id="13760177688", name="张三")
    for cid in (a, b, d):
        db.link_person(conn, cid, "u1")
    db.insert_messages(conn, a, [ingest.MsgRecord(msg_key="p:1", ts=10, content="[通话]", direction="in")])
    db.insert_messages(conn, b, [ingest.MsgRecord(msg_key="p:2", ts=20, content="[通话]", direction="out")])
    db.insert_messages(conn, d, [ingest.MsgRecord(msg_key="p:3", ts=30, content="[通话]", direction="in")])

    merged = db.dedup_phone_conversations(conn)
    assert merged == 1                                   # one duplicate folded
    convs = db.get_conversations(conn, person_id="u1")
    ids = sorted(c["chat_id"] for c in convs)
    assert ids == ["13686472775", "13760177688"]         # canonical kept, distinct stays
    kept = next(c for c in convs if c["chat_id"] == "13686472775")
    n = conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (kept["id"],)).fetchone()[0]
    assert n == 2                                         # both calls merged onto the kept conv


def test_log_event_appends_audit_trail(conn):
    db.log_event(conn, kind="sweep", person_id=None,
                 actor="user", detail={"red": 2})
    db.log_event(conn, kind="auto_add", person_id="u1",
                 actor="agent", detail={"prefix": "🟡jl-"})
    events = db.get_events(conn, limit=10)
    assert len(events) == 2
    assert events[0]["kind"] == "auto_add"   # newest first
    assert events[0]["detail"]["prefix"] == "🟡jl-"


def test_record_tokens_accumulates_usage(conn):
    db.record_tokens(conn, channel_kind="wechat", op="get_chat_history",
                     reach_count=3, tokens_in=120, tokens_out=40)
    total = db.token_summary(conn)
    assert total["reach_count"] == 3
    assert total["tokens_in"] == 120
    assert total["tokens_out"] == 40


def test_unify_by_wxid_merges_same_wxid_across_accounts(conn):
    from jl import ingest
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_me", tool="fullwechat")
    db.upsert_account(conn, account_id=5, platform="wechat", self_id="wxid_oki8", tool="powerdata")
    db.add_self_identity(conn, "wechat", "wxid_me")
    db.add_self_identity(conn, "wechat", "wxid_oki8")
    # same contact (wxid_shir) reachable from BOTH accounts → must merge to ONE person
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_shir", name="Shirley")
    b = db.upsert_conversation(conn, account_id=5, platform="wechat", chat_id="wxid_shir", name="养虾人")
    # a self chat must be skipped
    sc = db.upsert_conversation(conn, account_id=5, platform="wechat", chat_id="wxid_oki8", name="自己")
    # a different contact stays separate
    d = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_other", name="李四")
    out = db.unify_by_wxid(conn)
    pa = db.get_conversation(conn, a)["person_id"]
    pb = db.get_conversation(conn, b)["person_id"]
    assert pa and pa == pb                                  # both Shirley convs → one person
    assert db.get_conversation(conn, sc)["person_id"] is None   # self chat skipped
    assert db.get_conversation(conn, d)["person_id"] != pa      # different wxid not merged
