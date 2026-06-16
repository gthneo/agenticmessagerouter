"""Lark/Feishu ingestion adapter via the lark-cli (@larksuite/cli) subprocess.

Pure mappers unit-tested; live methods shell out to `lark-cli` (user identity) and
are verified by integration runs on the host where lark-cli is authenticated.
Scope: group/topic chats (chat-list). P2P/DM enumeration is a follow-up.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .. import ingest

SOURCE = "lark"
LARK_BIN = "lark-cli"


def _ts(s):
    """'YYYY-MM-DD HH:MM' (lark-cli local-formatted) -> unix seconds. Best-effort."""
    if not s:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return 0


def extract_text(msg_type, content):
    """Best-effort plain text from a lark message content JSON string."""
    try:
        c = json.loads(content) if content else {}
    except (ValueError, TypeError):
        return ""
    if not isinstance(c, dict):
        return ""
    if "text" in c and isinstance(c["text"], str):
        return c["text"]
    if "title" in c and isinstance(c["title"], str) and c["title"]:
        return c["title"]
    return f"[{msg_type}]"


def map_chat(chat):
    return ingest.ConvRecord(
        chat_id=chat["chat_id"],
        name=chat.get("name", ""),
        type="group",                 # chat-list returns group/topic only
        muted=True,                   # groups arrive muted (overload control)
    )


def map_message(msg):
    if msg.get("deleted"):
        return None
    sender = msg.get("sender") or {}
    return ingest.MsgRecord(
        msg_key=ingest.msg_key(source=SOURCE, stable_id=msg["message_id"]),
        ts=_ts(msg.get("create_time")),
        content=extract_text(msg.get("msg_type", ""), msg.get("content", "")),
        sender="",                    # display-name resolution is a follow-up
        sender_id=sender.get("id", ""),
        direction="in",               # outbound detection needs self open_id (follow-up)
        type=msg.get("msg_type", "text"),
        raw=msg,
    )


class LarkAdapter(ingest.IngestAdapter):
    platform = "feishu"

    def __init__(self, bin=LARK_BIN):
        self.bin = bin

    def _run(self, args):
        out = subprocess.run([self.bin, *args, "--format", "json"],
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            raise RuntimeError(f"lark-cli {args} failed: {out.stderr[:200]}")
        return json.loads(out.stdout or "{}")

    def _paged(self, base_args, list_key, page_size=50):
        items, token = [], None
        while True:
            args = list(base_args) + ["--page-size", str(page_size)]
            if token:
                args += ["--page-token", token]
            data = (self._run(args).get("data") or {})
            items.extend(data.get(list_key) or [])
            token = data.get("page_token")
            if not data.get("has_more") or not token:
                break
        return items

    def send(self, chat_id, text):
        """Send a text message to a feishu chat via lark-cli (user identity).
        Returns (ok, error)."""
        try:
            d = self._run(["im", "+messages-send", "--chat-id", chat_id,
                           "--text", text, "--as", "user"])
        except Exception as e:  # subprocess/transport failure → surface to human
            return False, str(e)
        if d.get("ok") is False:
            err = d.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err)
            return False, msg or "send failed"
        return True, ""

    def all_conversations(self, account):
        chats = self._paged(["im", "+chat-list", "--as", "user"], "chats")
        return [map_chat(c) for c in chats]

    list_conversations = all_conversations

    def _messages(self, chat_id):
        msgs = self._paged(["im", "+chat-messages-list", "--as", "user",
                            "--chat-id", chat_id], "messages")
        return [m for m in (map_message(x) for x in msgs) if m is not None]

    def backfill(self, account, conv, cursor):
        return self._messages(conv.chat_id), ""

    def pull_new(self, account, recent_limit=30):
        out = []
        for conv in self.all_conversations(account):
            out.append((conv, self._messages(conv.chat_id)))
        return out
