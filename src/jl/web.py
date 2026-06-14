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
                return self._send(404, {"error": "not found"})
            finally:
                conn.close()

        def do_POST(self):
            u = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(u.query).items()}
            if not _auth_ok(self.headers, params):
                return self._send(401, {"error": "unauthorized"})
            if u.path not in ("/api/ingest", "/api/link"):
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
                return self._send(200, api_link(conn, payload))
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
</style></head><body>
<div id=side>
 <div class=sec>👤 联系人</div><div id=persons></div>
 <div class=sec>🔗 待确认归并</div><div id=cands></div>
 <div class=sec>💬 会话</div>
 <div style=padding:8px><input id=q placeholder="🔍 搜索消息 (回车)"></div><div id=list></div></div>
<div id=main><div id=hdr><button onclick="goHome()" style="margin-right:8px">← 收件箱</button><b id=title>选择会话</b></div><div id=msgs></div></div>
<script>
const TOK=new URLSearchParams(location.search).get('token')||'';
const E=(s,p='')=>{const qs=[p,TOK&&'token='+encodeURIComponent(TOK)].filter(Boolean).join('&');return fetch('/api'+s+(qs?'?'+qs:'')).then(r=>r.json())};
const P=(s,body)=>{const qs=TOK?'?token='+encodeURIComponent(TOK):'';return fetch('/api'+s+qs,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}).then(r=>r.json())};
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN'):''}
async function loadConvs(){const c=await E('/conversations');document.getElementById('list').innerHTML=
 c.map(x=>`<div class=conv onclick="openConv(${x.id})"><div class=n>${esc(x.name||x.chat_id)}</div>
 <div class=p>${esc(x.platform)} · ${fmt(x.last_activity_at)}</div></div>`).join('')}
async function openConv(id){const m=await E('/conversations/'+id+'/messages');
 document.getElementById('msgs').innerHTML=m.map(x=>`<div class=m><span class=s>${esc(x.sender)}</span>
 <span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')||'(无消息)'}
async function loadPersons(){const ps=await E('/persons');document.getElementById('persons').innerHTML=
 ps.map(p=>`<div class=conv onclick="openPerson('${esc(p.id)}',this)"><div class=n>${esc(p.name||p.id)}
 ${(p.channels||[]).map(ch=>`<span class=badge>${esc(ch)}</span>`).join('')}</div>
 <div class=p>${p.conversations} 个会话 · ${fmt(p.last_activity_at)}</div></div>`).join('')||'<div class=p style=padding:8px>(暂无已归并联系人)</div>'}
async function openPerson(id){const m=await E('/persons/'+encodeURIComponent(id)+'/timeline');
 document.getElementById('title').textContent='👤 '+id+' 合并时间线';
 document.getElementById('msgs').innerHTML=m.map(x=>`<div class=m><span class=s>${esc(x.sender)}</span>
 <span class=badge>${esc(x.platform)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')||'(无消息)'}
async function loadCands(){const cs=await E('/merge-candidates');document.getElementById('cands').innerHTML=
 cs.map(c=>`<div class=cand><div class=n>${esc(c.name)} <span class=badge>${esc(c.platform)}</span></div>
 ${c.candidates.map(p=>`<div class=p>→ ${esc(p.name||p.id)}</div>
 <button onclick="confirmLink(${c.conversation_id},'${esc(p.id)}')">确认归并到 ${esc(p.name||p.id)}</button>`).join('')}
 </div>`).join('')||'<div class=p style=padding:8px>(无待确认项)</div>'}
function goHome(){document.getElementById('title').textContent='选择会话';
 document.getElementById('msgs').innerHTML='';loadPersons();loadCands();loadConvs()}
async function confirmLink(cid,pid){await P('/link',{conversation_id:cid,person_id:pid});
 goHome()}
document.getElementById('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
 const h=await E('/search','q='+encodeURIComponent(e.target.value));
 document.getElementById('msgs').innerHTML='<h3>搜索结果 ('+h.length+')</h3>'+h.map(x=>`<div class=m>
 <span class=s>${esc(x.sender)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')})
loadPersons();loadCands();loadConvs()
</script></body></html>"""
