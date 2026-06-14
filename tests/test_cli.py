"""CLI routing (pure) + command wiring (stubbed adapters, real in-memory db)."""
import pytest

from jl import cli, db


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


# ----- command wiring (stubbed live adapters) -------------------------------

@pytest.fixture
def seeded(monkeypatch):
    conn = db.connect(":memory:")
    db.init_db(conn)
    db.upsert_person(conn, id="u1", name="张三", category="biz",
                     threshold_days=3, aliases=["老张"])
    db.upsert_channel(conn, person_id="u1", kind="wechat",
                      identifier="wxid_test_001", label="张三会话")
    # stub the live WeChat adapter so the test never touches the MCP
    monkeypatch.setitem(cli._ADAPTERS, "wechat",
                        lambda ch, ctx: (1_000_000, "2026-06-13 14:38 me: hi"))
    yield conn
    conn.close()


def test_sweep_persists_interaction_event_and_tokens(seeded, capsys):
    cli.cmd_sweep(seeded, {})
    out = capsys.readouterr().out
    assert "张三" in out
    chans = db.get_channels(seeded, "u1")
    assert db.latest_interaction(seeded, chans[0]["id"])["ts"] == 1_000_000
    assert "sweep" in [e["kind"] for e in db.get_events(seeded)]
    assert db.token_summary(seeded)["reach_count"] >= 1


def test_detail_writes_audit_trace(seeded, capsys):
    cli.cmd_detail(seeded, {}, "老张")          # resolve by alias
    out = capsys.readouterr().out
    assert "张三" in out
    assert "detail" in [e["kind"] for e in db.get_events(seeded)]


def test_event_actor_comes_from_env(seeded, monkeypatch):
    monkeypatch.setenv("JL_ACTOR", "user1")
    cli.cmd_sweep(seeded, {})
    sweep_evt = [e for e in db.get_events(seeded) if e["kind"] == "sweep"][0]
    assert sweep_evt["actor"] == "user1"
