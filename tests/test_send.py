"""send dispatch (pure registry) — channel send mocked."""
from jl import send


def test_send_message_dispatches_by_platform(monkeypatch):
    calls = {}
    monkeypatch.setitem(send.SENDERS, "wechat",
                        lambda chat_id, body: calls.__setitem__("c", (chat_id, body)) or (True, ""))
    ok, err = send.send_message("wechat", "wxid_t", "你好")
    assert ok is True and err == ""
    assert calls["c"] == ("wxid_t", "你好")


def test_send_message_unknown_platform():
    ok, err = send.send_message("nope", "x", "y")
    assert ok is False and "unsupported" in err.lower()
