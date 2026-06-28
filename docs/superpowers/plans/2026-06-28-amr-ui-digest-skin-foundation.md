# AMR UI 地基:今日简报 + 可换皮肤架构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AMR web 的默认落地页改成「今日简报」(数字员工报告 + 需你拍板),并把界面重构成「数据层之上的可换皮肤」骨架,先落地皮肤①简报(桌面 + 手机)。

**Architecture:** 新增纯逻辑模块 `src/jl/digest.py`,只用**现有** db 数据(matters / persons 染色 / proactive 队列 / outbox)聚合出 5 份报告 + 「需你拍板」清单;尚无后端的维度(营销线索 / 自动沟通安全)显式标 `pending_backend` 占位(不假数据)。web 加只读端点 `GET /api/digest`。前端在 `INDEX_HTML` 引入**皮肤层**:`localStorage['amr_skin']` 决定渲染哪套,默认 `digest`(简报 L0);旧三栏收件箱保留为皮肤 `inbox`。简报视图消费 `/api/digest`,「需你拍板」里**已有后端的动作**(发草稿走 `/api/outbox/confirm`、撩走现有 proactive)真接,**无后端的**置灰标「待后端」。

**Tech Stack:** Python 3.10 stdlib(`http.server` / `sqlite3`),前端 `INDEX_HTML` 内原生 HTML/CSS/JS(无框架)。纯逻辑用 pytest;CSS/JS 用 Claude-in-Chrome 在桌面 + 手机视口实测。

**关键事实(实现前必读):**
- 纯数据 handler 都是 `api_*(conn, ...)`,在 `tests/` 里 pytest 覆盖(模式:连临时 db、用公开 db 函数播种、断言)。
- 现有可复用 db/assist/weighting 接口(本计划只用这些,不新增 schema):
  - `db.get_matters(conn, person_id=None, conversation_id=None, status=None)` → 事列表(含 `status` 字段)。
  - `db.persons_overview(conn)` / `db.get_persons(conn)` → person(含 `threshold_days`、`category`、`watch`)。
  - `weighting.color(days, threshold_days)` → `"🟢"/"🟡"/"🔴"`。
  - `assist._person_days(conn, person_id)` → 距今多少天没互动(float|None)。
  - `assist.primary_conversation(conn, person_id)` → 主会话 dict|None。
  - `db.get_suggestions(conn, conversation_id, kind=None)` → 草稿/opener。
  - `db.get_outbox(conn, status="pending")` → 待发队列。
  - `api_proactive(conn)`(web.py:127)已聚合「该主动联络的人」——简报「关系报告 / 撩」直接复用其形状。
- 路由:`do_GET`(web.py:341)按 path 分发;新 GET 端点加一行;`INDEX_HTML` 从 web.py:465 起。
- 鉴权:`_auth_ok`(web.py:323)`?token=` 或 `Authorization: Bearer`;`JL_WEB_TOKEN` 未设则放行(本地)。
- 主题已用 CSS 变量(web.py:468 起 `:root`),新皮肤复用这些变量,不写死颜色。
- 设计正本:`docs/superpowers/specs/2026-06-28-supervised-agentic-router-design.md` §8(皮肤/简报/手机端)。mockup:`docs/superpowers/specs/ui-mockups/mockup-0-daily-digest.html`(桌面)、`mockup-mobile.html`(手机三屏)。
- 部署 + 浏览器验证地址见仓库现有部署脚本/收件箱 URL(沿用 06-26 plan 的 scp+systemctl 流程)。

**全局约束:** DRY(报告卡渲染抽一个 `renderDigest`)、YAGNI(本计划不做 ④/② 皮肤、不做营销线索后端)、TDD(`digest.py` 先写失败测试)、公开仓库无 PII(测试用合成 `张三/李四`、`wxid_test_*`)、每个 Task 一次提交、LLM-optional(简报全程零 LLM)。

---

## File Structure

- **Create `src/jl/digest.py`** — 唯一职责:把现有 db 数据聚合成「今日简报」结构(5 报告 + 需你拍板)。纯函数,无 I/O 之外副作用,无 LLM。
- **Create `tests/test_digest.py`** — `digest.py` 的单测。
- **Modify `src/jl/web.py`**:
  - 加 `api_digest(conn)`(薄包 `digest.build`)+ `do_GET` 路由一行。
  - `INDEX_HTML`:加皮肤层(`amr_skin` localStorage + 皮肤路由)、简报视图(CSS + `renderDigest` + 数据拉取)、手机响应式(底 tab + 卡片流)。
- **不改** db schema、send 审计路径、现有 `api_*` 数据函数。

---

## Task 1: `digest.py` — 简报聚合(纯逻辑)

**Files:**
- Create: `src/jl/digest.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_digest.py`:
```python
import os, tempfile
from jl import db, digest


def _seed():
    """临时 db + 最小合成数据(无 PII)。"""
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = db.connect(path)
    # 两个事:一个进行、一个完结(成交)
    db.create_matter(conn, title="向张三回款", kind="落地", person_ids=[], conversation_ids=[])
    mid2 = db.create_matter(conn, title="给李四报价", kind="落地", person_ids=[], conversation_ids=[])
    db.set_matter_status(conn, mid2, "完结")
    conn.commit()
    return conn, path


def test_build_has_five_reports_and_gate():
    conn, path = _seed()
    try:
        d = digest.build(conn)
        # 结构:5 份报告 + 需你拍板清单
        assert set(d["reports"]) >= {"sales", "marketing", "relationship", "progress", "meta"}
        assert isinstance(d["gate"], list)
        # 销售报告从现有 matters 聚合:统计了 2 件事
        assert d["reports"]["sales"]["counts"]["total"] == 2
        # 尚无后端的维度显式标 pending,不假数据
        assert d["reports"]["marketing"]["pending_backend"] is True
        # LLM-optional:有数字/清单,narrative 可空
        assert "narrative" in d["reports"]["sales"]
    finally:
        conn.close(); os.unlink(path)


def test_relationship_report_reuses_proactive_shape():
    conn, path = _seed()
    try:
        d = digest.build(conn)
        rel = d["reports"]["relationship"]
        # 关系报告含染色计数(🔴/🟡/🟢)与「今日建议撩」清单(可空)
        assert set(rel["counts"]) >= {"red", "amber", "green"}
        assert isinstance(rel["nudge"], list)
    finally:
        conn.close(); os.unlink(path)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_digest.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'jl.digest'`）。

- [ ] **Step 3: 实现 `src/jl/digest.py`**

```python
"""今日简报聚合(L0 落地页)。纯逻辑、零 LLM、只读现有 db 数据。

每份报告 = {counts, items|nudge|..., narrative, pending_backend}。
narrative 留空字符串——LLM 可后续填(assist),无 LLM 也出数字/清单。
尚无后端的维度(营销线索 / 自动沟通安全细项)标 pending_backend=True,不假数据。
"""
from __future__ import annotations

from . import db, weighting, assist


def _sales(conn):
    matters = db.get_matters(conn)
    by = {}
    for m in matters:
        by[m["status"]] = by.get(m["status"], 0) + 1
    return {"counts": {"total": len(matters), "by_status": by},
            "items": matters[:8], "narrative": "", "pending_backend": False}


def _relationship(conn):
    red = amber = green = 0
    nudge = []
    for p in db.get_persons(conn):
        days = assist._person_days(conn, p["id"])
        c = weighting.color(days, p.get("threshold_days"))
        if c == "🔴": red += 1
        elif c == "🟡": amber += 1
        else: green += 1
        if p.get("watch") or c == "🔴":
            nudge.append({"person_id": p["id"], "name": p["name"],
                          "days": round(days, 1) if days is not None else None, "color": c})
    return {"counts": {"red": red, "amber": amber, "green": green},
            "nudge": nudge[:12], "narrative": "", "pending_backend": False}


def _progress(conn):
    matters = db.get_matters(conn)
    open_m = [m for m in matters if m["status"] not in ("完结", "丢弃")]
    return {"counts": {"open": len(open_m)},
            "items": open_m[:8], "narrative": "", "pending_backend": False}


def _meta(conn):
    sent = len(db.get_outbox(conn, status="sent")) if _has_status(conn, "sent") else 0
    pending = len(db.get_outbox(conn, status="pending"))
    return {"counts": {"sent": sent, "pending": pending},
            "narrative": "", "pending_backend": False}


def _has_status(conn, status):
    try:
        db.get_outbox(conn, status=status)
        return True
    except Exception:
        return False


def _marketing(conn):
    # 营销/线索后端(大群浮线索)尚未实现 → 显式占位,不假数据。
    return {"counts": {}, "items": [], "narrative": "",
            "pending_backend": True, "note": "营销线索后端待实现(设计 §1.5)"}


def build(conn):
    """组装今日简报。gate = 需你拍板清单(从各报告浮显著项,带可执行 action 标记)。"""
    reports = {"sales": _sales(conn), "marketing": _marketing(conn),
               "relationship": _relationship(conn), "progress": _progress(conn),
               "meta": _meta(conn)}
    gate = []
    # 已有后端的可拍板项:待发草稿(走 outbox/confirm)
    for row in db.get_outbox(conn, status="pending"):
        gate.append({"kind": "send_draft", "actionable": True,
                     "outbox_id": row["id"],
                     "text": f"待发给会话 {row['conversation_id']}：{row['body'][:40]}"})
    # 关系:今日建议撩(复用 relationship.nudge);动作走现有 proactive 流程
    for n in reports["relationship"]["nudge"][:5]:
        gate.append({"kind": "nudge", "actionable": True,
                     "person_id": n["person_id"],
                     "text": f"{n['name']} 已 {n['days']} 天未联系，建议主动撩"})
    return {"reports": reports, "gate": gate}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_digest.py -q`
Expected: PASS（2 passed）。若 `db.get_persons`/`assist._person_days` 等签名与上文不符，按 `src/jl/db.py`、`src/jl/assist.py` 实际签名微调实现(测试断言的是**结构**,不依赖具体值)。

- [ ] **Step 5: 全量回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿(现有 298 + 新 2 = 300 passed)。

- [ ] **Step 6: Commit**

```bash
git add src/jl/digest.py tests/test_digest.py
git commit -m "feat(digest): daily 简报 aggregation over existing data (5 reports + gate, LLM-optional)"
```

---

## Task 2: `GET /api/digest` 端点

**Files:**
- Modify: `src/jl/web.py`（加 `api_digest` + `do_GET` 路由 + import）
- Test: `tests/test_web_digest.py`

- [ ] **Step 1: 写失败测试**

`tests/test_web_digest.py`:
```python
import os, tempfile
from jl import db, web


def test_api_digest_shape():
    fd, path = tempfile.mkstemp(suffix=".db"); os.close(fd)
    conn = db.connect(path)
    try:
        out = web.api_digest(conn)
        assert "reports" in out and "gate" in out
        assert "sales" in out["reports"]
    finally:
        conn.close(); os.unlink(path)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_web_digest.py -q`
Expected: FAIL（`AttributeError: module 'jl.web' has no attribute 'api_digest'`）。

- [ ] **Step 3: 实现端点 + 路由**

在 `src/jl/web.py` 顶部 import 区(`from . import ingest` 那行后)加:
```python
from . import digest as _digest
```
在 `api_generate_drafts`（web.py:315 附近）函数后加:
```python
def api_digest(conn):
    """今日简报(L0 落地页):5 报告 + 需你拍板。纯只读、零 LLM。"""
    return _digest.build(conn)
```
在 `do_GET` 里 `if u.path == "/api/proactive":`（web.py:366）那行之前加:
```python
                if u.path == "/api/digest":
                    return self._send(200, api_digest(conn))
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_web_digest.py -q`
Expected: PASS（1 passed）。

- [ ] **Step 5: Commit**

```bash
git add src/jl/web.py tests/test_web_digest.py
git commit -m "feat(web): GET /api/digest endpoint (daily 简报 read-only)"
```

---

## Task 3: 皮肤层骨架（localStorage 选皮肤 + 默认简报）

**Files:**
- Modify: `src/jl/web.py` `INDEX_HTML`（`<body>` 起始处加皮肤容器 + 顶部皮肤切换 + 启动路由 JS）

- [ ] **Step 1: 加皮肤容器与切换(body 标记)**

在 `INDEX_HTML` 的 `<body>` 内容**最前面**(紧接 `<body...>` 之后、现有 `#side` 之前)插入:
```html
<div id=skinbar style="position:fixed;right:10px;bottom:10px;z-index:50;font-size:12px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:4px 8px">
 皮肤
 <select id=skinsel onchange="setSkin(this.value)">
  <option value=digest>今日简报</option>
  <option value=inbox>收件箱(三栏)</option>
 </select>
</div>
<div id=skin-digest style="display:none;height:100vh;overflow:auto"></div>
```
（现有三栏 `#side`/`#main`/`#right` 整体即皮肤 `inbox`,无需移动,用 JS 显隐。）

- [ ] **Step 2: 加皮肤路由 JS**

在 `INDEX_HTML` 的 `<script>` 区**最前面**(第一个既有函数之前)插入:
```javascript
function curSkin(){return localStorage.getItem('amr_skin')||'digest';}
function applySkin(){const s=curSkin();
 const dig=document.getElementById('skin-digest');
 const inboxEls=['side','main','right'].map(id=>document.getElementById(id)).filter(Boolean);
 if(s==='digest'){dig.style.display='block';inboxEls.forEach(e=>e.style.display='none');loadDigest();}
 else{dig.style.display='none';inboxEls.forEach(e=>e.style.display='');}
 const sel=document.getElementById('skinsel');if(sel)sel.value=s;}
function setSkin(s){localStorage.setItem('amr_skin',s);applySkin();}
```

- [ ] **Step 3: 启动时套用皮肤**

找到 `INDEX_HTML` 里页面初始化处(现有启动会调用如 `loadInbox()`/`load()` 之类的初始化函数;若有 `window.onload` 或末尾立即调用的 init)。在其**末尾**追加一行:
```javascript
applySkin();
```
若找不到统一 init,则在 `<script>` 末尾(`</script>` 前)追加:
```javascript
applySkin();
```
（`loadDigest` 在 Task 4 定义;本 Task 先放一个空壳避免报错——在 Step 2 的 JS 后追加:）
```javascript
function loadDigest(){/* Task 4 实现 */}
```

- [ ] **Step 4: 部署 + 浏览器验证(切皮肤显隐)**

部署后(沿用现有 scp + `systemctl --user restart amr-web`),Claude-in-Chrome navigate 收件箱 URL,然后:
```javascript
localStorage.setItem('amr_skin','digest');applySkin();
const digestShown=getComputedStyle(document.getElementById('skin-digest')).display!=='none';
const sideHidden=getComputedStyle(document.getElementById('side')).display==='none';
setSkin('inbox');
const inboxBack=getComputedStyle(document.getElementById('side')).display!=='none';
setSkin('digest');
JSON.stringify({digestShown,sideHidden,inboxBack})
```
Expected: `digestShown:true, sideHidden:true, inboxBack:true`（默认简报、旧收件箱可切回)。

- [ ] **Step 5: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): skin layer (localStorage amr_skin; default 'digest', legacy 'inbox')"
```

---

## Task 4: 皮肤① 今日简报(桌面渲染)

**Files:**
- Modify: `src/jl/web.py` `INDEX_HTML`（`#skin-digest` 的 CSS + `loadDigest`/`renderDigest`）

参照 mockup:`docs/superpowers/specs/ui-mockups/mockup-0-daily-digest.html`。

- [ ] **Step 1: 加简报 CSS**

在 `INDEX_HTML` `</style>` 之前插入:
```css
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
```

- [ ] **Step 2: 实现 `loadDigest`/`renderDigest`(替换 Task 3 的空壳)**

把 Task 3 Step 3 加的 `function loadDigest(){/* Task 4 实现 */}` 替换为:
```javascript
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
```
（`E`/`P`/`esc`/`toast` 是 `INDEX_HTML` 既有 helper;`gateGo` 对 `send_draft` 走现有 `/api/outbox/confirm`,其余先 toast 占位——对应后端未就绪。）

- [ ] **Step 3: 部署 + 浏览器验证(简报渲染)**

部署后 Claude-in-Chrome navigate 收件箱 URL,然后:
```javascript
localStorage.setItem('amr_skin','digest');applySkin();
await new Promise(r=>setTimeout(r,500));
const root=document.getElementById('skin-digest');
const hasTop=/今日简报/.test(root.textContent);
const cards=root.querySelectorAll('.rc').length;
JSON.stringify({hasTop,cards})
```
Expected: `hasTop:true, cards:5`（5 张报告卡;若有待发草稿,顶部出现「需你拍板」)。`computer screenshot save_to_disk:true` 留证,人工核对版式接近 mockup-0。

- [ ] **Step 4: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): skin① 今日简报 desktop (5 report cards + 需你拍板, /api/digest)"
```

---

## Task 5: 皮肤① 手机端响应式(底 tab + 卡片流)

**Files:**
- Modify: `src/jl/web.py` `INDEX_HTML`（简报手机 media query + 底部 tab 标记 + tab 切换 JS）

参照 mockup:`docs/superpowers/specs/ui-mockups/mockup-mobile.html`(第一屏)。

- [ ] **Step 1: 加底部 tab 标记**

在 Task 3 Step 1 加的 `<div id=skin-digest ...></div>` **之后**插入:
```html
<div id=mtab style="display:none">
 <div class=mt data-skin=digest onclick="setSkin('digest')"><span>📋</span>简报</div>
 <div class=mt data-skin=inbox onclick="setSkin('inbox')"><span>👥</span>人</div>
 <div class=mt onclick="toast('事视图皮肤待落地')"><span>🗂</span>事</div>
 <div class=mt onclick="toast('设置待落地')"><span>⚙</span>我</div>
</div>
```

- [ ] **Step 2: 加手机 media query**

在 `INDEX_HTML` `</style>` 之前插入:
```css
@media(max-width:640px){
 #skinbar{bottom:60px}
 #mtab{display:flex;position:fixed;left:0;right:0;bottom:0;height:50px;background:var(--panel);border-top:1px solid var(--border);z-index:40}
 #mtab .mt{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;font-size:10px;color:var(--fg2);gap:1px}
 #mtab .mt.on{color:var(--blue)}
 #skin-digest{padding-bottom:54px}
 #skin-digest .grid{grid-template-columns:1fr;padding:0 12px 18px}
 #skin-digest .gate{margin:8px 12px 12px}
}
```

- [ ] **Step 3: tab 高亮跟随当前皮肤**

在 `applySkin()` 函数体**末尾**(`if(sel)sel.value=s;` 那行后)追加:
```javascript
 document.querySelectorAll('#mtab .mt').forEach(t=>t.classList.toggle('on',t.dataset.skin===s));
```

- [ ] **Step 4: 部署 + 手机视口验证**

部署后 Claude-in-Chrome `resize_window` 390×800,navigate 收件箱 URL,然后:
```javascript
localStorage.setItem('amr_skin','digest');applySkin();
await new Promise(r=>setTimeout(r,400));
const tabShown=getComputedStyle(document.getElementById('mtab')).display!=='none';
const oneCol=getComputedStyle(document.querySelector('#skin-digest .grid')).gridTemplateColumns.split(' ').length===1;
JSON.stringify({tabShown,oneCol})
```
Expected: `tabShown:true, oneCol:true`（手机出现底 tab、报告卡单列)。`computer screenshot save_to_disk:true` 留证;`resize_window` 回 ≥1000 宽确认桌面端底 tab 隐藏、报告卡多列。

- [ ] **Step 5: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): skin① 简报 mobile responsive (bottom tab + single-column cards)"
```

---

## Task 6: 端到端验收 + 截图留证

**Files:** 无代码改动。

- [ ] **Step 1: 后端回归**

Run: `.venv/bin/python -m pytest -q`
Expected: 全绿（300 passed）。

- [ ] **Step 2: secrets 扫描**

Run: `bash scripts/secrets-scan.sh`
Expected: exit 0。

- [ ] **Step 3: 桌面验收(截图)**

Claude-in-Chrome `resize_window` ≥1280,navigate,`amr_skin=digest`,`computer screenshot save_to_disk:true`。人工核对:顶「今日简报」、5 报告卡、有 pending 草稿则「需你拍板」可放行、切「收件箱」皮肤旧三栏回来。

- [ ] **Step 4: 手机验收(截图)**

`resize_window` 390×800,`computer screenshot save_to_disk:true`。核对:卡片单列、底 tab 在、切 tab 切皮肤。

- [ ] **Step 5: 收尾提交(如有验收期微调)**

```bash
git status --short   # clean 则免
# 若有微调:git add src/jl/web.py && git commit -m "fix(web): digest skin polish from e2e review"
```

---

## Self-Review

**1. Spec coverage（对 §8):**
- 数据层之上可换皮肤 → Task 3(`amr_skin` 皮肤层) ✅
- 默认落地=今日简报(5 报告) → Task 1/2/4 ✅;LLM-optional(零 LLM 出数字清单) → `digest.py` 不 import llm ✅;每份浮「需你拍板 + 一手势控制」→ Task 4 `gate`/`gateGo` ✅;`pending_backend` 占位不假数据 → Task 1 `_marketing` ✅
- 手机端竖向 + 底 tab → Task 5 ✅
- 控制者「看总结/扳手柄」→ 简报 + gate 放行 ✅
- **未覆盖(本计划范围外,留后续 plan)**:②④③ 会话皮肤与 L1/L2 钻取、营销线索后端、lifecycle 引擎、双闸/自动沟通、§14 TLS/EasyTier。见下「后续 plan」。

**2. Placeholder scan:** 每个改码步骤给了完整代码与锚点;`pending_backend` 是**设计明确的占位语义**(非 TODO),已在测试断言。无裸「TODO/类似上文」。

**3. 名一致性:** `digest.build`(py)↔`_digest.build`/`api_digest`(web)↔`/api/digest`(route)↔`E('/digest')`(js) 一致;`amr_skin`/`applySkin`/`setSkin`/`curSkin`/`loadDigest`/`renderDigest`/`gateGo`/`#skin-digest`/`#mtab`/`.rc`/`.gate` 前后一致;报告键 `sales/marketing/relationship/progress/meta` 全程一致。

**注意:** 行号基于当前 `web.py`(804 行);每 Task 改动后行号下移,后续 Task 用函数名/字符串锚点定位。所有前端改动集中在 `INDEX_HTML`,按 Task 顺序串行执行(subagent 也串行,避免同文件冲突)。

---

## 后续 plan(本计划之后,各自独立)

1. **会话皮肤 ④双轴 + ②锚定**(设计 §8 候选 1/2):L1 人/事双轴 + L2 钻取;composer 钉左下、事·北极星钉右上;事视图 lifecycle 看板。依赖本计划的皮肤层。
2. **③ 真对角线**(差异化展示位,可选)。
3. **远程安全接入**(设计 §14):AMR web TLS(stdlib `ssl`,自签默认 + FQDN 真证书)+ EasyTier 部署文档(运维手册)。独立、与 UI 互不阻塞。
4. **简报后端补全**:营销线索(大群浮线索)、lifecycle 引擎(事推进/卡住)、自动沟通安全(meta 报告),逐个把 `pending_backend` 维度接真 —— 依赖记忆层 `recall`(设计 §4,最先做)。
5. **认证·授权·订阅(计费)**(设计 §15):前端登录(Email 注册)+ 授权 + 订阅;架构跃迁(单用户共享 token → 多用户 SaaS);**订阅框架待王总专门沟通后再设计**。与 §14 远程接入相关:远程开放前应先有每用户认证。
