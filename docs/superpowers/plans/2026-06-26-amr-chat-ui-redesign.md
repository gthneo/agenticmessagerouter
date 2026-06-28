# AMR 聊天界面重设计 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 AMR 收件箱中栏改成微信式气泡聊天、把发送闸改成"选话术/打字 → 倒数自动发(可改改)"统一模型、并适配手机小屏两级导航。

**Architecture:** 全部改动落在 `src/jl/web.py` 的 `INDEX_HTML` 字符串（CSS + 内联 JS）。后端 API / DB / 发送-审计路径**零改动**——发送仍走 `POST /api/outbox` → `POST /api/outbox/confirm`（留痕 events 不变），倒数只是 N 秒后自动调用发送。自动发偏好存浏览器 `localStorage`（纯 UI 偏好）。

**Tech Stack:** Python stdlib `http.server`（已有），前端为 `INDEX_HTML` 内的原生 HTML/CSS/JS（无框架）。验证用 Claude-in-Chrome 在桌面 + 手机视口实测（无 pytest 覆盖 CSS/JS；pytest 仅作后端回归保护）。

**关键事实（实现前必读）：**
- `INDEX_HTML` 从 `src/jl/web.py:434` 起，到 `:646` 的 `"""` 止。
- 消息 JSON（`GET /api/conversations/{id}/messages`，`api_messages` 用 `SELECT *`）含字段：`sender`、`ts`(unix秒)、`content`、`direction`(`'in'`/`'out'`)、`type`(文本为`'text'`，其余为字符串如`'10002'`)、`platform`。
- 既有发送相关 JS：`resetSendbar()`(:518)、`sendReply()`(:519)、`confirmSend()`(:524)、`openConv()`(:529)、`loadSuggestions()`(:548)、`useDraft()`(:553)、`goHome()`(:633)。
- 既有 helper：`E(path)`/`P(path,body)`(fetch)、`esc(s)`、`fmt(ts)`、`toast(msg)`、`window.NAMES`(convId→名)、`window.CURCONV`、`window.SUG`(草稿id→正文)。
- 部署：`sshpass -p dbos-miner scp src/jl/web.py dbos-user@192.168.31.178:/home/dbos-user/amr/jl/web.py` 然后 `ssh dbos-user@192.168.31.178 'systemctl --user restart amr-web'`。收件箱 URL：`http://192.168.31.178:8088/?token=cd1d80fffa2389f40fe1eb0994dc30c5`。
- 浏览器验证：Claude-in-Chrome `navigate` 打开上面 URL → `javascript_tool` 执行检查脚本 → 断言返回 JSON。何峰博会话 id 在运行时取（见各任务），修伟=conv 195。

**全局约束：** DRY（气泡渲染抽一个 `renderBubbles` 复用）、YAGNI（不做 1c 自治、不做头像）、公开仓库无 PII（不写真实联系人名进代码/注释）、每个 Task 一次提交。

---

## File Structure

唯一改动文件：`src/jl/web.py`（`INDEX_HTML`）。分区：
- **CSS**（`<style>`…`</style>`，约 :437–:471）：气泡样式、倒数条样式、移动端 media query。
- **body 标记**（:473–:502）：`#countbar` 新增、`#hdr` 加移动端按钮、`#sendbar` 按钮 onclick 改名。
- **JS**（`<script>`…`</script>`，:504–:646）：`renderBubbles`、发送闸（`armSend`/`doSend`/`cancelSend` 取代 `sendReply`/`confirmSend`/`resetSendbar`）、自动发偏好（`autoCfg`/`loadAuto`/`saveAuto`）、移动端视图切换。

---

## Task 1: 气泡聊天渲染（中栏 + 人时间线）

**Files:**
- Modify: `src/jl/web.py` CSS 行 `:447`（`#msgs`/`.m` 样式块）、`:529-531`（`openConv` 渲染）、`:570-573`（`openPerson` 渲染）。

- [ ] **Step 1: 替换消息区 CSS 为气泡样式**

把 `src/jl/web.py:447` 整行（当前 `#msgs{flex:1;overflow:auto;padding:12px}.m{...}.m .s{...}.m .t{...}`）及紧随其后 `:448` 我之前加的 `.m div{overflow-wrap:anywhere;word-break:break-word}` 两行，整体替换为：

```css
#msgs{flex:1;overflow:auto;padding:12px;background:#ededed}
.m{margin:5px 0;display:flex;flex-direction:column}.m.in{align-items:flex-start}.m.out{align-items:flex-end}
.m .s{font-size:11px;color:#999;margin:0 4px 2px}
.bub{max-width:72%;padding:7px 10px;border-radius:8px;overflow-wrap:anywhere;word-break:break-word;white-space:pre-wrap;line-height:1.4}
.m.in .bub{background:#fff}.m.out .bub{background:#95ec69}
.m .t{color:#aaa;font-size:11px;margin:1px 4px 0}
.sys{text-align:center;color:#888;font-size:12px;margin:8px auto;max-width:80%}
.tsep{text-align:center;color:#999;font-size:11px;margin:10px 0}
```

- [ ] **Step 2: 加 `renderBubbles` helper（DRY，供会话 + 人时间线复用）**

在 `src/jl/web.py:509`（`window.NAMES={};` 那行）之后插入：

```javascript
function renderBubbles(m,opt){opt=opt||{};let out='',last=0;
 (m||[]).forEach(x=>{
  if(x.ts&&last&&x.ts-last>300){out+='<div class=tsep>'+fmt(x.ts)+'</div>';}
  if(x.ts)last=x.ts;
  const sys=(x.type==='10002')||/撤回了一条消息$/.test(x.content||'');
  if(sys){out+='<div class=sys>'+esc(x.content)+'</div>';return;}
  const dir=x.direction==='out'?'out':'in';
  const tag=(opt.platform&&x.platform)?' <span class=badge>'+esc(x.platform)+'</span>':'';
  const name=dir==='in'?'<span class=s>'+esc(x.sender)+tag+'</span>':'';
  out+='<div class="m '+dir+'">'+name+'<div class=bub>'+esc(x.content)+'</div><span class=t>'+fmt(x.ts)+'</span></div>';
 });
 return out||'(无消息)';}
```

- [ ] **Step 3: 让 `openConv` 用气泡渲染**

把 `src/jl/web.py:529-531` 的 `openConv` 函数体里渲染 `#msgs` 的两行（当前 `document.getElementById('msgs').innerHTML=m.map(...).join('')||'(无消息)';`）替换为：

```javascript
 document.getElementById('msgs').innerHTML=renderBubbles(m);
```

（保留该函数其余部分：`window.CURCONV=id`、`await E(...)`、`loadSuggestions(id);loadMatters(id)`。本任务先不动 `resetSendbar()` 调用，Task 2 再改。）

- [ ] **Step 4: 让 `openPerson` 用气泡渲染（带平台徽标）**

把 `src/jl/web.py:572-573` 中 `openPerson` 渲染 `#msgs` 的两行（`document.getElementById('msgs').innerHTML=m.map(...).join('')||'(无消息)';`）替换为：

```javascript
 document.getElementById('msgs').innerHTML=renderBubbles(m,{platform:true});
```

（保留 `:571` 设置标题那行不变。）

- [ ] **Step 5: 后端回归 + 部署 + 浏览器验证**

```bash
cd /Users/neo/as/agenticmessagerouter && .venv/bin/python -m pytest -q
```
Expected: `276 passed`（后端未动，纯防回归）。

```bash
sshpass -p dbos-miner scp src/jl/web.py dbos-user@192.168.31.178:/home/dbos-user/amr/jl/web.py
ssh dbos-user@192.168.31.178 'systemctl --user restart amr-web && sleep 1 && systemctl --user is-active amr-web'
```
Expected: `active`

浏览器（Claude-in-Chrome）：navigate 到收件箱 URL，然后 `javascript_tool` 执行（何峰博会话——按名字找其 convId）：
```javascript
const cs=await E('/conversations');const hf=cs.find(c=>(c.name||'').includes('何峰博'));
await openConv(hf.id);await new Promise(r=>setTimeout(r,400));
const ins=document.querySelectorAll('#msgs .m.in').length, outs=document.querySelectorAll('#msgs .m.out').length,
 bubs=document.querySelectorAll('#msgs .bub').length, sys=document.querySelectorAll('#msgs .sys').length;
JSON.stringify({convId:hf.id, in:ins, out:outs, bubbles:bubs, sysmsgs:sys})
```
Expected: `in>0 && out>0 && bubbles>0`（左右气泡都有；何峰博会话里有自己发的=out 绿气泡、对方=in 白气泡）。

- [ ] **Step 6: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): WeChat-style message bubbles (left/right by direction, sys centered)"
```

---

## Task 2: 统一倒数发送闸（取代两步确认）

**Files:**
- Modify: `src/jl/web.py` CSS（在 `:456` `#right` 行后加 `#countbar` 样式）、body 标记 `:496-498`（加 `#countbar`、`#sendbar` 按钮 onclick 改 `armSend`）、JS `:518`(resetSendbar)、`:519-522`(sendReply)、`:524-527`(confirmSend)、`:529`(openConv 起始)、`:553`(useDraft)、`:633`(goHome)。

- [ ] **Step 1: 加倒数条 CSS**

在 `src/jl/web.py:456`（`#right{...flex-shrink:0}` 那行）之后插入一行：

```css
#countbar{padding:8px 12px;border-top:1px solid #eee;background:#fffbe6;display:flex;gap:8px;align-items:center;flex-wrap:wrap}#countbar.hide{display:none}#countbar .txt{flex:1;min-width:120px;color:#333;overflow-wrap:anywhere}#countbar .cd{font-weight:700;color:#a40;white-space:nowrap}#countbar button{padding:4px 12px;border-radius:6px;cursor:pointer;border:1px solid #ccc;background:#f7f7f7}#countbar button.go{border-color:#4a8;background:#e8f7ee;color:#176}#countbar button:disabled{opacity:.5;cursor:default}#countbar.err{background:#fbecec}
```

- [ ] **Step 2: 在输入区上方加 `#countbar`，并把发送按钮指向 `armSend`**

把 `src/jl/web.py:496-498`：
```html
 <div id=msgs></div>
 <div id=replybox><textarea id=reply rows=2 placeholder="点右侧「用此版」填入，可改；「暂存待发」后去左边确认真发"></textarea>
 <span id=sendbar><button onclick="sendReply()">发送 →</button></span>
```
替换为：
```html
 <div id=msgs></div>
 <div id=countbar class=hide></div>
 <div id=replybox><textarea id=reply rows=2 placeholder="选右侧话术或自己打字 → 发送后倒数自动发，倒数内可「改改」"></textarea>
 <span id=sendbar><button onclick="armSend()">发送 →</button></span>
```

- [ ] **Step 3: 用 `autoCfg`/`cancelSend`/`armSend`/`doSend` 取代 `resetSendbar`/`sendReply`/`confirmSend`**

把 `src/jl/web.py:517-527`（从 `function resetSendbar()...` 到 `confirmSend` 函数结束 `...loadOutbox()}` 这一整段三个函数）整体替换为：

```javascript
function autoCfg(){return {on:localStorage.getItem('amr_autosend')!=='0',
  secs:Math.max(1,parseInt(localStorage.getItem('amr_autosend_secs')||'5',10)||5)};}
function cancelSend(){if(window.SENDTIMER){clearInterval(window.SENDTIMER);window.SENDTIMER=null;}
  const c=document.getElementById('countbar');c.className='hide';c.innerHTML='';}
function armSend(){if(!window.CURCONV){alert('先选会话');return;}
  const ta=document.getElementById('reply'),body=ta.value.trim();if(!body)return;
  cancelSend();
  const who=window.NAMES[window.CURCONV]||'对方',cfg=autoCfg(),c=document.getElementById('countbar');
  c.className='';
  const bar=(head,goLabel)=>{c.innerHTML=head+
    ' <button class=go onclick="doSend()">'+goLabel+'</button>'+
    ' <button id=cancelbtn onclick="cancelSend();document.getElementById(\\'reply\\').focus()">改改</button>';
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
  if(r.ok){ta.value='';cancelSend();toast('已发送 ✅');loadOutbox();openConv(window.CURCONV);}
  else{c.className='err';c.innerHTML='<span class=txt>发送失败：'+esc(r.error||'未知')+'</span>'+
    ' <button class=go onclick="armSend()">重试</button> <button onclick="cancelSend()">取消</button>';}}
```

注意转义：上面字符串里 `document.getElementById(\\'reply\\')` 写进 Python 三引号字符串时，`\\'` 即源码里的 `\'`（JS 单引号转义），最终页面上是 `document.getElementById('reply')`。实现时确认页面渲染出的是合法 JS（见 Step 7 验证）。

- [ ] **Step 4: 选话术即决策——`useDraft` 填入后直接 arm**

把 `src/jl/web.py:553`（`function useDraft(id){...}` 整个，当前为填入 textarea + flash 聚焦那几行）替换为：

```javascript
function useDraft(id){const r=document.getElementById('reply');r.value=window.SUG[id]||'';armSend();}
```

- [ ] **Step 5: 切会话/回首页时取消进行中的倒数**

在 `src/jl/web.py` 的 `openConv`（:529）函数体最前面，把开头的 `window.CURCONV=id;resetSendbar();` 改为：
```javascript
window.CURCONV=id;cancelSend();
```
在 `goHome`（:633）里把 `resetSendbar();` 改为 `cancelSend();`。

- [ ] **Step 6: 确认无残留旧函数引用**

```bash
cd /Users/neo/as/agenticmessagerouter && grep -n "resetSendbar\|sendReply\|confirmSend" src/jl/web.py
```
Expected: 无输出（三个旧函数名已全部移除/替换）。若有残留，逐一改为 `cancelSend`/`armSend`/`doSend`。

- [ ] **Step 7: 后端回归 + 部署 + 浏览器验证（倒数自动发 / 改改聚焦 / 立即发置灰）**

```bash
.venv/bin/python -m pytest -q   # Expected: 276 passed
sshpass -p dbos-miner scp src/jl/web.py dbos-user@192.168.31.178:/home/dbos-user/amr/jl/web.py
ssh dbos-user@192.168.31.178 'systemctl --user restart amr-web && sleep 1 && systemctl --user is-active amr-web'   # active
```

浏览器验证（不真发——验 UI 行为；倒数设短便于观察，doSend 会真发故此处只验"改改"取消路径，不让其跑到 0）：
```javascript
localStorage.setItem('amr_autosend','1');localStorage.setItem('amr_autosend_secs','5');
const cs=await E('/conversations');const hf=cs.find(c=>(c.name||'').includes('何峰博'));
await openConv(hf.id);await new Promise(r=>setTimeout(r,300));
document.getElementById('reply').value='【UI测试草稿·勿真发】';armSend();
await new Promise(r=>setTimeout(r,300));
const cb=document.getElementById('countbar');
const visible=!cb.classList.contains('hide');
const hasCountdown=/⏳/.test(cb.textContent);
const focusOnCancel=document.activeElement&&document.activeElement.id==='cancelbtn';
// 点改改取消，焦点应回输入框
document.getElementById('cancelbtn').click();await new Promise(r=>setTimeout(r,100));
const cancelled=cb.classList.contains('hide');
const focusOnInput=document.activeElement&&document.activeElement.id==='reply';
JSON.stringify({visible,hasCountdown,focusOnCancel,cancelled,focusOnInput})
```
Expected: `visible:true, hasCountdown:true, focusOnCancel:true, cancelled:true, focusOnInput:true`。
（验证后清掉测试草稿：`document.getElementById('reply').value=''`。）

- [ ] **Step 8: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): unified countdown send gate (pick draft/type -> countdown -> auto-send, 改改 cancels+focus, 立即发 grays out)"
```

---

## Task 3: 自动发设置（开关 + 秒数，localStorage）

**Files:**
- Modify: `src/jl/web.py` 设置面板静态标记（`:490` `<h2>🔄 归一</h2>` 之前插入一节）、`loadSettings()`（:593，加载偏好）、新增 `saveAuto()`。

- [ ] **Step 1: 设置面板加"发送"小节**

在 `src/jl/web.py:490`（`  <h2>🔄 归一</h2>` 那行）之前插入：

```html
  <h2>📤 发送</h2>
  <div class=row><label><input type=checkbox id=autosend_on> 选定/发送后自动发送</label>
   倒数 <input id=autosend_secs type=number min=1 style=width:56px> 秒
   <button class=go onclick="saveAuto()">💾 保存</button>
   <span class=tag>关掉=必须点「确认发」才发</span></div>
```

- [ ] **Step 2: 加 `saveAuto`，并在 `loadSettings` 里回填**

在 `src/jl/web.py` 的 `saveProfile`（:621 附近，`async function saveProfile()...` 那行）之前插入：

```javascript
function saveAuto(){localStorage.setItem('amr_autosend',document.getElementById('autosend_on').checked?'1':'0');
 localStorage.setItem('amr_autosend_secs',String(Math.max(1,parseInt(document.getElementById('autosend_secs').value,10)||5)));
 toast('发送设置已存');}
```

在 `loadSettings()`（:593，`async function loadSettings(){` 之后的第一行）插入回填逻辑：

```javascript
 {const cfg=autoCfg();document.getElementById('autosend_on').checked=cfg.on;document.getElementById('autosend_secs').value=cfg.secs;}
```

- [ ] **Step 3: 部署 + 浏览器验证（关掉自动发 → 倒数条变手动确认）**

```bash
sshpass -p dbos-miner scp src/jl/web.py dbos-user@192.168.31.178:/home/dbos-user/amr/jl/web.py
ssh dbos-user@192.168.31.178 'systemctl --user restart amr-web && sleep 1 && systemctl --user is-active amr-web'   # active
```

浏览器验证：
```javascript
localStorage.setItem('amr_autosend','0');   // 关自动发
const cs=await E('/conversations');const hf=cs.find(c=>(c.name||'').includes('何峰博'));
await openConv(hf.id);await new Promise(r=>setTimeout(r,300));
document.getElementById('reply').value='【UI测试·勿真发】';armSend();await new Promise(r=>setTimeout(r,300));
const cb=document.getElementById('countbar');
const manual=/确认发/.test(cb.textContent)&&!/⏳/.test(cb.textContent);
document.getElementById('cancelbtn').click();document.getElementById('reply').value='';
localStorage.setItem('amr_autosend','1');   // 复位
JSON.stringify({manualMode:manual})
```
Expected: `manualMode:true`（关掉后无倒数、显示「确认发」，需手点）。

- [ ] **Step 4: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): auto-send toggle + seconds in settings (localStorage; off = manual confirm)"
```

---

## Task 4: 移动端两级响应式

**Files:**
- Modify: `src/jl/web.py` CSS（在 `:471` `</style>` 前加 media query + 移动端按钮默认隐藏）、`#hdr` 标记（:480，加返回 + 事 按钮）、JS `openConv`/`goHome`（加 `m-chat` 类切换）、新增 `toggleMatters()`。

- [ ] **Step 1: 加移动端 CSS**

在 `src/jl/web.py:471`（`#reuniout{...}` 那行）之后、`:472` `</style>` 之前插入：

```css
#mback,#mmatters{display:none}
@media(max-width:640px){
 body{flex-direction:column;height:100vh}
 #side{width:100%;flex:1;border-right:0;border-bottom:1px solid #ddd}
 #main{width:100%;flex:1}#right{width:100%;border-left:0}
 body.m-chat #side{display:none}
 body:not(.m-chat) #main,body:not(.m-chat) #right{display:none}
 body.m-chat:not(.m-matters) #right{display:none}
 body.m-chat.m-matters #right{position:fixed;inset:0;z-index:8;background:#fff;width:auto}
 #mback,#mmatters{display:inline-block}
 .bub{max-width:82%}
}
```

- [ ] **Step 2: `#hdr` 加返回 + 事 按钮（仅移动端可见）**

把 `src/jl/web.py:480`：
```html
<div id=main><div id=hdr><button onclick="goHome()" style="margin-right:8px">← 收件箱</button>
```
替换为：
```html
<div id=main><div id=hdr><button id=mback onclick="goHome()" style="margin-right:8px">← 列表</button>
 <button onclick="goHome()" style="margin-right:8px">← 收件箱</button>
 <button id=mmatters onclick="toggleMatters()" style="margin-right:8px">🩺事</button>
```
（桌面端 `#mback`/`#mmatters` 由 CSS 隐藏；`← 收件箱` 桌面端可见、移动端冗余但无害。）

- [ ] **Step 3: 进会话切到聊天视图，回首页/取消切回列表，加事面板开关**

在 `openConv`（:529）函数体最前面（`window.CURCONV=id;cancelSend();` 之后）追加：
```javascript
document.body.classList.add('m-chat');
```
在 `goHome`（:633）函数体里追加（与已有 `cancelSend()` 同处）：
```javascript
document.body.classList.remove('m-chat','m-matters');
```
在 `goHome` 函数之后新增：
```javascript
function toggleMatters(){document.body.classList.toggle('m-matters');}
```

- [ ] **Step 4: 部署 + 移动视口浏览器验证（两级导航）**

```bash
sshpass -p dbos-miner scp src/jl/web.py dbos-user@192.168.31.178:/home/dbos-user/amr/jl/web.py
ssh dbos-user@192.168.31.178 'systemctl --user restart amr-web && sleep 1 && systemctl --user is-active amr-web'   # active
```

浏览器：先把窗口/视口设到手机宽度再验。用 Claude-in-Chrome `resize_window` 到约 390×800（或 `computer` 截图核对），navigate 收件箱 URL，然后：
```javascript
const list0=getComputedStyle(document.getElementById('side')).display;   // 列表态：side 可见
const cs=await E('/conversations');const hf=cs.find(c=>(c.name||'').includes('何峰博'));
await openConv(hf.id);await new Promise(r=>setTimeout(r,300));
const sideHidden=getComputedStyle(document.getElementById('side')).display==='none';
const mainShown=getComputedStyle(document.getElementById('main')).display!=='none';
toggleMatters();const mattersShown=getComputedStyle(document.getElementById('right')).display!=='none';
goHome();await new Promise(r=>setTimeout(r,200));
const backToList=getComputedStyle(document.getElementById('side')).display!=='none';
JSON.stringify({list0,sideHidden,mainShown,mattersShown,backToList})
```
Expected: `list0!=='none'`(列表态显示)、`sideHidden:true`(进会话隐藏列表)、`mainShown:true`(显示聊天)、`mattersShown:true`(🩺事 弹出右栏)、`backToList:true`(返回回列表)。

桌面回归：`resize_window` 回到 ≥1000 宽，刷新，确认三栏仍并排（`#side`/`#main`/`#right` display 均非 none）。

- [ ] **Step 5: Commit**

```bash
git add src/jl/web.py
git commit -m "feat(web): mobile two-level responsive (list <-> full-screen chat, 事 slide-over)"
```

---

## Task 5: 端到端验收 + 截图留证

**Files:** 无代码改动（验收 + 截图）。

- [ ] **Step 1: 桌面端整体验收（截图）**

Claude-in-Chrome：`resize_window` ≥1280 宽，navigate 收件箱 URL，打开何峰博会话，`computer screenshot save_to_disk:true`。人工核对：左右气泡分明、系统消息居中、长链接换行、左栏在、右栏话术/事在。

- [ ] **Step 2: 倒数真发一次（端到端含 outbox/审计）**

> ⚠️ 这一步会**真发一条消息**。用一个安全目标（如发给自己的"代码班迪/笔记"会话，或先与用户确认目标），内容写明测试。验证倒数到 0 自动发 → 出现右绿真实气泡 → `loadOutbox` 无悬挂 pending。
```javascript
// 选定一个安全会话后：
document.getElementById('reply').value='AMR UI 自测：倒数自动发送 ✅';
localStorage.setItem('amr_autosend_secs','3');armSend();
// 等待 >3s，倒数归零自动发；随后检查最后一条是否为 out 气泡
```
Expected: 倒数归零自动发送、toast「已发送 ✅」、中栏末尾出现 out 绿气泡、`GET /api/outbox` 该条状态 sent。

- [ ] **Step 3: 移动视口验收（截图）**

`resize_window` 390×800，navigate，列表→何峰博会话→🩺事→返回，`computer screenshot save_to_disk:true` 各一张。人工核对两级导航顺手、气泡可读。

- [ ] **Step 4: 后端回归最终确认**

```bash
cd /Users/neo/as/agenticmessagerouter && .venv/bin/python -m pytest -q
```
Expected: `276 passed`。

- [ ] **Step 5: secrets-scan + 收尾提交（如有验收期间的微调）**

```bash
bash scripts/secrets-scan.sh   # exit 0
git status --short              # 若 clean 则无需提交
```
（若 Step 1–4 期间有微调改了 `web.py`，`git add src/jl/web.py && git commit -m "fix(web): chat UI polish from e2e review"`。）

---

## Self-Review

**1. Spec coverage：**
- 气泡三态（已收左/已发右绿/系统居中）→ Task 1 ✅；AI 草稿留右栏卡片（未改 `loadSuggestions`，仍在右栏）✅；待发=倒数条不进气泡流 → Task 2 `#countbar` 独立于 `#msgs` ✅。
- 统一倒数发送闸（选草稿/打字都进、焦点改改、立即发置灰、走 outbox 留痕、改改聚焦输入）→ Task 2 ✅。
- 自动发开关+秒数（默认开5、关=手动确认）→ Task 3 ✅。
- 移动端两级响应式（列表↔全屏聊天、事可达）→ Task 4 ✅；桌面三栏不变 → Task 4 media query 仅 ≤640px 生效 ✅。
- 范围不含 1c 自治 ✅（无相关任务）。
- 边界：切会话取消倒数（Task 2 Step 5）✅；发送失败回填重试（Task 2 doSend err 分支）✅；空正文不触发（armSend `if(!body)return`）✅；留痕不变（仍 outbox/confirm）✅。

**2. Placeholder scan：** 无 TBD/“类似上文”/裸描述；每个改码步骤都给了完整代码与精确行锚点。

**3. Type/名一致性：** `armSend`/`doSend`/`cancelSend`/`autoCfg`/`renderBubbles`/`saveAuto`/`toggleMatters` 全程一致；`#countbar`/`#cancelbtn`/`#autosend_on`/`#autosend_secs`/`m-chat`/`m-matters` ID/类名前后一致；localStorage 键 `amr_autosend`/`amr_autosend_secs` 一致。旧函数 `resetSendbar`/`sendReply`/`confirmSend` 已在 Task 2 Step 6 显式清零引用。

**注意事项（执行者必看）：** 行号基于 v0.9.2 (`72ff61b`) 当前 `web.py`；每完成一个 Task 行号会下移，后续 Task 用函数名/字符串锚点定位而非死记行号。所有改动在一个文件，建议按 Task 顺序串行执行（subagent-driven 也串行，避免同文件冲突）。
