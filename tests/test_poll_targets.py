"""TDD: _fullwechat_targets(conn) — per-account fullwechat backend routing.

Each account with tool=fullwechat gets its own (account_id, url, token) tuple.
url = account.host or default; token = cred_ref file content or default.
"""
import os
import tempfile

from jl import db, cli


def _seed():
    fd, p = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    c = db.connect(p)
    db.init_db(c)
    return c, p


def test_targets_default_when_host_empty():
    c, p = _seed()
    try:
        db.upsert_account(c, account_id=1, platform="wechat", label="codebandi",
                          self_id="wxid_acct1", tool="fullwechat", host="")
        t = cli._fullwechat_targets(c)
        assert any(aid == 1 for aid, url, tok in t)
        url1 = [u for a, u, _ in t if a == 1][0]
        assert url1  # non-empty (default)
    finally:
        c.close()
        os.unlink(p)


def test_targets_per_account_host():
    c, p = _seed()
    try:
        db.upsert_account(c, account_id=1, platform="wechat", label="codebandi",
                          self_id="wxid_acct1", tool="fullwechat", host="")
        db.upsert_account(c, account_id=4, platform="wechat", label="ren",
                          self_id="wxid_acct4",
                          tool="fullwechat", host="http://192.168.31.28:6174")
        db.upsert_account(c, account_id=2, platform="phone", label="phone",
                          self_id="+8613000000000", tool="")  # not fullwechat
        t = cli._fullwechat_targets(c)
        ids = sorted(a for a, _, _ in t)
        assert ids == [1, 4]                        # only fullwechat accounts
        url4 = [u for a, u, _ in t if a == 4][0]
        assert url4 == "http://192.168.31.28:6174"  # per-account host honored
    finally:
        c.close()
        os.unlink(p)


def test_targets_cred_ref_token(tmp_path):
    c, p = _seed()
    try:
        tf = tmp_path / "tok"
        tf.write_text("TESTTOKEN28")
        db.upsert_account(c, account_id=4, platform="wechat", label="ren",
                          self_id="wxid_acct4",
                          tool="fullwechat", host="http://192.168.31.28:6174",
                          cred_ref=str(tf))
        t = cli._fullwechat_targets(c)
        tok4 = [tok for a, _, tok in t if a == 4][0]
        assert tok4 == "TESTTOKEN28"               # token read from cred_ref file
    finally:
        c.close()
        os.unlink(p)
