"""reply-draft assistant — context build, version parse, generate (fake llm)."""
from jl import assist, db, llm, ingest


def _seed(conn):
    db.upsert_person(conn, id="u1", name="张三", category="GC0001", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="w1", name="张三", type="private")
    db.link_person(conn, cid, "u1")
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="m1", ts=1000, content="在吗", sender="张三", direction="in")])
    return cid


def test_build_context_includes_timeline_and_person():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid)
    joined = " ".join(m["content"] for m in msgs)
    assert any(m["role"] == "system" for m in msgs)
    assert "在吗" in joined and "张三" in joined


def test_parse_versions_splits_numbered_blocks():
    raw = "1) 稳妥: 您好,稍后回复\n2) 直接: 现在不方便\n3) 有温度: 在的,马上看"
    vs = assist.parse_versions(raw)
    assert len(vs) == 3
    assert vs[0]["body"] and vs[1]["body"] and vs[2]["body"]


def test_generate_drafts_stores_suggestions_with_fake_llm():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    fake = lambda messages, **kw: llm.LLMResult(
        text="1) 稳妥: A\n2) 直接: B\n3) 有温度: C", ok=True, provider="fake",
        model="f1", tokens_in=5, tokens_out=9)
    n = assist.generate_drafts(conn, cid, n=3, llm_complete=fake)
    assert n == 3
    rows = db.get_suggestions(conn, cid)
    assert [r["body"] for r in rows] == ["A", "B", "C"]


def test_generate_drafts_llm_unavailable_is_noop():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    fake = lambda messages, **kw: llm.LLMResult(ok=False, error="llm_unavailable")
    n = assist.generate_drafts(conn, cid, n=3, llm_complete=fake)
    assert n == 0 and db.get_suggestions(conn, cid) == []


def test_auto_draft_sweep_scopes_to_awaiting_private_linked():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="x", threshold_days=3, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wa", name="张三")
    db.link_person(conn, a, "u1")
    db.insert_messages(conn, a, [ingest.MsgRecord(msg_key="a1", ts=9, content="hi", direction="in")])
    g = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="g", name="群", type="group")
    db.insert_messages(conn, g, [ingest.MsgRecord(msg_key="g1", ts=9, content="x", direction="in")])
    b = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wb", name="李四")
    db.link_person(conn, b, "u1")
    db.insert_messages(conn, b, [ingest.MsgRecord(msg_key="b1", ts=9, content="已回", direction="out")])
    fake = lambda messages, **kw: llm.LLMResult(text="1) 稳妥: R", ok=True, model="f", provider="f")
    touched = assist.auto_draft_sweep(conn, llm_complete=fake)
    assert touched == [a]
