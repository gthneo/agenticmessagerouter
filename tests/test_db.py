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


def test_unify_by_wxid_merges_existing_no_autocreate(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_me", tool="fullwechat")
    db.upsert_account(conn, account_id=5, platform="wechat", self_id="wxid_oki8", tool="powerdata")
    db.add_self_identity(conn, "wechat", "wxid_oki8")
    # an EXISTING person on account1; the SAME wxid appears on account5 → merge to it
    db.upsert_person(conn, id="lisi", name="李四", category="biz", threshold_days=7, aliases=[])
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_shir", name="李四")
    db.link_person(conn, a, "lisi")
    b = db.upsert_conversation(conn, account_id=5, platform="wechat", chat_id="wxid_shir", name="养虾人")
    # an UNLINKED wxid (e.g. a 公众号) must NOT be auto-personified
    pub = db.upsert_conversation(conn, account_id=5, platform="wechat", chat_id="wxid_pub", name="某公众号")
    # a self chat skipped
    sc = db.upsert_conversation(conn, account_id=5, platform="wechat", chat_id="wxid_oki8", name="自己")
    out = db.unify_by_wxid(conn)
    assert db.get_conversation(conn, b)["person_id"] == "lisi"      # merged to existing person
    assert db.get_conversation(conn, pub)["person_id"] is None      # NO auto-create (HITL later)
    assert db.get_conversation(conn, sc)["person_id"] is None       # self skipped
    assert out["linked"] == 1


def test_merge_persons_combines_two_wxids(conn):
    from jl import ingest
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    db.upsert_person(conn, id="p_keep", name="李四", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="p_keep", kind="wechat", identifier="wxid_test_aaa")
    db.upsert_channel(conn, person_id="p_keep", kind="phone", identifier="13000000001")
    db.upsert_person(conn, id="wx-dup", name="李四别名", category="", threshold_days=7, aliases=[])
    db.upsert_channel(conn, person_id="wx-dup", kind="wechat", identifier="wxid_test_bbb")
    cv = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_test_bbb", name="李四")
    db.link_person(conn, cv, "wx-dup")
    assert db.merge_persons(conn, "p_keep", "wx-dup") is True
    assert db.get_person(conn, "wx-dup") is None                       # dup gone
    chans = {(c["kind"], c["identifier"]) for c in db.get_channels(conn, "p_keep")}
    assert ("wechat", "wxid_test_bbb") in chans and ("wechat", "wxid_test_aaa") in chans  # both wxids on one person
    assert db.get_conversation(conn, cv)["person_id"] == "p_keep"      # conv moved


def test_logs_layered_query(conn):
    db.log(conn, "INFO", "self", "我是谁 被读取", {"chars": 10})
    db.log(conn, "WARN", "send", "目标不在近期会话")
    db.log(conn, "ERROR", "llm", "provider down")
    db.log(conn, "DEBUG", "ingest", "pulled 5")
    assert len(db.get_logs(conn)) == 4
    assert [r["component"] for r in db.get_logs(conn, component="send")] == ["send"]
    warn_plus = db.get_logs(conn, level="WARN")        # WARN + ERROR only
    assert {r["level"] for r in warn_plus} == {"WARN", "ERROR"}
    assert db.get_logs(conn, level="INFO")             # INFO+ includes the self read


def test_canon_strips_wxid_device_suffix(conn):
    # PowerData account-key suffix stripped → matches base wxid used in contacts
    assert db._canon_identifier("wechat", "wxid_test12345_d898") == "wxid_test12345"
    assert db._canon_identifier("wechat", "wxid_test12345_e96b") == "wxid_test12345"
    assert db._canon_identifier("wechat", "wxid_test12345") == "wxid_test12345"   # base unchanged
    # non-wxid wechat ids untouched (custom id / 微信号 / gh_)
    assert db._canon_identifier("wechat", "adambb_joy") == "adambb_joy"
    assert db._canon_identifier("wechat", "gh_abc123") == "gh_abc123"
    # is_self matches the base form even if registered suffixed
    db.add_self_identity(conn, "wechat", "wxid_me0001_e96b")
    assert db.is_self(conn, "wechat", "wxid_me0001")          # base form (as seen in a contact)
    assert db.is_self(conn, "wechat", "wxid_me0001_d898")     # another instance's suffix


def test_migrate_types_to_kinds_dry_run_then_apply():
    import jl.db as d
    conn = d.connect(":memory:"); d.init_db(conn)
    conn.execute("INSERT INTO accounts(account_id,platform,created_at) VALUES(1,'wechat',0)")
    conn.execute("INSERT INTO conversations(account_id,platform,chat_id,created_at,updated_at) VALUES(1,'wechat','c',0,0)")
    cid = conn.execute("SELECT id FROM conversations").fetchone()[0]
    for i, t in enumerate(["34", "10002", "49", "text", "3"]):  # voice, system, appmsg, text, image
        conn.execute("INSERT INTO messages(conversation_id,account_id,platform,msg_key,ts,type,recorded_at) "
                     "VALUES(?,1,'wechat',?,?,?,0)", (cid, f"k{i}", i, t))
    conn.commit()
    dry = d.migrate_types_to_kinds(conn, dry_run=True)
    assert dry["changed"] == 3                       # 34,10002,3 (not 49/text)
    assert dry["by_kind"]["voice"] == 1 and dry["by_kind"]["system"] == 1 and dry["by_kind"]["image"] == 1
    assert dry["skipped_appmsg49"] == 1
    # dry-run did not write
    assert conn.execute("SELECT COUNT(*) FROM messages WHERE type='34'").fetchone()[0] == 1
    app = d.migrate_types_to_kinds(conn, dry_run=False)
    assert app["changed"] == 3
    assert conn.execute("SELECT type FROM messages WHERE msg_key='k0'").fetchone()[0] == "voice"
    assert conn.execute("SELECT type FROM messages WHERE msg_key='k1'").fetchone()[0] == "system"
    assert conn.execute("SELECT type FROM messages WHERE msg_key='k2'").fetchone()[0] == "49"   # 49 untouched
    # idempotent: second run changes nothing
    assert d.migrate_types_to_kinds(conn, dry_run=True)["changed"] == 0


# ----- media (voice ASR) ----------------------------------------------------

def _voice_conv(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_zhang", name="张三")
    return cid


def test_add_and_pending_voice_media(conn):
    cid = _voice_conv(conn)
    mid = conn.execute("INSERT INTO messages (conversation_id, account_id, platform, "
                       "msg_key, ts, type, recorded_at) VALUES (?,1,'wechat','k1',1,'voice',1)",
                       (cid,)).lastrowid
    media_id = db.add_media(conn, message_id=mid, kind="voice",
                            source_ref="https://x/v.silk", mime="audio/silk")
    pend = db.pending_voice_media(conn)
    assert len(pend) == 1
    assert pend[0]["id"] == media_id
    assert pend[0]["source_ref"] == "https://x/v.silk"
    assert pend[0]["mime"] == "audio/silk"


def test_pending_voice_media_excludes_transcribed_and_refless(conn):
    cid = _voice_conv(conn)
    mid = conn.execute("INSERT INTO messages (conversation_id, account_id, platform, "
                       "msg_key, ts, type, recorded_at) VALUES (?,1,'wechat','k1',1,'voice',1)",
                       (cid,)).lastrowid
    done = db.add_media(conn, message_id=mid, kind="voice", source_ref="https://x/a")
    db.set_media_transcript(conn, done, "已转写")          # has transcript → excluded
    db.add_media(conn, message_id=mid, kind="voice", source_ref="")  # no ref → excluded
    assert db.pending_voice_media(conn) == []


def test_set_media_transcript_sets_status_and_clears_pending(conn):
    cid = _voice_conv(conn)
    mid = conn.execute("INSERT INTO messages (conversation_id, account_id, platform, "
                       "msg_key, ts, type, recorded_at) VALUES (?,1,'wechat','k1',1,'voice',1)",
                       (cid,)).lastrowid
    media_id = db.add_media(conn, message_id=mid, kind="voice", source_ref="https://x/a")
    db.set_media_transcript(conn, media_id, "你好世界")
    row = conn.execute("SELECT transcript, status FROM media WHERE id=?", (media_id,)).fetchone()
    assert row["transcript"] == "你好世界" and row["status"] == "done"
    assert db.pending_voice_media(conn) == []


def test_ingest_voice_with_ref_creates_media_row(conn):
    from jl import ingest
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    conv = ingest.ConvRecord(chat_id="wxid_test_zhang", name="张三")
    rec = ingest.from_canonical(
        {"channel": "wechat", "kind": "voice", "text": "[语音]", "ts": 5, "sender": "张三",
         "msg_id": "v1", "media": {"placeholder": "[语音]", "ref": "https://x/v.silk",
                                   "mime": "audio/silk"}})
    cid, ins = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=[rec])
    assert ins == 1
    pend = db.pending_voice_media(conn)
    assert len(pend) == 1
    assert pend[0]["source_ref"] == "https://x/v.silk"
    assert pend[0]["mime"] == "audio/silk"
    m = conn.execute("SELECT kind FROM media WHERE id=?", (pend[0]["id"],)).fetchone()
    assert m["kind"] == "voice"


def test_ingest_voice_without_ref_creates_no_media(conn):
    from jl import ingest
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    conv = ingest.ConvRecord(chat_id="wxid_test_zhang", name="张三")
    rec = ingest.from_canonical(
        {"channel": "wechat", "kind": "voice", "text": "[语音]", "ts": 5, "sender": "张三",
         "msg_id": "v2", "media": {"placeholder": "[语音]"}})   # no ref
    db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=[rec])
    assert conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0


def test_voice_backend_transcript_skips_asr():
    """后端(微信)给了 transcript → media 行直接 done、不进待转写队列(王总 2026-06-28 钦定首选后端转写)。"""
    import jl.db as d, jl.ingest as ig
    conn = d.connect(":memory:"); d.init_db(conn)
    conn.execute("INSERT INTO accounts(account_id,platform,created_at) VALUES(1,'wechat',0)")
    conn.execute("INSERT INTO conversations(account_id,platform,chat_id,created_at,updated_at) VALUES(1,'wechat','c',0,0)")
    cid = conn.execute("SELECT id FROM conversations").fetchone()[0]
    # canonical voice envelope WITH a backend transcript
    env = {"schema": "message.canonical/1", "channel": "wechat", "kind": "voice",
           "text": "[语音]", "ts": 1, "sender": "张三", "direction": "in",
           "media": {"placeholder": "[语音]", "ref": "http://h/api/media/c/9",
                     "mime": "audio/silk", "duration": 3, "transcript": "明天下午三点开会"}}
    rec = ig.from_canonical(env, source="fullwx")
    d.insert_messages(conn, cid, [rec])
    m = conn.execute("SELECT kind,status,transcript FROM media WHERE kind='voice'").fetchone()
    assert m["status"] == "done" and m["transcript"] == "明天下午三点开会"
    assert d.pending_voice_media(conn) == []        # 有 transcript → 不待转写
