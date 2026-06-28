"""双闸安全区分类器测试 (§6 设计).

纯逻辑 / zero LLM / classify-only — gate.py 不发送任何内容.
Synthetic data only: 合成话术条目, 无真实联系人.
"""
import os
import tempfile

from jl import db, gate


def _seed():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    db.add_safe_phrase(c, "收到，马上处理", kind="确认")
    c.commit()
    return c, p


def test_gate1_whitelist_hit():
    c, p = _seed()
    try:
        hit, m = gate.matches_whitelist(c, "好的，收到，马上处理，谢谢")
        assert hit and m["pattern"] == "收到，马上处理"
        miss, _ = gate.matches_whitelist(c, "这个我得再想想，明天答复你金额")
        assert not miss
    finally:
        c.close()
        os.unlink(p)


def test_classify_auto_requires_whitelist_and_low_risk():
    c, p = _seed()
    try:
        r = gate.classify(c, "好的，收到，马上处理", llm_verdict="low")
        assert r["allow_auto"] is True and r["tier"] == "auto"
    finally:
        c.close()
        os.unlink(p)


def test_classify_no_llm_is_conservative():
    c, p = _seed()
    try:
        r = gate.classify(c, "好的，收到，马上处理", llm_verdict=None)
        assert r["allow_auto"] is False and r["tier"] == "needs_llm"  # 命中白名单但无 LLM → 交人
    finally:
        c.close()
        os.unlink(p)


def test_classify_high_risk_blocks():
    c, p = _seed()
    try:
        r = gate.classify(c, "好的，收到，马上处理", llm_verdict="high")
        assert r["allow_auto"] is False and r["tier"] == "human"
    finally:
        c.close()
        os.unlink(p)


def test_classify_off_whitelist_is_human():
    c, p = _seed()
    try:
        r = gate.classify(c, "我答应你下周一定付五万", llm_verdict="low")  # 未命中话术库
        assert r["allow_auto"] is False and r["tier"] == "human" and r["gate1"] is False
    finally:
        c.close()
        os.unlink(p)
