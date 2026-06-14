"""Data-layer tests — schema + CRUD over an in-memory SQLite db.

Fixtures use synthetic data only (张三/李四 placeholders, wxid_test_*,
+8613000000000-range numbers) — never real contacts. See CLAUDE.md.
"""
import time

import pytest

from jl import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def test_init_db_creates_five_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r["name"] for r in rows}
    assert {"persons", "channels", "events", "tokens"} <= names


def test_upsert_person_is_idempotent(conn):
    db.upsert_person(conn, id="u1", name="张三", category="family",
                     threshold_days=14, aliases=["小三"])
    db.upsert_person(conn, id="u1", name="张三改", category="family",
                     threshold_days=10, aliases=["小三", "阿三"])
    persons = db.get_persons(conn)
    assert len(persons) == 1
    p = persons[0]
    assert p["name"] == "张三改"               # updated, not duplicated
    assert p["threshold_days"] == 10
    assert p["aliases"] == ["小三", "阿三"]     # JSON round-trips to a list


def test_channels_belong_to_person(conn):
    db.upsert_person(conn, id="u2", name="李四", category="partner",
                     threshold_days=3, aliases=[])
    db.upsert_channel(conn, person_id="u2", kind="wechat",
                      identifier="wxid_test_002", label="测试会话~标签")
    db.upsert_channel(conn, person_id="u2", kind="phone",
                      identifier="+8613000000002", label="")
    chans = db.get_channels(conn, "u2")
    kinds = sorted(c["kind"] for c in chans)
    assert kinds == ["phone", "wechat"]


def test_upsert_channel_idempotent_on_person_kind_identifier(conn):
    db.upsert_person(conn, id="u3", name="王五", category="biz",
                     threshold_days=3, aliases=[])
    db.upsert_channel(conn, person_id="u3", kind="wechat",
                      identifier="wxid_test_003", label="王五会话")
    db.upsert_channel(conn, person_id="u3", kind="wechat",
                      identifier="wxid_test_003", label="王五备注")
    chans = db.get_channels(conn, "u3")
    assert len(chans) == 1
    assert chans[0]["label"] == "王五备注"


def test_log_event_appends_audit_trail(conn):
    db.log_event(conn, kind="sweep", person_id=None,
                 actor="user", detail={"red": 2})
    db.log_event(conn, kind="auto_add", person_id="u1",
                 actor="agent", detail={"prefix": "🟡jl-"})
    events = db.get_events(conn, limit=10)
    assert len(events) == 2
    assert events[0]["kind"] == "auto_add"   # newest first
    assert events[0]["detail"]["prefix"] == "🟡jl-"


def test_record_tokens_accumulates_usage(conn):
    db.record_tokens(conn, channel_kind="wechat", op="get_chat_history",
                     reach_count=3, tokens_in=120, tokens_out=40)
    total = db.token_summary(conn)
    assert total["reach_count"] == 3
    assert total["tokens_in"] == 120
    assert total["tokens_out"] == 40
