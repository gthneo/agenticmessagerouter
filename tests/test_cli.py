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
