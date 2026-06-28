# 监管下自动沟通 Phase 1 — 小范围自动回复(reply-only) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development。Steps 用 `- [ ]`。
> **敏感特性**:这是"系统替你发"的第一刀。**默认全关、逐会话 opt-in、人随时刹车、只发安全区无害料**。每个写动作留痕。

**Goal:** 建自动回复编排器:对**指定的小范围会话**(个人 + 几个群),按「观察/监管」挡,对进来的消息**拟一条安全区寒暄确认回复 → 双闸判 → 时间窗+限频+kill switch 过 → 观察挡只摆出来不发 / 监管挡走 countdown 自动发(人可否决)**。reply-only(不含撩/业务群发,那是 Phase 2)。

**Architecture:** 新增纯逻辑 `src/jl/autocomms.py`(编排:propose-only,不直接发)。复用现成件:`gate.classify`(双闸)、`db.get_safe_phrases`(料)、`outbox/confirm`+countdown(发)、`recall`(上下文)。新增**每会话自治挡**(`conv_autonomy`)+ **全局 kill switch** + **人本限频**。**观察挡=影子(不发)**;**监管挡=真发**(经 countdown 否决窗)。零外发风险默认态。

**Tech Stack:** Python stdlib;`autocomms.py`/db/限频 纯逻辑 TDD;UI 在 `INDEX_HTML`;LLM-optional(双闸闸二的 LLM verdict 由编排器在 LLM 可用时算,挂了→不自动→交人)。

**宪法约束(实现必守,见设计 §7.5):** 主动发起=Agent、决策=人;系统永不替你发地板;默认观察、人手动拨挡、系统不自升;自治安全栈四层。

**关键事实:**
- `gate.classify(conn, draft, *, person_id=None, purpose="reply", llm_verdict=None) -> {tier, allow_auto, gate1, gate2, ...}`。`allow_auto` 仅当命中话术库 ∧ `llm_verdict=="low"`;无 verdict→`needs_llm`(交人)。
- `db.get_safe_phrases(conn)` → 话术库(含内置6条)。`db.get_conversations` / messages 现成。
- 发送:`POST /api/outbox`(queue)→ `POST /api/outbox/confirm`(真发,留痕)。countdown UI 已现成(armSend/doSend)。
- `db.log_event(conn, kind=, actor=, detail=)` 留痕。`llm.available()`/`llm` 在 LLM 可用时可调(算 verdict)。

**全局约束:** TDD(autocomms/限频/dial 纯逻辑先写失败测试);默认全关;每 Task 一提交;公开仓无 PII;**绝不在 propose 里直接发**(发只走 outbox/confirm + 人否决窗)。

---

## File Structure
- **Create `src/jl/autocomms.py`** — 编排器(propose-only)。唯一职责:算"该不该/能不能自动回",**不发**。
- **Create `tests/test_autocomms.py`**。
- **Modify `src/jl/schema.sql` + `db.py`** — `conv_autonomy` 表(每会话挡)+ `settings` kv(全局 kill switch + 限频参数)+ helpers。
- **Modify `src/jl/web.py`** — `/api/auto-replies`(候选)+ `/api/autonomy`(设挡)+ `/api/killswitch` + UI 一节(拟自动回 + 挡位 + 刹车)。

---

## Task 1: 每会话自治挡 + 全局 kill switch(db)

**Files:** `src/jl/schema.sql`, `src/jl/db.py`, `tests/test_autonomy.py`

- [ ] **Step 1: 失败测试** `tests/test_autonomy.py`:
```python
import os, tempfile
from jl import db
def _c():
    fd,p=tempfile.mkstemp(suffix=".db");os.close(fd);c=db.connect(p);db.init_db(c);return c,p
def test_autonomy_default_off_and_set():
    c,p=_c()
    try:
        assert db.get_autonomy(c, 1) == "off"          # 默认关
        db.set_autonomy(c, 1, "observe")
        assert db.get_autonomy(c, 1) == "observe"
    finally: c.close(); os.unlink(p)
def test_killswitch_default_running():
    c,p=_c()
    try:
        assert db.killswitch_on(c) is False             # 默认未刹车(但自治默认关→也不发)
        db.set_killswitch(c, True)
        assert db.killswitch_on(c) is True
    finally: c.close(); os.unlink(p)
```
- [ ] **Step 2:** 运行→FAIL。
- [ ] **Step 3:** schema 加 `conv_autonomy(conversation_id INTEGER PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'off', updated_at INTEGER)` + 复用/新增 `app_settings(key TEXT PRIMARY KEY, value TEXT)`。db helpers:`get_autonomy(conn, cid)`(无行→"off")、`set_autonomy(conn, cid, mode)`(mode∈off/observe/supervised;**v1 不接受 autonomous**——Phase 3 才开)、`killswitch_on(conn)`(读 app_settings 'killswitch')、`set_killswitch(conn, on)`。`init_db` 建表。
- [ ] **Step 4:** 运行→PASS。
- [ ] **Step 5:** 全量回归(`pytest -q`,应 319+2)。
- [ ] **Step 6:** Commit `feat(autocomms): 每会话自治挡(off/observe/supervised,默认off) + 全局killswitch`。

---

## Task 2: `autocomms.py` 编排器(propose-only,纯逻辑)

**Files:** `src/jl/autocomms.py`, `tests/test_autocomms.py`

- [ ] **Step 1: 失败测试**(结构断言):
```python
import os, tempfile
from jl import db, autocomms
def _c():
    fd,p=tempfile.mkstemp(suffix=".db");os.close(fd);c=db.connect(p);db.init_db(c);return c,p
def test_propose_off_yields_nothing():
    c,p=_c()
    try:
        # 会话默认 off → 无候选
        assert autocomms.propose_replies(c, now=9_999_999_999) == []
    finally: c.close(); os.unlink(p)
def test_killswitch_blocks_all():
    c,p=_c()
    try:
        db.set_killswitch(c, True)
        assert autocomms.propose_replies(c, now=9_999_999_999) == []
    finally: c.close(); os.unlink(p)
```
（更细的命中/双闸/限频断言由实现者补:观察挡会话有"待回的入站"→产出候选含 `{conversation_id, draft, verdict, mode, action}`;`action`∈`shadow`(观察)/`arm`(监管+allow_auto+窗内+未超频)/`human`(其余)。）
- [ ] **Step 2:** 运行→FAIL。
- [ ] **Step 3: 实现 `autocomms.py`**(propose-only):
```python
"""监管下自动回复编排器(Phase 1, propose-only)。**绝不发** —— 只算候选;发由 outbox/confirm + 人否决窗。
设计 §7.5:默认观察、人拨挡、双闸+时间窗+限频+killswitch、Agent提议人决策。"""
from __future__ import annotations
from . import db, gate

def _in_window(conn, cid, now): ...        # 时间窗(无配置→默认上班时段 9-21,后续每账户可配)
def _under_rate(conn, cid, now): ...       # 人本限频(每会话每天上限 N 条;读 app_settings 或默认)
def _draft_ack(conn, conv, recent): ...    # 拟一条安全区寒暄确认(命中话术库的最合适项;拟不出→None,交人)
def _llm_verdict(conn, draft): ...         # LLM 可用→算 low/high;不可用→None(双闸→交人)

def propose_replies(conn, now):
    """对 observe/supervised 会话的"待回入站"产出候选(不发)。killswitch 开→空。"""
    if db.killswitch_on(conn):
        return []
    out = []
    for cv in db.get_conversations(conn):
        mode = db.get_autonomy(conn, cv["id"])
        if mode not in ("observe", "supervised"):
            continue
        recent = ...  # 该会话近端消息;判"有没有该回的入站(对方最后发、我没回)"
        if not _needs_reply(recent):
            continue
        draft = _draft_ack(conn, cv, recent)
        if not draft:                      # 拟不出安全话术 → 交人
            out.append({"conversation_id": cv["id"], "draft": None, "action": "human",
                        "reason": "无合适安全话术"}); continue
        verdict = gate.classify(conn, draft, llm_verdict=_llm_verdict(conn, draft))
        ok_window, ok_rate = _in_window(conn, cv["id"], now), _under_rate(conn, cv["id"], now)
        if mode == "observe":
            action = "shadow"              # 观察:只摆出来,绝不发
        elif verdict["allow_auto"] and ok_window and ok_rate:
            action = "arm"                 # 监管:可走 countdown 自动发
        else:
            action = "human"
        out.append({"conversation_id": cv["id"], "draft": draft, "mode": mode,
                    "verdict": verdict, "in_window": ok_window, "under_rate": ok_rate,
                    "action": action})
    return out
```
实现者补 `_needs_reply`/`_draft_ack`(命中话术库的寒暄确认;保守:拟不出就交人)/`_in_window`(默认 9–21)/`_under_rate`(默认每会话每天≤N,N 小、人本)/`_llm_verdict`(`llm.available()` 才算)。**绝不在此发送。**
- [ ] **Step 4:** 运行→PASS。
- [ ] **Step 5:** 回归 `pytest -q`。
- [ ] **Step 6:** Commit `feat(autocomms): propose_replies 编排器(propose-only·双闸+窗+限频+killswitch·零外发)`。

---

## Task 3: 端点 + 拟自动回 UI(观察看得见、监管可发、可刹车)

**Files:** `src/jl/web.py`

- [ ] **Step 1:** 加端点:`GET /api/auto-replies`(=propose_replies(now))、`POST /api/autonomy`{conversation_id,mode}(set_autonomy)、`POST /api/killswitch`{on}(set_killswitch + log_event)。POST 入白名单。
- [ ] **Step 2:** UI 一节(放简报/或会话右栏):**"🤖 拟自动回"** 列出候选——`shadow`(观察:灰条"本来会发:…",带"切监管"按钮)/`arm`(监管:"将自动发:…",走现有 countdown,可"改改/立即/否决")/`human`("交你回")。顶部一个 **🛑 全局刹车** 开关(killswitch)+ 每会话挡位下拉(off/观察/监管)。
- [ ] **Step 3:** 部署本地 + 浏览器验证:默认 off→无候选;某会话设 observe→出 shadow 候选(不发);设 supervised + killswitch off→候选 action=arm(命中话术库+窗内);开 killswitch→候选清空。截图留证。
- [ ] **Step 4:** Commit `feat(web): 拟自动回 UI + 自治挡位 + 全局刹车(/api/auto-replies,autonomy,killswitch)`。

---

## Task 4: 监管挡真发 wiring（复用 countdown,绝不绕过否决窗）

**Files:** `src/jl/web.py`(UI)

- [ ] **Step 1:** `arm` 候选点"将自动发"→ 复用 `armSend`(填入 draft → countdown N 秒 → doSend 走 `/api/outbox`+`/api/outbox/confirm`)。**人有 N 秒否决("改改/取消"),到点才发**。kill switch 开时 UI 禁用所有 arm。
- [ ] **Step 2:** 浏览器验证(监管挡、安全目标会话):arm 候选→倒数→否决能拦下;不否决→真发一条(走 outbox 留痕)。**用安全会话(发给自己/测试号),勿对真客户测**。
- [ ] **Step 3:** Commit `feat(web): 监管挡 arm→countdown→真发(经否决窗+留痕)`。

---

## Task 5: e2e + 安全验收

- [ ] `pytest -q` 全绿；`secrets-scan.sh` exit 0。
- [ ] 安全清单逐条验:① 默认全 off→零候选;② killswitch 开→全停;③ 观察挡永不发(只 shadow);④ 监管挡必经 countdown 否决窗;⑤ 双闸不过/无 LLM→交人;⑥ 窗外/超频→不 arm;⑦ 每次真发留痕(events)。
- [ ] 截图:观察 shadow / 监管 arm / 刹车态。
- [ ] **部署默认全关**到 .178/.156(`init_db` 加表,所有会话默认 off,killswitch off 但自治默认 off→不发)。**指定的小范围会话由王总手动拨 observe/supervised**,不预设。

---

## Self-Review
- **§7.5 覆盖**:观察/监管挡(A/C)→T1/T2;双闸+窗+限频+killswitch(D)→T2;Agent提议人决策(A)→propose-only+countdown→T2/T4;人本频率(F)→`_under_rate`→T2;默认全关/手动拨→T1/T5。**Phase 1 不含**:自治挡(C)、业务群发(B/E)、撩、AMP上游、拟人化口吻(G,复用已有口吻沉淀)——留 Phase 2/3。
- **占位语义**:`autonomous` 挡 v1 不接受、`_draft_ack` 拟不出→交人,都是**设计的保守口径**,非偷懒。
- **铁律**:propose_replies **绝不发**;发只走 outbox/confirm + countdown 否决窗;默认全 off + killswitch。

## Phase 2 / 3(本计划后,各自 plan)
- **Phase 2**:业务群发(②料 + B+C 收件人 + 进 lifecycle 成营销事由头 + AMP 入口) + 撩(主动·更窄安全区)。
- **Phase 3**:自治挡 + 简报 periodic 复盘 + 拟人化口吻深化(数字分身,全量数据学口吻)。
