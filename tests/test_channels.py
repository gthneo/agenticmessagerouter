"""Pure parsing/normalization inside the channel adapters."""
import time

from jl.channels import wechat, phone


def _unix(ts_str):
    return int(time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M")))


def test_wechat_parse_history_extracts_messages():
    raw = (
        "[2026-06-13 22:30] 张三: 明天见\n"
        "[2026-06-13 22:31] 李四: 好的\n"
        "[2026-06-13 22:35] 张三: 记得带合同"
    )
    msgs = wechat.parse_history(raw)
    assert len(msgs) == 3
    assert msgs[-1]["sender"] == "张三"
    assert msgs[-1]["body"] == "记得带合同"
    assert msgs[-1]["ts"] == _unix("2026-06-13 22:35")


def test_wechat_parse_history_handles_multiline_body():
    raw = (
        "[2026-06-13 09:00] A: 第一行\n继续第一行\n"
        "[2026-06-13 09:05] B: 第二条"
    )
    msgs = wechat.parse_history(raw)
    assert len(msgs) == 2
    assert "继续第一行" in msgs[0]["body"]


def test_wechat_parse_history_empty():
    assert wechat.parse_history("") == []
    assert wechat.parse_history("no timestamps here") == []


def test_wechat_pick_chat_prefers_stable_wxid_identifier():
    # messy emoji chat_name should not be the lookup key when a wxid exists
    ch = {"identifier": "wxid_test_001",
          "meta": {"wxid": "wxid_test_001", "chat_name": "messy😀群名🎈"}}
    assert wechat.pick_chat(ch) == "wxid_test_001"


def test_wechat_pick_chat_falls_back_to_chat_name():
    ch = {"identifier": "", "meta": {"chat_name": "测试会话名"}}
    assert wechat.pick_chat(ch) == "测试会话名"


def test_phone_norm_strips_non_digits():
    assert phone.norm_phone("+86 158-9128-333") == "8615891 28333".replace(" ", "")
    assert phone.norm_phone("(021) 1234 5678") == "02112345678"
    assert phone.norm_phone(None) == ""


def test_phone_tail_match():
    # numbers match if one is a suffix of the other (country-code tolerant)
    assert phone.tail_match("+8613000000001", "13000000001")
    assert phone.tail_match("13000000001", "+8613000000001")
    assert not phone.tail_match("13000000002", "13000000001")


def test_phone_tail_match_rejects_short_suffix_collision():
    # a 6-digit fragment is a literal suffix of a full number but is NOT the
    # same person — require enough shared digits before declaring a match
    assert not phone.tail_match("8613000000001", "000001")
    # genuine country-code-tolerant match (11 shared digits) still passes
    assert phone.tail_match("8613000000001", "13000000001")
