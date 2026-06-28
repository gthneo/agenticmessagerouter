"""TDD: 事 lifecycle engine — 确定性提议 + HITL advance。"""
import os
import tempfile

from jl import db, lifecycle


def _seed():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def test_propose_flags_open_commitment():
    c, p = _seed()
    try:
        mid = db.create_matter(c, title="向张三回款", kind="跟进", person_ids=[], conversation_ids=[])
        db.add_commitment(c, mid, "本周到账", due="", status="open")
        c.commit()
        props = lifecycle.propose(c, now=9_999_999_999, idle_days=7)
        assert any(pr["matter_id"] == mid and pr["signal"] == "承诺未结" for pr in props)
    finally:
        c.close()
        os.unlink(p)


def test_propose_flags_idle():
    c, p = _seed()
    try:
        mid = db.create_matter(c, title="老客户复盘", kind="跟进", person_ids=[], conversation_ids=[])
        c.commit()
        # now far in the future → matter looks idle (no open commitments)
        props = lifecycle.propose(c, now=9_999_999_999, idle_days=7)
        assert any(pr["matter_id"] == mid and pr["signal"] == "闲置" for pr in props)
    finally:
        c.close()
        os.unlink(p)


def test_advance_changes_status_and_logs():
    c, p = _seed()
    try:
        mid = db.create_matter(c, title="x", kind="", person_ids=[], conversation_ids=[])
        c.commit()
        r = lifecycle.advance(c, mid, "handled")
        assert r["ok"]
        ms = db.get_matters(c, status="handled")
        assert any(m["id"] == mid for m in ms)
    finally:
        c.close()
        os.unlink(p)
