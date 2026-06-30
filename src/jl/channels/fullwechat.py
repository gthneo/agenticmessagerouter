"""fullwechat ingestion adapter — HTTP client for the fullwechat REST backend.

Pure mappers (map_chat / map_message / is_ingestable) are unit-tested; the live
list_conversations / backfill / pull_new methods hit the REST API and are verified
by integration runs against the running backend.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from .. import ingest
from ..version import __version__

# AMR identity headers on every outbound request, so the data PROVIDER (fullwechat)
# can SEE which AMR version + contract is consuming it (observable in their logs).
# This is the Agent-facing half of the two-sided version handshake (spec
# 2026-06-28-contract-versioning-and-compat-v1).
_AMR_HEADERS = {
    "X-AMR-Version": __version__,
    "X-AMR-Consumes": "message.canonical/1",
}


def _auth_headers(token, *, extra=None):
    """Bearer auth + the AMR identity headers, plus any per-request extras."""
    h = {"Authorization": "Bearer " + (token or "")}
    h.update(_AMR_HEADERS)
    if extra:
        h.update(extra)
    return h


def _default_url():
    """fullwechat 后端地址，优先级：**设置文件 `~/.config/jl/fullwechat_url`（UI 可写，支持
    FQDN/域名如 http://wx.example.com:6174，权威）> env `AGENT_WECHAT_URL` > 内置默认**。
    设置文件优先于 env，使 Web 设置界面成为权威源（切后端/迁机只改设置即可，env 是部署兜底）。
    每次 adapter 实例化时读，故改设置后下次调用即生效(无需重启/改代码)。"""
    try:
        with open(os.path.expanduser("~/.config/jl/fullwechat_url"), encoding="utf-8") as f:
            v = f.read().strip()
            if v:
                return v.rstrip("/")
    except OSError:
        pass
    u = os.environ.get("AGENT_WECHAT_URL")
    if u:
        return u.rstrip("/")
    return "http://192.168.31.178:6174"


DEFAULT_URL = _default_url()
SOURCE = "fullwx"

# WeChat message-type → human placeholder. Non-text messages carry raw XML in
# `content`; without this they dump <msg>…cdnthumb…</msg> blobs into the timeline.
_TYPE_PLACEHOLDER = {
    3: "[图片]", 34: "[语音]", 42: "[名片]", 43: "[视频]",
    47: "[表情]", 48: "[位置]", 62: "[小视频]", 2000: "[转账]", 2001: "[红包]",
}
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_SYS_CONTENT_RE = re.compile(r"<content>(.*?)</content>", re.S)


def clean_content(msg_type, content):
    """Turn a raw message into display text. Text → itself; media → a placeholder;
    app messages (49) → the backend's readable text (quote-reply / filename / link),
    falling back to '[链接] <title>' only when the content is raw <appmsg> XML;
    system messages (10002 <sysmsg>: 撤回/拍一拍…) → their readable inner text;
    leaked XML in any type → a placeholder."""
    try:
        t = int(msg_type)
    except (TypeError, ValueError):
        t = 1
    c = content or ""
    if t in _TYPE_PLACEHOLDER:
        return _TYPE_PLACEHOLDER[t]
    # system messages (revoke / pat / group-notice) arrive as <sysmsg> XML — surface
    # the human-readable <content> (e.g. "修伟 撤回了一条消息") instead of leaking XML.
    if t == 10002 or "<sysmsg" in c:
        m = _SYS_CONTENT_RE.search(c)
        if m:
            return m.group(1).strip()   # e.g. '"修伟" 撤回了一条消息' (WeChat 原样含引号)
        return "[系统消息]"
    if t == 49 or "<appmsg" in c:
        # Raw <appmsg> XML → pull the <title>. But this backend usually pre-cleans
        # type-49 into readable text (a quoted-reply's text, a filename, or
        # "[Link] 标题\n<url>"); keep that verbatim instead of dropping the real
        # content to a generic placeholder.
        if c.lstrip().startswith("<") or "<appmsg" in c:
            m = _TITLE_RE.search(c)
            title = (m.group(1).strip() if m else "")
            return f"[链接] {title}" if title else "[链接/文件]"
        return c.strip() or "[链接/文件]"
    # defensive: a media blob mislabeled as text must not leak raw XML
    if c.lstrip().startswith("<") and ("<msg" in c or "cdnthumb" in c or "<img" in c):
        return "[图片]"
    return c

_SKIP_PREFIXES = ("gh_", "placeholder", "_")
_SKIP_IDS = {"brandsessionholder"}


def _chat_from_messages_path(path):
    """Extract the chat id from a `/api/messages/<urlquoted>?...` path — the fallback
    chatId for a read_unavailable signal whose body omitted it. '' if not a messages path."""
    m = re.search(r"/api/messages/([^?]+)", path or "")
    return unquote(m.group(1)) if m else ""


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
    # 后端已按 Message Channel 契约吐 canonical 信封 → 薄映射 (kind/结构化/direction 都由后端给)。
    # 否则走下面的原始 fullwechat 解析(向前兼容，后端未升级时不变)。
    if ingest.is_canonical(msg):
        return ingest.from_canonical(msg, source=SOURCE)
    # serverId 全局唯一稳定; localId 是会话内位置号(会重用/重置 → 撞键丢新消息), 故优先 serverId
    server = msg.get("serverId") or 0
    stable = ("s" + str(server)) if server else str(msg.get("localId") or "")
    return ingest.MsgRecord(
        msg_key=ingest.msg_key(source=SOURCE, stable_id=stable),
        ts=_ts(msg.get("timestamp")),
        content=clean_content(msg.get("type"), msg.get("content", "") or ""),
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
    tool = "fullwechat"
    can_send = True

    def __init__(self, url=None, token=None, validate_conn=None):
        self.url = (url or _default_url()).rstrip("/")  # 每次实例化读最新地址(设置可改 FQDN)
        self.token = token or _token()
        # OPT-IN runtime contract boundary check: if a DB conn is set, each fetched batch
        # of canonical envelopes is validate-and-warned (gated by contract_validate_enabled).
        # Set by ingest_run.ignite; None in unit tests / send-only use → no validation.
        self.validate_conn = validate_conn

    def _get(self, path):
        req = urllib.request.Request(self.url + path,
                                     headers=_auth_headers(self.token))
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 契约 §6.4：读不可用 = HTTP 409 + {error:{code:"read_unavailable",...}}。
            # 翻成 ReadUnavailable 让上层逐会话处理（标"读不到"、不当 0 互动），而不是
            # 让一个会话的 409 把整账号 poll 抛断（2026-07-01 实测 .178 poll 哑火根因）。
            if e.code == 409:
                ru = ingest.parse_read_unavailable(
                    e.read().decode("utf-8", "replace"),
                    fallback_chat_id=_chat_from_messages_path(path))
                if ru is not None:
                    raise ru from None
            raise

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
        # _get raises ingest.ReadUnavailable on a 409 read-unavailable (契约 §6.4);
        # callers (pull_new/backfill) catch it per-conv so it never aborts the account.
        raw = self._get(f"/api/messages/{quote(chat_id, safe='')}?limit={limit}&offset={offset}")
        if self.validate_conn is not None:   # opt-in boundary check (validate-and-warn only)
            try:
                from .. import contract_validate
                contract_validate.check_and_log(self.validate_conn, raw, channel=self.platform)
            except Exception:                # never let validation break ingest (人在回路兜底)
                pass
        return [map_message(m) for m in raw]

    def _note_unreadable(self, conv, ru):
        """Record a 读不到 conversation (contract read_unavailable) so the orchestrator
        can surface it (event + health count) instead of letting it look like 0 互动."""
        if not hasattr(self, "_unreadable") or self._unreadable is None:
            self._unreadable = []
        self._unreadable.append({"chat_id": ru.chat_id or conv.chat_id,
                                 "reason": ru.reason, "coverage": ru.coverage})

    def drain_unreadable(self):
        """Return + clear the 读不到 conversations collected since the last drain."""
        u = getattr(self, "_unreadable", None) or []
        self._unreadable = []
        return u

    def backfill(self, account, conv, cursor):
        offset = int(cursor or "0")
        try:
            page = self._messages(conv.chat_id, 200, offset)
        except ingest.ReadUnavailable as ru:
            self._note_unreadable(conv, ru)      # 读不到 → 停在此会话, 不抛断
            return [], ""
        nxt = "" if len(page) < 200 else str(offset + len(page))
        return page, nxt

    def pull_new(self, account, recent_limit=30):
        """Return [(ConvRecord, [MsgRecord])] for every ingestable, READABLE conversation,
        each with its most recent `recent_limit` messages (dedup absorbs overlap). A
        conversation that returns 409 read_unavailable is **skipped (not yielded) and
        recorded** — one unreadable chat must not abort the whole account's poll."""
        self._unreadable = []
        out = []
        for conv in self.all_conversations(account):
            try:
                out.append((conv, self._messages(conv.chat_id, recent_limit, 0)))
            except ingest.ReadUnavailable as ru:
                self._note_unreadable(conv, ru)
                continue
        return out

    def _live_chat_ids(self):
        """Set of currently-selectable chat ids, or None if the list can't be fetched.
        Fetch the FULL list (not just the top page) — a quiet contact can sit past the
        first 200 yet still be selectable; a short limit wrongly判定 them unsendable."""
        try:
            chats = self._get("/api/chats?limit=1000&offset=0")
            return {c.get("id") for c in chats}
        except Exception:
            return None  # unknown — don't block the send on a failed pre-check

    def send(self, chat_id, text):
        """Send a text message via fullwechat. Returns (ok, error). Pre-checks that the
        target is selectable so a stale/raw-wxid chat_id fails with an actionable message
        instead of the backend's cryptic 'No action selected'. Never guesses a target."""
        live = self._live_chat_ids()
        if live is not None and chat_id not in live:
            return False, ("TA 不在微信近期会话,发送端选不中。先在微信里打开与 TA 的对话,"
                           "或用「连渠道」把 TA 连到正确的微信会话,再发。")
        body = json.dumps({"chatId": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(self.url + "/api/messages/send", data=body,
                                     method="POST",
                                     headers=_auth_headers(self.token,
                                              extra={"Content-Type": "application/json"}))
        try:
            # GUI 自动化发送(开会话→打字→点发送)偶尔 >30s, 给足 60s 避免把"还在发"
            # 误报成"timed out 失败"(读取路径仍 30s, 读很快)。
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8", "replace"))
        except Exception as e:  # surface any transport error to the human
            return False, str(e)
        # 契约 §2.1: canonical 读 `ok`,过渡期回落旧端点的 `success`。
        ok = bool(res.get("ok", res.get("success")))
        err = res.get("error", "") or ""
        if not ok and "no action" in err.lower():
            err = "发送端选不中该会话(TA 可能不在微信近期列表)。先在微信里打开与 TA 的对话再发。"
        return ok, err


def send_text(chat_id, text):
    """Module-level convenience: send via a default FullWechatAdapter."""
    return FullWechatAdapter().send(chat_id, text)
