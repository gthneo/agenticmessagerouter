import os, tempfile
from jl import db, digest


def _seed():
    """临时 db + 最小合成数据(无 PII)。"""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    db.create_matter(conn, title="向张三回款", kind="落地", person_ids=[], conversation_ids=[])
    mid2 = db.create_matter(conn, title="给李四报价", kind="落地", person_ids=[], conversation_ids=[])
    db.set_matter_status(conn, mid2, "完结")
    conn.commit()
    return conn, path


def test_build_has_five_reports_and_gate():
    conn, path = _seed()
    try:
        d = digest.build(conn)
        assert set(d["reports"]) >= {"sales", "marketing", "relationship", "progress", "meta"}
        assert isinstance(d["gate"], list)
        assert d["reports"]["sales"]["counts"]["total"] == 2
        assert d["reports"]["marketing"]["pending_backend"] is True
        assert "narrative" in d["reports"]["sales"]
    finally:
        conn.close(); os.unlink(path)


def test_relationship_report_reuses_proactive_shape():
    conn, path = _seed()
    try:
        d = digest.build(conn)
        rel = d["reports"]["relationship"]
        assert set(rel["counts"]) >= {"red", "amber", "green"}
        assert isinstance(rel["nudge"], list)
    finally:
        conn.close(); os.unlink(path)
