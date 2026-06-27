"""reply-draft assistant — context build, version parse, generate (fake llm)."""
import time

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


def test_load_playbook_reads_local_file(tmp_path, monkeypatch):
    p = tmp_path / "playbook.md"
    p.write_text("互惠原则: 先给后取。\nM4 给确定: 不用'等会'。", encoding="utf-8")
    monkeypatch.setenv("AMR_PLAYBOOK", str(p))
    assert "互惠原则" in assist.load_playbook() and "给确定" in assist.load_playbook()


def test_load_playbook_absent_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("AMR_PLAYBOOK", str(tmp_path / "nope.md"))
    assert assist.load_playbook() == ""   # absent → degrade to base style guide


def test_build_context_injects_playbook_when_present():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid, playbook="互惠原则: 先给后取。")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    assert "互惠原则" in sysmsg            # playbook content injected
    assert "守底线" in sysmsg or "不操纵" in sysmsg   # guardrail framing present


def test_build_context_no_playbook_keeps_base_guide():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid, playbook="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    assert "打法库" not in sysmsg          # no playbook → no method block, 1a unchanged


def test_voice_block_injects_my_sent_messages():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    oid = db.queue_outbox(conn, conversation_id=cid, body="哈哈行嘞，我这就安排上", actor="me")
    db.mark_outbox(conn, oid, "sent")
    msgs = assist.build_context(conn, cid, playbook="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    assert "模仿我的口吻" in sysmsg and "哈哈行嘞" in sysmsg   # 口吻沉淀 fed into the draft


def test_voice_block_empty_when_no_sent_history():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid, playbook="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    assert "模仿我的口吻" not in sysmsg   # no sends yet → no voice block (degrade)


def test_get_voice_samples_prefers_this_conversation():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    a = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wa", name="张三")
    b = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wb", name="李四")
    oa = db.queue_outbox(conn, conversation_id=a, body="对A说的话", actor="me"); db.mark_outbox(conn, oa, "sent")
    ob = db.queue_outbox(conn, conversation_id=b, body="对B说的话", actor="me"); db.mark_outbox(conn, ob, "sent")
    s = db.get_voice_samples(conn, conversation_id=a)
    assert s[0] == "对A说的话"   # this conversation's voice first


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


# ---- C / B3: proactive openers ------------------------------------------------

_OPENER = lambda messages, **kw: llm.LLMResult(
    text="1) 稳妥: 开A\n2) 直接: 开B\n3) 有温度: 开C", ok=True,
    provider="fake", model="f1", tokens_in=5, tokens_out=9)


def test_build_opener_context_uses_opener_guide_and_timeline():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_opener_context(conn, "u1", playbook="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    usermsg = next(m["content"] for m in msgs if m["role"] == "user")
    assert "主动" in sysmsg and "开场" in sysmsg          # opener guide, not the reply guide
    assert "在吗" in usermsg and "张三" in usermsg         # timeline + person


def test_build_opener_context_cold_when_no_history():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="cold", name="王五", category="biz", threshold_days=7, aliases=[])
    msgs = assist.build_opener_context(conn, "cold", playbook="")
    usermsg = next(m["content"] for m in msgs if m["role"] == "user")
    assert "初次" in usermsg and "尚无" in usermsg            # cold-start framing
    assert "不要编造" in usermsg or "不要假装" in usermsg      # anti-fabrication guard


def test_primary_conversation_picks_sendable_private_most_msgs():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    small = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="a", name="张三")
    db.link_person(conn, small, "u1")
    db.insert_messages(conn, small, [ingest.MsgRecord(msg_key="s1", ts=9, content="hi", direction="in")])
    big = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="b", name="张三")
    db.link_person(conn, big, "u1")
    db.insert_messages(conn, big, [ingest.MsgRecord(msg_key=f"b{i}", ts=10 + i, content="x", direction="in") for i in range(3)])
    grp = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="g", name="群", type="group")
    db.link_person(conn, grp, "u1")
    pc = assist.primary_conversation(conn, "u1")
    assert pc is not None and pc["id"] == big          # most-messages private wins, group excluded


def test_primary_conversation_prefers_most_recent_over_more_messages():
    # a freshly-active chat (fewer msgs but newer) beats a stale chat with more history
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    stale = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="old", name="张三")
    db.link_person(conn, stale, "u1")
    db.insert_messages(conn, stale, [ingest.MsgRecord(msg_key=f"o{i}", ts=100 + i, content="x", direction="in") for i in range(5)])
    fresh = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="new", name="张三")
    db.link_person(conn, fresh, "u1")
    db.insert_messages(conn, fresh, [ingest.MsgRecord(msg_key="n1", ts=9999, content="刚聊", direction="in")])
    assert assist.primary_conversation(conn, "u1")["id"] == fresh   # recency wins


def test_primary_conversation_uses_best_endpoint(monkeypatch):
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    stale = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_stale", name="张三")
    live = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="wxid_live", name="张三")
    for cid in (stale, live):
        db.link_person(conn, cid, "u1")
    db.insert_messages(conn, stale, [ingest.MsgRecord(msg_key="s1", ts=999, content="x", direction="in")])
    db.insert_messages(conn, live, [ingest.MsgRecord(msg_key="l1", ts=1, content="y", direction="in")])
    # only wxid_live is sendable → routing picks it despite lower recency
    monkeypatch.setattr(assist, "_sendable_chat_ids", lambda: {"wxid_live"})
    assert assist.primary_conversation(conn, "u1")["chat_id"] == "wxid_live"


def test_primary_conversation_none_when_no_conversation():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="cold", name="王五", category="biz", threshold_days=7, aliases=[])
    assert assist.primary_conversation(conn, "cold") is None


def test_generate_opener_stores_opener_kind():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    n = assist.generate_opener(conn, "u1", n=3, llm_complete=_OPENER)
    assert n == 3
    openers = db.get_suggestions(conn, cid, kind="opener")
    assert [o["body"] for o in openers] == ["开A", "开B", "开C"]
    assert db.get_suggestions(conn, cid, kind="reply") == []   # tagged opener, not reply


def test_generate_opener_no_conversation_returns_zero():
    conn = db.connect(":memory:"); db.init_db(conn)
    db.upsert_person(conn, id="cold", name="王五", category="biz", threshold_days=7, aliases=[])
    assert assist.generate_opener(conn, "cold", llm_complete=_OPENER) == 0


def test_generate_opener_llm_unavailable_is_noop():
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    fake = lambda messages, **kw: llm.LLMResult(ok=False, error="llm_unavailable")
    assert assist.generate_opener(conn, "u1", llm_complete=fake) == 0
    assert db.get_suggestions(conn, cid, kind="opener") == []


def test_proactive_sweep_scopes_watch_red_and_missing_channel():
    conn = db.connect(":memory:"); db.init_db(conn)
    now = int(time.time())
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="s")
    # 🔴 overdue (old msg) — enters via color
    db.upsert_person(conn, id="red", name="张三", category="biz", threshold_days=3, aliases=[])
    rc = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="r", name="张三")
    db.link_person(conn, rc, "red")
    db.insert_messages(conn, rc, [ingest.MsgRecord(msg_key="r1", ts=now - 9 * 86400, content="老消息", direction="in")])
    # 🟢 fresh but WATCHED — watch overrides color
    db.upsert_person(conn, id="grn", name="李四", category="biz", threshold_days=14, aliases=[])
    db.set_watch(conn, "grn", True)
    gc = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="g", name="李四")
    db.link_person(conn, gc, "grn")
    db.insert_messages(conn, gc, [ingest.MsgRecord(msg_key="g1", ts=now - 3600, content="刚聊", direction="in")])
    # 🟢 fresh, NOT watched — excluded
    db.upsert_person(conn, id="skip", name="王五", category="biz", threshold_days=14, aliases=[])
    sc = db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="s2", name="王五")
    db.link_person(conn, sc, "skip")
    db.insert_messages(conn, sc, [ingest.MsgRecord(msg_key="s1", ts=now - 3600, content="也刚聊", direction="in")])
    # watched but NO channel → missing_channel (救补)
    db.upsert_person(conn, id="nochan", name="赵六", category="biz", threshold_days=3, aliases=[])
    db.set_watch(conn, "nochan", True)

    out = assist.proactive_sweep(conn, llm_complete=_OPENER)
    drafted = {d["person_id"] for d in out["drafted"]}
    missing = set(out["missing_channel"])
    assert drafted == {"red", "grn"}            # 🔴 + watched-🟢
    assert "skip" not in drafted                # fresh + unwatched excluded
    assert missing == {"nochan"}                # watched, no send channel → 救补
    # dedup: a second sweep skips the ones already drafted
    out2 = assist.proactive_sweep(conn, llm_complete=_OPENER)
    assert {d["person_id"] for d in out2["drafted"]} == set()


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


def test_self_profile_injected_into_drafts(tmp_path, monkeypatch):
    p = tmp_path / "self_profile.md"
    p.write_text("班迪：直爽、重义气、爱算力与液冷；做事给确定。", encoding="utf-8")
    monkeypatch.setenv("AMR_SELF_PROFILE", str(p))
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    msgs = assist.build_context(conn, cid, playbook="")
    sysmsg = next(m["content"] for m in msgs if m["role"] == "system")
    assert "我是谁" in sysmsg and "重义气" in sysmsg          # natural-person profile reaches the prompt


def test_self_profile_absent_no_block(tmp_path, monkeypatch):
    monkeypatch.setenv("AMR_SELF_PROFILE", str(tmp_path / "nope.md"))
    conn = db.connect(":memory:"); db.init_db(conn); cid = _seed(conn)
    sysmsg = next(m["content"] for m in assist.build_context(conn, cid, playbook="") if m["role"] == "system")
    assert "我是谁" not in sysmsg                              # no profile → no block (degrade)


def test_save_then_load_self_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("AMR_SELF_PROFILE", str(tmp_path / "sp.md"))
    assist.save_self_profile("性格核心词")
    assert assist.load_self_profile() == "性格核心词"


# ---- voice ASR sweep (LLM-optional) ----------------------------------------

def _seed_voice_media(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="self")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_zhang", name="张三")
    mid = conn.execute("INSERT INTO messages (conversation_id, account_id, platform, "
                       "msg_key, ts, type, recorded_at) VALUES (?,1,'wechat','v1',1,'voice',1)",
                       (cid,)).lastrowid
    return db.add_media(conn, message_id=mid, kind="voice", source_ref="https://x/v.silk")


def test_transcribe_sweep_writes_transcript(monkeypatch):
    from jl import asr
    conn = db.connect(":memory:"); db.init_db(conn)
    media_id = _seed_voice_media(conn)
    monkeypatch.setattr(asr, "available", lambda: True)
    monkeypatch.setattr(asr, "transcribe",
                        lambda audio, **kw: asr.ASRResult(text="转写结果", ok=True))
    res = assist.transcribe_sweep(conn, fetch=lambda ref: b"audiobytes")
    assert res["transcribed"] == 1 and res["skipped"] == 0
    row = conn.execute("SELECT transcript FROM media WHERE id=?", (media_id,)).fetchone()
    assert row["transcript"] == "转写结果"
    assert db.pending_voice_media(conn) == []


def test_transcribe_sweep_no_provider_is_noop(monkeypatch):
    from jl import asr
    conn = db.connect(":memory:"); db.init_db(conn)
    media_id = _seed_voice_media(conn)
    monkeypatch.setattr(asr, "available", lambda: False)
    res = assist.transcribe_sweep(conn, fetch=lambda ref: b"audiobytes")
    assert res == {"transcribed": 0, "skipped": 0}
    row = conn.execute("SELECT transcript FROM media WHERE id=?", (media_id,)).fetchone()
    assert row["transcript"] == ""          # nothing written — degrades to [语音]


def test_transcribe_sweep_skips_unfetchable(monkeypatch):
    from jl import asr
    conn = db.connect(":memory:"); db.init_db(conn)
    _seed_voice_media(conn)
    monkeypatch.setattr(asr, "available", lambda: True)
    res = assist.transcribe_sweep(conn, fetch=lambda ref: b"")   # fetch yields nothing
    assert res == {"transcribed": 0, "skipped": 1}
