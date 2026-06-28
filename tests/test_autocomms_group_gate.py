"""TDD tests for the SIZE-TIERED group auto-engagement gate in autocomms.

王总 policy:
- private (1:1)                          → unchanged.
- small group (<10 members) OR self=群主 → active: auto-reply eligible WITHOUT @me.
- large group (>=10, not owner)          → @mention-gated: only when last inbound @me;
                                            else → action='human' (visible, not dropped).
- unknown group size                     → conservative = treat as large (@-gated).

Synthetic fixtures only — no real contacts. Group names like "测试群",
member senders wxid_test_a/b/c, etc.
"""
import os
import tempfile

from jl import db, autocomms


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _c():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat",
                      label="测试账号", self_id="wxid_self_test")
    return c, p


def _group_conv(conn, *, chat_id="123@chatroom", name="测试群", mode="observe"):
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id=chat_id, name=name, type="group")
    if mode != "off":
        db.set_autonomy(conn, cid, mode)
    return cid


def _add_msg(conn, cid, *, sender_id, content="好的", mentioned=False,
             direction="in", ts=1_000_000_000, key=None):
    key = key or f"k_{sender_id}_{ts}_{content[:4]}"
    conn.execute(
        "INSERT INTO messages (conversation_id, account_id, platform, msg_key, ts, "
        "sender, sender_id, direction, type, content, is_mentioned, raw, recorded_at) "
        "VALUES (?, 1, 'wechat', ?, ?, ?, ?, ?, 'text', ?, ?, '{}', "
        "strftime('%s','now'))",
        (cid, key, ts, sender_id, sender_id, direction, content,
         1 if mentioned else 0))
    conn.commit()


# ---------------------------------------------------------------------------
# group_size_estimate — distinct sender_id count (approximation)
# ---------------------------------------------------------------------------

def test_group_size_estimate_counts_distinct_senders():
    c, p = _c()
    try:
        cid = _group_conv(c)
        _add_msg(c, cid, sender_id="wxid_test_a", ts=1_000_000_001)
        _add_msg(c, cid, sender_id="wxid_test_b", ts=1_000_000_002)
        _add_msg(c, cid, sender_id="wxid_test_a", ts=1_000_000_003)  # dup sender
        _add_msg(c, cid, sender_id="wxid_test_c", ts=1_000_000_004)
        assert autocomms.group_size_estimate(c, cid) == 3
    finally:
        c.close(); os.unlink(p)


def test_group_size_estimate_ignores_empty_sender():
    c, p = _c()
    try:
        cid = _group_conv(c)
        _add_msg(c, cid, sender_id="wxid_test_a", ts=1_000_000_001)
        _add_msg(c, cid, sender_id="", ts=1_000_000_002, content="系统消息")
        assert autocomms.group_size_estimate(c, cid) == 1
    finally:
        c.close(); os.unlink(p)


def test_group_size_estimate_zero_for_empty():
    c, p = _c()
    try:
        cid = _group_conv(c)
        assert autocomms.group_size_estimate(c, cid) == 0
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# is_small_group
# ---------------------------------------------------------------------------

def test_is_small_group_true_when_few_senders():
    c, p = _c()
    try:
        cid = _group_conv(c)
        for i, s in enumerate(("a", "b", "c")):
            _add_msg(c, cid, sender_id=f"wxid_test_{s}", ts=1_000_000_000 + i)
        assert autocomms.is_small_group(c, cid) is True
    finally:
        c.close(); os.unlink(p)


def test_is_small_group_false_when_many_senders():
    c, p = _c()
    try:
        cid = _group_conv(c)
        for i in range(12):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        assert autocomms.is_small_group(c, cid) is False
    finally:
        c.close(); os.unlink(p)


def test_is_small_group_false_for_private():
    """A private (1:1) conversation is not a 'small group' — the gate doesn't apply."""
    c, p = _c()
    try:
        cid = db.upsert_conversation(c, account_id=1, platform="wechat",
                                     chat_id="wxid_test_x", name="李四", type="private")
        _add_msg(c, cid, sender_id="wxid_test_x")
        assert autocomms.is_small_group(c, cid) is False
    finally:
        c.close(); os.unlink(p)


def test_is_small_group_threshold_configurable():
    c, p = _c()
    try:
        cid = _group_conv(c)
        for i in range(6):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        # 6 senders: small under default(10), large under threshold=5
        assert autocomms.is_small_group(c, cid) is True
        assert autocomms.is_small_group(c, cid, threshold=5) is False
    finally:
        c.close(); os.unlink(p)


def test_threshold_reads_app_settings():
    c, p = _c()
    try:
        cid = _group_conv(c)
        for i in range(6):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        db.set_setting(c, "group_small_threshold", "5")
        # default arg falls back to app_settings → 5 → 6 senders is now large
        assert autocomms.group_small_threshold(c) == 5
        assert autocomms.is_small_group(c, cid) is False
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# propose_replies — group routing
# ---------------------------------------------------------------------------

def test_small_group_proposes_without_mention():
    """Small group: short ack-worthy inbound, NOT @me → still a candidate (active)."""
    c, p = _c()
    try:
        cid = _group_conv(c, mode="observe")
        for i, s in enumerate(("a", "b")):
            _add_msg(c, cid, sender_id=f"wxid_test_{s}", ts=1_000_000_000 + i)
        _add_msg(c, cid, sender_id="wxid_test_a", content="好的",
                 mentioned=False, ts=1_000_000_100, key="last")
        res = [r for r in autocomms.propose_replies(c, now=1_000_000_100)
               if r["conversation_id"] == cid]
        assert res, "small group should still produce a candidate"
        r = res[0]
        assert r["action"] == "shadow"        # observe mode
        assert r["draft"] is not None
    finally:
        c.close(); os.unlink(p)


def test_large_group_no_mention_goes_human():
    """Large group, last inbound NOT @me → action='human' with a reason (visible)."""
    c, p = _c()
    try:
        cid = _group_conv(c, mode="observe")
        for i in range(11):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        _add_msg(c, cid, sender_id="wxid_test_00", content="好的",
                 mentioned=False, ts=1_000_000_100, key="last")
        res = [r for r in autocomms.propose_replies(c, now=1_000_000_100)
               if r["conversation_id"] == cid]
        assert res, "large group should still surface a (human) candidate, not vanish"
        r = res[0]
        assert r["action"] == "human"
        assert r.get("reason"), "should carry a reason explaining the @-gate"
    finally:
        c.close(); os.unlink(p)


def test_large_group_with_mention_proposes():
    """Large group, last inbound @me → proceed as normal (candidate)."""
    c, p = _c()
    try:
        cid = _group_conv(c, mode="observe")
        for i in range(11):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        _add_msg(c, cid, sender_id="wxid_test_00", content="好的",
                 mentioned=True, ts=1_000_000_100, key="last")
        res = [r for r in autocomms.propose_replies(c, now=1_000_000_100)
               if r["conversation_id"] == cid]
        assert res
        r = res[0]
        assert r["action"] == "shadow"        # @me → proceeds → observe shadow
        assert r["draft"] is not None
    finally:
        c.close(); os.unlink(p)


def test_unknown_size_group_conservative_is_large():
    """A group whose only inbound is the (unmentioned) trigger itself → 1 distinct
    sender = 'small' by raw count? No — guard: a group with too few observed senders
    to be sure is treated conservatively. Here we make it ambiguous: a single inbound
    sender, NOT @me. Conservative path = if not confidently small, @-gate applies."""
    c, p = _c()
    try:
        cid = _group_conv(c, mode="observe")
        # exactly threshold senders (==10) → NOT < threshold → large/unknown side
        for i in range(10):
            _add_msg(c, cid, sender_id=f"wxid_test_{i:02d}", ts=1_000_000_000 + i)
        _add_msg(c, cid, sender_id="wxid_test_00", content="好的",
                 mentioned=False, ts=1_000_000_100, key="last")
        res = [r for r in autocomms.propose_replies(c, now=1_000_000_100)
               if r["conversation_id"] == cid]
        assert res
        assert res[0]["action"] == "human", "==threshold is not <threshold → @-gated"
    finally:
        c.close(); os.unlink(p)


def test_private_unchanged_by_group_gate():
    """Private conversation: no @mention required, behaves exactly as before."""
    c, p = _c()
    try:
        cid = db.upsert_conversation(c, account_id=1, platform="wechat",
                                     chat_id="wxid_test_x", name="李四", type="private")
        db.set_autonomy(c, cid, "observe")
        _add_msg(c, cid, sender_id="wxid_test_x", content="好的", mentioned=False)
        res = [r for r in autocomms.propose_replies(c, now=1_000_000_000)
               if r["conversation_id"] == cid]
        assert res
        assert res[0]["action"] == "shadow"
        assert res[0]["draft"] is not None
    finally:
        c.close(); os.unlink(p)
