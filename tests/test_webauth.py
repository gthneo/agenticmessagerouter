"""Username/password gate — pure, over a temp auth file (no socket, no network)."""
import json
import os

from jl import webauth


def test_set_then_verify_roundtrip(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    assert webauth.verify("wang", "secret123", path=p) is True


def test_wrong_password_rejected(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    assert webauth.verify("wang", "nope", path=p) is False


def test_wrong_user_rejected(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    assert webauth.verify("eve", "secret123", path=p) is False


def test_unconfigured_returns_false_and_not_configured(tmp_path):
    p = str(tmp_path / "missing.json")
    assert webauth.is_configured(path=p) is False
    assert webauth.verify("wang", "secret123", path=p) is False


def test_is_configured_true_after_set(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    assert webauth.is_configured(path=p) is True


def test_no_plaintext_password_on_disk(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    raw = open(p, encoding="utf-8").read()
    assert "secret123" not in raw          # only the hash is stored
    d = json.loads(raw)
    assert set(d) >= {"user", "salt", "hash"}
    assert d["hash"] != "secret123"


def test_file_is_0600(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    assert (os.stat(p).st_mode & 0o777) == 0o600


def test_short_password_rejected(tmp_path):
    p = str(tmp_path / "web_auth.json")
    try:
        webauth.set_auth("wang", "12345", path=p)
        assert False, "should have raised"
    except ValueError:
        pass


def test_rotating_password_changes_salt(tmp_path):
    p = str(tmp_path / "web_auth.json")
    webauth.set_auth("wang", "secret123", path=p)
    s1 = json.loads(open(p).read())["salt"]
    webauth.set_auth("wang", "secret123", path=p)
    s2 = json.loads(open(p).read())["salt"]
    assert s1 != s2                         # fresh salt each write
