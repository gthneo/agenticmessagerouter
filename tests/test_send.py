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


def test_can_send_capability_map():
    assert send.can_send("fullwechat") is True
    assert send.can_send("lark-cli") is True
    assert send.can_send("powerdata") is False
    assert send.can_send("callhistory") is False
    assert send.can_send("unknown-tool") is False   # conservative default


def test_send_message_readonly_tool_refuses_without_sending(monkeypatch):
    # any sender must NOT be invoked when the tool is read-only
    called = {"n": 0}
    monkeypatch.setitem(send.SENDERS, "wechat",
                        lambda chat_id, body: called.__setitem__("n", called["n"] + 1) or (True, ""))
    ok, err = send.send_message("wechat", "wxid_t", "你好", tool="powerdata")
    assert ok is False and err
    assert called["n"] == 0   # never外发


def test_send_message_sendable_tool_dispatches(monkeypatch):
    calls = {}
    monkeypatch.setitem(send.SENDERS, "wechat",
                        lambda chat_id, body: calls.__setitem__("c", (chat_id, body)) or (True, ""))
    ok, err = send.send_message("wechat", "wxid_t", "你好", tool="fullwechat")
    assert ok is True and err == ""
    assert calls["c"] == ("wxid_t", "你好")
