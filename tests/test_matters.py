"""事(matters) data layer — M:N persons/conversations + commitments (synthetic only)."""
from jl import db


def _seed():
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_person(c, id="u1", name="张三", category="biz", threshold_days=7, aliases=[])
    db.upsert_person(c, id="u2", name="李四", category="biz", threshold_days=7, aliases=[])
    db.upsert_account(c, account_id=1, platform="wechat", self_id="s")
    cid = db.upsert_conversation(c, account_id=1, platform="wechat", chat_id="w1", name="张三")
    db.link_person(c, cid, "u1")
    return c, cid


def test_create_matter_links_persons_and_conversations():
    conn, cid = _seed()
    db.create_matter(conn, title="周四饭局", kind="跟进",
                     person_ids=["u1", "u2"], conversation_ids=[cid])
    got = db.get_matters(conn)[0]
    assert got["title"] == "周四饭局" and got["status"] == "open"
    assert sorted(got["person_ids"]) == ["u1", "u2"]   # cross-person matter
    assert got["conversation_ids"] == [cid]


def test_get_matters_filters_by_person_and_conversation():
    conn, cid = _seed()
    db.create_matter(conn, title="A", person_ids=["u1"], conversation_ids=[cid])
    db.create_matter(conn, title="B", person_ids=["u2"])
    assert [m["title"] for m in db.get_matters(conn, person_id="u1")] == ["A"]
    assert [m["title"] for m in db.get_matters(conn, person_id="u2")] == ["B"]
    assert [m["title"] for m in db.get_matters(conn, conversation_id=cid)] == ["A"]


def test_set_matter_status_and_filter():
    conn, _ = _seed()
    mid = db.create_matter(conn, title="A", person_ids=["u1"])
    db.set_matter_status(conn, mid, "handled")
    assert db.get_matters(conn, status="open") == []
    assert [m["title"] for m in db.get_matters(conn, status="handled")] == ["A"]


def test_commitments_tracked_per_matter():
    conn, _ = _seed()
    mid = db.create_matter(conn, title="还键盘", person_ids=["u1"])
    c1 = db.add_commitment(conn, mid, "周五前还", due="2026-06-20")
    db.add_commitment(conn, mid, "不再撒谎")
    db.set_commitment_status(conn, c1, "kept")
    cs = db.get_matters(conn)[0]["commitments"]
    assert len(cs) == 2
    assert next(c for c in cs if c["id"] == c1)["status"] == "kept"


def test_set_matter_diagnosis_round_trips():
    conn, _ = _seed()
    mid = db.create_matter(conn, title="A", person_ids=["u1"])
    db.set_matter_diagnosis(conn, mid, {"圆": "缺", "对方姿态": "牙", "一句话诊断": "没接情绪"})
    d = db.get_matters(conn)[0]["diagnosis"]
    assert d["圆"] == "缺" and d["对方姿态"] == "牙"
