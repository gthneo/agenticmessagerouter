"""PowerData ingestion adapter — READ-ONLY WeChat access via a PowerData MCP server.

PowerData exposes WeChat over MCP-over-HTTP (streamable HTTP / SSE). It serves
self-accounts that other tools can't see, so AMR reads them through here. This tool
can only read — sends degrade to "人手发". URL/token come from env/local files only.

Pure prose parsers (parse_sessions / parse_history) are the testable core and are
unit-tested. The `_call` transport hits the live MCP endpoint and is verified by
integration runs against the running server (the exact MCP handshake is live-checked).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime

from .. import ingest

# URL is environment/local-config only — the PowerData host is never committed.
# Set POWERDATA_URL, or it falls back to ~/.config/jl/powerdata_url (git-ignored).
def _default_url():
    u = os.environ.get("POWERDATA_URL")
    if u:
        return u
    path = os.path.expanduser("~/.config/jl/powerdata_url")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


DEFAULT_URL = _default_url()
SOURCE = "powerdata"

# A session entry header. BOTH the group marker and the unread count are OPTIONAL —
# a 0-unread non-group chat shows as bare `[05-31 15:17] 代码班迪`.
#   `[06-16 20:04] 群名 [群] (575条未读)`  ·  `[05-22 11:07] shirley2775~养虾人🦐`
_SESSION_RE = re.compile(
    r"^\[\d{2}-\d{2}\s+\d{2}:\d{2}\]\s+"          # [MM-DD HH:MM]
    r"(?P<name>.+?)"                                # name (non-greedy)
    r"(?P<group>\s+\[群\])?"                        # optional group marker
    r"(?:\s*\((?P<unread>\d+)\s*条未读\))?\s*$"      # optional (<n>条未读)
)
# A history message line: `[2026-06-16 07:59] 中国企业家杂志: [链接] 标题`.
_HISTORY_RE = re.compile(
    r"^\[(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\]\s+"
    r"(?P<sender>.+?):\s?(?P<content>.*)$"
)
# Preview prefixes PowerData prepends to the indented preview line.
_PREVIEW_PREFIX_RE = re.compile(r"^(?:链接/文件|文本|图片|视频|语音|文件|链接)\s*:\s*")


def _ts(stamp: str) -> int:
    """`YYYY-MM-DD HH:MM` → unix seconds. Parsed as local-naive (matches the
    machine PowerData runs on); robust to a stray value → 0."""
    try:
        return int(datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp())
    except (TypeError, ValueError):
        return 0


def parse_sessions(text: str) -> list[dict]:
    """Parse `get_recent_sessions` prose → list of
    {name, is_group: bool, unread: int, preview: str}.

    The header line (e.g. `最近 6 个会话:`) is skipped. Each entry is a header line
    matched by _SESSION_RE; the immediately-following indented line is its preview
    (with the `链接/文件:` / `文本:` prefix stripped)."""
    out: list[dict] = []
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _SESSION_RE.match(line.strip())
        if not m:
            i += 1
            continue
        # the preview is the next non-empty line if it is indented (not a new header)
        preview = ""
        j = i + 1
        if j < len(lines):
            nxt = lines[j]
            if nxt.strip() and not _SESSION_RE.match(nxt.strip()):
                preview = _PREVIEW_PREFIX_RE.sub("", nxt.strip())
                i = j  # consume the preview line
        out.append({
            "name": m.group("name").strip(),
            "is_group": bool(m.group("group")),
            "unread": int(m.group("unread") or 0),
            "preview": preview,
        })
        i += 1
    return out


def parse_history(text: str, *, self_label: str = "me") -> list[ingest.MsgRecord]:
    """Parse `get_chat_history` prose → [MsgRecord].

    Each message line is `[YYYY-MM-DD HH:MM] <sender>: <content>`. PowerData gives
    no message id, so the dedup key is synthesized from a sha1 of (ts|sender|content).
    Sender literally equal to `self_label` (default "me") → direction "out", else "in";
    finer outbound detection (self wxid) is left to db.apply_self_directions downstream.

    LIMITATION (v0.9): one line == one message. Multi-line message bodies (a body that
    wraps onto the next line) are not stitched; the continuation is dropped as a non-match.
    """
    out: list[ingest.MsgRecord] = []
    for line in (text or "").splitlines():
        m = _HISTORY_RE.match(line)
        if not m:
            continue
        stamp, sender, content = m.group("ts"), m.group("sender").strip(), m.group("content")
        ts = _ts(stamp)
        stable = hashlib.sha1(f"{ts}|{sender}|{content}".encode("utf-8")).hexdigest()[:16]
        out.append(ingest.MsgRecord(
            msg_key=ingest.msg_key(source=SOURCE, stable_id=stable),
            ts=ts,
            content=content,
            sender=sender,
            direction="out" if sender == self_label else "in",
            type="text",
        ))
    return out


def _token() -> str:
    t = os.environ.get("WX_MCP_TOKEN")
    if t:
        return t
    path = os.path.expanduser("~/.config/jl/wechat_mcp_token")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


def _extract_jsonrpc(body: str, content_type: str) -> dict:
    """Pull the JSON-RPC envelope out of an MCP HTTP response. The body is either
    `application/json` (one object) or `text/event-stream` (SSE: `data: {json}` lines)."""
    if "text/event-stream" in (content_type or ""):
        for raw in body.splitlines():
            raw = raw.strip()
            if raw.startswith("data:"):
                payload = raw[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    continue
                obj = json.loads(payload)
                # the JSON-RPC response carries an "id"/"result"; skip notifications
                if "result" in obj or "error" in obj:
                    return obj
        raise ValueError("no JSON-RPC data frame in SSE response")
    return json.loads(body)


class PowerDataAdapter(ingest.IngestAdapter):
    """READ-ONLY WeChat adapter over a PowerData MCP-over-HTTP server."""
    platform = "wechat"
    tool = "powerdata"
    can_send = False

    def __init__(self, url: str = DEFAULT_URL, token: str | None = None):
        self.url = url
        self.token = token if token is not None else _token()
        self._session_id: str | None = None
        self._rpc_id = 0

    def _post(self, payload: dict, *, with_session: bool = True) -> urllib.request.addinfourl:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if with_session and self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method="POST", headers=headers)
        return urllib.request.urlopen(req, timeout=30)

    def _next_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    def _initialize(self):
        """MCP streamable-HTTP handshake: `initialize` → capture Mcp-Session-Id."""
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "amr-powerdata", "version": "0.9"},
            },
        }
        with self._post(payload, with_session=False) as r:
            self._session_id = r.headers.get("Mcp-Session-Id")
            # drain the initialize result; some servers require reading it
            r.read()
        # per spec the client should send `notifications/initialized` after init
        if self._session_id:
            try:
                note = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
                with self._post(note) as r:
                    r.read()
            except Exception:
                pass  # best-effort; not all servers require the notification

    def _call(self, tool_name: str, **args) -> str:
        """JSON-RPC `tools/call` over MCP-over-HTTP → the prose text string.

        Best-effort transport; the exact MCP handshake is verified live by the
        controller. Raises on any error so the caller can degrade (HITL stays in).
        """
        if self._session_id is None:
            self._initialize()
        payload = {
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": tool_name, "arguments": args},
        }
        with self._post(payload) as r:
            body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("Content-Type", "")
        env = _extract_jsonrpc(body, ctype)
        if "error" in env:
            raise RuntimeError(f"powerdata MCP error: {env['error']}")
        return env["result"]["content"][0]["text"]

    # ---- IngestAdapter contract --------------------------------------------
    def list_conversations(self, account) -> list[ingest.ConvRecord]:
        # chat_id uses the contact name (PowerData addresses by name, no wxid out).
        # TODO: backfill wxid via get_contacts for cross-tool identity unification.
        text = self._call("get_recent_sessions", account=account, limit=50)
        return [
            ingest.ConvRecord(
                chat_id=s["name"],
                name=s["name"],
                type="group" if s["is_group"] else "private",
                muted=s["is_group"],
                unread=s["unread"],
            )
            for s in parse_sessions(text)
        ]

    def pull_new(self, account, recent_limit=30):
        """[(ConvRecord, [MsgRecord])] — each session with its newest messages."""
        out = []
        for conv in self.list_conversations(account):
            text = self._call("get_chat_history", chat_name=conv.chat_id, limit=recent_limit)
            out.append((conv, parse_history(text)))
        return out

    def backfill(self, account, conv, cursor):
        """PowerData history is name-addressed without a real cursor; one pass then
        done. Kept for contract compatibility — returns ([], '') to signal complete."""
        return [], ""

    def send(self, chat_id, text):
        return False, "powerdata 只读，无法发送"
