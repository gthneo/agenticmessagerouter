"""UI-telemetry tests — PII-FREE-by-construction埋点 (db insert / endpoint
validation / uireview aggregation).

The HARD RULE is asserted here: a payload that smuggles a contact name / wxid /
chat_id / message content / arbitrary extra keys must be rejected or stripped so
that NOTHING PII reaches the ui_trace table. Synthetic data only (张三/wxid_test_*).
"""
import pytest

from jl import cli, db, web


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


# ---- schema / db.insert_ui_trace -------------------------------------------

def test_init_db_creates_ui_trace_table(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='ui_trace'"
    ).fetchall()
    assert rows, "ui_trace table should be created by init_db"


def test_ui_trace_has_no_content_columns(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ui_trace)").fetchall()}
    # Only the closed PII-free column set — no content/text/name/body/etc.
    assert cols == {"id", "session", "action", "ui", "n", "ts", "recorded_at"}


def test_insert_ui_trace_persists_allowed_fields(conn):
    n = db.insert_ui_trace(conn, "sess-abc", [
        {"action": "click", "ui": "send", "ts": 1000},
        {"action": "rage", "ui": "inbox-tab", "n": 4, "ts": 2000},
    ])
    assert n == 2
    rows = conn.execute("SELECT session, action, ui, n FROM ui_trace ORDER BY id").fetchall()
    assert rows[0]["session"] == "sess-abc"
    assert rows[0]["action"] == "click" and rows[0]["ui"] == "send"
    assert rows[1]["action"] == "rage" and rows[1]["n"] == 4


# ---- endpoint validation / PII-strip (the mandatory test) -------------------

def test_api_uitrace_rejects_disallowed_action(conn):
    res = web.api_uitrace(conn, {"session": "s", "events": [
        {"action": "keylog", "ui": "send", "ts": 1},   # not in allowlist
    ]})
    assert res["stored"] == 0
    assert conn.execute("SELECT COUNT(*) FROM ui_trace").fetchone()[0] == 0


def test_api_uitrace_pii_is_never_stored(conn):
    """HARD RULE: a payload carrying a fake name / wxid / chat_id / message content /
    input value / arbitrary extra keys must be stripped — nothing PII reaches the DB."""
    res = web.api_uitrace(conn, {
        "session": "sess-1",
        # smuggled top-level PII the server must ignore entirely:
        "wxid": "wxid_test_secret",
        "chat_id": "g-real-group",
        "events": [{
            "action": "click",
            "ui": "proactive-item",
            "ts": 1234,
            # smuggled per-event PII the server must strip:
            "name": "张三",
            "content": "带合同来下午三点",
            "input": "我的银行卡号 6222...",
            "innerText": "张三",
            "wxid": "wxid_test_abc",
            "chat_id": "m-private",
        }],
    })
    assert res["stored"] == 1
    # Dump EVERYTHING stored and assert none of the PII strings appear anywhere.
    dump = " ".join(
        str(v) for row in conn.execute("SELECT * FROM ui_trace").fetchall() for v in tuple(row)
    )
    for leak in ("张三", "带合同来", "银行卡", "wxid_test", "g-real-group", "m-private", "6222"):
        assert leak not in dump, f"PII leaked into ui_trace: {leak!r}"
    # the legitimate, whitelisted bits ARE stored:
    row = conn.execute("SELECT session, action, ui FROM ui_trace").fetchone()
    assert row["session"] == "sess-1" and row["action"] == "click" and row["ui"] == "proactive-item"


def test_api_uitrace_caps_ui_length_and_charset(conn):
    res = web.api_uitrace(conn, {"session": "s", "events": [
        {"action": "click", "ui": "x" * 200, "ts": 1},          # too long
        {"action": "click", "ui": "drop table; --", "ts": 2},   # illegal charset
        {"action": "skin", "ui": "inbox", "ts": 3},             # ok
    ]})
    assert res["stored"] == 1
    rows = [r["ui"] for r in conn.execute("SELECT ui FROM ui_trace").fetchall()]
    assert rows == ["inbox"]


def test_api_uitrace_caps_session_and_batch(conn):
    # giant session id is rejected (not a 40-char-ish random token)
    res = web.api_uitrace(conn, {"session": "s" * 500, "events": [
        {"action": "click", "ui": "send", "ts": 1},
    ]})
    assert res["stored"] == 0


def test_api_uitrace_config_reflects_setting(conn):
    assert web.api_uitrace_config(conn)["enabled"] is True   # default ON
    db.set_setting(conn, "ui_trace_enabled", "0")
    assert web.api_uitrace_config(conn)["enabled"] is False


def test_api_uitrace_disabled_drops_events(conn):
    db.set_setting(conn, "ui_trace_enabled", "0")
    res = web.api_uitrace(conn, {"session": "s", "events": [
        {"action": "click", "ui": "send", "ts": 1},
    ]})
    assert res["stored"] == 0
    assert conn.execute("SELECT COUNT(*) FROM ui_trace").fetchone()[0] == 0


# ---- uireview aggregation ---------------------------------------------------

def _seed_traces(conn):
    db.insert_ui_trace(conn, "s1", [
        {"action": "click", "ui": "send", "ts": 1},
        {"action": "click", "ui": "send", "ts": 2},
        {"action": "click", "ui": "inbox-tab", "ts": 3},
        {"action": "rage", "ui": "inbox-tab", "n": 4, "ts": 4},
        {"action": "rage", "ui": "show-groups", "n": 3, "ts": 5},
        {"action": "deadend", "ui": "search", "ts": 6},
        {"action": "skin", "ui": "inbox", "ts": 7},
        {"action": "click-unnamed", "ui": "div", "ts": 8},
        {"action": "click-unnamed", "ui": "span", "ts": 9},
    ])


def test_cmd_uireview_aggregates(conn, capsys):
    _seed_traces(conn)
    cli.cmd_uireview(conn, {})
    out = capsys.readouterr().out
    assert "send" in out          # top-clicked control
    assert "inbox-tab" in out     # rage hotspot
    assert "search" in out        # dead-end
    assert "rage" in out.lower() or "卡" in out


def test_uireview_report_structure(conn):
    _seed_traces(conn)
    rep = web.uireview_report(conn)
    rage = {r["ui"]: r["count"] for r in rep["rage_hotspots"]}
    assert rage["inbox-tab"] == 1
    clicks = {r["ui"]: r["count"] for r in rep["top_clicks"]}
    assert clicks["send"] == 2
    deadends = {r["ui"] for r in rep["deadend_hotspots"]}
    assert "search" in deadends
    assert rep["unnamed_clicks"] == 2
