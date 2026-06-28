import os, tempfile
from jl import db, web


def test_api_digest_shape():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = db.connect(path)
    db.init_db(conn)
    try:
        out = web.api_digest(conn)
        assert "reports" in out and "gate" in out
        assert "sales" in out["reports"]
    finally:
        conn.close(); os.unlink(path)
