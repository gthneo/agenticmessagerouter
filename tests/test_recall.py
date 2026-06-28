"""TDD: 记忆层 recall — 显著性检索 (零 LLM, 只读)。写这里先跑 FAIL, 再实现 recall.py。"""
import os
import tempfile

from jl import db
from jl import recall


def _seed():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    db.upsert_person(c, id="zhangsan", name="张三", threshold_days=7)
    mid = db.create_matter(c, title="向张三回款", kind="跟进",
                           person_ids=["zhangsan"], conversation_ids=[])
    db.add_commitment(c, mid, "本周到账", due="", status="open")
    c.commit()
    return c, p


def test_recall_bundle_shape():
    c, p = _seed()
    try:
        b = recall.recall(c, "zhangsan", now=9_999_999_999)
        assert set(b) >= {"person", "recent", "open_matters",
                          "due_commitments", "temperature", "purpose"}
        assert b["person"]["handle"] == {"type": "person", "id": "zhangsan"}
        assert any(m["title"] == "向张三回款" for m in b["open_matters"])
        assert b["open_matters"][0]["handle"]["type"] == "matter"
        assert len(b["due_commitments"]) == 1
        assert "color" in b["temperature"]
    finally:
        c.close()
        os.unlink(p)


def test_expand_matter():
    c, p = _seed()
    try:
        b = recall.recall(c, "zhangsan", now=9_999_999_999)
        h = b["open_matters"][0]["handle"]
        full = recall.expand(c, h)
        assert full["type"] == "matter"
        assert len(full["commitments"]) == 1
    finally:
        c.close()
        os.unlink(p)


def test_recall_unknown_person_still_structural():
    c, p = _seed()
    try:
        b = recall.recall(c, "nobody", now=9_999_999_999)
        assert b["recent"] == [] and b["open_matters"] == []
        assert "temperature" in b
    finally:
        c.close()
        os.unlink(p)
