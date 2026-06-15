"""fullwechat ingestion adapter — HTTP client for the fullwechat REST backend.

Pure mappers (map_chat / map_message / is_ingestable) are unit-tested; the live
list_conversations / backfill / pull_new methods hit the REST API and are verified
by integration runs against the running backend.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote

from .. import ingest

DEFAULT_URL = os.environ.get("AGENT_WECHAT_URL", "http://192.168.31.178:6174")
SOURCE = "fullwx"

_SKIP_PREFIXES = ("gh_", "placeholder", "_")
_SKIP_IDS = {"brandsessionholder"}


def _token():
    t = os.environ.get("AGENT_WECHAT_TOKEN")
    if t:
        return t
    path = os.path.expanduser("~/.config/agent-wechat/token")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _ts(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(iso.replace("Z", "+00:00"))
                   .astimezone(timezone.utc).timestamp())
    except ValueError:
        return 0


def is_ingestable(chat):
    cid = chat.get("id", "")
    if cid in _SKIP_IDS:
        return False
    return not any(cid.startswith(p) for p in _SKIP_PREFIXES)


def map_chat(chat):
    is_group = bool(chat.get("isGroup"))
    return ingest.ConvRecord(
        chat_id=chat["id"],
        name=chat.get("name", ""),
        type="group" if is_group else "private",
        muted=is_group,
        unread=chat.get("unreadCount", 0) or 0,
        last_activity_at=_ts(chat.get("lastActivityAt")),
    )


def map_message(msg):
    local = msg.get("localId") or 0
    stable = str(local) if local else "s" + str(msg.get("serverId") or "")
    return ingest.MsgRecord(
        msg_key=ingest.msg_key(source=SOURCE, stable_id=stable),
        ts=_ts(msg.get("timestamp")),
        content=msg.get("content", "") or "",
        sender=msg.get("senderName", "") or "",
        sender_id=msg.get("sender", "") or "",
        # NOTE: always inbound in this MVP — outbound detection (sender == self wxid)
        # needs self-id reconciliation (device-suffix mismatch); deferred to B 续.
        direction="in",
        type="text" if msg.get("type") == 1 else str(msg.get("type")),
        is_mentioned=bool(msg.get("isMentioned")),
        raw=msg,
    )


class FullWechatAdapter(ingest.IngestAdapter):
    platform = "wechat"

    def __init__(self, url=DEFAULT_URL, token=None):
        self.url = url.rstrip("/")
        self.token = token or _token()

    def _get(self, path):
        req = urllib.request.Request(self.url + path,
                                     headers={"Authorization": "Bearer " + self.token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def list_conversations(self, account, limit=50, offset=0):
        chats = self._get(f"/api/chats?limit={limit}&offset={offset}")
        return [map_chat(c) for c in chats if is_ingestable(c)]

    def all_conversations(self, account, page=200, max_pages=20):
        """Page through the whole activity-sorted chat list, keeping only
        ingestable (non-folder, non-official) conversations. The list is
        dominated by official accounts, so a single small page misses real chats."""
        out, offset = [], 0
        for _ in range(max_pages):
            chats = self._get(f"/api/chats?limit={page}&offset={offset}")
            if not chats:
                break
            out.extend(map_chat(c) for c in chats if is_ingestable(c))
            if len(chats) < page:
                break
            offset += len(chats)
        return out

    def _messages(self, chat_id, limit, offset):
        raw = self._get(f"/api/messages/{quote(chat_id, safe='')}?limit={limit}&offset={offset}")
        return [map_message(m) for m in raw]

    def backfill(self, account, conv, cursor):
        offset = int(cursor or "0")
        page = self._messages(conv.chat_id, 200, offset)
        nxt = "" if len(page) < 200 else str(offset + len(page))
        return page, nxt

    def pull_new(self, account, recent_limit=30):
        """Return [(ConvRecord, [MsgRecord])] for every ingestable conversation,
        each with its most recent `recent_limit` messages (dedup absorbs overlap)."""
        out = []
        for conv in self.all_conversations(account):
            out.append((conv, self._messages(conv.chat_id, recent_limit, 0)))
        return out

    def send(self, chat_id, text):
        """Send a text message via fullwechat. Returns (ok, error)."""
        body = json.dumps({"chatId": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/messages/send", data=body,
                                     method="POST",
                                     headers={"Authorization": "Bearer " + self.token,
                                              "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # surface any transport error to the human
            return False, str(e)
        return bool(res.get("success")), res.get("error", "") or ""


def send_text(chat_id, text):
    """Module-level convenience: send via a default FullWechatAdapter."""
    return FullWechatAdapter().send(chat_id, text)
