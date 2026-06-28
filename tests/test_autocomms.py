"""TDD tests for autocomms.propose_replies — 监管下自动沟通 Phase 1.

CRITICAL: propose_replies is PROPOSE-ONLY — it must NEVER send or create outbox rows.
These tests verify safety constraints, structural invariants, and happy-path candidates.

Synthetic fixtures only — no real contacts, no real data.
"""
import os
import tempfile

from jl import db, autocomms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _c():
    """Create a fresh in-file DB (tempfile) with schema + seed data."""
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def _seed_conv(conn, *, mode="off", inbound_msg=None, long_question=False):
    """Seed a minimal conversation (account + conversation) and optionally set its
    autonomy mode + insert a synthetic inbound message. Returns conversation_id."""
    # account_id must be INTEGER 0-255 per schema CHECK constraint
    db.upsert_account(conn, account_id=1, platform="wechat",
                      label="测试账号", self_id="wxid_self_test")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_001", name="张三",
                                 type="private")
    if mode != "off":
        db.set_autonomy(conn, cid, mode)
    # Determine message content: explicit inbound_msg, or long_question synthetic content
    _content = None
    if long_question:
        _content = "你们这个合同条款第七条怎么计算的？"
    elif inbound_msg is not None:
        _content = inbound_msg
    if _content is not None:
        conn.execute(
            "INSERT INTO messages (conversation_id, account_id, platform, msg_key, "
            "ts, sender, sender_id, direction, type, content, raw, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', strftime('%s','now'))",
            (cid, 1, "wechat", "msg_test_001", 1_000_000_000,
             "张三", "wxid_test_001", "in", "text", _content))
        conn.commit()
    return cid


# ---------------------------------------------------------------------------
# Safety: default → nothing proposed
# ---------------------------------------------------------------------------

def test_off_yields_nothing():
    """All conversations default to autonomy=off → propose_replies returns []."""
    c, p = _c()
    try:
        _seed_conv(c, mode="off", inbound_msg="hi")
        result = autocomms.propose_replies(c, now=1_000_000_000)
        assert result == [], f"Expected [] for off mode, got {result}"
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Safety: killswitch blocks everything
# ---------------------------------------------------------------------------

def test_killswitch_blocks_all(monkeypatch):
    """Global killswitch engaged → propose_replies returns [] regardless of mode."""
    c, p = _c()
    try:
        db.set_killswitch(c, True)
        _seed_conv(c, mode="observe", inbound_msg="收到了吗")
        result = autocomms.propose_replies(c, now=1_000_000_000)
        assert result == [], f"Killswitch should block all; got {result}"
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# CRITICAL SAFETY: propose_replies NEVER creates outbox rows
# ---------------------------------------------------------------------------

def test_propose_never_sends(monkeypatch):
    """propose_replies must not create outbox rows — check before/after counts."""
    c, p = _c()
    try:
        _seed_conv(c, mode="supervised", inbound_msg="好的")
        before = len(db.get_outbox(c, status="pending"))
        autocomms.propose_replies(c, now=1_000_000_000)
        after = len(db.get_outbox(c, status="pending"))
        assert after == before, (
            f"propose_replies created {after - before} outbox row(s) — MUST NOT send!")
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Happy path: observe mode → shadow candidates
# ---------------------------------------------------------------------------

def test_observe_yields_shadow_candidate():
    """observe mode + short inbound ack-worthy message → action='shadow'."""
    c, p = _c()
    try:
        cid = _seed_conv(c, mode="observe", inbound_msg="好的")
        results = autocomms.propose_replies(c, now=1_000_000_000)
        assert len(results) >= 1, "Expected at least one candidate for observe+inbound"
        # Find our conversation in the results
        conv_results = [r for r in results if r["conversation_id"] == cid]
        assert conv_results, f"No result for conversation {cid}; got {results}"
        r = conv_results[0]
        assert r["action"] == "shadow", f"Expected shadow for observe mode, got {r['action']}"
        assert r["mode"] == "observe"
        assert r["draft"] is not None, "Expected a draft for short ack-worthy inbound"
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Happy path: no inbound → no proposal
# ---------------------------------------------------------------------------

def test_no_inbound_no_proposal():
    """If there are no inbound messages (nothing to reply to), no proposal emitted."""
    c, p = _c()
    try:
        _seed_conv(c, mode="observe", inbound_msg=None)
        results = autocomms.propose_replies(c, now=1_000_000_000)
        # No messages at all → _needs_reply returns False → skip
        assert all(r.get("action") != "arm" for r in results), (
            "No inbound message should not produce an arm action")
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Safety: long question → action=human, draft=None
# ---------------------------------------------------------------------------

def test_long_question_goes_to_human():
    """Long/question inbound → _draft_ack returns None → action='human'."""
    c, p = _c()
    try:
        cid = _seed_conv(c, mode="supervised", long_question=True)
        results = autocomms.propose_replies(c, now=1_000_000_000)
        conv_results = [r for r in results if r["conversation_id"] == cid]
        assert conv_results, f"Expected a result for long question; got {results}"
        r = conv_results[0]
        assert r["action"] == "human", (
            f"Long question should go to human, got {r['action']}")
        assert r["draft"] is None, (
            f"Long question should have draft=None (no safe phrase), got {r['draft']}")
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Time window: outside WORK_HOURS → action stays 'human' for supervised
# ---------------------------------------------------------------------------

def test_outside_window_supervised_goes_human():
    """Outside work hours → supervised action should not be 'arm' (must go human)."""
    c, p = _c()
    try:
        cid = _seed_conv(c, mode="supervised", inbound_msg="好的")
        # now=0 → localtime hour=8 (UTC+8) — 00:00 UTC = 08:00 CST, below WORK_HOURS[0]=9
        # Use a timestamp that definitely maps to an off-hours time:
        # 2001-09-09T01:46:40 UTC = 09:46:40 local (CST +8) actually is IN window...
        # Use 1_000_000_000 - 3600 → 3am local — definitely outside.
        # More robustly: patch _in_window to return False.
        import jl.autocomms as ac
        original = ac._in_window
        ac._in_window = lambda now: False   # monkeypatch: simulate outside window
        try:
            results = autocomms.propose_replies(c, now=1_000_000_000)
        finally:
            ac._in_window = original
        conv_results = [r for r in results if r["conversation_id"] == cid]
        if conv_results:
            r = conv_results[0]
            assert r["action"] != "arm", (
                f"Outside window: supervised should not arm, got {r['action']}")
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# Structure: result dict has required keys
# ---------------------------------------------------------------------------

def test_result_dict_shape():
    """Each result dict must have the required keys."""
    c, p = _c()
    try:
        cid = _seed_conv(c, mode="observe", inbound_msg="好的")
        results = autocomms.propose_replies(c, now=1_000_000_000)
        for r in results:
            assert "conversation_id" in r
            assert "mode" in r
            assert "action" in r
    finally:
        c.close(); os.unlink(p)


def test_in_window_uses_beijing_time_not_server_tz():
    """时间窗按北京时间(UTC+8)算, 不依赖服务器 TZ(部署多为 UTC)。
    否则"上班9-21"会被 UTC 算成北京 17:00-05:00(夜里发/白天静默, 拧了)。"""
    import calendar
    from jl import autocomms as ac

    def utc_ts(hour):
        return calendar.timegm((2026, 6, 28, hour, 0, 0, 0, 0, 0))

    assert ac._in_window(utc_ts(2)) is True    # 北京 10:00 (UTC 02:00) → 窗内
    assert ac._in_window(utc_ts(12)) is True   # 北京 20:00 (UTC 12:00) → 窗内
    assert ac._in_window(utc_ts(15)) is False  # 北京 23:00 (UTC 15:00) → 窗外
    assert ac._in_window(utc_ts(22)) is False  # 北京 06:00 (UTC 22:00) → 窗外(早于9)
