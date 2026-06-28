# AMR 会话皮肤 ④双轴(人视图⇄事视图) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** 在已落地的皮肤层上加第二套皮肤「会话双轴」——保留人视图(像现有三栏)、新增**事视图(lifecycle 看板,按 status 分列)**,并给人视图加隐喻点缀(composer 概念钉左下、当前事卡高亮为右上"北极星")。

**Architecture:** 纯前端,全在 `src/jl/web.py` 的 `INDEX_HTML`。复用 v0.10 已加的皮肤层(`amr_skin`/`applySkin`)。事视图消费**现有** `GET /api/matters`(返回事,含 `status`/`title`/`kind`),按 status 分组渲染成看板;点一件事 → 复用现有会话渲染(该事关联会话的合并 timeline)。新增一个皮肤值 `dual`(人视图⇄事视图,顶部切轴),与 `digest`/`inbox` 并列。零后端改动(lifecycle 引擎是后续 plan;本皮肤按 matters 现有 status 呈现)。

**Tech Stack:** Python stdlib `http.server`(已有),`INDEX_HTML` 原生 HTML/CSS/JS。纯逻辑无新增(只读现有端点);CSS/JS 用 Claude-in-Chrome 在本地起的 AMR server 上实测(同 v0.10 流程:`PYTHONPATH` 起 `web.serve` on 127.0.0.1:8901,合成 db,无 PII)。

**关键事实(实现前必读):**
- 皮肤层(v0.10 已落地):`curSkin()`/`applySkin()`/`setSkin(s)` 在 `INDEX_HTML` script 顶部;`localStorage['amr_skin']` 选皮肤;`applySkin` 用 display 显隐 `#skin-digest` vs `#side`/`#main`/`#right`。皮肤选择器 `#skinsel`(`#skinbar`)。**本 plan 增加皮肤值 `dual`。**
- 现有数据端点:`GET /api/matters`(可带 `?status=`/`?person=`/`?conversation=`,见 `api_matters`)返回事 list(字段含 `id`/`title`/`status`/`kind`/`diagnosis`);`GET /api/conversations/{id}/messages` 返回消息;`POST /api/matters/status` 改 status(已有 `api_matter_status`)。
- 事的 status 取值(看现网/`db.set_matter_status`):本 plan **不假设固定枚举**,按返回数据里实际出现的 status 动态分组,另加一个"其它"兜底列(设计骨架 候选/进行/等待/完结,见 spec §2)。
- 现有会话渲染:`openConv(id)`/`renderBubbles(m,opt)`/`loadMatters(cid)`(右栏事)/`#msgs`/`#right`。事视图点事后,取该事的关联会话 —— 现有 `api_matters` 返回的事**未必直接带 conversation_id**;若没有,事视图点事先用 `/api/matters?` 拿到事详情里的会话引用,没有就提示"该事未挂会话"。**实现者需先读 `api_matters`/`db.get_matters` 确认事对象是否含会话引用**;无则本 plan 事视图点事仅展示事卡详情(title/status/诊断),会话钻取标 TODO(后续接)。
- 皮肤选择器要把新皮肤加进 `#skinsel` 的 `<option>`。
- 设计正本:`docs/.../2026-06-28-supervised-agentic-router-design.md` §8;mockup:`docs/superpowers/specs/ui-mockups/mockup-3-dualaxis-accent.html`。
- 本地验证:同 v0.10 —— `python3 -c "from jl import web; web.serve('<tmpdb>','127.0.0.1',8901)"`,Chrome 连 `http://127.0.0.1:8901/`,合成 db 播种几条 matter(不同 status)+ person。

**全局约束:** DRY(看板列/事卡渲染抽函数)、YAGNI(不做 lifecycle 引擎、不做拖拽改 stage——点事卡用现有 `/api/matters/status` 下拉即可)、公开仓库无 PII、每 Task 一次提交、零 LLM(纯只读呈现)。

---

## File Structure
- **Modify `src/jl/web.py`**(`INDEX_HTML` 唯一):
  - 皮肤选择器加 `dual` option;`applySkin` 支持 `dual`(显隐一个新容器 `#skin-dual`)。
  - 新容器 `#skin-dual`:顶部「人视图/事视图」切轴 + 两个子视图。人视图复用现有三栏(或其精简);事视图 = 看板。
  - CSS + JS:`loadDual`/`renderMatterBoard`/`switchAxis`。
- **不改** 后端 `api_*`、db、send 路径。

---

## Task 1: 皮肤值 `dual` 接入皮肤层 + 容器骨架

**Files:** Modify `src/jl/web.py`(`INDEX_HTML`)

- [ ] **Step 1: 选择器加 option**

把 `#skinsel` 内的两个 `<option>`(`digest`/`inbox`)后追加一行(在 `</select>` 前):
```html
  <option value=dual>会话双轴</option>
```

- [ ] **Step 2: 加 `#skin-dual` 容器(body 标记)**

在 `<div id=skin-digest ...></div>` 之后插入:
```html
<div id=skin-dual style="display:none;flex:1;width:100%;height:100vh;overflow:auto">
 <div id=axisbar><span class=axt data-axis=people onclick="switchAxis('people')">👥 人视图</span><span class=axt data-axis=matters onclick="switchAxis('matters')">🗂 事视图</span></div>
 <div id=axis-people></div>
 <div id=axis-matters></div>
</div>
```

- [ ] **Step 3: `applySkin` 支持 dual**

在 `applySkin()` 里,找到 digest/inbox 显隐逻辑,改为三皮肤齐显隐。把现有
```javascript
 if(s==='digest'){dig.style.display='block';inboxEls.forEach(e=>e.style.display='none');loadDigest();}
 else{dig.style.display='none';inboxEls.forEach(e=>e.style.display='');}
```
替换为:
```javascript
 const dual=document.getElementById('skin-dual');
 dig.style.display = s==='digest'?'block':'none';
 dual.style.display = s==='dual'?'flex':'none';
 inboxEls.forEach(e=>e.style.display = s==='inbox'?'':'none');
 if(s==='digest')loadDigest();
 if(s==='dual')loadDual();
```
并在 script 里加空壳(Task 2/3 实现):
```javascript
function loadDual(){switchAxis(localStorage.getItem('amr_axis')||'people');}
function switchAxis(a){localStorage.setItem('amr_axis',a);/* Task 2/3 渲染 */
 document.querySelectorAll('#axisbar .axt').forEach(t=>t.classList.toggle('on',t.dataset.axis===a));
 document.getElementById('axis-people').style.display=a==='people'?'block':'none';
 document.getElementById('axis-matters').style.display=a==='matters'?'block':'none';}
```

- [ ] **Step 4: CSS for axisbar**

在 `</style>` 前插入:
```css
#axisbar{display:flex;gap:4px;padding:8px 14px;border-bottom:1px solid var(--border);background:var(--panel)}
#axisbar .axt{font-size:13px;padding:5px 16px;border-radius:8px;cursor:pointer;color:var(--fg2)}
#axisbar .axt.on{background:var(--bg);font-weight:700;color:var(--fg)}
```

- [ ] **Step 5: 部署本地 + 浏览器验证(切到 dual 皮肤、切轴)**

本地起 server(合成 db),Chrome:
```javascript
localStorage.setItem('amr_skin','dual');applySkin();
const dualShown=getComputedStyle(document.getElementById('skin-dual')).display!=='none';
switchAxis('matters');const mattersAxis=getComputedStyle(document.getElementById('axis-matters')).display!=='none';
switchAxis('people');const peopleAxis=getComputedStyle(document.getElementById('axis-people')).display!=='none';
JSON.stringify({dualShown,mattersAxis,peopleAxis})
```
Expected: `dualShown:true, mattersAxis:true, peopleAxis:true`。

- [ ] **Step 6: 回归 + Commit**

`.venv/bin/python -m pytest -q`(301 passed,后端未动)。
```bash
git add src/jl/web.py
git commit -m "feat(web): skin 'dual' scaffold (人视图/事视图 切轴 + 容器)"
```

---

## Task 2: 事视图 lifecycle 看板(消费 /api/matters)

**Files:** Modify `src/jl/web.py`(`INDEX_HTML`)

- [ ] **Step 1: 看板 CSS**

在 `</style>` 前插入:
```css
#axis-matters .board{display:flex;gap:12px;padding:14px;overflow-x:auto;align-items:flex-start}
#axis-matters .col{flex:0 0 240px;background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:8px}
#axis-matters .col h4{font-size:12px;color:var(--fg2);margin:2px 4px 8px}
#axis-matters .mc{background:var(--bg);border:1px solid var(--border);border-left:3px solid var(--accbd);border-radius:8px;padding:8px 10px;margin-bottom:8px;cursor:pointer}
#axis-matters .mc .t1{font-size:13px;font-weight:600}
#axis-matters .mc .t2{font-size:11px;color:var(--fg2);margin-top:3px}
```

- [ ] **Step 2: `renderMatterBoard` + 接进 switchAxis**

在 script 里加:
```javascript
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
```
在 `switchAxis(a)` 里,把 `/* Task 2/3 渲染 */` 替换为:
```javascript
 if(a==='matters')loadMatterBoard();
 if(a==='people')loadDualPeople();
```
并加 `loadDualPeople` 空壳(Task 3):
```javascript
function loadDualPeople(){/* Task 3 */}
```

- [ ] **Step 3: 部署本地 + 浏览器验证(看板按 status 分列)**

合成 db 先播种 3 个不同 status 的 matter。Chrome:
```javascript
localStorage.setItem('amr_skin','dual');applySkin();switchAxis('matters');
await new Promise(r=>setTimeout(r,400));
const cols=document.querySelectorAll('#axis-matters .col').length;
const cards=document.querySelectorAll('#axis-matters .mc').length;
JSON.stringify({cols,cards})
```
Expected: `cols>=1, cards>=1`(有事则分列;每列标题带计数)。screenshot 留证。

- [ ] **Step 4: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): 事视图 lifecycle 看板 (matters by status, /api/matters)"
```

---

## Task 3: 人视图(复用三栏 + 隐喻点缀)

**Files:** Modify `src/jl/web.py`(`INDEX_HTML`)

**说明:** 人视图直接**复用现有三栏皮肤的渲染**(人列表/会话/事卡)。最小做法:`loadDualPeople` 把现有 `#side`/`#main`/`#right` 三栏"借"进 `#axis-people`不现实(DOM 唯一)。故本 plan 采用**指引式**:人视图里放一句"人视图沿用收件箱三栏"+ 一个按钮切到 `inbox` 皮肤,避免 DOM 复制。隐喻点缀(composer 左下/事卡北极星)落在 inbox 皮肤本身的轻样式上。

- [ ] **Step 1: `loadDualPeople` 放指引**

把 `function loadDualPeople(){/* Task 3 */}` 替换为:
```javascript
function loadDualPeople(){document.getElementById('axis-people').innerHTML=
 '<div style="padding:24px;color:var(--fg2);font-size:14px">👥 人视图沿用收件箱三栏（左人 / 中会话 / 右事卡）。'+
 '<div style="margin-top:10px"><button class=go onclick="setSkin(\\'inbox\\')">切到收件箱三栏</button></div></div>';}
```
（注意 `\\'` 在 Python 三引号里渲染成 JS 的 `\'`。）

- [ ] **Step 2: inbox 皮肤隐喻轻点缀(事卡北极星高亮)**

给现有右栏 `#right` 顶部第一张事卡加一个"北极星"高亮 class —— 在右栏事渲染处(`loadMatters` 注入 `#right` 的地方)若首卡存在,加 class `northstar`。**实现者读 `loadMatters` 后**:把首个事卡外层 class 追加 `northstar`;并加 CSS:
```css
#right .northstar{border:1.5px solid var(--accbd);background:var(--accbg)}
```
（若 `loadMatters` 结构不便加 class,则仅加 CSS 规则、class 暂不挂,标 DONE_WITH_CONCERNS 说明——不强改既有渲染。）

- [ ] **Step 3: 部署本地 + 浏览器验证**

```javascript
localStorage.setItem('amr_skin','dual');applySkin();switchAxis('people');
await new Promise(r=>setTimeout(r,200));
const hasGuide=/人视图沿用收件箱/.test(document.getElementById('axis-people').textContent);
JSON.stringify({hasGuide})
```
Expected: `hasGuide:true`。

- [ ] **Step 4: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): dual 人视图指引 + inbox 事卡北极星点缀"
```

---

## Task 4: 端到端验收 + 截图

- [ ] **Step 1:** `.venv/bin/python -m pytest -q` → 301 passed。
- [ ] **Step 2:** `bash scripts/secrets-scan.sh` → exit 0。
- [ ] **Step 3:** 本地 server + Chrome:切到 `dual` 皮肤,事视图看板截图、人视图指引截图、皮肤选择器三选(简报/收件箱/会话双轴)可切。`computer screenshot save_to_disk:true` 留证。
- [ ] **Step 4:** 桌面/手机两视口各看一眼(看板横向滚动在手机可用)。
- [ ] **Step 5:** 如有微调提交 `fix(web): dual skin polish from e2e review`。

---

## Self-Review
**1. Spec coverage(§8 双轴皮肤候选3):** 人视图/事视图切轴 → Task 1 ✅;事视图 lifecycle 看板(按 status) → Task 2 ✅;事卡北极星点缀 → Task 3 ✅;皮肤可换并列(digest/inbox/dual) → Task 1 ✅。**范围外(后续)**:lifecycle 引擎(stage transition)、事→会话钻取(openMatter 现为 toast 占位,依赖事带会话引用)、②锚定皮肤、对角线皮肤。
**2. Placeholder scan:** `openMatter` toast 与 Task 3 指引是**有意的占位语义**(lifecycle/钻取是后续 plan),非 TODO 偷懒;其余步骤给了完整代码。
**3. 名一致性:** `loadDual`/`switchAxis`/`loadMatterBoard`/`loadDualPeople`/`openMatter`/`#skin-dual`/`#axisbar`/`.axt`/`#axis-people`/`#axis-matters`/`.board`/`.col`/`.mc` 前后一致;皮肤值 `dual` 与 option/applySkin/localStorage 一致。
**注意:** 所有改动集中在 `INDEX_HTML`,按 Task 顺序串行(subagent 也串行)。行号会移,用函数名/字符串锚点定位。
