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


def api_conversations(conn, params):
    """Active conversations (unmuted) by default; ?muted=1 shows muted ones."""
    if params.get("muted") == "1":
        return db.get_conversations(conn, muted=True)
    return db.get_conversations(conn, muted=False)


def api_messages(conn, conversation_id, limit=200):
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY ts ASC LIMIT ?",
        (conversation_id, limit),
    ).fetchall()
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


def api_dismiss_suggestion(conn, payload):
    db.set_suggestion_status(conn, int(payload["id"]), "dismissed")
    return {"ok": True}


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
                return self._send(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
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
                if u.path == "/api/proactive":
                    return self._send(200, api_proactive(conn))
                if u.path == "/api/matters":
                    return self._send(200, api_matters(conn, params))
                if u.path == "/api/outbox":
                    return self._send(200, api_list_outbox(conn))
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
                              "/api/matters", "/api/matters/status", "/api/diagnose"):
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
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,system-ui,sans-serif;display:flex;height:100vh}
#side{width:300px;border-right:1px solid #ddd;overflow:auto}#main{flex:1;display:flex;flex-direction:column}
.conv{padding:8px 12px;border-bottom:1px solid #eee;cursor:pointer}.conv:hover{background:#f5f5f5}
.conv .n{font-weight:600}.conv .p{color:#888;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sec{padding:6px 10px;font-weight:600;background:#fafafa;border-bottom:1px solid #eee;color:#555;font-size:13px}
.badge{display:inline-block;padding:1px 6px;margin-left:4px;border-radius:8px;background:#eef;color:#446;font-size:11px}
.cand{padding:8px 12px;border-bottom:1px solid #eee}.cand .p{color:#888;font-size:12px}
.cand button{margin-top:4px;padding:3px 10px;border:1px solid #4a8;background:#e8f7ee;color:#176;border-radius:6px;cursor:pointer}
.cand button:hover{background:#d4f0e0}
#hdr{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:8px;align-items:center}
#msgs{flex:1;overflow:auto;padding:12px}.m{margin:6px 0}.m .s{font-weight:600;color:#333}.m .t{color:#aaa;font-size:11px;margin-left:6px}
input{padding:6px 8px;border:1px solid #ccc;border-radius:6px;width:100%}
.ob{padding:8px 12px;border-bottom:1px solid #eee}.ob .p{color:#888;font-size:12px}.ob .b{margin:3px 0}
.ob button{margin:4px 6px 0 0;padding:3px 10px;border-radius:6px;cursor:pointer;border:1px solid #ccc;background:#f7f7f7}
.ob .send{border-color:#4a8;background:#e8f7ee;color:#176}.ob .send:hover{background:#d4f0e0}
#replybox{border-top:1px solid #ddd;padding:8px 12px;display:flex;gap:8px;align-items:flex-start}
#replybox textarea{flex:1;padding:6px 8px;border:1px solid #ccc;border-radius:6px;font:inherit;resize:vertical}
#replybox button{padding:6px 12px;border:1px solid #48a;background:#e8f0fb;color:#147;border-radius:6px;cursor:pointer;white-space:nowrap}
#right{width:330px;border-left:1px solid #ddd;overflow:auto;display:flex;flex-direction:column}
.matter{padding:8px 12px;border-bottom:1px solid #eee}.matter .h{font-weight:600}
.matter .dg{color:#a40;font-size:12px;margin:3px 0}.matter .cm{color:#555;font-size:12px}
.matter button{margin-top:4px;padding:2px 8px;border:1px solid #ccc;background:#f7f7f7;border-radius:6px;cursor:pointer;font-size:12px}
</style></head><body>
<div id=side>
 <div class=sec>📞 该联系谁</div><div id=proactive></div>
 <div class=sec>👤 联系人</div><div id=persons></div>
 <div class=sec>📤 待发送 outbox</div><div id=outbox></div>
 <div class=sec>🔗 待确认归并</div><div id=cands></div>
 <div class=sec>💬 会话</div>
 <div style=padding:8px><input id=q placeholder="🔍 搜索消息 (回车)"></div><div id=list></div></div>
<div id=main><div id=hdr><button onclick="goHome()" style="margin-right:8px">← 收件箱</button><b id=title>选择会话</b></div><div id=msgs></div>
 <div id=replybox><textarea id=reply rows=2 placeholder="点右侧「用此版」填入，可改；「暂存待发」后去左边确认真发"></textarea>
 <button onclick="draft()">暂存待发 →</button>
 <button onclick="aiDraft()">✨ AI 拟话术</button></div></div>
<div id=right>
 <div class=sec>🗂 事（这条会话）<button onclick="createMatter()" style="float:right;font-size:12px">＋记一件事</button></div>
 <div id=matters></div>
 <div class=sec>✨ 话术</div><div id=suggest></div></div>
<script>
const TOK=new URLSearchParams(location.search).get('token')||'';
const E=(s,p='')=>{const qs=[p,TOK&&'token='+encodeURIComponent(TOK)].filter(Boolean).join('&');return fetch('/api'+s+(qs?'?'+qs:'')).then(r=>r.json())};
const P=(s,body)=>{const qs=TOK?'?token='+encodeURIComponent(TOK):'';return fetch('/api'+s+qs,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN'):''}
async function loadConvs(){const c=await E('/conversations');document.getElementById('list').innerHTML=
 c.map(x=>`<div class=conv onclick="openConv(${x.id})"><div class=n>${esc(x.name||x.chat_id)}</div>
 <div class=p>${esc(x.platform)} · ${fmt(x.last_activity_at)}</div></div>`).join('')}
async function openConv(id){window.CURCONV=id;const m=await E('/conversations/'+id+'/messages');
 document.getElementById('msgs').innerHTML=m.map(x=>`<div class=m><span class=s>${esc(x.sender)}</span>
 <span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')||'(无消息)';
 loadSuggestions(id);loadMatters(id)}
async function loadMatters(id){const ms=await E('/matters','conversation='+id);
 document.getElementById('matters').innerHTML=ms.map(m=>{
 const d=m.diagnosis||{};const dg=d['一句话诊断']?`<div class=dg>🩺 ${esc(d['一句话诊断'])}</div>`:'';
 const cm=(m.commitments||[]).map(c=>`<div class=cm>📌 ${esc(c.text)} <span class=badge>${esc(c.status)}</span></div>`).join('');
 const dx=`<button onclick="diagnose(${m.id})">🩺 诊断</button>`;
 const act=m.status==='open'?`<button onclick="matterStatus(${m.id},'handled')">✓ 办结</button>`:`<span class=badge>${esc(m.status)}</span>`;
 return `<div class=matter><div class=h>${esc(m.title)} ${m.kind?`<span class=badge>${esc(m.kind)}</span>`:''}</div>${dg}${cm}${dx} ${act}</div>`}).join('')||'<div class=p style=padding:8px>(暂无事项，＋记一件事)</div>'}
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
function useDraft(id){document.getElementById('reply').value=window.SUG[id]||'';}
async function aiDraft(){if(!window.CURCONV){alert('先选会话');return}
 const r=await P('/draft-assist',{conversation_id:window.CURCONV});
 if(!r.ok){alert(r.error||'LLM 不可用');return}loadSuggestions(window.CURCONV)}
async function dismissSug(id){await P('/suggestions/dismiss',{id});loadSuggestions(window.CURCONV)}
async function draft(){if(!window.CURCONV){alert('先选会话');return}
 const ta=document.getElementById('reply'),body=ta.value.trim();if(!body)return;
 await P('/outbox',{conversation_id:window.CURCONV,body});ta.value='';loadOutbox()}
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
 document.getElementById('msgs').innerHTML=m.map(x=>`<div class=m><span class=s>${esc(x.sender)}</span>
 <span class=badge>${esc(x.platform)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')||'(无消息)'}
async function loadProactive(){const ps=await E('/proactive');document.getElementById('proactive').innerHTML=
 ps.map(p=>{const tag=p.red?'🔴':(p.watch?'⭐':'');const days=p.days!=null?p.days+'天':'';
 if(p.missing_channel)return `<div class=conv><div class=n>${tag} ${esc(p.name)} <span class=badge>缺渠道·救补</span></div><div class=p>${days} · 补微信/飞书号再拟</div></div>`;
 return `<div class=conv onclick="openConv(${p.conversation_id})"><div class=n>${tag} ${esc(p.name)} ${p.openers?`<span class=badge>${p.openers}版开场</span>`:''}</div><div class=p>${days}${p.openers?' · 点开挑/改/发':' · 待拟'}</div></div>`}).join('')||'<div class=p style=padding:8px>(无 关注/🔴 待联络)</div>'}
async function loadCands(){const cs=await E('/merge-candidates');document.getElementById('cands').innerHTML=
 cs.map(c=>`<div class=cand><div class=n>${esc(c.name)} <span class=badge>${esc(c.platform)}</span></div>
 ${c.candidates.map(p=>`<div class=p>→ ${esc(p.name||p.id)}</div>
 <button onclick="confirmLink(${c.conversation_id},'${esc(p.id)}')">确认归并到 ${esc(p.name||p.id)}</button>`).join('')}
 </div>`).join('')||'<div class=p style=padding:8px>(无待确认项)</div>'}
function goHome(){document.getElementById('title').textContent='选择会话';
 document.getElementById('msgs').innerHTML='';document.getElementById('suggest').innerHTML='';
 document.getElementById('matters').innerHTML='';
 loadProactive();loadPersons();loadCands();loadConvs();loadOutbox()}
async function confirmLink(cid,pid){await P('/link',{conversation_id:cid,person_id:pid});
 goHome()}
document.getElementById('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
 const h=await E('/search','q='+encodeURIComponent(e.target.value));
 document.getElementById('msgs').innerHTML='<h3>搜索结果 ('+h.length+')</h3>'+h.map(x=>`<div class=m>
 <span class=s>${esc(x.sender)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')})
loadProactive();loadPersons();loadCands();loadConvs();loadOutbox()
</script></body></html>"""
