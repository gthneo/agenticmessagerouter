# Conformance — schema 表达不了的语义口径

一份 JSON schema 能管住**结构**（字段在不在、类型对不对、枚举值合不合法），
但管不住**语义**（这个值在跨消息、跨会话、跨重抓的维度上是否自洽）。本文件把这些
**schema 表达不了、但消费方必须遵守的语义口径**写成可验证的一致性规则。

> 教训源头（2026-06-28 实战故障）：schema 校验全过，群消息仍神秘不入库 —— 因为 `msg_id`
> 的**语义**（全局唯一）被破坏了。所以：**结构校验是地板，语义口径 + 运行时边界校验才是 ground truth。**

---

## C1. `msg_id` 必须全局唯一（不得用会话内 localId）—— 最高优先

**规则**：canonical 信封的 `msg_id` 必须是**全局唯一 + 跨重抓稳定**的消息 id
（用 WeChat `serverId` 这类服务端全局唯一值）。**严禁用会话内 `localId` / 位置序号**：
localId 是会话内位置、会重用 / 重置，拿它当 `msg_id` → **同一会话内的新消息撞到老消息的 key**
→ 被去重约束 `UNIQUE(conversation_id, msg_key)` 的 `INSERT OR IGNORE` **静默丢弃**
→ 消息神秘不入库、自动回复无候选（这正是 2026-06-28 的故障）。

**为什么 schema 抓不住**：`msg_id` 在结构上只是「一个非空字符串」，单条信封怎么看都合法。
错误只在**跨多条消息**时才显形（值重复、且是小序号）。

**验法（消费方 CI 可自动化）**：
> 同一后端**跨多个会话各取 N 条消息**，断言它们的 `msg_id`：
> 1. **互不相同**（跨会话全局唯一，不只是会话内唯一）；且
> 2. **不是小序号**（不是 `1/2/3…` 这类会话内位置号 —— 例如全是 < 1000 的递增整数串 = 强烈嫌疑）。
>
> 任一条不满足 = 红：该后端很可能在拿 localId 冒充 msg_id。

**运行时兜底（即使没自动化测）**：消费方在 ingest 边界做**碰撞检测** —— 当一条新消息的
`(conversation_id, msg_key)` 已存在但 content/ts 不同（= 真碰撞，非重复），**响亮报警 + 计数**
而非静默丢。参考实现：AMR `db.insert_messages` + `/api/health` 的 `contract_violations_24h`。

---

## C2. `direction` 必须由后端按 self_id 判定，不得一律 `"in"`

`direction` 结构上只需 ∈ `{in, out}`，但**语义**要求后端按自己的 self_id 真判出/入站。
当前 fullwechat 曾硬编码 `"in"`（`canonical.md` §1.4 / §9.8）。判不准时后端**声明
`direction:false` 能力位**，交 AMR 后处理（`apply_self_directions`）—— 不要假装判出。

**验法**：取一段含「自己发的」消息的会话，断言至少有 `direction:"out"` 的条目
（全 `in` 且明知有自己发的 = 嫌疑）。

---

## C3. `text` 永远非空（向后兼容地板）

每条信封无论 kind 为何，`text` 都必须非空（最差给占位 `[图片]` / `[链接/文件]` / `[系统消息]`）。
纯文本消费方（旧 UI、CLI grep、FTS）只读 `text` 也不丢可用性。

**验法**：对任意 kind 的信封断言 `text` 非空字符串。

---

## C4. `kind` 落在封闭枚举，认不出落 `unknown`（不得自创 kind）

`kind` 必须 ∈ `canonical.md` §3 的 15 个封闭值。后端认不出的原始子类**就近映射或落 `unknown`**，
**严禁自创新 kind**（自创 = 消费方按 `unknown` 处理，富渲染丢失）。新增 kind 走本仓 PR + minor bump。

**验法**：断言每条信封的 `kind` ∈ 枚举；fuzz 一条 `kind:"自造词"` 给消费方校验器应判违规。

---

## C5. `schema` 前缀决定兼容路由

`schema` 必须以 `message.canonical/` 打头，主版本号用于兼容路由（`/1` → `/2` = major，不兼容）。
消费方按前缀路由，遇到更高 minor 内的未知字段**忽略**（向后兼容），遇到更高 major **拒绝或降级**。

---

## C6. 读空必可区分（read-empty must be distinguishable）—— 与 C1 同根的哑失败

**规则**（`canonical.md` §6.4）：读调用（`read_messages` / `GET /api/messages`）在**无法确认会话
覆盖完整**时（分片密钥缺失 / 消息表缺失或未映 / 任何说不清的不可读），**不得返回空数组**——
必须给可区分信号：REST = HTTP **409** + `{"error":{"code":"read_unavailable","reason":…,"chatId":…}}`；
MCP = **工具错误**（`isError`）。空数组**仅当**后端能确认「真读到 0 条、覆盖完整」时允许。

**为什么 schema 抓不住**：`[]` 在结构上永远合法；「这个空到底是真空还是读不到」是**语义**，
单看返回值分辨不了——和 C1（`msg_id` 全局唯一）**同根的「哑失败」**。

**危害**：下游（家人雷达 / 关系节奏）把「读不到」误读成「0 互动」→ 对最该报警的沉默关系
**漏报**——违背 0 号宪法第 2 条「结果回交给人看」。

**验法（消费方 CI / 运行时）**：
> 对一个**已知分片密钥缺失**的会话调 read：断言**不是**空数组，而是 409 / 工具错误，且体里
> `error.code=="read_unavailable"` + 有 `reason`/`chatId`。reseed 后同会话返回全量历史（每条含 `ts`）。
> 信号体示例 + 机器校验见 `fixtures/read-contract/read_unavailable.json`（`scripts/conformance.py`
> 对它做结构闸）。**运行时兜底**：消费方收到空数组时对照 capabilities `read.coverage`/`unreadableChats`
> 二次确认——是覆盖不全的会话则当作不可读、不喂下游当 0 互动。

---

> 以上口径是**契约的语义部分**，与 `message/canonical.md`（结构 + 映射）配套。任何一条有疑义
> → 回本仓提 PR 改这里，不在各自仓硬编码私自解释（README PR 工作流）。
