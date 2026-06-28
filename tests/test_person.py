"""TDD: productized `jl person refresh-name`.

Person display names go stale: persons.name is set at seed/link time and does
NOT auto-update, but the live conversation name IS kept fresh by ingest
(upsert_conversation does name=excluded.name). This refreshes a person's name
to its primary/linked conversation's roster-fresh name, behind the same HITL
dry-run→--commit gate as `jl account`.

Synthetic fixtures only: 张三/李四/王五, wxid_test_*. NEVER real PII (public repo).
"""
import pytest

from jl import db, cli, person


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    db.upsert_account(c, account_id=1, platform="wechat", self_id="wxid_test_self")
    yield c
    c.close()


def _linked_conv(conn, *, person_id, chat_id, conv_name, account_id=1,
                 platform="wechat"):
    """Create a conversation with a (possibly diverging) live name and link it."""
    cid = db.upsert_conversation(conn, account_id=account_id, platform=platform,
                                 chat_id=chat_id, name=conv_name)
    db.link_person(conn, cid, person_id)
    return cid


# ----- name_refresh_plan (pure) — drives the dry-run summary ----------------

def test_plan_detects_stale_name(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    plan = person.name_refresh_plan(conn)
    assert plan == [{"person_id": "zhangsan", "old": "张三", "new": "张三新名"}]


def test_plan_skips_when_name_already_fresh(conn):
    db.upsert_person(conn, id="lisi", name="李四", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="lisi", chat_id="wxid_test_ls", conv_name="李四")
    assert person.name_refresh_plan(conn) == []


def test_plan_skips_empty_conv_name(conn):
    db.upsert_person(conn, id="wangwu", name="王五", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="wangwu", chat_id="wxid_test_ww", conv_name="")
    assert person.name_refresh_plan(conn) == []


def test_plan_scans_all_persons(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    db.upsert_person(conn, id="lisi", name="李四", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    _linked_conv(conn, person_id="lisi", chat_id="wxid_test_ls", conv_name="李四")  # fresh
    plan = person.name_refresh_plan(conn)
    ids = {d["person_id"] for d in plan}
    assert ids == {"zhangsan"}


def test_plan_uses_most_recent_conversation(conn):
    """When a person has many linked conversations, the most-recently-active one is
    authoritative (roster-fresh)."""
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    old = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_old", name="张三旧会话")
    new = db.upsert_conversation(conn, account_id=1, platform="wechat",
                                 chat_id="wxid_test_new", name="张三新名")
    db.link_person(conn, old, "zhangsan")
    db.link_person(conn, new, "zhangsan")
    # bump the "new" conversation's activity so it sorts first
    conn.execute("UPDATE conversations SET last_activity_at=9999999999 WHERE id=?", (new,))
    conn.commit()
    plan = person.name_refresh_plan(conn)
    assert plan == [{"person_id": "zhangsan", "old": "张三", "new": "张三新名"}]


def test_plan_single_person_arg(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    db.upsert_person(conn, id="lisi", name="李四", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    _linked_conv(conn, person_id="lisi", chat_id="wxid_test_ls", conv_name="李四改")
    plan = person.name_refresh_plan(conn, person_id="zhangsan")
    assert plan == [{"person_id": "zhangsan", "old": "张三", "new": "张三新名"}]


def test_plan_single_person_fresh_is_empty(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三")
    assert person.name_refresh_plan(conn, person_id="zhangsan") == []


def test_plan_person_no_conversation_is_empty(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    assert person.name_refresh_plan(conn, person_id="zhangsan") == []


# ----- apply_name_refresh (side-effecting) ----------------------------------

def test_apply_updates_name(conn):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    person.apply_name_refresh(conn, [{"person_id": "zhangsan", "old": "张三", "new": "张三新名"}])
    assert db.get_person(conn, "zhangsan")["name"] == "张三新名"


# ----- route() wiring -------------------------------------------------------

def test_route_person_bare_defaults_to_refresh_name_scan():
    cmd, params = cli.route(["person", "refresh-name"])
    assert cmd == "person"
    assert params["sub"] == "refresh-name"
    assert params["target"] is None
    assert params["commit"] is False


def test_route_person_refresh_name_with_target():
    cmd, params = cli.route(["person", "refresh-name", "zhangsan"])
    assert cmd == "person"
    assert params["sub"] == "refresh-name"
    assert params["target"] == "zhangsan"


def test_route_person_commit_and_yes_alias():
    _, p1 = cli.route(["person", "refresh-name", "--commit"])
    _, p2 = cli.route(["person", "refresh-name", "--yes"])
    assert p1["commit"] is True
    assert p2["commit"] is True


# ----- cmd_person HITL gate -------------------------------------------------

def test_cmd_person_dry_run_does_not_write(conn, capsys):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    cli.cmd_person(conn, {"sub": "refresh-name", "target": None, "commit": False})
    out = capsys.readouterr().out
    assert "张三" in out and "张三新名" in out
    assert "dry-run" in out.lower() or "确认" in out
    # nothing written
    assert db.get_person(conn, "zhangsan")["name"] == "张三"
    assert db.get_events(conn) == []


def test_cmd_person_commit_writes_and_audits(conn, capsys):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    cli.cmd_person(conn, {"sub": "refresh-name", "target": None, "commit": True})
    assert db.get_person(conn, "zhangsan")["name"] == "张三新名"
    events = [e for e in db.get_events(conn) if e["kind"] == "name_refresh"]
    assert len(events) == 1
    assert events[0]["person_id"] == "zhangsan"
    assert events[0]["detail"] == {"from": "张三", "to": "张三新名"}


def test_cmd_person_single_target(conn, capsys):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    db.upsert_person(conn, id="lisi", name="李四", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    _linked_conv(conn, person_id="lisi", chat_id="wxid_test_ls", conv_name="李四改")
    cli.cmd_person(conn, {"sub": "refresh-name", "target": "zhangsan", "commit": True})
    assert db.get_person(conn, "zhangsan")["name"] == "张三新名"
    assert db.get_person(conn, "lisi")["name"] == "李四"  # untouched


def test_cmd_person_resolve_by_display_name(conn, capsys):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三新名")
    cli.cmd_person(conn, {"sub": "refresh-name", "target": "张三", "commit": True})
    assert db.get_person(conn, "zhangsan")["name"] == "张三新名"


def test_cmd_person_unknown_target_errors(conn, capsys):
    cli.cmd_person(conn, {"sub": "refresh-name", "target": "nobody", "commit": True})
    out = capsys.readouterr().out
    assert "找不到" in out or "❌" in out


def test_cmd_person_nothing_to_refresh(conn, capsys):
    db.upsert_person(conn, id="zhangsan", name="张三", category="biz",
                     threshold_days=7, aliases=[])
    _linked_conv(conn, person_id="zhangsan", chat_id="wxid_test_zs", conv_name="张三")
    cli.cmd_person(conn, {"sub": "refresh-name", "target": None, "commit": True})
    out = capsys.readouterr().out
    assert "无" in out or "没有" in out or "一致" in out
