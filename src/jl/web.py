"""Read-only web inbox: stdlib http.server over the AMR store.

Pure data handlers (api_*) are unit-tested; serve() wires them to BaseHTTPRequestHandler.
Read-only by design — replying/sending is sub-project E (human-in-the-loop outbox).
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import db


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
                    cid = int(u.path.split("/")[3])
                    return self._send(200, api_messages(conn, cid))
                if u.path == "/api/search":
                    return self._send(200, api_search(conn, params.get("q", "")))
                return self._send(404, {"error": "not found"})
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
#hdr{padding:8px 12px;border-bottom:1px solid #ddd;display:flex;gap:8px;align-items:center}
#msgs{flex:1;overflow:auto;padding:12px}.m{margin:6px 0}.m .s{font-weight:600;color:#333}.m .t{color:#aaa;font-size:11px;margin-left:6px}
input{padding:6px 8px;border:1px solid #ccc;border-radius:6px;width:100%}
</style></head><body>
<div id=side><div style=padding:8px><input id=q placeholder="🔍 搜索消息 (回车)"></div><div id=list></div></div>
<div id=main><div id=hdr><b id=title>选择会话</b></div><div id=msgs></div></div>
<script>
const E=(s,p='')=>fetch('/api'+s+(p?'?'+p:'')).then(r=>r.json());
function esc(s){return (s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmt(ts){return ts?new Date(ts*1000).toLocaleString('zh-CN'):''}
async function loadConvs(){const c=await E('/conversations');document.getElementById('list').innerHTML=
 c.map(x=>`<div class=conv onclick="openConv(${x.id})"><div class=n>${esc(x.name||x.chat_id)}</div>
 <div class=p>${esc(x.platform)} · ${fmt(x.last_activity_at)}</div></div>`).join('')}
async function openConv(id){const m=await E('/conversations/'+id+'/messages');
 document.getElementById('msgs').innerHTML=m.map(x=>`<div class=m><span class=s>${esc(x.sender)}</span>
 <span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')||'(无消息)'}
document.getElementById('q').addEventListener('keydown',async e=>{if(e.key!=='Enter')return;
 const h=await E('/search','q='+encodeURIComponent(e.target.value));
 document.getElementById('msgs').innerHTML='<h3>搜索结果 ('+h.length+')</h3>'+h.map(x=>`<div class=m>
 <span class=s>${esc(x.sender)}</span><span class=t>${fmt(x.ts)}</span><div>${esc(x.content)}</div></div>`).join('')})
loadConvs()
</script></body></html>"""
