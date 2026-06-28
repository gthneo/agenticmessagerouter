"""TDD: per-account onboarding surface (jl account ls/add/set).

Pure helpers (cred_path_for / plan builders) + the token-file writer (copy+chmod,
tested via tmp files) + the add/set command handlers' HITL dry-run→commit gate.

Synthetic fixtures only: wxid_test_*, placeholder hosts, token "TESTTOKEN123".
NEVER real wxid/token/PII (public repo). See CLAUDE.md.
"""
import os
import stat
import tempfile

import pytest

from jl import db, cli, onboard


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


# ----- cred_path_for (pure) -------------------------------------------------

def test_cred_path_for_uses_standard_convention():
    p = onboard.cred_path_for("fullwechat", 4)
    assert p == os.path.expanduser("~/.config/jl/cred/fullwechat_4.token")


def test_cred_path_for_distinguishes_tool_and_id():
    assert onboard.cred_path_for("powerdata", 2).endswith("/cred/powerdata_2.token")
    assert onboard.cred_path_for("fullwechat", 2) != onboard.cred_path_for("powerdata", 2)


# ----- write_token (copy + chmod 600, tested via tmp) -----------------------

def test_write_token_copies_and_chmods_600(tmp_path):
    src = tmp_path / "src.token"
    src.write_text("TESTTOKEN123")
    dest = tmp_path / "cred" / "fullwechat_9.token"   # parent does not exist yet
    out = onboard.write_token(str(src), str(dest))
    assert out == str(dest)
    assert dest.read_text() == "TESTTOKEN123"          # bytes moved file->file
    mode = stat.S_IMODE(os.stat(dest).st_mode)
    assert mode == 0o600                                # owner-only


def test_write_token_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        onboard.write_token(str(tmp_path / "nope"), str(tmp_path / "out"))


# ----- plan builder (pure) — drives the dry-run summary ---------------------

def test_build_add_plan_allocates_and_sets_cred(conn):
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_test_a")
    plan = onboard.build_plan(conn, op="add", flags={
        "platform": "wechat", "tool": "fullwechat",
        "host": "http://10.0.0.28:6174", "self_id": "wxid_test_ren",
        "label": "ren", "token_file": "/tmp/tok",
    })
    assert plan["account_id"] == 2                      # next free
    assert plan["op"] == "add"
    assert plan["before"] is None                       # brand new
    after = plan["after"]
    assert after["tool"] == "fullwechat"
    assert after["host"] == "http://10.0.0.28:6174"
    assert after["cred_ref"] == onboard.cred_path_for("fullwechat", 2)
    assert plan["copy_token"] is True


def test_build_set_plan_overwrites_only_given_flags(conn):
    # account 4 starts as powerdata; repoint to fullwechat @ .28 (the 仁兄 case)
    db.upsert_account(conn, account_id=4, platform="wechat", self_id="wxid_test_ren",
                      label="仁兄号", tool="powerdata", host="http://old:6174")
    plan = onboard.build_plan(conn, op="set", account_id=4, flags={
        "tool": "fullwechat", "host": "http://10.0.0.28:6174",
        "token_file": "/tmp/tok",
    })
    assert plan["account_id"] == 4
    assert plan["before"]["tool"] == "powerdata"
    after = plan["after"]
    assert after["tool"] == "fullwechat"                # changed
    assert after["host"] == "http://10.0.0.28:6174"     # changed
    assert after["self_id"] == "wxid_test_ren"          # untouched (not given)
    assert after["label"] == "仁兄号"                    # untouched
    assert after["cred_ref"] == onboard.cred_path_for("fullwechat", 4)
    assert plan["copy_token"] is True


def test_build_set_plan_no_token_no_copy(conn):
    db.upsert_account(conn, account_id=4, platform="wechat", self_id="wxid_test_ren",
                      tool="fullwechat", host="http://h:6174",
                      cred_ref="/existing/cred")
    plan = onboard.build_plan(conn, op="set", account_id=4, flags={"label": "新名"})
    assert plan["after"]["label"] == "新名"
    assert plan["after"]["cred_ref"] == "/existing/cred"  # kept (no token given)
    assert plan["copy_token"] is False


def test_build_set_plan_unknown_account_raises(conn):
    with pytest.raises(ValueError):
        onboard.build_plan(conn, op="set", account_id=99, flags={"label": "x"})


# ----- apply_plan writes account row + copies token -------------------------

def test_apply_plan_add_writes_account_and_token(conn, tmp_path):
    src = tmp_path / "tok"
    src.write_text("TESTTOKEN123")
    dest = tmp_path / "cred" / "fullwechat_2.token"
    plan = {
        "op": "add", "account_id": 2, "before": None, "copy_token": True,
        "token_file": str(src), "cred_dest": str(dest),
        "after": {"platform": "wechat", "tool": "fullwechat",
                  "host": "http://h:6174", "self_id": "wxid_test_ren",
                  "label": "ren", "cred_ref": str(dest)},
    }
    onboard.apply_plan(conn, plan)
    a = {x["account_id"]: x for x in db.get_accounts(conn)}[2]
    assert a["tool"] == "fullwechat"
    assert a["cred_ref"] == str(dest)
    assert dest.read_text() == "TESTTOKEN123"


# ----- route() wiring -------------------------------------------------------

def test_route_account_ls():
    cmd, params = cli.route(["account", "ls"])
    assert cmd == "account"
    assert params["sub"] == "ls"


def test_route_account_bare_defaults_to_ls():
    cmd, params = cli.route(["account"])
    assert (cmd, params["sub"]) == ("account", "ls")


def test_route_account_add_collects_flags():
    cmd, params = cli.route([
        "account", "add", "--platform", "wechat", "--tool", "fullwechat",
        "--host", "http://h:6174", "--self-id", "wxid_test_x",
        "--label", "x", "--token-file", "/tmp/t", "--commit",
    ])
    assert cmd == "account"
    assert params["sub"] == "add"
    assert params["commit"] is True
    f = params["flags"]
    assert f["platform"] == "wechat"
    assert f["tool"] == "fullwechat"
    assert f["host"] == "http://h:6174"
    assert f["self_id"] == "wxid_test_x"
    assert f["token_file"] == "/tmp/t"


def test_route_account_set_takes_id():
    cmd, params = cli.route(["account", "set", "4", "--tool", "fullwechat"])
    assert cmd == "account"
    assert params["sub"] == "set"
    assert params["account_id"] == 4
    assert params["commit"] is False           # dry-run by default
    assert params["flags"]["tool"] == "fullwechat"
