"""TDD: config-driven backend onboarding — `jl account onboard`.

A registry JSON (one entry per fullwechat backend) drives onboarding: for each
enabled entry we run an *identity preflight* (GET /api/status/auth, compare the
backend's loggedInUser base wxid to the configured self_id) BEFORE building a
dry-run plan — the second gate that stops the wrong-account-binding bug (a backend
GUI showing account A while the REST API still reports account B). Default is
dry-run; --commit applies via the existing onboard.apply_plan machinery.

Synthetic fixtures ONLY: wxid_test_*, http://HOST:6174, token "TESTTOKEN123".
NEVER real wxid/IP/token (public repo). See CLAUDE.md.
"""
import json
import os

import pytest

from jl import db, cli, onboard


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def _entry(**over):
    e = {
        "label": "测试后端",
        "amr_account_slot": 4,
        "tool": "fullwechat",
        "host": "http://HOST:6174",
        "token_file": "",          # filled per-test with a real tmp token path
        "self_id": "wxid_test_abc",
        "instance_name": "Test-instance",
        "customer": "测试客户",
        "enabled": True,
    }
    e.update(over)
    return e


def _token_file(tmp_path, name="token", body="TESTTOKEN123"):
    p = tmp_path / name
    p.write_text(body)
    return str(p)


# ----- registry loader ------------------------------------------------------

def test_load_registry_parses_entries(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"backends": [_entry(), _entry(amr_account_slot=5)]}))
    backends = onboard.load_registry(str(reg))
    assert len(backends) == 2
    assert backends[0]["amr_account_slot"] == 4


def test_load_registry_accepts_bare_list(tmp_path):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps([_entry()]))           # a bare top-level list also works
    assert onboard.load_registry(str(reg))[0]["self_id"] == "wxid_test_abc"


def test_load_registry_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        onboard.load_registry(str(tmp_path / "nope.json"))


# ----- identity preflight (the safety gate) ---------------------------------

def test_preflight_match_base_equals_self_id(tmp_path):
    # backend reports a device-suffixed wxid; its base must equal self_id
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {"loggedInUser": "wxid_test_abc_d12"})
    assert pre["ok"] is True
    assert pre["logged_in"] == "wxid_test_abc_d12"
    assert pre["base"] == "wxid_test_abc"


def test_preflight_mismatch_is_not_ok(tmp_path):
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {"loggedInUser": "wxid_other_x9"})
    assert pre["ok"] is False
    assert pre["reason"] == "mismatch"
    assert pre["logged_in"] == "wxid_other_x9"


# ----- login-state gate (防绑登出/死号) — identity right but session not live ----

def test_preflight_logged_out_blocks_even_if_identity_matches(tmp_path):
    # .28 real case (2026-07-01): identity correct (wangliren123_2325) but logged out.
    # Binding it would create a dead account that ingests nothing → must NOT be ok.
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {
        "loggedInUser": "wxid_test_abc_d12", "status": "logged_out",
        "signals": {"chatsListPresent": False, "view": "login_account"}})
    assert pre["ok"] is False
    assert pre["reason"] == "logged_out"
    assert pre["logged_in"] == "wxid_test_abc_d12"   # identity carried for the audit trail


def test_preflight_logged_in_status_is_ok(tmp_path):
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {
        "loggedInUser": "wxid_test_abc_d12", "status": "logged_in",
        "signals": {"chatsListPresent": True}})
    assert pre["ok"] is True and pre["reason"] is None


def test_preflight_no_chatlist_blocks_when_no_status(tmp_path):
    # backend omits a top-level status but signals show the chat list is absent → not live
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {
        "loggedInUser": "wxid_test_abc_d12", "signals": {"chatsListPresent": False}})
    assert pre["ok"] is False and pre["reason"] == "logged_out"


def test_preflight_lenient_when_no_liveness_signal(tmp_path):
    # legacy backends report only loggedInUser (no status, no signals) → don't block
    # on liveness (back-compat with the original preflight contract).
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {"loggedInUser": "wxid_test_abc_d12"})
    assert pre["ok"] is True and pre["reason"] is None


def test_preflight_mismatch_takes_precedence_over_liveness(tmp_path):
    # wrong account AND logged out → the identity mismatch is the headline (more dangerous)
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {
        "loggedInUser": "wxid_other_x9", "status": "logged_out"})
    assert pre["ok"] is False and pre["reason"] == "mismatch"


def test_preflight_unreachable_is_graceful(tmp_path):
    entry = _entry(token_file=_token_file(tmp_path))

    def boom(*a, **k):
        raise OSError("connection refused")

    pre = onboard.preflight_identity(entry, fetch=boom)
    assert pre["ok"] is False
    assert pre["reason"] == "unreachable"        # never raises


def test_preflight_missing_token_file_is_graceful(tmp_path):
    entry = _entry(token_file=str(tmp_path / "no-such-token"))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {"loggedInUser": "wxid_test_abc"})
    assert pre["ok"] is False
    assert pre["reason"] == "no_token"


def test_preflight_bad_response_is_graceful(tmp_path):
    entry = _entry(token_file=_token_file(tmp_path))
    pre = onboard.preflight_identity(entry, fetch=lambda *a, **k: {"unexpected": "shape"})
    assert pre["ok"] is False
    assert pre["reason"] == "no_user"


# ----- onboard_entry: preflight -> plan, gated --------------------------------

def test_onboard_entry_match_builds_plan(conn, tmp_path):
    tok = _token_file(tmp_path)
    entry = _entry(self_id="wxid_test_abc", host="http://HOST:6174", token_file=tok)
    res = onboard.onboard_entry(
        conn, entry,
        fetch=lambda host, path, token, **k: (
            {"loggedInUser": "wxid_test_abc_d12"} if path.endswith("auth")
            else {"schema": "canonical", "kinds": ["text", "image"]}
        ),
    )
    assert res["preflight"]["ok"] is True
    plan = res["plan"]
    assert plan is not None
    assert plan["account_id"] == 4
    assert plan["after"]["tool"] == "fullwechat"
    assert plan["after"]["host"] == "http://HOST:6174"
    assert plan["after"]["self_id"] == "wxid_test_abc"
    assert plan["copy_token"] is True
    assert plan["token_file"] == tok               # token copied from the backend's local file


def test_onboard_entry_mismatch_skips_no_plan(conn, tmp_path):
    entry = _entry(self_id="wxid_test_abc", token_file=_token_file(tmp_path))
    res = onboard.onboard_entry(
        conn, entry,
        fetch=lambda host, path, token, **k: {"loggedInUser": "wxid_wrong_one"},
    )
    assert res["preflight"]["ok"] is False
    assert res["plan"] is None                     # NEVER onboard on mismatch


def test_onboard_entry_unreachable_skips_gracefully(conn, tmp_path):
    entry = _entry(token_file=_token_file(tmp_path))

    def boom(*a, **k):
        raise OSError("refused")

    res = onboard.onboard_entry(conn, entry, fetch=boom)
    assert res["plan"] is None                     # one bad backend doesn't crash the run


# ----- the command: dry-run vs --commit, disabled, logging -------------------

def _write_registry(tmp_path, entries):
    reg = tmp_path / "reg.json"
    reg.write_text(json.dumps({"backends": entries}))
    return str(reg)


def test_cmd_onboard_disabled_entry_ignored(conn, tmp_path, capsys):
    reg = _write_registry(tmp_path, [_entry(enabled=False, token_file=_token_file(tmp_path))])
    cli.cmd_account_onboard(
        conn, {"registry": reg, "commit": False},
        fetch=lambda *a, **k: {"loggedInUser": "wxid_test_abc"},
    )
    assert db.get_accounts(conn) == []             # nothing written
    out = capsys.readouterr().out
    assert "停用" in out or "disabled" in out.lower() or "跳过" in out


def test_cmd_onboard_dry_run_writes_nothing(conn, tmp_path, capsys):
    reg = _write_registry(tmp_path, [_entry(token_file=_token_file(tmp_path))])
    cli.cmd_account_onboard(
        conn, {"registry": reg, "commit": False},
        fetch=lambda host, path, token, **k: (
            {"loggedInUser": "wxid_test_abc_d12"} if path.endswith("auth")
            else {"schema": "canonical"}
        ),
    )
    assert db.get_accounts(conn) == []             # dry-run: zero writes
    out = capsys.readouterr().out
    assert "dry-run" in out


def test_cmd_onboard_commit_upserts_and_logs(conn, tmp_path):
    tok = _token_file(tmp_path)
    reg = _write_registry(tmp_path, [_entry(amr_account_slot=4, token_file=tok)])
    cli.cmd_account_onboard(
        conn, {"registry": reg, "commit": True},
        fetch=lambda host, path, token, **k: (
            {"loggedInUser": "wxid_test_abc_d12"} if path.endswith("auth")
            else {"schema": "canonical", "kinds": ["text"]}
        ),
    )
    accts = {a["account_id"]: a for a in db.get_accounts(conn)}
    assert 4 in accts
    a = accts[4]
    assert a["tool"] == "fullwechat"
    assert a["self_id"] == "wxid_test_abc"
    # token landed at the standard cred path with its contents
    dest = os.path.expanduser(a["cred_ref"])
    assert os.path.exists(dest)
    with open(dest) as f:
        assert f.read() == "TESTTOKEN123"
    os.remove(dest)                                # clean up real cred file under ~/.config
    # an audit event was logged
    events = conn.execute(
        "SELECT kind FROM events WHERE kind='account_onboard'").fetchall()
    assert len(events) == 1


def test_cmd_onboard_mismatch_does_not_write_even_with_commit(conn, tmp_path):
    reg = _write_registry(tmp_path, [_entry(amr_account_slot=4, token_file=_token_file(tmp_path))])
    cli.cmd_account_onboard(
        conn, {"registry": reg, "commit": True},
        fetch=lambda host, path, token, **k: {"loggedInUser": "wxid_imposter_z9"},
    )
    assert db.get_accounts(conn) == []             # mismatch under --commit still writes nothing


# ----- route() wiring -------------------------------------------------------

def test_route_account_onboard():
    cmd, params = cli.route(["account", "onboard"])
    assert cmd == "account"
    assert params["sub"] == "onboard"
    assert params["commit"] is False


def test_route_account_onboard_registry_and_commit():
    cmd, params = cli.route(["account", "onboard", "--registry", "/tmp/r.json", "--commit"])
    assert params["sub"] == "onboard"
    assert params["registry"] == "/tmp/r.json"
    assert params["commit"] is True
