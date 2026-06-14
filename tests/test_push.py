"""push payload building (pure) with a fake adapter."""
from jl import push, ingest


class FakeAdapter:
    platform = "phone"
    def pull_new(self, account, recent_limit=500):
        return [(ingest.ConvRecord(chat_id="p1", name="张三", last_activity_at=5),
                 [ingest.MsgRecord(msg_key="phone:1", ts=5, content="[通话] 10s")])]


def test_build_payload_shape():
    p = push.build_payload(FakeAdapter(), account_id=2, label="iPhone")
    assert p["account"] == {"account_id": 2, "platform": "phone", "label": "iPhone"}
    assert len(p["conversations"]) == 1
    item = p["conversations"][0]
    assert item["conv"]["chat_id"] == "p1" and item["conv"]["name"] == "张三"
    assert item["msgs"][0]["msg_key"] == "phone:1"
    assert item["msgs"][0]["content"] == "[通话] 10s"


def test_build_payload_includes_self_id_when_given():
    p = push.build_payload(FakeAdapter(), account_id=2, label="x", self_id="me")
    assert p["account"]["self_id"] == "me"
