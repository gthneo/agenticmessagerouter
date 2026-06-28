"""Read-only web inbox: stdlib http.server over the AMR store.

Pure data handlers (api_*) are unit-tested; serve() wires them to BaseHTTPRequestHandler.
Read-only by design — replying/sending is sub-project E (human-in-the-loop outbox).
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

from . import db
from . import ingest
from . import autocomms
from .version import CONSUMES, __version__
from . import digest as _digest
from . import lifecycle as _lifecycle
from . import recall as _recall


def api_conversations(conn, params):
    """Active conversations (unmuted) by default; ?muted=1 shows muted ones."""
    if params.get("muted") == "1":
        return db.get_conversations(conn, muted=True)
    return db.get_conversations(conn, muted=False)


def api_messages(conn, conversation_id, limit=200):
    rows = conn.execute(
        "SELECT m.*, (SELECT transcript FROM media md WHERE md.message_id=m.id "
        "AND md.kind='voice' AND md.transcript!='' LIMIT 1) AS transcript "
        "FROM messages m WHERE m.conversation_id=? ORDER BY m.ts ASC LIMIT ?",
        (conversation_id, limit)).fetchall()
    return [dict(r) for r in rows]


def api_search(conn, query, limit=50):
    q = (query or "").strip()
    if not q:
        return []
    return db.search_messages(conn, q, limit=limit)


def api_ingest(conn, payload):
    """Ingest a pushed batch from an edge collector. Idempotent (dedup on msg_key).
    payload = {"account": {account_id, platform, label?, self_id?, host?},
               "conversations": [{"conv": {ConvRecord fields}, "msgs": [{MsgRecord fields}]}]}"""
    acct = payload["account"]
    db.upsert_account(conn, account_id=acct["account_id"], platform=acct["platform"],
                      label=acct.get("label", ""), self_id=acct.get("self_id", ""),
                      host=acct.get("host", ""))
    n_conv = n_msg = 0
    for item in payload.get("conversations", []):
        cv = item["conv"]
        conv = ingest.ConvRecord(
            chat_id=cv["chat_id"], name=cv.get("name", ""),
            type=cv.get("type", "private"), muted=cv.get("muted", False),
            unread=cv.get("unread", 0), last_activity_at=cv.get("last_activity_at"))
        msgs = [ingest.MsgRecord(
            msg_key=m["msg_key"], ts=m["ts"], content=m.get("content", ""),
            sender=m.get("sender", ""), sender_id=m.get("sender_id", ""),
            direction=m.get("direction", "in"), type=m.get("type", "text"),
            media_ref=m.get("media_ref", ""),
            is_mentioned=m.get("is_mentioned", False), raw=m.get("raw", {}))
            for m in item.get("msgs", [])]
        _, ins = db.ingest_records(conn, account_id=acct["account_id"],
                                   platform=acct["platform"], conv=conv, msgs=msgs)
        n_conv += 1
        n_msg += ins
    db.apply_self_directions(conn)  # 自我发出消息标 direction=out (右绿气泡)
    return {"accounts": 1, "conversations": n_conv, "messages": n_msg}


def api_persons(conn):
    return db.persons_overview(conn)


def api_person_timeline(conn, person_id, limit=500):
    rows = conn.execute(
        """SELECT m.* FROM messages m
           JOIN conversations c ON c.id = m.conversation_id
           WHERE c.person_id = ? ORDER BY m.ts ASC LIMIT ?""",
        (person_id, limit)).fetchall()
    return [dict(r) for r in rows]


def api_merge_candidates(conn):
    return db.suggest_merges(conn)


def api_link(conn, payload):
    db.set_conversation_person(conn, int(payload["conversation_id"]), payload["person_id"])
    return {"ok": True}


def api_queue_outbox(conn, payload):
    oid = db.queue_outbox(conn, conversation_id=int(payload["conversation_id"]),
                          body=payload["body"], actor=payload.get("actor", "user"))
    return db.get_outbox_row(conn, oid)


def api_list_outbox(conn):
    return db.get_outbox(conn, status="pending")


def api_confirm_outbox(conn, payload):
    from . import send
    row = db.get_outbox_row(conn, int(payload["id"]))
    if row is None or row["status"] != "pending":
        return {"ok": False, "error": "not a pending outbox item"}
    ok, err = send.send_message(row["platform"], row["chat_id"], row["body"])
    db.mark_outbox(conn, row["id"], "sent" if ok else "failed", error=err)
    db.log_event(conn, kind="send", actor=payload.get("actor", "user"),
                 detail={"outbox_id": row["id"], "platform": row["platform"],
                         "chat_id": row["chat_id"], "ok": ok, "error": err})
    return {"ok": ok, "error": err}


def api_cancel_outbox(conn, payload):
    oid = int(payload["id"])
    db.mark_outbox(conn, oid, "canceled")
    db.log_event(conn, kind="outbox_cancel", actor=payload.get("actor", "user"),
                 detail={"outbox_id": oid})
    return {"ok": True}


def api_suggestions(conn, conversation_id):
    return db.get_suggestions(conn, conversation_id)


def api_proactive(conn):
    """Read-only 主动联络队列: watched/🔴 persons with their queued opener count and
    send target. Generation happens in the poll/CLI; the web only surfaces the queue."""
    from . import assist, weighting
    out = []
    for p in db.get_persons(conn):
        days = assist._person_days(conn, p["id"])
        is_red = weighting.color(days, p["threshold_days"]) == "🔴"
        if not (p.get("watch") or is_red):
            continue
        conv = assist.primary_conversation(conn, p["id"])
        openers = db.get_suggestions(conn, conv["id"], kind="opener") if conv else []
        out.append({
            "person_id": p["id"], "name": p["name"], "category": p["category"],
            "watch": bool(p.get("watch")), "red": is_red,
            "days": round(days, 1) if days is not None else None,
            "conversation_id": conv["id"] if conv else None,
            "openers": len(openers), "missing_channel": conv is None,
        })
    return out


def api_logs(conn, params):
    """分层运维日志 for 运维Agent/工程师: filter by ?level=WARN&component=llm&limit=."""
    cid = params.get("limit")
    return db.get_logs(conn, level=params.get("level") or None,
                       component=params.get("component") or None,
                       limit=int(cid) if cid else 200)


def api_dismiss_suggestion(conn, payload):
    db.set_suggestion_status(conn, int(payload["id"]), "dismissed")
    return {"ok": True}


# 接入后端地址(可填 FQDN/域名)。fullwechat/powerdata 各读自己的 ~/.config/jl/<tool>_url 文件，
# adapter 每次实例化时读 → 改了下次调用即生效。env 优先级仍高于文件(见各 adapter _default_url)。
_BACKEND_FILES = {"fullwechat": "~/.config/jl/fullwechat_url",
                  "powerdata": "~/.config/jl/powerdata_url"}


def api_backends(conn):
    from .channels import fullwechat, powerdata
    return {"fullwechat": fullwechat._default_url(), "powerdata": powerdata._default_url()}


def api_version():
    """AMR's machine-readable identity — the consumer half of the two-sided version
    handshake. Agents / ops query this to learn AMR's version + what it consumes
    (mirror of the backend's /api/status.version + /api/capabilities.schema)."""
    return {"amr_version": __version__, "consumes": CONSUMES}


def _probe_backend(host, *, timeout=2):
    """Best-effort backend version probe for /api/health. FAST + graceful: a short
    timeout, and any failure → reachable:False / backend_version:None (never raises,
    never blocks health on a slow backend). Reuses onboard.probe_backend_versions but
    only surfaces the (PII-free) version — no schema/host/self_id egress here."""
    from . import onboard
    res = onboard.probe_backend_versions(host, token=None, timeout=timeout)
    ver = res.get("version")
    if not ver or ver in ("unreachable", "?"):
        return {"reachable": False, "backend_version": None}
    return {"reachable": True, "backend_version": ver}


def api_health(conn, *, now=None, probe=_probe_backend, propose=None):
    """PII-FREE ops/health projection — public, read-only, zero contact PII.

    Built for an EXTERNAL 数字运维工程师 Agent to MONITOR AMR without the main
    JL_WEB_TOKEN and without ever touching the PII-laden business endpoints
    (/api/digest, /api/persons, /api/conversations all expose names/wxid/content).
    HARD RULE: this returns ONLY operational metrics + state — counts, booleans,
    versions, slot ints, timestamps. NO names, NO wxid/chat_id, NO message/draft
    content, NO labels. `probe`/`propose`/`now` are injectable for fast, network-free
    tests and graceful degradation. The ops Agent OBSERVES + ALERTS; control actions
    (killswitch/autonomy/outbox confirm) stay token-gated with the human (人在回路)."""
    import time
    from . import autocomms
    now = int(now if now is not None else time.time())

    # autonomy: group conv_autonomy by mode; off = total convs minus those dialed.
    total_convs = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    dialed = conn.execute(
        "SELECT mode, COUNT(*) FROM conv_autonomy GROUP BY mode").fetchall()
    dialed_map = {row[0]: row[1] for row in dialed}
    observe = int(dialed_map.get("observe", 0))
    supervised = int(dialed_map.get("supervised", 0))
    autonomy = {"off": int(total_convs) - observe - supervised,
                "observe": observe, "supervised": supervised}

    # auto_replies: propose_replies grouped by action. GUARDED — if slow/raises,
    # degrade this whole field to null, never 500.
    propose = propose or autocomms.propose_replies
    auto_replies = None
    try:
        cands = propose(conn, now)
        auto_replies = {"armed": 0, "shadow": 0, "human": 0}
        for cand in cands:
            act = cand.get("action")
            if act == "arm":
                auto_replies["armed"] += 1
            elif act in ("shadow", "human"):
                auto_replies[act] += 1
    except Exception:                            # noqa: BLE001 — graceful by design
        auto_replies = None

    # outbox: pending + failed in the last 24h.
    pending = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE status='pending'").fetchone()[0]
    failed_recent = conn.execute(
        "SELECT COUNT(*) FROM outbox WHERE status='failed' AND created_at>=?",
        (now - 86400,)).fetchone()[0]

    # backends: slot/tool/reachable/backend_version ONLY. No self_id, no host (host
    # could leak an internal LAN IP — OMITTED on purpose).
    backends = []
    for a in db.get_accounts(conn):
        host = a.get("host", "")
        if host and probe is not None:
            try:
                pr = probe(host)
            except Exception:                    # noqa: BLE001 — graceful by design
                pr = {"reachable": None, "backend_version": None}
        else:
            pr = {"reachable": None, "backend_version": None}
        backends.append({
            "slot": a["account_id"],
            "tool": a.get("tool", ""),
            "reachable": pr.get("reachable"),
            "backend_version": pr.get("backend_version"),
        })

    # events_recent.errors_24h — the events table records no explicit error/failed
    # marker (kinds are sweep/reach/send/...), so we use failed outbox in the last 24h
    # as the error proxy (documented in the ops-API reference). Cheap and PII-free.
    errors_24h = int(failed_recent)
    last_ev = conn.execute("SELECT MAX(ts) FROM events").fetchone()[0]

    return {
        "amr_version": __version__,
        "ok": True,
        "ts": now,
        "killswitch": db.killswitch_on(conn),
        "autonomy": autonomy,
        "auto_replies": auto_replies,
        "outbox": {"pending": int(pending), "failed_recent": int(failed_recent)},
        "backends": backends,
        "events_recent": {"errors_24h": errors_24h},
        "last_event_ts": int(last_ev) if last_ev is not None else None,
    }


def api_set_backend(conn, payload):
    tool = payload.get("tool")
    url = (payload.get("url") or "").strip()
    if tool not in _BACKEND_FILES:
        return {"ok": False, "error": "unknown tool"}
    p = os.path.expanduser(_BACKEND_FILES[tool])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(url)
    db.log_event(conn, kind="set_backend", actor=payload.get("actor", "user"),
                 detail={"tool": tool, "url": url})
    return {"ok": True, "url": url}


def api_self(conn):
    """SELF(自我) settings: registered own-identities + auto suggestions for HITL checkbox."""
    return {"registered": db.get_self_identities(conn),
            "suggestions": db.suggest_self_identities(conn)}


def api_add_self(conn, payload):
    db.add_self_identity(conn, payload["kind"], payload["identifier"],
                         persona=payload.get("persona", "自我"),
                         label=payload.get("label", ""))
    db.log_event(conn, kind="self_add", actor=payload.get("actor", "user"),
                 detail={"kind": payload["kind"], "identifier": payload["identifier"],
                         "persona": payload.get("persona", "自我")})
    return {"ok": True}


def api_set_self_persona(conn, payload):
    db.set_self_persona(conn, payload["kind"], payload["identifier"], payload["persona"])
    return {"ok": True}


def api_get_self_profile(conn):
    from . import assist
    return {"profile": assist.load_self_profile()}


def api_set_self_profile(conn, payload):
    from . import assist
    assist.save_self_profile(payload.get("profile", ""))
    db.log_event(conn, kind="self_profile", actor=payload.get("actor", "user"), detail={})
    return {"ok": True}


def api_remove_self(conn, payload):
    db.remove_self_identity(conn, payload["kind"], payload["identifier"])
    db.log_event(conn, kind="self_remove", actor=payload.get("actor", "user"),
                 detail={"kind": payload["kind"], "identifier": payload["identifier"]})
    return {"ok": True}


def api_reunify(conn, payload):
    """启动/复位归一 (HITL; reset only clears AUTO links). Returns {linked, candidates}."""
    stats = db.reunify(conn, reset=bool(payload.get("reset")))
    db.log_event(conn, kind="reunify", actor=payload.get("actor", "user"),
                 detail={"reset": bool(payload.get("reset")), **stats})
    return {"ok": True, **stats}


def api_mark_person_self(conn, payload):
    """Declare a wrongly-listed contact as actually the USER (pick, not type)."""
    n = db.mark_person_self(conn, payload["person_id"])
    db.log_event(conn, kind="self_person", actor=payload.get("actor", "user"),
                 detail={"person_id": payload["person_id"]})
    return {"ok": True, "self_count": n}


def api_watch(conn, payload):
    on = bool(payload.get("on", True))
    db.set_watch(conn, payload["person_id"], on)
    db.log_event(conn, kind="watch", person_id=payload["person_id"],
                 actor=payload.get("actor", "user"), detail={"on": on})
    return {"ok": True}


def api_unlink(conn, payload):
    freed = db.unlink_conversation(conn, int(payload["conversation_id"]))
    db.log_event(conn, kind="unlink", person_id=freed, actor=payload.get("actor", "user"),
                 detail={"conversation_id": int(payload["conversation_id"]), "freed": freed})
    return {"ok": bool(freed), "freed": freed}


def api_connect(conn, payload):
    """Link a person to a live fullwechat chat id (mirrors cli.cmd_connect): ensure the
    wechat account, pull recent messages, ingest, link. Network-bound → try/except."""
    from . import ingest
    from .channels.fullwechat import FullWechatAdapter, DEFAULT_URL
    person_id, chat_id = payload["person_id"], payload["chat_id"]
    if 1 not in {a["account_id"] for a in db.get_accounts(conn)}:
        db.upsert_account(conn, account_id=1, platform="wechat",
                          label="fullwechat #1", host=DEFAULT_URL)
    try:
        msgs = FullWechatAdapter()._messages(chat_id, 30, 0)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    conv = ingest.ConvRecord(chat_id=chat_id, name="", type="private")
    cid, n = db.ingest_records(conn, account_id=1, platform="wechat", conv=conv, msgs=msgs)
    db.link_person(conn, cid, person_id)
    db.log_event(conn, kind="connect", person_id=person_id, actor=payload.get("actor", "user"),
                 detail={"chat_id": chat_id, "conversation_id": cid, "msgs": n})
    return {"ok": True, "conversation_id": cid, "msgs": n}


def api_matters(conn, params):
    """事 for the right pane, filtered by the current person / conversation."""
    cid = params.get("conversation")
    return db.get_matters(conn, person_id=params.get("person") or None,
                          conversation_id=int(cid) if cid else None,
                          status=params.get("status") or None)


def api_create_matter(conn, payload):
    mid = db.create_matter(
        conn, title=(payload.get("title") or "").strip() or "(未命名)",
        kind=payload.get("kind", ""),
        person_ids=payload.get("person_ids") or [],
        conversation_ids=payload.get("conversation_ids") or [])
    db.log_event(conn, kind="matter_create", actor=payload.get("actor", "user"),
                 detail={"matter_id": mid})
    return {"ok": True, "id": mid}


def api_matter_status(conn, payload):
    db.set_matter_status(conn, int(payload["id"]), payload["status"])
    return {"ok": True}


def api_diagnose(conn, payload):
    """T4 诊断 a matter's conversation → structured diagnosis stored on the matter."""
    from . import diagnosis, llm
    if not llm.available():
        return {"ok": False, "error": "LLM 未配置——可手填诊断 (LLM-optional)"}
    d = diagnosis.diagnose(conn, int(payload["conversation_id"]),
                           matter_id=int(payload["matter_id"]))
    db.log_event(conn, kind="diagnose", actor=payload.get("actor", "user"),
                 detail={"matter_id": payload.get("matter_id")})
    return {"ok": bool(d), "diagnosis": d}


def api_generate_drafts(conn, payload):
    from . import assist, llm
    if not llm.available():
        return {"ok": False, "error": "LLM 未配置(ANTHROPIC_API_KEY)"}
    n = assist.generate_drafts(conn, int(payload["conversation_id"]))
    return {"ok": n > 0, "count": n}


def api_digest(conn):
    """今日简报(L0 落地页):5 报告 + 需你拍板。纯只读、零 LLM。"""
    return _digest.build(conn)


def api_lifecycle_proposals(conn):
    """事生命周期「待推进提议」(只读、确定性、零 LLM)。"""
    import time
    return _lifecycle.propose(conn, now=time.time())


def api_recall(conn, person_id, purpose="reply"):
    """记忆层 recall 显著上下文包(只读、零 LLM)。"""
    import time
    return _recall.recall(conn, person_id, now=time.time(), purpose=purpose)


def api_safe_phrases(conn):
    """话术库(双闸·闸一白名单) — 列出。"""
    return db.get_safe_phrases(conn)


def api_add_safe_phrase(conn, payload):
    """话术库 — 新增一条白名单话术/意图。"""
    pid = db.add_safe_phrase(conn, payload["pattern"], kind=payload.get("kind", ""))
    db.log_event(conn, kind="safe_phrase_add", actor=payload.get("actor", "user"),
                 detail={"id": pid})
    return {"ok": True, "id": pid}


def api_delete_safe_phrase(conn, payload):
    """话术库 — 删除一条白名单话术。内置不可删。"""
    ok = db.delete_safe_phrase(conn, int(payload["id"]))
    if ok:
        db.log_event(conn, kind="safe_phrase_del", actor=payload.get("actor", "user"),
                     detail={"id": payload.get("id")})
    return {"ok": ok, "error": "" if ok else "内置安全话术不可删"}


def api_auto_replies(conn):
    """监管下自动回复候选(只读·propose-only·不发)。"""
    import time
    return autocomms.propose_replies(conn, time.time())


def api_set_autonomy(conn, payload):
    ok = db.set_autonomy(conn, int(payload["conversation_id"]), payload["mode"])
    if ok:
        db.log_event(conn, kind="autonomy", actor=payload.get("actor", "user"),
                     detail={"conversation_id": payload["conversation_id"], "mode": payload["mode"]})
    return {"ok": ok, "error": "" if ok else "挡位不接受(off/observe/supervised;autonomous=Phase3)"}


def api_killswitch(conn, payload):
    db.set_killswitch(conn, bool(payload.get("on")))
    db.log_event(conn, kind="killswitch", actor=payload.get("actor", "user"),
                 detail={"on": bool(payload.get("on"))})
    return {"ok": True, "on": bool(payload.get("on"))}


def _auth_ok(headers, params):
    want = os.environ.get("JL_WEB_TOKEN")
    if not want:
        return True
    got = params.get("token") or headers.get("Authorization", "").replace("Bearer ", "")
    return got == want


def make_handler(db_path):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if u.path in ("/", "/index.html"):
                return self._send(200, _index_html().encode(), "text/html; charset=utf-8")
            if u.path == "/api/version":
                # identity endpoint — no token (mirror of the backend's public
                # /api/status.version); lets an Agent/ops discover AMR before auth.
                return self._send(200, api_version())
            if u.path == "/api/health":
                # PII-FREE ops/health — PUBLIC (no token, like /api/version). Lets an
                # external 运维Agent monitor AMR (counts/state/versions only) without
                # the main token and without touching any PII-laden business endpoint.
                conn = db.connect(db_path)
                try:
                    return self._send(200, api_health(conn))
                finally:
                    conn.close()
            if not _auth_ok(self.headers, params):
                return self._send(401, {"error": "unauthorized"})
            conn = db.connect(db_path)
            try:
                if u.path == "/api/conversations":
                    return self._send(200, api_conversations(conn, params))
                if u.path.startswith("/api/conversations/") and u.path.endswith("/messages"):
                    try:
                        cid = int(u.path.split("/")[3])
                    except (ValueError, IndexError):
                        return self._send(404, {"error": "bad conversation id"})
                    return self._send(200, api_messages(conn, cid))
                if u.path == "/api/search":
                    return self._send(200, api_search(conn, params.get("q", "")))
                if u.path == "/api/persons":
                    return self._send(200, api_persons(conn))
                if u.path.startswith("/api/persons/") and u.path.endswith("/timeline"):
                    return self._send(200, api_person_timeline(conn, unquote(u.path.split("/")[3])))
                if u.path == "/api/merge-candidates":
                    return self._send(200, api_merge_candidates(conn))
                if u.path == "/api/digest":
                    return self._send(200, api_digest(conn))
                if u.path == "/api/safe-phrases":
                    return self._send(200, api_safe_phrases(conn))
                if u.path == "/api/lifecycle/proposals":
                    return self._send(200, api_lifecycle_proposals(conn))
                if u.path == "/api/recall":
                    return self._send(200, api_recall(conn, params.get("person", ""), params.get("purpose", "reply")))
                if u.path == "/api/proactive":
                    return self._send(200, api_proactive(conn))
                if u.path == "/api/self-profile":
                    return self._send(200, api_get_self_profile(conn))
                if u.path == "/api/logs":
                    return self._send(200, api_logs(conn, params))
                if u.path == "/api/self":
                    return self._send(200, api_self(conn))
                if u.path == "/api/backends":
                    return self._send(200, api_backends(conn))
                if u.path == "/api/matters":
                    return self._send(200, api_matters(conn, params))
                if u.path == "/api/outbox":
                    return self._send(200, api_list_outbox(conn))
                if u.path == "/api/auto-replies":
                    return self._send(200, api_auto_replies(conn))
                if u.path.startswith("/api/conversations/") and u.path.endswith("/suggestions"):
                    try:
                        cid = int(u.path.split("/")[3])
                    except (ValueError, IndexError):
                        return self._send(404, {"error": "bad conversation id"})
                    return self._send(200, api_suggestions(conn, cid))
                return self._send(404, {"error": "not found"})
            finally:
                conn.close()

        def do_POST(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not _auth_ok(self.headers, params):
                return self._send(401, {"error": "unauthorized"})
            if u.path not in ("/api/ingest", "/api/link", "/api/outbox",
                              "/api/outbox/confirm", "/api/outbox/cancel",
                              "/api/suggestions/dismiss", "/api/draft-assist",
                              "/api/matters", "/api/matters/status", "/api/diagnose",
                              "/api/self", "/api/self/remove", "/api/self/person", "/api/self/persona", "/api/self-profile",
                              "/api/reunify", "/api/watch", "/api/connect", "/api/unlink",
                              "/api/safe-phrases", "/api/safe-phrases/delete",
                              "/api/autonomy", "/api/killswitch"):
                return self._send(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length", 0) or 0)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            conn = db.connect(db_path)
            try:
                if u.path == "/api/ingest":
                    return self._send(200, api_ingest(conn, payload))
                if u.path == "/api/link":
                    return self._send(200, api_link(conn, payload))
                if u.path == "/api/outbox":
                    return self._send(200, api_queue_outbox(conn, payload))
                if u.path == "/api/outbox/confirm":
                    return self._send(200, api_confirm_outbox(conn, payload))
                if u.path == "/api/suggestions/dismiss":
                    return self._send(200, api_dismiss_suggestion(conn, payload))
                if u.path == "/api/draft-assist":
                    return self._send(200, api_generate_drafts(conn, payload))
                if u.path == "/api/matters":
                    return self._send(200, api_create_matter(conn, payload))
                if u.path == "/api/matters/status":
                    return self._send(200, api_matter_status(conn, payload))
                if u.path == "/api/diagnose":
                    return self._send(200, api_diagnose(conn, payload))
                if u.path == "/api/self":
                    return self._send(200, api_add_self(conn, payload))
                if u.path == "/api/self/remove":
                    return self._send(200, api_remove_self(conn, payload))
                if u.path == "/api/self/person":
                    return self._send(200, api_mark_person_self(conn, payload))
                if u.path == "/api/self/persona":
                    return self._send(200, api_set_self_persona(conn, payload))
                if u.path == "/api/self-profile":
                    return self._send(200, api_set_self_profile(conn, payload))
                if u.path == "/api/backend":
                    return self._send(200, api_set_backend(conn, payload))
                if u.path == "/api/reunify":
                    return self._send(200, api_reunify(conn, payload))
                if u.path == "/api/watch":
                    return self._send(200, api_watch(conn, payload))
                if u.path == "/api/connect":
                    return self._send(200, api_connect(conn, payload))
                if u.path == "/api/unlink":
                    return self._send(200, api_unlink(conn, payload))
                if u.path == "/api/safe-phrases":
                    return self._send(200, api_add_safe_phrase(conn, payload))
                if u.path == "/api/safe-phrases/delete":
                    return self._send(200, api_delete_safe_phrase(conn, payload))
                if u.path == "/api/autonomy":
                    return self._send(200, api_set_autonomy(conn, payload))
                if u.path == "/api/killswitch":
                    return self._send(200, api_killswitch(conn, payload))
                return self._send(200, api_cancel_outbox(conn, payload))
            except (KeyError, TypeError) as e:
                return self._send(400, {"error": f"bad payload: {e}"})
            finally:
                conn.close()

        def log_message(self, *a):
            pass
    return H


def serve(conn_path=None, host="0.0.0.0", port=8088):
    db_path = conn_path or db.DEFAULT_DB
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"🌐 AMR inbox: http://{host}:{port}  (db={db_path})")
    httpd.serve_forever()


INDEX_HTML = """<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>AMR 收件箱</title><style>
:root{
 --bg:#fff;--fg:#222;--fg2:#888;--fg3:#555;--border:#ddd;--border2:#eee;
 --panel:#fafafa;--hover:#f5f5f5;--chat:#ededed;--bubin:#fff;--bubout:#95ec69;--bubfg:#222;
 --acc:#176;--accbd:#4a8;--accbg:#e8f7ee;--blue:#147;--bluebd:#48a;--bluebg:#e8f0fb;
 --danger:#a33;--dangerbd:#c66;--dangerbg:#fbecec;--countbg:#fffbe6;--badgefg:#446;--badgebg:#eef;
}
:root[data-theme=dark]{
 --bg:#1c1c1e;--fg:#e6e6e8;--fg2:#9a9aa0;--fg3:#b8b8be;--border:#3a3a3c;--border2:#2c2c2e;
 --panel:#2a2a2c;--hover:#2f2f33;--chat:#141416;--bubin:#2a2a2e;--bubout:#1f6b34;--bubfg:#eef;
 --acc:#5fce86;--accbd:#3c7a52;--accbg:#1d3526;--blue:#7fb0ec;--bluebd:#3a5f88;--bluebg:#1d2a3a;
 --danger:#e89a9a;--dangerbd:#7a3a3a;--dangerbg:#3a1e1e;--countbg:#3a3420;--badgefg:#aab8e0;--badgebg:#23304a;
}
@media (prefers-color-scheme:dark){
 :root:not([data-theme=light]):not([data-theme=dark]){
  --bg:#1c1c1e;--fg:#e6e6e8;--fg2:#9a9aa0;--fg3:#b8b8be;--border:#3a3a3c;--border2:#2c2c2e;
  --panel:#2a2a2c;--hover:#2f2f33;--chat:#141416;--bubin:#2a2a2e;--bubout:#1f6b34;--bubfg:#eef;
  --acc:#5fce86;--accbd:#3c7a52;--accbg:#1d3526;--blue:#7fb0ec;--bluebd:#3a5f88;--bluebg:#1d2a3a;
  --danger:#e89a9a;--dangerbd:#7a3a3a;--dangerbg:#3a1e1e;--countbg:#3a3420;--badgefg:#aab8e0;--badgebg:#23304a;
 }
}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,system-ui,sans-serif;display:flex;height:100vh;background:var(--bg);color:var(--fg)}
#side{width:300px;border-right:1px solid var(--border);overflow:auto;flex-shrink:0}#main{flex:1;display:flex;flex-direction:column;min-width:0}
.conv{padding:8px 12px;border-bottom:1px solid var(--border2);cursor:pointer}.conv:hover{background:var(--hover)}
.conv .n{font-weight:600}.conv .p{color:var(--fg2);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sec{padding:6px 10px;font-weight:600;background:var(--panel);border-bottom:1px solid var(--border2);color:var(--fg3);font-size:13px}
.badge{display:inline-block;padding:1px 6px;margin-left:4px;border-radius:8px;background:var(--badgebg);color:var(--badgefg);font-size:11px}
.cand{padding:8px 12px;border-bottom:1px solid var(--border2)}.cand .p{color:var(--fg2);font-size:12px}
.cand button{margin-top:4px;padding:3px 10px;border:1px solid var(--accbd);background:var(--accbg);color:var(--acc);border-radius:6px;cursor:pointer}
.cand button:hover{background:var(--accbg)}
#hdr{padding:8px 12px;border-bottom:1px solid var(--border);display:flex;gap:8px;align-items:center}
#msgs{flex:1;overflow:auto;padding:12px;background:var(--chat)}
.m{margin:5px 0;display:flex;flex-direction:column}.m.in{align-items:flex-start}.m.out{align-items:flex-end}
.m .s{font-size:11px;color:var(--fg2);margin:0 4px 2px}
.bub{max-width:72%;padding:7px 10px;border-radius:8px;overflow-wrap:anywhere;word-break:break-word;white-space:pre-wrap;line-height:1.4;color:var(--bubfg)}
.m.in .bub{background:var(--bubin)}.m.out .bub{background:var(--bubout)}
.m .t{color:var(--fg2);font-size:11px;margin:1px 4px 0}
.sys{text-align:center;color:var(--fg2);font-size:12px;margin:8px auto;max-width:80%}
.tsep{text-align:center;color:var(--fg2);font-size:11px;margin:10px 0}
.bub.card{display:flex;flex-direction:column;gap:2px}.card .ct{font-weight:600}.card .cs{font-size:11px;color:var(--fg2)}.card .cu{font-size:11px;color:var(--blue);overflow-wrap:anywhere;opacity:.9}
.qref{margin-top:5px;padding:4px 7px;border-left:2px solid var(--fg2);background:rgba(127,127,127,.12);font-size:12px;color:var(--fg2);border-radius:3px;white-space:pre-wrap}
input{padding:6px 8px;border:1px solid var(--border);border-radius:6px;width:100%;background:var(--bg);color:var(--fg)}
.ob{padding:8px 12px;border-bottom:1px solid var(--border2)}.ob .p{color:var(--fg2);font-size:12px}.ob .b{margin:3px 0}
.ob button{margin:4px 6px 0 0;padding:3px 10px;border-radius:6px;cursor:pointer;border:1px solid var(--border);background:var(--panel)}
.ob .send{border-color:var(--accbd);background:var(--accbg);color:var(--acc)}.ob .send:hover{background:var(--accbg)}
#replybox{border-top:1px solid var(--border);padding:8px 12px;display:flex;gap:8px;align-items:flex-start}
#replybox textarea{flex:1;padding:6px 8px;border:1px solid var(--border);border-radius:6px;font:inherit;resize:vertical;background:var(--bg);color:var(--fg)}
#replybox button{padding:6px 12px;border:1px solid var(--bluebd);background:var(--bluebg);color:var(--blue);border-radius:6px;cursor:pointer;white-space:nowrap}
#right{width:330px;border-left:1px solid var(--border);overflow:auto;display:flex;flex-direction:column;flex-shrink:0}
#countbar{padding:8px 12px;border-top:1px solid var(--border2);background:var(--countbg);display:flex;gap:8px;align-items:center;flex-wrap:wrap}#countbar.hide{display:none}#countbar .txt{flex:1;min-width:120px;color:var(--fg);overflow-wrap:anywhere}#countbar .cd{font-weight:700;color:#a40;white-space:nowrap}#countbar button{padding:4px 12px;border-radius:6px;cursor:pointer;border:1px solid var(--border);background:var(--panel)}#countbar button.go{border-color:var(--accbd);background:var(--accbg);color:var(--acc)}#countbar button:disabled{opacity:.5;cursor:default}#countbar.err{background:var(--dangerbg)}
@keyframes fl{from{background:#fff3cd}to{background:#fff}}.flash{animation:fl .7s}
.matter{padding:8px 12px;border-bottom:1px solid var(--border2)}.matter .h{font-weight:600}
.matter .dg{color:#a40;font-size:12px;margin:3px 0}.matter .cm{color:var(--fg3);font-size:12px}
.matter button{margin-top:4px;padding:2px 8px;border:1px solid var(--border);background:var(--panel);border-radius:6px;cursor:pointer;font-size:12px}
#settings{position:absolute;inset:0;background:var(--bg);overflow:auto;padding:16px 20px;z-index:5}
#autocomms{position:absolute;inset:0;background:var(--bg);overflow:auto;padding:16px 20px;z-index:5}
#settings.hide,#unify.hide,#prefs.hide,#autocomms.hide{display:none}#main{position:relative}
#hdr{position:relative;z-index:6;background:var(--bg)}
#settings h2{font-size:16px;margin:18px 0 8px;border-bottom:1px solid var(--border2);padding-bottom:4px}
#settings .row{padding:6px 0;border-bottom:1px solid var(--border2);display:flex;gap:8px;align-items:center;flex-wrap:wrap}
#settings .id{color:var(--fg3)}#settings .tag{color:var(--fg2);font-size:12px}
#settings button{padding:3px 10px;border:1px solid var(--border);background:var(--panel);border-radius:6px;cursor:pointer}
#settings button.go{border-color:var(--accbd);background:var(--accbg);color:var(--acc)}#settings button.go:hover{background:var(--accbg)}
#settings button.danger{border-color:var(--dangerbd);background:var(--dangerbg);color:var(--danger)}
#settings select,#settings input{padding:3px 6px;border:1px solid var(--border);border-radius:6px;width:auto}
#settings .x{border-color:var(--dangerbd);color:var(--danger);padding:1px 7px}
#reuniout{color:#176;font-size:13px;margin-left:8px}
#mback,#mmatters,#mclose{display:none}
@media(max-width:640px){
 body{flex-direction:column;height:100vh}
 #side{width:100%;flex:1;border-right:0;border-bottom:1px solid #ddd}
 #main{width:100%;flex:1}#right{width:100%;border-left:0}
 body.m-chat #side{display:none}
 body:not(.m-chat) #main,body:not(.m-chat) #right{display:none}
 body.m-chat:not(.m-matters) #right{display:none}
 body.m-chat.m-matters #right{position:fixed;inset:0;z-index:8;background:#fff;width:auto}
 body.m-chat.m-matters #mclose{display:block;position:sticky;top:0;z-index:9;width:100%;padding:10px 12px;background:#f7f7f7;border:0;border-bottom:1px solid #ddd;text-align:left;font-size:14px;cursor:pointer}
 #mback,#mmatters{display:inline-block}
 .bub{max-width:82%}
}
#skin-digest .dtop{padding:12px 18px 6px;display:flex;align-items:baseline;gap:12px}
#skin-digest .dtop h1{font-size:19px}.dtop .sub{font-size:12px;color:var(--fg2)}
#skin-digest .gate{background:var(--countbg);border:1.5px solid var(--accbd);border-radius:12px;padding:10px 14px;margin:8px 18px 14px}
#skin-digest .gate h2{font-size:13px;color:var(--acc);margin-bottom:8px}
#skin-digest .gi{display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px dashed var(--border2);font-size:13px}
#skin-digest .gi:last-child{border:0}.gi .d{flex:1}
#skin-digest .dbtn{font-size:12px;border-radius:14px;padding:3px 12px;cursor:pointer;border:1px solid var(--bluebd);background:var(--bg);white-space:nowrap}
#skin-digest .dbtn.go{border-color:var(--accbd);background:var(--accbg);color:var(--acc)}
#skin-digest .dbtn:disabled{opacity:.45;cursor:default}
#skin-digest .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px;padding:0 18px 18px}
#skin-digest .rc{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px 14px}
#skin-digest .rc h3{font-size:14px;margin-bottom:8px}.rc .ld{font-size:13px;background:var(--hover);border-radius:8px;padding:7px 9px;margin-bottom:8px}
#skin-digest .st{display:flex;gap:14px}.st .s{font-size:12px;color:var(--fg2)}.st .s b{display:block;font-size:18px;color:var(--fg)}
#skin-digest .pend{font-size:12px;color:var(--fg2);font-style:italic}
#mtab{display:none}
@media(max-width:640px){
 #skinbar{bottom:60px}
 #mtab{display:flex;position:fixed;left:0;right:0;bottom:0;height:50px;background:var(--panel);border-top:1px solid var(--border);z-index:40}
 #mtab .mt{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:10px;color:var(--fg2);gap:1px}
 #mtab .mt.on{color:var(--blue)}
 #skin-digest{padding-bottom:54px}
 #skin-digest .grid{grid-template-columns:1fr;padding:0 12px 18px}
 #skin-digest .gate{margin:8px 12px 12px}
}
#axisbar{display:flex;gap:4px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--panel)}
#axisbar .axt{font-size:13px;padding:5px 16px;border-radius:8px;cursor:pointer;color:var(--fg2)}
#axisbar .axt.on{background:var(--bg);font-weight:700;color:var(--fg)}
#axis-matters .board{display:flex;gap:12px;padding:14px;overflow-x:auto;align-items:flex-start}
#axis-matters .col{flex:0 0 240px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:8px}
#axis-matters .col h4{font-size:12px;color:var(--fg2);margin:2px 4px 8px}
#axis-matters .mc{background:var(--bg);border:1px solid var(--border);border-left:3px solid var(--accbd);border-radius:8px;padding:8px 10px;margin-bottom:8px;cursor:pointer}
#axis-matters .mc .t1{font-size:13px;font-weight:600}
#axis-matters .mc .t2{font-size:11px;color:var(--fg2);margin-top:3px}
#right .northstar{border:1.5px solid var(--accbd);background:var(--accbg)}
#skin-anchor .anchor-grid{display:grid;grid-template-columns:1fr 300px;grid-template-rows:1fr auto;gap:12px;padding:14px;min-height:calc(100vh - 28px)}
#skin-anchor .field{grid-row:1/2;grid-column:1/2;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px;overflow:auto}
#skin-anchor .field h3{font-size:13px;color:var(--fg2);margin-bottom:8px}
#skin-anchor .ns{grid-row:1/3;grid-column:2/3;background:linear-gradient(180deg,var(--accbg),var(--panel));border:1.5px solid var(--accbd);border-radius:12px;padding:12px}
#skin-anchor .ns h3{font-size:13px;color:var(--acc);margin-bottom:8px}
#skin-anchor .me{grid-row:2/3;grid-column:1/2;background:var(--bluebg);border:1px solid var(--bluebd);border-radius:12px;padding:12px}
#skin-anchor .me .who{font-weight:700;color:var(--fg);margin-bottom:6px}
#skin-anchor .gear{font-size:12px;color:var(--blue);display:flex;gap:14px;flex-wrap:wrap;margin-top:6px}
#skin-anchor .pi{padding:6px 8px;border-bottom:1px dashed var(--border2);font-size:13px}
#skin-anchor .pi:last-child{border:0}
#skin-anchor .tag{font-size:11px;background:var(--accbg);color:var(--acc);border-radius:10px;padding:1px 8px}
#settings .acc{border:1px solid var(--border);border-radius:8px;margin:6px 0}
#settings .acc .ah{padding:9px 12px;cursor:pointer;font-size:13px;color:var(--fg3)}
#settings .acc.open .ah{border-bottom:1px solid var(--border);color:var(--fg)}
#settings .acc .ab{display:none;padding:8px 12px}
#settings .acc.open .ab{display:block}
.fab{position:fixed;right:18px;bottom:74px;z-index:30;background:var(--accbg);border:1.5px solid var(--accbd);color:var(--acc);border-radius:22px;padding:9px 15px;font-weight:700;font-size:14px;box-shadow:0 6px 18px #0005;cursor:pointer}
</style><script>(function(){var t=localStorage.getItem('amr_theme');if(t==='dark'||t==='light')document.documentElement.dataset.theme=t;})();</script></head><body>
<div id=skinbar style="position:fixed;right:10px;bottom:10px;z-index:50;font-size:12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:4px 8px">
 皮肤
 <select id=skinsel onchange="setSkin(this.value)">
  <option value=digest>今日简报</option>
  <option value=inbox>收件箱(三栏)</option>
  <option value=dual>会话双轴</option>
  <option value=anchor>锚定矩形</option>
 </select>
</div>
<div id=skin-digest style="display:none;flex:1;width:100%;height:100vh;overflow:auto"></div>
<div id=skin-dual style="display:none;flex-direction:column;flex:1;width:100%;height:100vh;overflow:auto">
 <div id=axisbar><span class=axt data-axis=people onclick="switchAxis('people')">👥 人视图</span><span class=axt data-axis=matters onclick="switchAxis('matters')">🗂 事视图</span></div>
 <div id=axis-people></div>
 <div id=axis-matters></div>
</div>
<div id=skin-anchor style="display:none;width:100%;height:100vh;overflow:auto">
 <div class=anchor-grid>
  <div class=field id=anchor-field></div>
  <div class=ns id=anchor-ns></div>
  <div class=me id=anchor-me></div>
 </div>
</div>
<div id=mtab>
 <div class=mt data-skin=digest onclick="setSkin('digest')"><span>📋</span>简报</div>
 <div class=mt data-skin=inbox onclick="setSkin('inbox')"><span>👥</span>人</div>
 <div class=mt onclick="toast('事视图皮肤待落地')"><span>🗂</span>事</div>
 <div class=mt onclick="toast('设置待落地')"><span>⚙</span>我</div>
</div>
<div id=side>
 <div class=sec>📞 该联系谁</div><div id=proactive></div>
 <div class=sec>👤 联系人</div><div id=persons></div>
 <div class=sec>📤 待发送 outbox</div><div id=outbox></div>
 <div class=sec>🔗 待确认归并</div><div id=cands></div>
 <div class=sec>💬 会话 <label style="float:right;font-weight:400;font-size:12px;cursor:pointer"><input type=checkbox id=show_groups onchange="loadConvs()"> 显示群</label></div>
 <div style=padding:8px><input id=q placeholder="🔍 搜索消息 (回车)"></div><div id=list></div></div>
<div id=main><div id=hdr><button id=mback onclick="goHome()" style="margin-right:8px">← 列表</button>
 <button onclick="goHome()" style="margin-right:8px">← 收件箱</button>
 <button id=mmatters onclick="toggleMatters()" style="margin-right:8px">🩺事</button>
 <button onclick="toggleUnify()">🔄 归一</button>
 <button onclick="togglePrefs()">⭐ 偏好</button>
 <button onclick="toggleAutocomms()">🤖 自动</button>
 <button onclick="toggleSettings()">⚙ 设置</button><b id=title>选择会话</b></div>
 <div id=unify class=hide>
  <div style="display:flex;justify-content:space-between;align-items:center">
   <b>🔄 归一工作台</b><button class=go onclick="toggleUnify()">✕ 关闭</button></div>
  <h2>🪞 我的身份（SELF · 这些号发的都认作「我」）</h2><div id=self_reg></div>
  <div class=sec style="margin-top:6px">🔎 疑似你自己的号（点「这是我」才纳入；不理会也行）</div><div id=self_sug></div>
  <h2>🧩 人归并候选</h2><div id=unify_cands></div>
  <h2>🧬 我是谁（用于 LLM·随时可改）</h2>
  <div class=row><textarea id=selfprofile rows=5 style="width:100%;border:1px solid #ccc;border-radius:6px;padding:6px" placeholder="班迪这个自然人：性格 / 灵魂 / 喜好 / 工作中的特征 / 核心词……（喂给 AI 起草/诊断，体现你的人味）"></textarea></div>
  <div class=row><button class=go onclick="saveProfile()">💾 保存「我是谁」</button></div>
  <h2>🔄 归并操作</h2>
  <div class=row><button class=go onclick="runReunify(false)">🔄 启动归一</button>
   <button class=danger onclick="runReunify(true)">♻️ 复位归一</button><span id=reuniout></span></div>
  <h2>👥 人管理</h2><div id=people></div>
 </div>
 <div id=prefs class=hide>
  <div style="display:flex;justify-content:space-between;align-items:center">
   <b>⭐ 用户偏好</b><button class=go onclick="togglePrefs()">✕ 关闭</button></div>
  <h2>🎨 主题</h2>
  <div class=row>
   <label><input type=radio name=theme value=system onchange="setTheme('system')"> 跟随系统</label>
   <label><input type=radio name=theme value=light onchange="setTheme('light')"> 浅色</label>
   <label><input type=radio name=theme value=dark onchange="setTheme('dark')"> 深色</label>
  </div>
  <h2>📤 发送</h2>
  <div class=row><label><input type=checkbox id=autosend_on> 选定/发送后自动发送</label>
   倒数 <input id=autosend_secs type=number min=1 style=width:56px> 秒
   <button class=go onclick="saveAuto()">💾 保存</button>
   <span class=tag>关掉=必须点「确认发」才发</span></div>
  <h2>🛡️ 安全话术库（自动发白名单 · 你自己灌）</h2>
  <div class=tag style="display:block;margin-bottom:6px">只有命中这里已批准话术的草稿，才可能进自动发（双闸闸一）；其余永远等你拍板。</div>
  <div id=safe_phrases></div>
  <div class=row>
   <input id=sp_pattern placeholder="已批准话术/意图，如：收到，马上处理" style="flex:1;min-width:200px">
   <input id=sp_kind placeholder="类型(寒暄/确认/FAQ)" style="width:130px">
   <button class=go onclick="addSafePhrase()">➕ 添加</button>
  </div>
 </div>
 <div id=autocomms class=hide>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
   <b>🤖 拟自动回（监管下 · 不发）</b><button class=go onclick="toggleAutocomms()">✕ 关闭</button></div>
  <div class=row style="margin-bottom:10px">
   <label style="display:flex;align-items:center;gap:8px;font-size:14px">
    <input type=checkbox id=ks_on onchange="toggleKill(this.checked)"> 🛑 全局刹车（勾选=全停·不产生任何候选）
   </label>
  </div>
  <div class=tag style="display:block;margin-bottom:10px">默认全关；某会话要自动回，去会话挡位设 observe/supervised。</div>
  <div style="margin-bottom:8px">
   <b>当前会话挡位</b>（对 <span id=ac_curconv>（未选会话）</span>）：
   <select id=ac_dial onchange="setAutonomyDial(this.value)" style="margin-left:6px;padding:3px 8px;border:1px solid var(--border);border-radius:6px">
    <option value=off>off（关）</option>
    <option value=observe>observe（观察·只摆不发）</option>
    <option value=supervised>supervised（监管·待Task4接countdown）</option>
   </select>
  </div>
  <div class=sec style="margin-bottom:8px">候选列表</div>
  <div id=ac_list></div>
 </div>
 <div id=settings class=hide>
  <div style="display:flex;justify-content:space-between;align-items:center">
   <b>⚙ 设置（运维）</b><button class=go onclick="toggleSettings()">✕ 关闭设置</button></div>
  <div class=tag style="margin:4px 0;display:block">用户一般不碰；现场 Build / 运维 Agent 才需要。</div>
  <div class=acc><div class=ah onclick="accTog(this)">▸ 🔌 接入后端（可填 FQDN 域名）</div>
   <div class=ab>
    <div class=row><span class=tag>fullwechat</span>
     <input id=be_fullwechat style="flex:1;min-width:200px" placeholder="http://wx.example.com:6174">
     <button class=go onclick="saveBackend('fullwechat')">💾 保存</button></div>
    <div class=row><span class=tag>powerdata</span>
     <input id=be_powerdata style="flex:1;min-width:200px" placeholder="http://host:8765/mcp">
     <button class=go onclick="saveBackend('powerdata')">💾 保存</button></div>
   </div></div>
 </div>
 <div id=msgs></div>
 <div id=countbar class=hide></div>
 <div id=replybox><textarea id=reply rows=2 placeholder="选右侧话术或自己打字 → 发送后倒数自动发，倒数内可「改改」"></textarea>
 <span id=sendbar><button onclick="armSend()">发送 →</button></span>
 <button onclick="aiDraft()">✨ AI 拟话术</button></div></div>
<div id=right>
 <button id=mclose onclick="toggleMatters()">← 返回会话</button>
 <div class=sec>🗂 事（这条会话）<button onclick="createMatter()" style="float:right;font-size:12px">＋记一件事</button></div>
 <div id=matters></div>
 <div class=sec>✨ 话术 <span style="font-weight:400;color:#a40;font-size:12px">· 先🩺诊断更准</span></div><div id=suggest></div></div>
<script>
function curSkin(){return localStorage.getItem('amr_skin')||'digest';}
function applySkin(){const s=curSkin();
 const dig=document.getElementById('skin-digest');
 const inboxEls=['side','main','right'].map(id=>document.getElementById(id)).filter(Boolean);
 const dual=document.getElementById('skin-dual');
 const anchor=document.getElementById('skin-anchor');
 dig.style.display = s==='digest'?'block':'none';
 dual.style.display = s==='dual'?'flex':'none';
 anchor.style.display = s==='anchor'?'block':'none';
 inboxEls.forEach(e=>e.style.display = s==='inbox'?'':'none');
 if(s==='digest')loadDigest();
 if(s==='dual')loadDual();
 if(s==='anchor')loadAnchor();
 const sel=document.getElementById('skinsel');if(sel)sel.value=s;
 document.querySelectorAll('#mtab .mt').forEach(t=>t.classList.toggle('on',t.dataset.skin===s));}
function setSkin(s){localStorage.setItem('amr_skin',s);applySkin();}
function loadDual(){switchAxis(localStorage.getItem('amr_axis')||'people');}
async function loadAnchor(){
 let ms=[],props=[];
 try{ms=await E('/matters');}catch(e){}
 try{props=await E('/lifecycle/proposals');}catch(e){}
 const open=(ms||[]).filter(m=>m.status==='open');
 const star=open[0];
 document.getElementById('anchor-ns').innerHTML='<h3>◎ 事·北极星</h3>'+
  (star?'<div style="font-weight:700;font-size:15px">'+esc(star.title||'(未命名)')+'</div><div style="margin-top:4px"><span class=tag>'+esc(star.status)+'</span> '+esc(star.kind||'')+'</div>':'<div style="color:var(--fg2)">暂无进行中的事</div>')+
  '<h3 style="margin-top:14px">该你推进</h3>'+
  ((props||[]).length?(props.map(p=>'<div class=pi>'+esc(p.title||'')+' · <span class=tag>'+esc(p.signal||'')+'</span> '+esc(p.reason||'')+'</div>').join('')):'<div style="color:var(--fg2)">无待推进提议</div>');
 document.getElementById('anchor-field').innerHTML='<h3>信息·能量·物质 的流动场 —— 待推进('+(props||[]).length+')</h3>'+
  ((props||[]).length?props.map(p=>'<div class=pi>🗂 '+esc(p.title||'')+' —— '+esc(p.reason||'')+' → 建议'+esc(p.suggestion||'')+'</div>').join(''):'<div style="color:var(--fg2)">场内暂无待办（事都在推进或已结）</div>');
 document.getElementById('anchor-me').innerHTML='<div class=who>▣ 我·站位</div>'+
  '<div style="color:var(--fg2);font-size:12px">实际发消息在收件箱三栏 —— <button class=go onclick="setSkin(\\'inbox\\')">去收件箱</button></div>'+
  '<div class=gear><span>👤 身份</span><span>🕘 时间窗</span><span>🔌 后端</span><span>🎨 主题</span></div>';}
function switchAxis(a){localStorage.setItem('amr_axis',a);
 document.querySelectorAll('#axisbar .axt').forEach(t=>t.classList.toggle('on',t.dataset.axis===a));
 document.getElementById('axis-people').style.display=a==='people'?'block':'none';
 document.getElementById('axis-matters').style.display=a==='matters'?'block':'none';
 if(a==='matters')loadMatterBoard();
 if(a==='people')loadDualPeople();}
function loadDualPeople(){document.getElementById('axis-people').innerHTML=
 '<div style="padding:24px;color:var(--fg2);font-size:14px">👥 人视图沿用收件箱三栏（左人 / 中会话 / 右事卡）。'+
 '<div style="margin-top:10px"><button class=go onclick="setSkin(\\'inbox\\')">切到收件箱三栏</button></div></div>';}
const STAGE_ORDER=['候选','进行','等待','阻塞','完结'];
async function loadMatterBoard(){
 let ms; try{ms=await E('/matters');}catch(e){ms=[];}
 const groups={};(ms||[]).forEach(m=>{(groups[m.status||'其它']=groups[m.status||'其它']||[]).push(m);});
 const order=[...STAGE_ORDER.filter(s=>groups[s]),...Object.keys(groups).filter(s=>!STAGE_ORDER.includes(s))];
 const cols=order.map(s=>'<div class=col><h4>'+esc(s)+'（'+groups[s].length+'）</h4>'+
  groups[s].map(m=>'<div class=mc onclick="openMatter('+m.id+')"><div class=t1>'+esc(m.title||'(未命名)')+'</div>'+
   '<div class=t2>'+esc(m.kind||'')+'</div></div>').join('')+'</div>').join('');
 document.getElementById('axis-matters').innerHTML='<div class=board>'+(cols||'(暂无事)')+'</div>';}
function openMatter(id){toast('事 #'+id+' 详情/会话钻取 — 后续接 lifecycle');}
async function loadDigest(){
 let d; try{d=await E('/digest');}catch(e){d={reports:{},gate:[]};}
 document.getElementById('skin-digest').innerHTML=renderDigest(d);}
function renderDigest(d){
 const R=d.reports||{},g=d.gate||[];
 const gate=g.length?('<div class=gate><h2>⚖ 需你拍板（'+g.length+'）</h2>'+
  g.map(it=>'<div class=gi><div class=d>'+esc(it.text||'')+'</div>'+
   (it.actionable?'<span class="dbtn go" onclick="gateGo('+JSON.stringify(it).replace(/"/g,'&quot;')+')">放行</span><span class=dbtn>改改</span>'
    :'<span class=dbtn disabled>待后端</span>')+'</div>').join('')+'</div>'):'';
 const card=(emoji,name,rep)=>{if(!rep)return '';
  if(rep.pending_backend)return '<div class=rc><h3>'+emoji+' '+name+'</h3><div class=pend>待后端 · '+esc(rep.note||'')+'</div></div>';
  const c=rep.counts||{};
  const stats=Object.keys(c).filter(k=>typeof c[k]==='number').map(k=>'<span class=s><b>'+c[k]+'</b>'+esc(k)+'</span>').join('');
  return '<div class=rc><h3>'+emoji+' '+name+'</h3>'+
   (rep.narrative?'<div class=ld>'+esc(rep.narrative)+'</div>':'')+
   '<div class=st>'+stats+'</div></div>';};
 return '<div class=dtop><h1>今日简报</h1><span class=sub>数字员工报告 · 看总结 / 扳手柄</span></div>'+
  gate+'<div class=grid>'+
  card('📈','销售报告',R.sales)+card('🤝','关系报告',R.relationship)+
  card('📢','营销报告',R.marketing)+card('🗂','业务进展',R.progress)+
  card('🧭','meta 报告',R.meta)+'</div>';}
function gateGo(it){
 if(it.kind==='send_draft'){P('/outbox/confirm',{id:it.outbox_id}).then(r=>{toast(r.ok?'已发送 ✅':'失败:'+(r.error||''));loadDigest();});}
 else{toast('已记录(撩动作走主动队列)');}}
const TOK=new URLSearchParams(location.search).get('token')||'';
const E=(s,p='')=>{const qs=[p,TOK&&'token='+encodeURIComponent(TOK)].filter(Boolean).join('&');return fetch('/api'+s+(qs?'?'+qs:'')).then(r=>r.json())};
const P=(s,body)=>{const qs=TOK?'?token='+encodeURIComponent(TOK):'';return fetch('/api'+s+qs,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN'):''}
window.NAMES={};window.SCROLLPOS={};
function _mjson(s){try{return (s&&typeof s==='object')?s:JSON.parse(s||'{}');}catch(e){return {};}}
function _sz(n){n=+n||0;return n>=1048576?(n/1048576).toFixed(1)+'MB':n>=1024?(n/1024).toFixed(0)+'KB':n?n+'B':'';}
const RICHKINDS=new Set(['link','file','quote','miniprogram','chat_history','location']);
// 按 canonical kind 富渲气泡内体；canonical 字段在 messages.raw(JSON)；非 canonical kind 返回 null → 退回纯文本
function renderKind(x){const m=_mjson(x.raw),k=x.type,b=esc(x.content||m.text||'');
 if(k==='quote'){const a=esc(m.author||''),r=esc(m.refText||'');
  return '<div class=bub>'+b+'<div class=qref>↩ '+(a?a+'：':'')+r+'</div></div>';}
 if(k==='link'){return '<div class="bub card"><div class=ct>🔗 '+esc(m.title||x.content||'[链接]')+'</div>'
  +(m.source?'<div class=cs>'+esc(m.source)+'</div>':'')+(m.url?'<div class=cu>'+esc(m.url)+'</div>':'')+'</div>';}
 if(k==='file'){const s=_sz(m.size);return '<div class="bub card"><div class=ct>📎 '+esc(m.name||x.content||'[文件]')+'</div>'
  +((m.ext||s)?'<div class=cs>'+esc(m.ext||'')+(m.ext&&s?' · ':'')+s+'</div>':'')+'</div>';}
 if(k==='miniprogram'){return '<div class="bub card"><div class=ct>🔲 '+esc(m.title||x.content||'[小程序]')+'</div>'
  +(m.source?'<div class=cs>'+esc(m.source)+'</div>':'')+'</div>';}
 if(k==='chat_history'){const n=(m.items&&m.items.length)?' · '+m.items.length+'条':'';
  return '<div class="bub card"><div class=ct>🗂 '+esc(m.title||x.content||'[聊天记录]')+n+'</div></div>';}
 if(k==='location'){return '<div class="bub card"><div class=ct>📍 '+esc(m.label||m.poi||x.content||'[位置]')+'</div></div>';}
 return null;}
function renderBubbles(m,opt){opt=opt||{};let out='',last=0;
 (m||[]).forEach(x=>{
  if(x.ts&&last&&x.ts-last>300){out+='<div class=tsep>'+fmt(x.ts)+'</div>';}
  if(x.ts)last=x.ts;
  const sys=(x.type==='10002')||(x.type==='system')||/撤回了一条消息$/.test(x.content||'');
  if(sys){const sm=_mjson(x.raw);out+='<div class=sys>'+esc(sm.text||x.content)+'</div>';return;}
  const dir=x.direction==='out'?'out':'in';
  const tag=(opt.platform&&x.platform)?' <span class=badge>'+esc(x.platform)+'</span>':'';
  const name=dir==='in'?'<span class=s>'+esc(x.sender)+tag+'</span>':'';
  let body;
  if(x.type==='voice'&&x.transcript){body='<div class=bub>🎤 '+esc(x.content||'[语音]')+'<div class=qref>'+esc(x.transcript)+'</div></div>';}
  else{const rich=RICHKINDS.has(x.type)?renderKind(x):null;body=(rich!=null)?rich:('<div class=bub>'+esc(x.content)+'</div>');}
  out+='<div class="m '+dir+'">'+name+body+'<span class=t>'+fmt(x.ts)+'</span></div>';
 });
 return out||'(无消息)';}
async function loadConvs(){
 const showG=(document.getElementById('show_groups')||{}).checked;
 let c=await E('/conversations');
 if(showG){try{const g=await E('/conversations','muted=1');c=c.concat(g);}catch(e){}}  // 群默认静音隐藏, 勾选才拉
 c.sort((a,b)=>(b.last_activity_at||0)-(a.last_activity_at||0));
 c.forEach(x=>window.NAMES[x.id]=x.name||x.chat_id);
 document.getElementById('list').innerHTML=
 c.map(x=>`<div class=conv onclick="openConv(${x.id})"><div class=n>${esc(x.name||x.chat_id)} ${x.type==='group'?'<span class=badge>群</span>':''}</div>
 <div class=p>${esc(x.platform)} · ${fmt(x.last_activity_at)}</div></div>`).join('')}
function toast(msg){const t=document.createElement('div');t.textContent=msg;
 t.style.cssText='position:fixed;bottom:20px;right:20px;background:#176;color:#fff;padding:8px 14px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.2);z-index:9';
 document.body.appendChild(t);setTimeout(()=>t.remove(),2500)}
function applyTheme(t){if(t==='dark'||t==='light')document.documentElement.dataset.theme=t;else delete document.documentElement.dataset.theme;}
function setTheme(t){localStorage.setItem('amr_theme',t);applyTheme(t);}
function autoCfg(){return {on:localStorage.getItem('amr_autosend')!=='0',
  secs:Math.max(1,parseInt(localStorage.getItem('amr_autosend_secs')||'5',10)||5)};}
function cancelSend(){if(window.SENDTIMER){clearInterval(window.SENDTIMER);window.SENDTIMER=null;}
  const c=document.getElementById('countbar');c.className='hide';c.innerHTML='';}
function cancelEdit(){cancelSend();document.getElementById('reply').focus();}
function armSend(){if(!window.CURCONV){alert('先选会话');return;}
  const ta=document.getElementById('reply'),body=ta.value.trim();if(!body)return;
  cancelSend();
  const who=window.NAMES[window.CURCONV]||'对方',cfg=autoCfg(),c=document.getElementById('countbar');
  c.className='';
  const bar=(head,goLabel)=>{c.innerHTML=head+
    ' <button class=go onclick="doSend()">'+goLabel+'</button>'+
    ' <button id=cancelbtn onclick="cancelEdit()">改改</button>';
    const cb=document.getElementById('cancelbtn');if(cb)cb.focus();};
  if(cfg.on){let left=cfg.secs;
    const tick=()=>bar('<span class=cd>⏳ '+left+'s</span><span class=txt>后自动发给 '+esc(who)+'：'+esc(body)+'</span>','立即发');
    tick();
    window.SENDTIMER=setInterval(()=>{left--;if(left<=0){doSend();}else{tick();}},1000);
  }else{
    bar('<span class=txt>确认发给 '+esc(who)+'：'+esc(body)+'</span>','确认发');
  }}
async function doSend(){if(window.SENDTIMER){clearInterval(window.SENDTIMER);window.SENDTIMER=null;}
  const ta=document.getElementById('reply'),body=ta.value.trim(),c=document.getElementById('countbar');
  if(!body){cancelSend();return;}
  c.querySelectorAll('button').forEach(b=>{b.disabled=true;});
  const row=await P('/outbox',{conversation_id:window.CURCONV,body});
  const r=await P('/outbox/confirm',{id:row.id});
  if(r.ok){ta.value='';cancelSend();toast('已发送 ✅');loadOutbox();
    const ms=document.getElementById('msgs');ms.insertAdjacentHTML('beforeend','<div class="m out"><div class=bub>'+esc(body)+'</div><span class=t>刚刚</span></div>');ms.scrollTop=ms.scrollHeight;}
  else{c.className='err';c.innerHTML='<span class=txt>发送失败：'+esc(r.error||'未知')+'</span>'+
    ' <button class=go onclick="armSend()">重试</button> <button onclick="cancelSend()">取消</button>';}}
async function openConv(id){window.CURCONV=id;cancelSend();document.body.classList.add('m-chat');const m=await E('/conversations/'+id+'/messages');
 const ms=document.getElementById('msgs');ms.innerHTML=renderBubbles(m);
 const sp=window.SCROLLPOS[id];ms.scrollTop=sp!=null?sp:ms.scrollHeight;  // 上次离开的位置，没有则到最新
 loadSuggestions(id);loadMatters(id)}
async function loadMatters(id){const ms=await E('/matters','conversation='+id);
 document.getElementById('matters').innerHTML=ms.map((m,i)=>{
 const d=m.diagnosis||{};const dg=d['一句话诊断']?`<div class=dg>🩺 ${esc(d['一句话诊断'])}</div>`:'';
 const cm=(m.commitments||[]).map(c=>`<div class=cm>📌 ${esc(c.text)} <span class=badge>${esc(c.status)}</span></div>`).join('');
 const dx=`<button onclick="diagnose(${m.id})">🩺 诊断</button>`;
 const act=m.status==='open'?`<button onclick="matterStatus(${m.id},'handled')">✓ 办结</button>`:`<span class=badge>${esc(m.status)}</span>`;
 return `<div class="matter${i===0?' northstar':''}"><div class=h>${esc(m.title)} ${m.kind?`<span class=badge>${esc(m.kind)}</span>`:''}</div>${dg}${cm}${dx} ${act}</div>`}).join('')||'<div class=p style=padding:8px>(暂无事项，＋记一件事)</div>'}
async function diagnose(mid){if(!window.CURCONV)return;
 const r=await P('/diagnose',{matter_id:mid,conversation_id:window.CURCONV});
 if(!r.ok){alert(r.error||'诊断失败');return}loadMatters(window.CURCONV)}
async function createMatter(){if(!window.CURCONV){alert('先选会话');return}
 const t=prompt('记一件事（标题）：');if(!t)return;
 await P('/matters',{title:t,conversation_ids:[window.CURCONV]});loadMatters(window.CURCONV)}
async function matterStatus(id,s){await P('/matters/status',{id,status:s});loadMatters(window.CURCONV)}
window.SUG={};
async function loadSuggestions(id){const s=await E('/conversations/'+id+'/suggestions');
 window.SUG={};s.forEach(x=>window.SUG[x.id]=x.body);
 document.getElementById('suggest').innerHTML=(s.length?'<div class=p>✨ '+(s[0].kind==='opener'?'主动开场':'话术')+'（用此版填入下方，可改）:</div>':'')+
 s.map(x=>`<div class=ob><div class=p>[${esc(x.stance)}]</div><div class=b>${esc(x.body)}</div>
 <button onclick="useDraft(${x.id})">用此版</button> <button onclick="dismissSug(${x.id})">✕</button></div>`).join('')}
function useDraft(id){const r=document.getElementById('reply');r.value=window.SUG[id]||'';armSend();}
async function aiDraft(){if(!window.CURCONV){alert('先选会话');return}
 const r=await P('/draft-assist',{conversation_id:window.CURCONV});
 if(!r.ok){alert(r.error||'LLM 不可用');return}loadSuggestions(window.CURCONV)}
async function dismissSug(id){await P('/suggestions/dismiss',{id});loadSuggestions(window.CURCONV)}
async function loadOutbox(){const o=await E('/outbox');document.getElementById('outbox').innerHTML=
 o.map(x=>`<div class=ob><div class=p>→ ${esc(x.chat_id)} <span class=badge>${esc(x.platform)}</span></div>
 <div class=b>${esc(x.body)}</div>
 <button class=send onclick="confirmOutbox(${x.id})">✅ 确认发送</button>
 <button onclick="cancelOutbox(${x.id})">✕ 取消</button></div>`).join('')||'<div class=p style=padding:8px>(无待发送)</div>'}
async function confirmOutbox(id){const r=await P('/outbox/confirm',{id});
 alert(r.ok?'已发送 ✅':'发送失败：'+(r.error||'未知'));loadOutbox()}
async function cancelOutbox(id){await P('/outbox/cancel',{id});loadOutbox()}
async function loadPersons(){const ps=await E('/persons');document.getElementById('persons').innerHTML=
 ps.map(p=>`<div class=conv onclick="openPerson('${esc(p.id)}',this)"><div class=n>${esc(p.name||p.id)}
 ${(p.channels||[]).map(ch=>`<span class=badge>${esc(ch)}</span>`).join('')}</div>
 <div class=p>${p.conversations} 个会话 · ${fmt(p.last_activity_at)}</div></div>`).join('')||'<div class=p style=padding:8px>(暂无已归并联系人)</div>'}
async function openPerson(id){const m=await E('/persons/'+encodeURIComponent(id)+'/timeline');
 document.getElementById('title').textContent='👤 '+id+' 合并时间线';
 document.getElementById('msgs').innerHTML=renderBubbles(m,{platform:true});}
async function loadProactive(){const ps=await E('/proactive');document.getElementById('proactive').innerHTML=
 ps.map(p=>{const tag=p.red?'🔴':(p.watch?'⭐':'');const days=p.days!=null?p.days+'天':'';
 if(p.missing_channel)return `<div class=conv><div class=n>${tag} ${esc(p.name)} <span class=badge>缺渠道·救补</span></div><div class=p>${days} · 补微信/飞书号再拟</div></div>`;
 return `<div class=conv onclick="openConv(${p.conversation_id})"><div class=n>${tag} ${esc(p.name)} ${p.openers?`<span class=badge>${p.openers}版开场</span>`:''}</div><div class=p>${days}${p.openers?' · 点开挑/改/发':' · 待拟'}</div></div>`}).join('')||'<div class=p style=padding:8px>(无 关注/🔴 待联络)</div>'}
async function loadCands(){const cs=await E('/merge-candidates');document.getElementById('cands').innerHTML=
 cs.map(c=>`<div class=cand><div class=n>${esc(c.name||c.peer)} <span class=badge>${esc(c.platform)}</span></div>
 <div class=p>本会话标识：${esc(c.peer)}</div>
 ${c.candidates.map(p=>{const star=p.strength>=3?'🟢强':(p.strength==2?'🟡中':'⚪弱');
  const ev=(p.evidence||[]).join('、');
  const chs=(p.channels||[]).map(x=>esc(x.identifier)).join(' · ')||'(无已知渠道)';
  return `<div class=p>→ ${esc(p.name||p.id)} <span class=badge>${star}</span> ${esc(ev)}
  <br><span style=color:#888>已有：${chs}</span></div>
  <button onclick="confirmLink(${c.conversation_id},'${esc(p.id)}')">确认归并到 ${esc(p.name||p.id)}</button>`}).join('')}
 </div>`).join('')||'<div class=p style=padding:8px>(无待确认项)</div>'}
const PERSONAS=['工作','生活','学习'];
function personaSel(kind,id,cur){const o=PERSONAS.map(p=>`<option${p===cur?' selected':''}>${p}</option>`).join('');
 return `<select onchange="setPersona('${esc(kind)}','${esc(id)}',this.value)">${o}</select>`}
function _hidePanels(){['unify','prefs','settings','autocomms'].forEach(id=>{const e=document.getElementById(id);if(e)e.classList.add('hide');});}
function toggleSettings(){const s=document.getElementById('settings');const was=s.classList.contains('hide');_hidePanels();if(was){s.classList.remove('hide');loadSettings();}}
function toggleUnify(){const u=document.getElementById('unify');const was=u.classList.contains('hide');_hidePanels();if(was){u.classList.remove('hide');loadUnify();}}
function togglePrefs(){const p=document.getElementById('prefs');const was=p.classList.contains('hide');_hidePanels();if(was){p.classList.remove('hide');loadSettings();}}
function accTog(el){el.parentElement.classList.toggle('open');const open=el.parentElement.classList.contains('open');el.textContent=el.textContent.replace(/^[▸▾]/, open?'▾':'▸');}
async function loadUnify(){await loadCands();await loadSettings();const u=document.getElementById('unify_cands'),c=document.getElementById('cands');if(u&&c)u.innerHTML=c.innerHTML;}
async function loadSettings(){
 {const cfg=autoCfg();document.getElementById('autosend_on').checked=cfg.on;document.getElementById('autosend_secs').value=cfg.secs;}
 {const t=localStorage.getItem('amr_theme')||'system';const el=document.querySelector('input[name=theme][value="'+t+'"]');if(el)el.checked=true;}
 {const b=await E('/backends');if(b){document.getElementById('be_fullwechat').value=b.fullwechat||'';document.getElementById('be_powerdata').value=b.powerdata||'';}}
 const prof=await E('/self-profile');document.getElementById('selfprofile').value=(prof&&prof.profile)||'';
 const d=await E('/self');
 document.getElementById('self_reg').innerHTML=(d.registered||[]).map(s=>
  `<div class=row><b>${esc(s.kind)}</b> <span class=id>${esc(s.identifier)}</span>
   ${personaSel(s.kind,s.identifier,s.persona)}<span class=tag>${s.label?' · '+esc(s.label):''}</span>
   <button class=x onclick="removeSelf('${esc(s.kind)}','${esc(s.identifier)}')">✕</button></div>`
  ).join('')||'<div class=tag style=padding:6px>(还没登记自我身份)</div>';
 document.getElementById('self_sug').innerHTML=(d.suggestions||[]).map(s=>{
  const opts=PERSONAS.map(p=>`<option>${p}</option>`).join('');
  return `<div class=row><b>${esc(s.kind)}</b> <span class=id>${esc(s.identifier)}</span>
   <span class=tag>${esc(s.name||'')}${s.reason?' · '+esc(s.reason):''}</span>
   <select>${opts}</select>
   <button class=go onclick="addSelf('${esc(s.kind)}','${esc(s.identifier)}',this)">✅ 这是我，纳入</button></div>`
  }).join('')||'<div class=tag style=padding:6px>(暂无建议)</div>';
 const ps=await E('/persons');
 document.getElementById('people').innerHTML=(ps||[]).map(p=>
  `<div class=row><b>${esc(p.name||p.id)}</b> <span class=tag>${esc(p.id)}</span>
   <button onclick="watchPerson('${esc(p.id)}')">⭐关注</button>
   <button class=danger onclick="markSelf('${esc(p.id)}','${esc(p.name||p.id)}')">🪞这其实是我</button>
   <input placeholder="微信chat_id" data-pid="${esc(p.id)}">
   <button class=go onclick="connectChannel('${esc(p.id)}',this)">🔗连渠道</button></div>`
  ).join('')||'<div class=tag style=padding:6px>(暂无已归并联系人)</div>';
 loadSafePhrases();}
async function markSelf(pid,name){if(!confirm('把「'+name+'」标为你自己?其身份将纳入自我、从联系人移除。'))return;
 await P('/self/person',{person_id:pid});toast('已设为自我 🪞');loadSettings()}
async function addSelf(kind,identifier,btn){const persona=btn.parentNode.querySelector('select').value;
 await P('/self',{kind,identifier,persona});toast('已纳入「我的」');loadSettings()}
async function setPersona(kind,identifier,persona){await P('/self/persona',{kind,identifier,persona});toast('persona 已改: '+persona)}
async function saveBackend(tool){const url=document.getElementById('be_'+tool).value.trim();
 const r=await P('/backend',{tool,url});
 if(r.ok)toast(tool+' 地址已存：'+(r.url||'(空→默认)')+'（下次拉取生效，poll 自动）');else alert('保存失败：'+(r.error||'未知'));}
function saveAuto(){localStorage.setItem('amr_autosend',document.getElementById('autosend_on').checked?'1':'0');
 localStorage.setItem('amr_autosend_secs',String(Math.max(1,parseInt(document.getElementById('autosend_secs').value,10)||5)));
 toast('发送设置已存');}
async function loadSafePhrases(){let ps;try{ps=await E('/safe-phrases');}catch(e){ps=[];}
 document.getElementById('safe_phrases').innerHTML=(ps||[]).map(p=>'<div class=row><span class=tag>'+esc(p.kind||'')+'</span> '+esc(p.pattern)+' '+(p.builtin ? '<span class=tag title="内置·不可删">🔒</span>' : '<span class=x onclick="delSafePhrase('+p.id+')">✕</span>')+'</div>').join('')||'<div class=tag style=padding:6px>(还没灌话术，自动发安全区为空 → 一切都交人)</div>';}
async function addSafePhrase(){const pat=document.getElementById('sp_pattern').value.trim();if(!pat){toast('先填话术');return;}await P('/safe-phrases',{pattern:pat,kind:document.getElementById('sp_kind').value.trim()});document.getElementById('sp_pattern').value='';document.getElementById('sp_kind').value='';toast('已灌入话术库 🛡️');loadSafePhrases();}
async function delSafePhrase(id){await P('/safe-phrases/delete',{id});loadSafePhrases();}
async function saveProfile(){await P('/self-profile',{profile:document.getElementById('selfprofile').value});toast('「我是谁」已保存 🧬')}
async function removeSelf(kind,identifier){await P('/self/remove',{kind,identifier});loadSettings()}
async function runReunify(reset){
 if(reset&&!confirm('复位会清掉自动归并(保留人工确认的),确定?'))return;
 const r=await P('/reunify',{reset});
 document.getElementById('reuniout').textContent=`✅ 连接 ${r.linked} · 候选 ${r.candidates}`;
 loadProactive();loadPersons();loadCands()}
async function watchPerson(pid){await P('/watch',{person_id:pid,on:true});toast('已关注 ⭐');loadProactive()}
async function connectChannel(pid,btn){const inp=btn.parentNode.querySelector('input'),chat_id=inp.value.trim();
 if(!chat_id){alert('先填微信 chat_id');return}
 const r=await P('/connect',{person_id:pid,chat_id});
 if(r.ok){toast('已连渠道 🔗 ('+r.msgs+'条)');inp.value='';loadPersons()}else{alert('连失败：'+(r.error||'未知'))}}
function goHome(){document.getElementById('title').textContent='选择会话';window.CURCONV=null;cancelSend();document.body.classList.remove('m-chat','m-matters');
 _hidePanels();   // 关所有覆盖面板(归一/偏好/自动/设置), 否则点「收件箱」回不去(面板还盖着)
 document.getElementById('msgs').innerHTML='';document.getElementById('suggest').innerHTML='';
 document.getElementById('matters').innerHTML='';
 loadProactive();loadPersons();loadCands();loadConvs();loadOutbox()}
function toggleMatters(){document.body.classList.toggle('m-matters');}
async function confirmLink(cid,pid){await P('/link',{conversation_id:cid,person_id:pid});
 goHome()}
window.AUTOTIMERS=window.AUTOTIMERS||{};
window.AUTODRAFTS=window.AUTODRAFTS||{};   // cid -> draft text (never inlined into onclick → no escaping fragility)
function _acRow(cid,inner){return '<div class=row id="acrow_'+cid+'">'+inner+'</div>';}
async function loadAutocomms(){let cs;try{cs=await E('/auto-replies');}catch(e){cs=[];}
 const ic={shadow:'👁',arm:'⏳',human:'🙋'};
 const ksOn=(document.getElementById('ks_on')||{}).checked;
 window.AUTODRAFTS={};
 document.getElementById('ac_list').innerHTML=(cs||[]).map(c=>{
  const cid=c.conversation_id,head=(ic[c.action]||'')+' [会话 '+cid+'] ';
  // 闸二把情绪/敏感场景交回人 → 不是死胡同: 一键让 AI 拟 3 版有温度的话术供你挑发。
  if(c.action==='human')return _acRow(cid,head+'交你回 ('+esc(c.reason||'')+') '+
   '<button class=go onclick="humanAssist('+cid+')">✨ AI 拟有温度的话术</button>');
  if(c.action==='shadow')return _acRow(cid,head+'本来会回(观察·不发):「'+esc(c.draft||'')+'」');
  // action==='arm' — 监管挡: 人点击 → 倒计时否决窗 → 真发(outbox/confirm)
  if(c.action==='arm'){
   if(ksOn)return _acRow(cid,head+'🛑 全局刹车中(不可自动发):「'+esc(c.draft||'')+'」');
   window.AUTODRAFTS[cid]=c.draft||'';   // stash draft; lookup by cid at arm/send time
   return _acRow(cid,head+'将自动发:「'+esc(c.draft||'')+'」 '+
    '<button class=go onclick="armAuto('+cid+')">▶ 将自动发（倒计时可否决）</button>');
  }
  return _acRow(cid,head+'「'+esc(c.draft||'')+'」');
 }).join('')
  ||'<div class=tag style=padding:6px>(暂无候选 — 所有会话默认关；去会话挡位设 observe/supervised)</div>';}
async function humanAssist(cid){
 // 「交人」(尤其闸二判情绪/敏感高风险) 不该断在这里 → 进该会话 + AI 拟 3 版有温度话术,
 // 你挑一版有感情的→发。决策权在你, AI 把"有温度的话"备好, 顺势往下走。
 document.getElementById('autocomms').classList.add('hide');   // 收起自动面板, 露出会话
 await openConv(cid);                                          // 进会话(中栏消息+右栏话术区), CURCONV=cid
 toast('正在为你拟有温度的话术…');
 await aiDraft();                                              // /draft-assist → 3版(稳妥/直接/有温度) → 右栏「✨话术」, 用此版→发
}
function armAuto(cid){
 if((document.getElementById('ks_on')||{}).checked){toast('🛑 全局刹车中，不能自动发');return;}
 const draft=window.AUTODRAFTS[cid];if(draft==null){toast('草稿已失效，请刷新');return;}
 if(window.AUTOTIMERS[cid]){clearInterval(window.AUTOTIMERS[cid]);delete window.AUTOTIMERS[cid];}
 const row=document.getElementById('acrow_'+cid);if(!row)return;
 const cfg=autoCfg();let left=Math.max(1,cfg.secs);
 const tick=()=>{row.innerHTML='⏳ <b>'+left+'s</b> 后自动发给 [会话 '+cid+']：「'+esc(draft)+'」 '+
   '<button class=go onclick="doAutoSend('+cid+')">立即发</button> '+
   '<button onclick="vetoAuto('+cid+')">✕ 否决</button>';};
 tick();
 window.AUTOTIMERS[cid]=setInterval(()=>{left--;if(left<=0){doAutoSend(cid);}else{tick();}},1000);
}
function vetoAuto(cid){if(window.AUTOTIMERS[cid]){clearInterval(window.AUTOTIMERS[cid]);delete window.AUTOTIMERS[cid];}
 toast('已否决，未发送');loadAutocomms();}
async function doAutoSend(cid){
 if(window.AUTOTIMERS[cid]){clearInterval(window.AUTOTIMERS[cid]);delete window.AUTOTIMERS[cid];}
 if((document.getElementById('ks_on')||{}).checked){toast('🛑 全局刹车中，已拦下自动发');loadAutocomms();return;}
 const draft=window.AUTODRAFTS[cid];if(draft==null){toast('草稿已失效，未发送');loadAutocomms();return;}
 try{const row=await P('/outbox',{conversation_id:cid,body:draft});
  const r=await P('/outbox/confirm',{id:row.id});
  if(r.ok){toast('已自动发送 ✅');}else{toast('发送失败：'+(r.error||'未知'));}
 }catch(e){toast('发送失败：'+e);}
 loadAutocomms();loadOutbox();}
async function toggleKill(on){await P('/killswitch',{on});toast(on?'🛑 已全局刹车':'已恢复自动回');loadAutocomms();}
function toggleAutocomms(){const p=document.getElementById('autocomms');const was=p.classList.contains('hide');_hidePanels();if(was){p.classList.remove('hide');loadAutocommsPanel();}}
function loadAutocommsPanel(){
 const dial=document.getElementById('ac_dial');
 const lbl=document.getElementById('ac_curconv');
 if(window.CURCONV){lbl.textContent='会话 #'+window.CURCONV;dial.disabled=false;}
 else{lbl.textContent='（未选会话）';dial.disabled=true;}
 loadAutocomms();}
async function setAutonomyDial(mode){if(!window.CURCONV){toast('先选会话');return;}
 const r=await P('/autonomy',{conversation_id:window.CURCONV,mode});
 if(r.ok){toast('挡位已设为 '+mode);loadAutocomms();}else{toast('设挡位失败：'+(r.error||''));document.getElementById('ac_dial').value='off';}}
document.getElementById('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
 const h=await E('/search','q='+encodeURIComponent(e.target.value));
 document.getElementById('msgs').innerHTML='<h3>搜索结果 ('+h.length+')</h3>'+h.map(x=>`<div class=m>
 <span class=s>${esc(x.sender)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')})
document.getElementById('msgs').addEventListener('scroll',()=>{if(window.CURCONV!=null)window.SCROLLPOS[window.CURCONV]=document.getElementById('msgs').scrollTop;});
loadProactive();loadPersons();loadCands();loadConvs();loadOutbox();applySkin();
</script><div class=fab onclick="createMatter()">＋ 记一件事</div>
<div id=amrver title="AMR 版本（消费侧）· /api/version 看消费清单">AMR v__AMR_VERSION__</div>
<style>#amrver{position:fixed;right:8px;bottom:6px;z-index:5;font-size:10px;color:var(--fg2);opacity:.55;pointer-events:none;letter-spacing:.3px}</style>
</body></html>"""


def _index_html():
    """Render the inbox page with the live AMR version baked in. Single-sourced from
    `jl.__version__`; surfaced as a subtle always-visible badge (present in every skin,
    incl. the default 今日简报) so 用户/运维 always see which AMR they're looking at."""
    return INDEX_HTML.replace("__AMR_VERSION__", __version__)
