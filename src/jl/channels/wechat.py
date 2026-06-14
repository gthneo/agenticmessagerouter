"""WeChat channel adapter (via the powerdata wechat MCP).

Pure parsing (`parse_history`) is unit-tested; the live `last()` path talks to
the MCP endpoint and is exercised by integration runs, not unit tests.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

# LAN/Tailscale MCP bearer — NEVER hard-code (repo is public). Loaded from the
# WX_MCP_TOKEN env var, else from ~/.config/jl/wechat_mcp_token (a local, git-ignored
# secret file). Empty when unconfigured: the adapter then simply reports offline.
def _load_token():
    t = os.environ.get("WX_MCP_TOKEN")
    if t:
        return t
    path = os.path.expanduser("~/.config/jl/wechat_mcp_token")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    return ""


WX_TOKEN = _load_token()
WX_URLS = [
    "http://ln-p15vg1-aigc.tail2d4a3f.ts.net:8765/mcp",
    "http://192.168.31.193:8765/mcp",
]

_MSG_RE = re.compile(
    r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\] ([^:\n]+?):\s*"
    r"(.*?)(?=\n\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]|\Z)",
    re.DOTALL,
)


def parse_history(raw: str):
    """Parse MCP chat-history text into [{ts, sender, body}], oldest first."""
    out = []
    for ts_str, sender, body in _MSG_RE.findall(raw or ""):
        try:
            ts = int(time.mktime(time.strptime(ts_str, "%Y-%m-%d %H:%M")))
        except ValueError:
            ts = 0
        out.append({
            "ts": ts,
            "sender": sender.strip(),
            "body": re.sub(r"\s+", " ", body).strip(),
        })
    return out


# ----- live MCP plumbing ----------------------------------------------------

def pick_endpoint():
    for u in WX_URLS:
        try:
            req = urllib.request.Request(
                u.replace("/mcp", "/health"), headers={"Authorization": WX_TOKEN})
            with urllib.request.urlopen(req, timeout=5) as r:
                if '"ok"' in r.read().decode(errors="replace"):
                    return u
        except Exception:
            pass
    return None


def _call(url, name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": WX_TOKEN, "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read().decode(errors="replace")
    except Exception:
        return ""
    text = ""
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith("event:"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if "result" in d:
            for c in d["result"].get("content", []):
                if c.get("type") == "text":
                    text = c["text"]
    return text


def pick_chat(channel):
    """Lookup key for get_chat_history. Prefer the stable wxid identifier over a
    display chat_name, which may contain emoji the MCP can't resolve."""
    meta = channel.get("meta") or {}
    return channel.get("identifier") or meta.get("wxid") or meta.get("chat_name") or ""


def last(channel, url=None):
    """Return (ts, summary) of the latest message for a wechat channel row."""
    url = url or pick_endpoint()
    if not url:
        return (0, "")
    chat = pick_chat(channel)
    if not chat:
        return (0, "")
    raw = _call(url, "get_chat_history", {"chat_name": chat, "limit": 5})
    msgs = parse_history(raw)
    if not msgs:
        return (0, "")
    m = msgs[-1]
    ts_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(m["ts"])) if m["ts"] else "?"
    return (m["ts"], f"{ts_str} {m['sender']}: {m['body'][:60]}")
