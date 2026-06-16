"""SELF(自我) identity registry — declare own ids, 出站识别, self-conversation exclusion."""
from jl import db, assist, ingest


def _seed():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="wxid_me")
    return c


def test_add_get_remove_is_self_phone_canon():
    c = _seed()
    db.add_self_identity(c, "phone", "+8613800000000", persona="自我")
    assert db.is_self(c, "phone", "13800000000")        # canon match (+86 stripped)
    assert [s["identifier"] for s in db.get_self_identities(c)] == ["13800000000"]
    db.remove_self_identity(c, "phone", "13800000000")
    assert not db.is_self(c, "phone", "+8613800000000")


def test_persona_defaults_and_override():
    c = _seed()
    db.add_self_identity(c, "wechat", "wxid_bot", persona="AI分身", label="代码班迪")
    s = db.get_self_identities(c)[0]
    assert s["persona"] == "AI分身" and s["label"] == "代码班迪"


def test_seed_self_from_accounts():
    c = _seed()
    n = db.seed_self_from_accounts(c)
    assert n == 1 and db.is_self(c, "wechat", "wxid_me")


def test_suggest_self_identities_only_account_selfid_no_name_garbage():
    c = _seed()
    # a contact mislabeled with "我" must NOT be suggested as self (name-guess removed)
    db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="filehelper", name="文件传输助手")
    db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_mix", name="Roy/我/Connie")
    sug = {(s["kind"], s["identifier"]) for s in db.suggest_self_identities(c)}
    assert sug == {("wechat", "wxid_me")}               # only the account self_id, no garbage


def test_mark_person_self_converts_contact_to_self():
    c = _seed()
    db.upsert_person(c, id="renxiong", name="仁兄", category="biz", threshold_days=7, aliases=[])
    cv = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wangliren123", name="仁兄")
    db.link_person(c, cv, "renxiong")
    db.mark_person_self(c, "renxiong")
    assert db.is_self(c, "wechat", "wangliren123")       # their id is now SELF
    assert db.get_person(c, "renxiong") is None          # the wrong contact is gone
    assert db.get_conversation(c, cv)["person_id"] is None  # conv unlinked (becomes self-chat)


def test_apply_self_directions_marks_my_messages_out():
    c = _seed()
    db.add_self_identity(c, "wechat", "wxid_me")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_peer", name="张三")
    db.insert_messages(c, cid, [
        ingest.MsgRecord(msg_key="m1", ts=1, content="我说的", sender_id="wxid_me", direction="in"),
        ingest.MsgRecord(msg_key="m2", ts=2, content="对方说的", sender_id="wxid_peer", direction="in")])
    assert db.apply_self_directions(c) == 1
    dirs = {r["content"]: r["direction"] for r in c.execute("SELECT content, direction FROM messages")}
    assert dirs["我说的"] == "out" and dirs["对方说的"] == "in"


def test_self_conversation_excluded_from_routing():
    c = _seed()
    db.upsert_person(c, id="renxiong", name="仁兄", category="biz", threshold_days=7, aliases=[])
    sc = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_self_alt", name="仁兄")
    db.link_person(c, sc, "renxiong")
    db.insert_messages(c, sc, [ingest.MsgRecord(msg_key="s1", ts=1, content="hi", direction="in")])
    db.add_self_identity(c, "wechat", "wxid_self_alt")     # declare it as MY own account
    assert db.conversation_is_self(c, sc)
    # routing must NOT pick a self conversation (never reach yourself)
    assert assist.primary_conversation(c, "renxiong") is None


def test_reunify_reset_keeps_human_confirmed_clears_auto():
    c = _seed()
    db.upsert_person(c, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_channel(c, person_id="u1", kind="wechat", identifier="wxid_auto")
    auto = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_auto", name="张三")
    human = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="wxid_human", name="李四")
    db.link_conversations(c)                               # auto-links 'auto' (channel match)
    db.set_conversation_person(c, human, "u1")             # human-confirmed (logs 'link' event)
    db.reunify(c, reset=True)
    assert db.get_conversation(c, human)["person_id"] == "u1"   # human link kept
    # auto link cleared then re-derived by link_conversations (channel still matches) → relinked
    assert db.get_conversation(c, auto)["person_id"] == "u1"
