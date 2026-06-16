"""feishu send via lark-cli — dispatch + adapter (subprocess mocked)."""
from jl import send
from jl.channels import lark


def test_lark_send_ok(monkeypatch):
    a = lark.LarkAdapter()
    # mock _run to simulate a successful lark-cli send
    monkeypatch.setattr(a, "_run", lambda args: {"ok": True, "data": {"message_id": "om_x"}})
    ok, err = a.send("oc_chat", "你好")
    assert ok is True and err == ""


def test_lark_send_failure(monkeypatch):
    a = lark.LarkAdapter()
    monkeypatch.setattr(a, "_run", lambda args: {"ok": False, "error": {"message": "no perm"}})
    ok, err = a.send("oc_chat", "你好")
    assert ok is False and "no perm" in err


def test_lark_send_transport_error(monkeypatch):
    a = lark.LarkAdapter()
    def boom(args):
        raise RuntimeError("lark-cli exploded")
    monkeypatch.setattr(a, "_run", boom)
    ok, err = a.send("oc_chat", "你好")
    assert ok is False and "exploded" in err


def test_send_message_dispatches_feishu(monkeypatch):
    rec = {}
    monkeypatch.setitem(send.SENDERS, "feishu",
                        lambda chat_id, body: (rec.setdefault("c", (chat_id, body)) is None) and None or (True, ""))
    # cleaner: replace with a plain function
    def _f(chat_id, body):
        rec["c"] = (chat_id, body); return (True, "")
    monkeypatch.setitem(send.SENDERS, "feishu", _f)
    ok, err = send.send_message("feishu", "oc_chat", "hi")
    assert ok is True and rec["c"] == ("oc_chat", "hi")
