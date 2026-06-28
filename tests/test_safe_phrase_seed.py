"""TDD: 内置默认安全话术种子 — builtin=1，不可删；用户话术仍可删。"""
import os
import tempfile

from jl import db


def _c():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)  # init_db now seeds defaults
    return c, p


def test_init_seeds_builtin_defaults():
    c, p = _c()
    try:
        ps = db.get_safe_phrases(c)
        assert len(ps) >= 6
        assert any(x["pattern"] == "收到，马上处理" and x["builtin"] == 1 for x in ps)
    finally:
        c.close()
        os.unlink(p)


def test_seed_idempotent():
    c, p = _c()
    try:
        before = len(db.get_safe_phrases(c))
        added = db.seed_default_safe_phrases(c)  # re-seed
        assert added == 0 and len(db.get_safe_phrases(c)) == before
    finally:
        c.close()
        os.unlink(p)


def test_builtin_not_deletable_user_deletable():
    c, p = _c()
    try:
        builtin_id = [x["id"] for x in db.get_safe_phrases(c) if x["builtin"] == 1][0]
        assert db.delete_safe_phrase(c, builtin_id) is False           # 内置拒删
        uid = db.add_safe_phrase(c, "我自己加的", kind="测试")
        assert db.delete_safe_phrase(c, uid) is True                   # 用户的可删
    finally:
        c.close()
        os.unlink(p)
