"""CLI routing (pure) + command wiring (messages-derived path, real in-memory db)."""
import pytest

from jl import cli, db, ingest


def test_route_no_args_is_sweep():
    assert cli.route([]) == ("sweep", {})


def test_route_migrate_flag():
    assert cli.route(["--migrate"]) == ("migrate", {})


def test_route_dump_yaml_flag():
    assert cli.route(["--dump-yaml"]) == ("dump_yaml", {})


def test_route_tokens_flag():
    assert cli.route(["--tokens"]) == ("tokens", {})


def test_route_quebu_keyword():
    assert cli.route(["救补"]) == ("quebu", {})
    assert cli.route(["--missing"]) == ("quebu", {})


def test_route_name_is_detail():
    assert cli.route(["张三"]) == ("detail", {"name": "张三"})


def test_route_proactive():
    assert cli.route(["主动"]) == ("proactive", {"name": None})
    assert cli.route(["proactive"]) == ("proactive", {"name": None})
    assert cli.route(["主动", "张三"]) == ("proactive", {"name": "张三"})


def test_route_watch_toggle():
    assert cli.route(["关注", "张三"]) == ("watch", {"name": "张三", "on": True})
    assert cli.route(["关注", "--off", "张三"]) == ("watch", {"name": "张三", "on": False})


def test_route_connect_channel():
    assert cli.route(["连", "张三", "adambb_joy"]) == ("connect", {"name": "张三", "chat_id": "adambb_joy"})
    assert cli.route(["连渠道", "张三", "m123"]) == ("connect", {"name": "张三", "chat_id": "m123"})


# ----- command wiring (messages-derived path, real in-memory db) ------------

@pytest.fixture
def seeded():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz",
                     threshold_days=3, aliases=["老张"])
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三", type="private")
    db.link_person(conn, cid, "u1")
    db.insert_messages(conn, cid, [
        ingest.MsgRecord(msg_key="w:1", ts=1_000_000, content="hi", sender="张三")])
    yield conn
    conn.close()


def test_sweep_reads_derived_interactions(seeded, capsys):
    cli.cmd_sweep(seeded, {})
    out = capsys.readouterr().out
    assert "张三" in out
    assert "sweep" in [e["kind"] for e in db.get_events(seeded)]


def test_detail_reads_derived_and_audits(seeded, capsys):
    cli.cmd_detail(seeded, {}, "老张")          # resolve by alias
    out = capsys.readouterr().out
    assert "张三" in out
    assert "wechat" in out                       # derived platform shown
    assert "detail" in [e["kind"] for e in db.get_events(seeded)]


def test_event_actor_comes_from_env(seeded, monkeypatch):
    monkeypatch.setenv("JL_ACTOR", "user1")
    cli.cmd_sweep(seeded, {})
    sweep_evt = [e for e in db.get_events(seeded) if e["kind"] == "sweep"][0]
    assert sweep_evt["actor"] == "user1"


def test_route_reset():
    assert cli.route(["reset"]) == ("reset", {"confirm": False, "platform": None,
                                              "include_accounts": False})


def test_route_reset_confirm_all():
    cmd, params = cli.route(["reset", "--confirm", "--all"])
    assert cmd == "reset"
    assert params["confirm"] is True
    assert params["include_accounts"] is True


def test_cmd_reset_dry_run_does_not_delete(capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三")
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    cli.cmd_reset(conn, {"confirm": False, "platform": None, "include_accounts": False})
    out = capsys.readouterr().out
    assert "dry-run" in out.lower() or "确认" in out
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 1
    conn.close()


def test_cmd_reset_confirm_wipes_and_audits(capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    cid = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="c1", name="张三")
    db.insert_messages(conn, cid, [ingest.MsgRecord(msg_key="x:1", ts=1, content="hi")])
    cli.cmd_reset(conn, {"confirm": True, "platform": None, "include_accounts": False})
    assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 0
    assert "reset" in [e["kind"] for e in db.get_events(conn)]
    conn.close()


def test_cmd_reset_scope_label_includes_accounts_with_channel(capsys):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_account(conn, account_id=1, platform="wechat", self_id="wxid_self_1")
    db.upsert_conversation(conn, account_id=1, platform="wechat", chat_id="c1", name="张三")
    cli.cmd_reset(conn, {"confirm": False, "platform": "wechat", "include_accounts": True})
    out = capsys.readouterr().out
    assert "wechat + accounts" in out              # scope label shows full blast radius
    conn.close()


def test_opt_value_skips_following_flag():
    assert cli._opt_value(["reset", "--channel", "--confirm"], "--channel") is None
    assert cli._opt_value(["reset", "--channel", "wechat"], "--channel") == "wechat"


def test_route_ignite():
    assert cli.route(["ignite"]) == ("ignite", {"channel": "wechat"})


def test_route_ignite_default_channel():
    assert cli.route(["ignite"]) == ("ignite", {"channel": "wechat"})


def test_route_ignite_lark():
    assert cli.route(["ignite", "lark"]) == ("ignite", {"channel": "lark"})


def test_route_poll():
    cmd, params = cli.route(["poll"])
    assert cmd == "poll"
    assert params["interval"] == 300


def test_route_poll_custom_interval():
    cmd, params = cli.route(["poll", "--interval", "60"])
    assert params["interval"] == 60


def test_route_web():
    cmd, params = cli.route(["web"])
    assert cmd == "web"
    assert params["port"] == 8088
    assert params["host"] == "0.0.0.0"


def test_route_web_custom_port():
    _, params = cli.route(["web", "--port", "9000"])
    assert params["port"] == 9000


def test_route_push():
    cmd, params = cli.route(["push", "phone", "--remote", "http://x:8088", "--token", "t"])
    assert cmd == "push"
    assert params["channel"] == "phone"
    assert params["remote"] == "http://x:8088"
    assert params["token"] == "t"


def test_route_push_defaults_channel_phone():
    cmd, params = cli.route(["push"])
    assert cmd == "push" and params["channel"] == "phone"


def test_route_link():
    assert cli.route(["link"]) == ("link", {})


def test_route_draft_assist():
    assert cli.route(["draft-assist", "5"]) == ("draft_assist", {"conversation_id": 5})


def test_route_draft_assist_missing_id():
    assert cli.route(["draft-assist"]) == ("draft_assist", {"conversation_id": None})


def test_account_ls_shows_probed_backend_version_and_schema(capsys):
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", tool="fullwechat",
                      self_id="wxid_test_self", host="http://backend.test:6174",
                      label="测试号")

    def fake_probe(host, token):
        return {"version": "0.12.0", "schema": "message.canonical/1"}

    cli._account_ls(c, probe=fake_probe)
    out = capsys.readouterr().out
    assert "0.12.0" in out
    assert "message.canonical/1" in out
    assert "测试号" in out


def test_account_ls_unreachable_backend_does_not_crash(capsys):
    c = db.connect(":memory:"); db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", tool="fullwechat",
                      self_id="wxid_test_self", host="http://down.test:6174",
                      label="掉线号")

    def boom(host, token):
        raise OSError("refused")

    cli._account_ls(c, probe=boom)  # must not raise
    out = capsys.readouterr().out
    assert "unreachable" in out
