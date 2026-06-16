"""Outbound send dispatch — provider-agnostic registry keyed by platform.

The ONLY module that emits messages outward. Callers (the confirm endpoint) invoke
send_message AFTER the human-in-the-loop confirmation gate. Approval-only this slice.
"""
from __future__ import annotations


def _wechat(chat_id, body):
    from .channels.fullwechat import send_text
    return send_text(chat_id, body)


def _feishu(chat_id, body):
    from .channels.lark import LarkAdapter
    return LarkAdapter().send(chat_id, body)


SENDERS = {
    "wechat": _wechat,
    "feishu": _feishu,
}

# Per-tool send capability. A read-only access tool (powerdata/callhistory) can read a
# channel but cannot emit — sending through it degrades to "人手发" (守 HITL), never外发.
TOOL_CAPS = {
    "fullwechat": True,
    "lark-cli": True,
    "callhistory": False,
    "powerdata": False,
}


def can_send(tool):
    """True if the access tool can send. Unknown tools default to False (conservative)."""
    return TOOL_CAPS.get(tool, False)


def send_message(platform, chat_id, body, *, tool=None):
    """Dispatch a send. Returns (ok, error).

    If `tool` is given and that access tool is read-only (can_send False), refuse to send
    and hand back to the human — read-only tools never外发. When `tool` is None the legacy
    platform-keyed SENDERS path runs unchanged (full backward compat)."""
    if tool is not None and not can_send(tool):
        return False, "该工具只读(只读工具不可发),请人手发或换可发工具"
    fn = SENDERS.get(platform)
    if fn is None:
        return False, f"unsupported platform: {platform}"
    return fn(chat_id, body)
