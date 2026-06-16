# AMR v0.7 — Endpoint 路由 · 人/会话/事 · 外圆内方诊断引擎 — Design

**Date:** 2026-06-16
**Status:** approved design (brainstorm, 多轮质疑收敛), ready for implementation plan
**Scope:** three coupled blocks of the next AMR evolution, framed by the product's three
marketing pillars (营销三支柱):

| 支柱 | 一句话 | 落到本 spec |
|---|---|---|
| **关系智能** | 人和事谐 | Block 1 — Endpoint 路由模型 |
| **沟通教练** | 外圆内方 | Block 3 — T4 诊断引擎固化 + 起草 |
| **养心护体** | 保健医师 | 待己/养心 —— 语料偏弱，**本 spec 留位，下一批 dogfood** |

Grounded in dogfooded corpus `~/as/arh-im/docs/04_tools_and_scenarios_corpus.md`
(T1–T10 工具 / S1–S11 场景). MVP 主链 = **T1拉 → T4诊断 → T5起草 → T6人审**.

公开仓库纪律：方法内容（错位词典 / rubric / 打法库）**留本地**，仓库只暴露机制；测试
全用合成名（张三/李四/王五），真实测试人只在 .178 dogfood。

---

## Block 1 — Endpoint 路由模型（关系智能：人事和谐）

### 问题
一个人有**多渠道、每渠道多号**（李夏宁：微信×2 + 电话×2 + 飞书DM×1 = 5 端点）。现状：
`channels` 表本是端点注册表但①电话号未规范化→重复 ②`derive_last_interactions` 按
platform 坍缩，同渠道多号只看得见最新一个 ③发送只挑一条会话，无"多端点择优路由"。

### 端点 = 一等公民
**Endpoint = `(person, channel_kind, identifier, account)`** —— 一个人在一个渠道上的一个
可达地址。`channels` 表扶正为权威端点注册表。

- **规范化写入**：`channels.identifier` 入库即 canon —— 电话 `canon_phone`（去 +86/前导0），
  微信用可发 chat-id，飞书用 chat_id。去重（同 canon 折叠）。
- **端点元数据**：`last_activity_at`（每端点各自，不再 platform 坍缩）、`sendable`（运行时
  解析：渠道有 sender 且（微信）在实时会话列表）、`pinned`（人钦点的首选端点）。
- **conversation ↔ endpoint 对账**：按 `(kind, canon(chat_id))` 对齐；端点新鲜度从其会话派生。

### 路由（AMR 核心：管通道本身）
- `derive_last_interactions` 升到**端点级**：返回每个 `(kind, identifier)` 的最近互动。
- `weighting.combine` 跨端点取最优（现有 渠道权重×recency 逻辑，喂端点级数据）。
- `best_endpoint(conn, person_id)`：在**可发**端点里按 `权重 × recency` 选最优；
  `pinned` 人钦点覆盖自动；首选不可发 → 降级到次优可发端点；全不可发 → 救补（留痕）。
- 发送 / 起草上下文 → 用 `best_endpoint` 的发送目标 + **跨全部端点的合并时间线**。
  （取代 v0.6 的 `assist.primary_conversation` 单会话择优——那是 stopgap。）

### 飞书 DM 端点
加飞书私聊(DM)作为第三渠道：`lark` adapter 已能发(`LarkAdapter.send`)；ingest DM（P2P
私聊）入 endpoint。群聊仍 muted，不进主动路由。

### 测试矩阵（一人多渠道每渠道多号）
合成 fixtures：1. 2微信+2电话+1飞书DM → **5 端点**（canon 后无重复）；2. `channels` 电话
canon 去重；3. 端点级 recency（A号刚聊/B号冷，各自正确不坍缩）；4. `best_endpoint` 选最优 +
`pinned` 覆盖 + 并列稳定；5. 发送降级（首选不可发→次优可发→全无→救补）；6. 合并时间线跨全部
端点；7. `combine` 跨渠道×跨号；8. HITL pin 留痕。
.178 dogfood 真人：何峰博 / 李夏宁 / 仁兄(=用户本人 wangliren123) / 赵冰 / 刘宏玏。

---

## Block 2 — 事(matters) 模型 + 人/会话/事 三栏 UI（关系智能）

### 三栏 UI（多轮质疑锁定）
**左 人** ｜ **中 会话** ｜ **右 事卡**：
- **左 人**：关系账户色 🟢🟡🔴 + 「📞 该联系谁」（主动队列）。
- **中 会话**：跨端点**合并时间线**，**纯阅读**（不再内嵌话术）。
- **右 事卡**：每张卡 = 一件事的生命周期 = **MVP 主链**：
  `T4诊断(外圆内方一句话) → T5多版话术(稳/直/温) → T6人审发(outbox) → T10承诺/T9跟进`。
  话术从中栏**移入**右栏事卡。

### 事 = 一等实体
- `matters` 表：`id, title, kind, status(open|handled|dropped), surface_on(T9), created/updated`。
  诊断字段见 Block 3（内联或子表）。
- **多对多**：`matter_persons(matter_id, person_id)`、`matter_conversations(matter_id, conversation_id)`
  —— 一件事跨多人（罗鹏会议）、一条会话含多事（Shirley 三件）。三栏的"人→会话→事"是
  **默认导航动线**，不是数据约束。
- **来源（都过人闸，LLM-optional）**：① LLM *建议*事（从会话抽诊断/承诺）→ 人确认入库；
  ② 人手记一件事。**事不依赖 LLM 存在**：无模型时人手记、会话与回复照常。
- **承诺台账 T10**：`commitments(matter_id, text, due, status)`，LLM 可建议抽取、人确认。

### UI / API
- 右栏事卡渲染：诊断 + 话术(suggestions 改为挂 matter) + 人审发(复用 outbox confirm) + 承诺。
- 新读路由 `GET /api/matters?person=&conversation=`；写 `POST /api/matters`（建/确认/改状态）。
- 保持"简单到不需手册"：一件事一张卡，不是四个面板。

### 测试
matters CRUD；M:N 链接；事按 person/conversation 过滤；LLM 建议→人确认（fake llm，含
degrade）；承诺抽取人确认；web `/api/matters` shape。

---

## Block 3 — T4 诊断引擎固化（沟通教练：外圆内方）

### 模型（锁定：混合 本地rubric + LLM打分 + 人审）
护城河命门，从"仁德现想+markdown"固化成**可复用、可复现、不黑箱、LLM-optional**的引擎。

- **核心博弈原则：以牙还牙、以蜜还蜜**（tit-for-tat 对等互惠，governing「何时圆/何时方·力」
  的那层校准）。Axelrod 最稳策略，四性：**善意(先释蜜/圆，主动合作的第一步) · 对等(对方善则
  蜜还蜜，对方恶则克制对等回应=牙/方·力) · 不升级不记仇(回应有度，绝非报复升级) · 可宽恕
  (对方回头立即回蜜)**。它是 外圆内方 之上的博弈层：决定这一回合该偏 圆(蜜) 还是 方·力(牙)。
- **rubric / 错位词典 = 可编辑本地文件**（`~/.config/jl/diagnosis_rubric.md`，路径走
  `AMR_DIAGNOSIS_RUBRIC`，**内容留本地、不进公开仓库**，同打法库机制）。内容：以牙还牙以蜜还蜜
  博弈原则 + 外圆内方(儒圆 礼恕仁 / 法方 对事不对人 / 墨力 执行给确定) + 错位6型(圆缺/圆过/
  方污/方先/力先/力无)及其修法 + 伦理护栏(真心校验：辅助真心✅ / 制造假暖❌；诚实>漂亮；
  对等≠报复升级)。
- **结构化诊断对象**（结构是脚手架，nuance 在自由文本）：
  `{对方姿态: 蜜|牙|中性, 对等: ok|过软(该牙没牙,当了软柿子)|过硬(该蜜没蜜,寒了人心),
    圆: ok|缺|过, 方: ok|污|先, 力: ok|先|无, 错位: <enum|''>, 真心: ok|假暖,
    一句话诊断: str, 口径: str}`。
  —— 先判**对方姿态(蜜/牙/中性)** → 校验拟回应是否**对等**（蜜还蜜 / 牙还牙而克制）→ 再走
  外圆内方/错位6型。对等失衡(过软/过硬)本身即一类高优先级错位。
- **流程**：`diagnose(conn, matter/conversation)` → 组装上下文(合并时间线 + 关系账户 + rubric)
  → LLM 按 rubric 出结构化诊断 → 人审改 → 存到 matter → 驱动 `T5 起草`（诊断口径喂起草 prompt）。
- **LLM-optional**：无模型 → 返回空诊断，人可手填 rubric 或跳过；起草/会话照常。
- **不黑箱**：结构化 + 一句话理由可查；rubric 可读可改。
- **越用越准（T8）**：人确认/修改的诊断 → 错位案例回填本地词典（人审），acceptance 可追踪。
- **伦理护栏**：rubric 内置真心校验，引擎**永不为操纵/假暖优化**；诊断带 `真心:假暖` 警示位。
- diagnosis = **事卡第一格**。

### 测试
rubric 加载（本地文件，缺→degrade）；`diagnose` 结构化输出解析（fake llm 返回结构化）；
LLM 不可用→空诊断 degrade；错位 enum 校验；真心=假暖 警示；人改→回填词典；诊断口径进起草上下文。

---

## 数据流（贯通三块）
ingest（多端点）→ 端点注册/对账(Block1) → 路由 best_endpoint → 会话合并时间线 → 人/LLM 起一件**事**(Block2)
→ 事卡 **T4诊断**(Block3) → **T5起草**(诊断口径驱动) → **T6人审发**(outbox confirm，唯一发送口) → **T10承诺/T9跟进**。
任一处 LLM 不可用 → 降级到人手操作，绝不阻断读/写/发。

## 安全 / 原则（贯穿）
- **人在回路铁律**：敏感关系消息**绝不自动发**；只有 outbox confirm 发。事/诊断/路由钦点全留痕(events)。
- **LLM-optional**：诊断/起草/事抽取 全可降级到人手；无任何模型仍能读/会话/发。
- **不黑箱**：诊断结构化可查；路由可解释；rubric/词典/打法库可读可改。
- **公开仓库洁净**：方法内容(rubric/词典/打法库)留本地；测试合成名；真人只在 .178。
- **真心校验 / 诚实>漂亮**：引擎不为假暖/操纵优化（语料伦理底线）。

## 分批（步骤分批，不跳批）
1. **Block 1 Endpoint** —— 端点模型 + canon channels 去重 + 端点级 recency + best_endpoint 路由 + 飞书DM。
2. **Block 2 事+三栏UI** —— matters 模型 + M:N + 事卡(话术移入) + 三栏前端。
3. **Block 3 T4 诊断** —— 混合诊断引擎 + rubric 机制 + 驱动起草。
每块各自可测、可独立交付。养心护体(保健医师)留待下一批 dogfood。

## Out of scope
养心/待己 对内场景引擎；B 层 P2P(APM)；多模态(图片/语音内容理解，仅占位)；1c 全自动回复白名单
(沿用既有渐进自治路线，独立推进)。

## 客户端交付文档（minor bump 纪律）
v0.7 落地时同步 4 套交付文档的 delta（营销资料按三支柱话术 / 使用手册 人机协同 / 培训 / 运维）。
