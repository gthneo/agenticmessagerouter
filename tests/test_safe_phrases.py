"""TDD: 话术库自灌 UI — safe_phrases CRUD API endpoints (双闸·闸一白名单)."""
import os
import tempfile

from jl import db, web


def _c():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def test_add_list_delete():
    c, p = _c()
    try:
        r = web.api_add_safe_phrase(c, {"pattern": "收到，马上处理", "kind": "确认"})
        assert r["ok"] and r["id"]
        lst = web.api_safe_phrases(c)
        assert any(x["pattern"] == "收到，马上处理" for x in lst)
        web.api_delete_safe_phrase(c, {"id": r["id"]})
        assert all(x["id"] != r["id"] for x in web.api_safe_phrases(c))
    finally:
        c.close()
        os.unlink(p)
