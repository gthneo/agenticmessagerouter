"""Outbound send dispatch — provider-agnostic registry keyed by platform.

The ONLY module that emits messages outward. Callers (the confirm endpoint) invoke
send_message AFTER the human-in-the-loop confirmation gate. Approval-only this slice.
"""
from __future__ import annotations


def _wechat(chat_id, body):
    from .channels.fullwechat import send_text
    return send_text(chat_id, body)


SENDERS = {
    "wechat": _wechat,
}


def send_message(platform, chat_id, body):
    """Dispatch a send to the platform's sender. Returns (ok, error)."""
    fn = SENDERS.get(platform)
    if fn is None:
        return False, f"unsupported platform: {platform}"
    return fn(chat_id, body)
