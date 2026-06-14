"""phone adapter — pure mapping from CallHistory rows (live DB read verified manually)."""
from jl.channels import phone
from jl import ingest


def test_map_call_outgoing():
    row = {"Z_PK": 7, "ZADDRESS": "+8613000000001", "ZDATE": 100,
           "ZDURATION": 42.0, "ZORIGINATED": 1}
    m = phone.map_call(row)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "phone:7"
    assert m.ts == 100 + phone.APPLE_OFFSET
    assert m.direction == "out"
    assert "42" in m.content and m.type == "call"


def test_map_call_incoming_missed():
    row = {"Z_PK": 8, "ZADDRESS": "13000000002", "ZDATE": 200,
           "ZDURATION": 0.0, "ZORIGINATED": 0}
    m = phone.map_call(row)
    assert m.direction == "in"
    assert "未接" in m.content or "miss" in m.content.lower()


def test_conversations_from_calls_groups_by_number():
    rows = [
        {"Z_PK": 1, "ZADDRESS": "+8613000000001", "ZDATE": 100, "ZDURATION": 10, "ZORIGINATED": 1},
        {"Z_PK": 2, "ZADDRESS": "+8613000000001", "ZDATE": 300, "ZDURATION": 0, "ZORIGINATED": 0},
        {"Z_PK": 3, "ZADDRESS": "13000000002", "ZDATE": 200, "ZDURATION": 5, "ZORIGINATED": 1},
    ]
    convs = phone.conversations_from_calls(rows, name_resolver=lambda n: "")
    by_id = {c.chat_id: (c, msgs) for c, msgs in convs}
    assert set(by_id) == {"+8613000000001", "13000000002"}
    conv1, msgs1 = by_id["+8613000000001"]
    assert conv1.type == "private" and conv1.last_activity_at == 300 + phone.APPLE_OFFSET
    assert len(msgs1) == 2


def test_conversations_from_calls_uses_name_resolver():
    rows = [{"Z_PK": 1, "ZADDRESS": "+8613000000001", "ZDATE": 100, "ZDURATION": 10, "ZORIGINATED": 1}]
    convs = phone.conversations_from_calls(rows, name_resolver=lambda n: "张三")
    conv, _ = convs[0]
    assert conv.name == "张三"
