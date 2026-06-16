"""lark (feishu) adapter — pure mapping (live lark-cli verified manually)."""
from jl.channels import lark
from jl import ingest


def test_extract_text_plain():
    assert lark.extract_text("text", '{"text":"你好"}') == "你好"


def test_extract_text_post_falls_back_to_title_or_type():
    assert lark.extract_text("interactive", '{"x":1}') == "[interactive]"
    assert lark.extract_text("post", '{"title":"周报","content":[]}') == "周报"


def test_extract_text_bad_json():
    assert lark.extract_text("text", "not json") == ""


def test_map_message():
    raw = {"message_id": "om_abc", "msg_type": "text", "create_time": "2026-06-14 16:00",
           "sender": {"id": "ou_sender", "id_type": "open_id"},
           "content": '{"text":"见附件"}', "chat_id": "oc_1", "deleted": False}
    m = lark.map_message(raw)
    assert isinstance(m, ingest.MsgRecord)
    assert m.msg_key == "lark:om_abc"
    assert m.content == "见附件"
    assert m.sender_id == "ou_sender"
    assert m.type == "text"
    assert m.ts == lark._ts("2026-06-14 16:00") and m.ts > 0


def test_map_message_skips_deleted_via_none():
    raw = {"message_id": "om_x", "msg_type": "text", "create_time": "2026-06-14 16:00",
           "sender": {"id": "ou_s"}, "content": '{"text":"x"}', "deleted": True}
    assert lark.map_message(raw) is None


def test_map_chat_group_muted():
    c = lark.map_chat({"chat_id": "oc_1", "name": "项目群", "chat_mode": "group"})
    assert c.chat_id == "oc_1" and c.type == "group" and c.muted is True


def test_map_p2p_chat_is_private_unmuted():
    c = lark.map_chat({"chat_id": "oc_dm1", "name": "李四", "chat_mode": "p2p"})
    assert c.type == "private" and c.muted is False   # DM = active endpoint


def test_adapter_paginates_with_page_token(monkeypatch):
    a = lark.LarkAdapter()
    calls = []
    pages = {
        None: {"data": {"chats": [{"chat_id": "oc_1", "name": "群1", "chat_mode": "group"}],
                        "has_more": True, "page_token": "tok2"}},
        "tok2": {"data": {"chats": [{"chat_id": "oc_2", "name": "群2", "chat_mode": "group"}],
                         "has_more": False, "page_token": None}},
    }
    def fake_run(args):
        # capture whether --page-all is ever used (must NOT be), and the page-token
        assert "--page-all" not in args, "must not use --page-all"
        tok = args[args.index("--page-token") + 1] if "--page-token" in args else None
        calls.append(tok)
        return pages[tok]
    monkeypatch.setattr(a, "_run", fake_run)
    convs = a.all_conversations(None)
    assert [c.chat_id for c in convs] == ["oc_1", "oc_2"]   # both pages collected
    assert calls == [None, "tok2"]                          # followed page_token
