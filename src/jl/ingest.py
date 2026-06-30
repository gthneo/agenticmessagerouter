"""Pure ingestion contracts shared by all channel adapters (B implements them).

No I/O here — dataclasses, the adapter ABC, dedup-key helpers, and the
content-addressed blob path. Adapters in sub-project B import these so dedup and
storage layout are uniform across platforms.
"""
from __future__ import annotations

import abc
import hashlib
import json
from dataclasses import dataclass, field


class ReadUnavailable(Exception):
    """A read could NOT be performed — contract §6.4 `read_unavailable` (shard key
    missing / message table unmapped / coverage unconfirmable). **Distinct from
    "genuinely zero messages"**: the caller must record it and NOT treat the conversation
    as 0 互动 (that误判 is exactly the 哑失败 this contract clause exists to eliminate)."""

    def __init__(self, chat_id="", reason="unknown", coverage=None, channel=""):
        self.chat_id = chat_id
        self.reason = reason
        self.coverage = coverage or {}
        self.channel = channel
        super().__init__(f"read_unavailable chat={chat_id} reason={reason}")


def parse_read_unavailable(body, *, fallback_chat_id=""):
    """If `body` (dict or JSON str) is a contract read_unavailable error envelope
    (`{"error":{"code":"read_unavailable",...}}`), return a `ReadUnavailable`; else None.
    Pure, never raises. An empty array / normal payload → None (it is NOT a signal)."""
    try:
        d = body if isinstance(body, dict) else json.loads(body or "")
    except (ValueError, TypeError):
        return None
    err = d.get("error") if isinstance(d, dict) else None
    if not isinstance(err, dict) or err.get("code") != "read_unavailable":
        return None
    return ReadUnavailable(
        chat_id=err.get("chatId") or fallback_chat_id,
        reason=err.get("reason") or "unknown",
        coverage=err.get("coverage") or {},
        channel=err.get("channel") or "",
    )


@dataclass
class ConvRecord:
    """Normalized conversation as an adapter reports it."""
    chat_id: str
    name: str = ""
    type: str = "private"            # private | group
    muted: bool = False
    unread: int = 0
    last_activity_at: int | None = None


@dataclass
class MsgRecord:
    """Normalized message as an adapter reports it."""
    msg_key: str
    ts: int
    content: str = ""
    sender: str = ""
    sender_id: str = ""
    direction: str = "in"            # in | out
    type: str = "text"
    media_ref: str = ""
    is_mentioned: bool = False
    raw: dict = field(default_factory=dict)


def content_hash(*, ts: int, sender: str, content: str) -> str:
    """16-hex digest of (minute-resolution ts, sender, content).

    Same minute + sender + content collides on purpose — that is a true duplicate
    for text backends that lack a stable message id.
    """
    minute = ts - (ts % 60)
    h = hashlib.sha1(f"{minute}|{sender}|{content}".encode("utf-8"))
    return h.hexdigest()[:16]


def msg_key(*, source: str, stable_id: str | None,
            ts: int = 0, sender: str = "", content: str = "") -> str:
    """Stable dedup key. Prefer the platform id; else a content hash."""
    if stable_id:
        return f"{source}:{stable_id}"
    return "h:" + content_hash(ts=ts, sender=sender, content=content)


# Message Channel 规范化契约 v1 (docs/superpowers/specs/2026-06-26-message-canonical-contract-v1.md):
# kind → 它的结构化子对象在信封里的键名。子对象原样进 MsgRecord.raw，供 UI 按 kind 富渲。
_KIND_SUBOBJ = {
    "link": "link", "file": "file", "quote": "quote",
    "miniprogram": "miniprogram", "chat_history": "chat_history",
    "location": "location", "system": "system",
    "image": "media", "voice": "media", "video": "media", "sticker": "media",
    "transfer": "payment", "red_packet": "payment",
}


def is_canonical(msg: dict) -> bool:
    """True if a backend message is already a canonical envelope (schema message.canonical/*
    或 至少带 kind+text)。非 canonical(如 fullwechat 原始 dict 含 localId/无 kind) → False。"""
    if not isinstance(msg, dict):
        return False
    if str(msg.get("schema", "")).startswith("message.canonical/"):
        return True
    return "kind" in msg and "text" in msg


def from_canonical(env: dict, *, source: str | None = None) -> MsgRecord:
    """薄映射：Message Channel canonical 信封 → MsgRecord。kind→type、text→content、
    该 kind 的结构化子对象→raw(UI 据此富渲)、media.ref→media_ref。后端未实现前不会走到这里
    (is_canonical 把关)，所以这是"后端吐 canonical 即生效"的消费点。source 默认取 channel。"""
    kind = env.get("kind") or "text"
    ts = int(env.get("ts") or 0)
    sender = env.get("sender", "") or ""
    text = env.get("text", "") or ""
    sub = env.get(_KIND_SUBOBJ.get(kind, "\0"), {})
    if not isinstance(sub, dict):
        sub = {}
    media = env.get("media") if isinstance(env.get("media"), dict) else {}
    return MsgRecord(
        # serverId 全局唯一且稳定; msg_id 后端常=会话内 localId(位置号, 会重用/重置 →
        # 新消息撞老 key 被 INSERT OR IGNORE 丢弃 → 不 ingest)。故优先 serverId。
        msg_key=msg_key(source=source or env.get("channel", "msg"),
                        stable_id=(str(env["serverId"]) if env.get("serverId")
                                   else (env.get("msg_id") or None)),
                        ts=ts, sender=sender, content=text),
        ts=ts, content=text, sender=sender, sender_id=env.get("sender_id", "") or "",
        direction="out" if env.get("direction") == "out" else "in",
        type=kind, media_ref=(media or {}).get("ref", "") or "",
        is_mentioned=bool(env.get("is_mentioned")), raw=sub or {},
    )


def blob_path(sha256: str, root: str = "") -> str:
    """Content-addressed path, sharded by the first two hex chars."""
    rel = f"blobs/{sha256[:2]}/{sha256}"
    return f"{root.rstrip('/')}/{rel}" if root else rel


class IngestAdapter(abc.ABC):
    """Contract every channel adapter (B) implements."""
    platform: str = ""

    @abc.abstractmethod
    def list_conversations(self, account) -> list[ConvRecord]: ...

    @abc.abstractmethod
    def backfill(self, account, conv, cursor: str) -> tuple[list[MsgRecord], str]:
        """Return (messages, next_cursor); '' next_cursor means done."""

    @abc.abstractmethod
    def pull_new(self, account, recent_limit=30):
        """Incremental pull. Returns [(ConvRecord, [MsgRecord])] — each active
        conversation with its newest messages (dedup absorbs overlap)."""
