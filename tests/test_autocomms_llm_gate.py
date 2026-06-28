"""TDD tests for autocomms._llm_verdict — 监管下自动回复 Phase 1 Task 4 闸二接真实LLM.

闸二 (LLM 风险判定) must:
- return None when LLM unavailable OR the call itself failed (→ human, conservative).
- return 'low' ONLY when the model clearly says low-risk.
- return 'high' for any other non-empty answer (ambiguous/risky → human side).
- never raise (LLM-optional contract) — exceptions degrade to None.

Synthetic fixtures only — no real contacts, no real data.
"""
import os
import tempfile

from jl import db, autocomms, llm


def _c():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def _seed_conv(conn, *, mode="off", inbound_msg=None):
    db.upsert_account(conn, account_id=1, platform="wechat",
                      label="测试账号", self_id="wxid_self_test")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_001", name="张三",
                                 type="private")
    if mode != "off":
        db.set_autonomy(conn, cid, mode)
    if inbound_msg is not None:
        conn.execute(
            "INSERT INTO messages (conversation_id, account_id, platform, msg_key, "
            "ts, sender, sender_id, direction, type, content, raw, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', strftime('%s','now'))",
            (cid, 1, "wechat", "msg_test_001", 1_000_000_000,
             "张三", "wxid_test_001", "in", "text", inbound_msg))
        conn.commit()
    return cid


# ---------------------------------------------------------------------------
# _llm_verdict — unit
# ---------------------------------------------------------------------------

def test_verdict_none_when_unavailable(monkeypatch):
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: False)
        assert autocomms._llm_verdict(c, "收到", inbound="嗯") is None
    finally:
        c.close(); os.unlink(p)


def test_verdict_low_when_model_says_low(monkeypatch):
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: True)
        monkeypatch.setattr(llm, "complete",
                            lambda *a, **k: llm.LLMResult(ok=True, text="low"))
        assert autocomms._llm_verdict(c, "收到", inbound="嗯") == "low"
    finally:
        c.close(); os.unlink(p)


def test_verdict_high_when_model_says_high(monkeypatch):
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: True)
        monkeypatch.setattr(llm, "complete",
                            lambda *a, **k: llm.LLMResult(ok=True, text="high — 涉及金额"))
        assert autocomms._llm_verdict(c, "收到", inbound="转两万给你") == "high"
    finally:
        c.close(); os.unlink(p)


def test_verdict_none_when_call_failed(monkeypatch):
    """A failed call is 'no assist' → None (human), NOT 'high'."""
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: True)
        monkeypatch.setattr(llm, "complete",
                            lambda *a, **k: llm.LLMResult(ok=False, error="llm_unavailable"))
        assert autocomms._llm_verdict(c, "收到", inbound="嗯") is None
    finally:
        c.close(); os.unlink(p)


def test_verdict_none_when_exception(monkeypatch):
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: True)

        def _boom(*a, **k):
            raise RuntimeError("network down")
        monkeypatch.setattr(llm, "complete", _boom)
        assert autocomms._llm_verdict(c, "收到", inbound="嗯") is None
    finally:
        c.close(); os.unlink(p)


def test_verdict_none_when_empty_text(monkeypatch):
    c, p = _c()
    try:
        monkeypatch.setattr(llm, "available", lambda: True)
        monkeypatch.setattr(llm, "complete",
                            lambda *a, **k: llm.LLMResult(ok=True, text="   "))
        assert autocomms._llm_verdict(c, "收到", inbound="嗯") is None
    finally:
        c.close(); os.unlink(p)


# ---------------------------------------------------------------------------
# propose_replies integration — 'low' verdict → supervised candidate arms
# ---------------------------------------------------------------------------

def test_supervised_arms_when_verdict_low(monkeypatch):
    c, p = _c()
    try:
        cid = _seed_conv(c, mode="supervised", inbound_msg="好的")
        monkeypatch.setattr(autocomms, "_llm_verdict",
                            lambda *a, **k: "low")
        # freeze now into WORK_HOURS window (12:00 local)
        monkeypatch.setattr(autocomms, "_in_window", lambda now: True)
        results = autocomms.propose_replies(c, now=1_000_000_000)
        conv = [r for r in results if r["conversation_id"] == cid]
        assert conv, f"expected a candidate for conv {cid}; got {results}"
        assert any(r["action"] == "arm" for r in conv), (
            f"expected an arm candidate with low verdict; got {conv}")
    finally:
        c.close(); os.unlink(p)
