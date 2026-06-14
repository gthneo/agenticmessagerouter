"""Pure ingestion contracts shared by all channel adapters (B implements them).

No I/O here — dataclasses, the adapter ABC, dedup-key helpers, and the
content-addressed blob path. Adapters in sub-project B import these so dedup and
storage layout are uniform across platforms.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field


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
    def pull_new(self, account):
        """Incremental pull. Returns [(ConvRecord, [MsgRecord])] — each active
        conversation with its newest messages (dedup absorbs overlap)."""
