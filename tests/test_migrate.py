"""persons.json -> SQLite migration (item N7 groundwork). Must be idempotent.

Synthetic fixtures only (张三/李四 placeholders, wxid_test_*, test numbers).
"""
import json

import pytest

from jl import db, migrate


PERSONS_JSON = {
    "persons": [
        {
            "id": "u1", "name": "张三", "aliases": ["老张"],
            "category": "biz", "threshold_days": 3,
            "wechat": {"chat_name": "张三会话", "wxid": "wxid_test_001"},
            "phone": [],
        },
        {
            "id": "u2", "name": "李四", "aliases": ["小李"],
            "category": "channel", "threshold_days": 5,
            "wechat": {}, "phone": ["13000000001"],
        },
        {
            "id": "u3", "name": "王五", "aliases": [],
            "category": "family", "threshold_days": 7,
            "wechat": {}, "phone": ["+8613000000002"],
        },
    ]
}


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def json_path(tmp_path):
    p = tmp_path / "persons.json"
    p.write_text(json.dumps(PERSONS_JSON, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_migrate_loads_all_persons(conn, json_path):
    n = migrate.migrate_persons_json(conn, json_path)
    assert n == 3
    persons = {p["id"]: p for p in db.get_persons(conn)}
    assert set(persons) == {"u1", "u2", "u3"}
    assert persons["u1"]["aliases"] == ["老张"]
    assert persons["u1"]["threshold_days"] == 3


def test_migrate_maps_wechat_to_channel_with_wxid_identifier(conn, json_path):
    migrate.migrate_persons_json(conn, json_path)
    chans = db.get_channels(conn, "u1")
    wx = [c for c in chans if c["kind"] == "wechat"]
    assert len(wx) == 1
    assert wx[0]["identifier"] == "wxid_test_001"
    assert wx[0]["label"] == "张三会话"


def test_migrate_maps_phone_list_to_channels(conn, json_path):
    migrate.migrate_persons_json(conn, json_path)
    chans = db.get_channels(conn, "u3")
    assert [c["kind"] for c in chans] == ["phone"]
    assert chans[0]["identifier"] == "+8613000000002"


def test_migrate_skips_empty_channels(conn, json_path):
    migrate.migrate_persons_json(conn, json_path)
    # 李四 has no wechat -> only a phone channel, no empty wechat row
    chans = db.get_channels(conn, "u2")
    assert [c["kind"] for c in chans] == ["phone"]


def test_migrate_is_idempotent(conn, json_path):
    migrate.migrate_persons_json(conn, json_path)
    migrate.migrate_persons_json(conn, json_path)
    assert len(db.get_persons(conn)) == 3
    assert len(db.get_channels(conn, "u1")) == 1


def test_migrate_logs_migration_event(conn, json_path):
    migrate.migrate_persons_json(conn, json_path)
    kinds = [e["kind"] for e in db.get_events(conn)]
    assert "migration" in kinds
