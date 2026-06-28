# 控制权 & 口吻契约 v1 — Control & Voice Contract（人机互斥 + 口吻喂养）

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`control-and-voice/` → AMR/@gthneo）。

> **日期** 2026-06-28 ｜ **状态** 定稿（fullwechat 起草请求 → AMR 评审落约）｜ **作者** AMR
> **from** AgenticMessageRouter (AMR) ｜ **to** fullwechat 后端（agent-server）
> **真相源** `agentic-contracts` 仓（本文件）—— AMR 定义契约，fullwechat 实现 §5。口吻学习（§4）由 AMR 实现。
> **姊妹契约** `../message/canonical.md`（`authored_by` 并入其中，§4.1）/ `../send-target/send-target.md` / `../group-metadata/group-metadata.md` / `../moments/read-like.md` + `../moments/publish.md`。
> **原始请求** `~/as/fullwechet/docs/control-and-voice-contract-request.md`（本文件是其评审定稿）。

---

## §0 这份契约是什么 / 为什么

把旧的 **viewonly（远程只读看自己微信）** 升级为两件事：

1. **人机控制权互斥** —— 人随时能进自己微信亲手发消息（**服务于人**）；人一动手，Agent 立刻让位、停所有驱动界面的写动作（**风险隔离，不和人抢界面**）；人停手后 Agent 恢复。
2. **口吻喂养** —— 人**亲手发**的消息 = 真实口吻 ground truth，**高权**喂 AI 学口吻；Agent 自己发的**降权/排除**（防 AI 学自己→自我强化→口吻漂移）。AMR 还**主动促人定期手动发**，让起草越来越像本人，同时真人活动增加账号可信度（风控）。

- **本契约是**：控制态（AGENT/HUMAN）的通知 + AMR 侧让位行为 + `authored_by` 标注 + 口吻学习与主动喂养机制 + viewonly 新口径。
- **本契约不是**：安全远程访问（TLS / 认证页 / 订阅关联 / per-客户隔离）—— 那是 fullwechat/ops 侧（原请求 §7），与本契约一体但不在此定义。

**真相源**：AMR 仓。fullwechat 有疑义 → 提 AMR，AMR 改 spec，后端按新版实现。

---

## §1 控制权互斥（两态机，绝不重叠）

| 态 | 谁占界面 | 触发 |
|---|---|---|
| **AGENT** | Agent（默认） | 人输入空闲超阈后恢复 |
| **HUMAN** | 人独占 | 检测到**非 Agent 注入**的输入（人经 noVNC 操作） |

- **检测 = 自动（fullwechat 做）**：agent-server 是唯一经 `/opt/tools` 注入输入者；任何它没注入的 X 输入 = 人在动手 → 进 **HUMAN 态**。人无感，直接用。
- **界面提示（fullwechat 做）**：进 HUMAN 态后，界面上给客户显示「有人在操作」visual indicator，让人知道当前是人占。
- **恢复 = 输入空闲（非 wall-clock）**：HUMAN 态下监测**人输入空闲**（鼠标/键盘 input-idle），空闲超阈 → 人停了 → 恢复 AGENT 态。
  - **阈值口径（AMR 定，王总授权）：`idle_threshold = 120` 秒（可配）**。理由：足够长，让人在任务中途停下读/想时不被 Agent 抢回界面（避免人机抢界面）；足够短，人真离开后 Agent 及时恢复。信号是「输入空闲」不是 wall-clock。fullwechat 用 `xprintidle` 类手段测，AMR 不依赖具体实现，只消费 `state`/`idle_seconds`（§7）。

---

## §2 暂停粒度（AMR 侧让位行为 · 关键破局）

HUMAN 态期间，对该账号：

| 动作 | HUMAN 态 | 原因 |
|---|---|---|
| **写 / 发消息 / 点赞 / 发布 / 任何驱动 GUI 的动作** | **停**（AMR 不调、不 arm、不发） | 否则和人抢界面 |
| **读**（消息/动态/名册） | **照常** | fullwechat 读全走 **SQLCipher 直读库**（message.db/sns.db/contact.db），**不碰 GUI** → 不冲突、界面不变、不用锁 |

**即：界面归人独占，Agent 仍可后台从库里读。** AMR 据此对该账号：
- **暂停**：outbox confirm（真发）、moments like/publish、自动回 arm/countdown、主动 opener 发送 —— 一切对外写。
- **照常**：ingest/poll 读入库、recall、染色、拟草稿（草稿进 `suggestions`/候选，**不发**）、UI 展示。

**实现位置**：本态在 AMR 侧表现为一个**每账号的「人占」闸**，**位于既有 killswitch / 自治挡之上**（最外层）：
```
对外写许可 = (人占闸=AGENT) ∧ (全局 killswitch off) ∧ (会话自治挡 ∈ observe/supervised 的相应许可) ∧ (双闸…)
```
HUMAN 态 → 人占闸关 → 该账号所有对外写直接挡下（候选可继续算、可展示「人占中，暂不发」，但 arm/confirm 被拒）。`human_released` → 人占闸开 → 恢复。

---

## §3 通知 AMR（推为主 + 轮巡兜底）

- **agent-server 主动推（primary）**：进/出 HUMAN 态时推 `control.event`（§7.2）给 AMR —— 快。
- **AMR 轮巡（backup）**：AMR 定期拉 `GET /api/control_state`（§7.1）兜底，防推丢。
- **两者优化 + 隔离，不打架**：
  - **以最新态为准**：每个事件/快照带 `at`/`since`（unix 秒）+ 单调 `seq`（若 fullwechat 给得出）；AMR 取**时间戳最新**的态，丢弃过期。
  - **幂等**：同一态重复推/拉 = no-op（AMR 按 `(account_id, state, since)` 去重）。
  - **推丢了轮巡纠正**：轮巡周期建议 ≤ idle_threshold（如 30–60s），保证最坏情况下数十秒内自愈。
  - **保守兜底**：AMR 拉不到 control_state（接口挂/超时）时，对「已知支持本能力」的账号**默认按 HUMAN 态处理（暂停对外写）**——宁可少发，不可在人可能在场时抢界面。能力声明见 §6。

---

## §4 authored_by + 口吻学习（AMR 重点强化）

### §4.1 `authored_by`（并入 message canonical）

agent-server 知道哪些 out 消息是它自己发的；它没发的 / HUMAN 态发的 out 消息 = 人手动发。**在 message.canonical 信封新增字段**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `authored_by` | `"human"` \| `"agent"` | 否（仅 `direction="out"` 有意义） | out 消息的撰写来源。agent-server 发的 = `"agent"`；其余 out（人在 HUMAN 态手发）= `"human"`。判不准 → 省略（AMR 按「未知」处理，**不计入口吻 ground truth**，保守）。`direction="in"` 不带此字段。 |

- 这是对 message-canonical 契约的**加项**（v1.x 兼容增量）：旧实现不给 `authored_by` 也跑，AMR 缺省按「未知」。fullwechat 实现后并入 canonical 附录。

### §4.2 口吻学习的加权（AMR 侧，防漂移）

| 来源 | 权重 | 为什么 |
|---|---|---|
| `authored_by:"human"` | **高权（ground truth）** | 用户亲手写 = 真声音，是口吻的锚 |
| `authored_by:"agent"` | **降权 / 排除** | 防 **AI 学自己生成的内容 → 循环自我强化 → 口吻漂移**。机器拟稿不是锚 |
| 未知 / 缺 `authored_by` | 不计入 ground truth | 保守：判不准就不污染口吻样本 |

- **红线**：口吻样本池**绝不**把 `agent` 产出当 ground truth 回灌。Agent 起草参考的「本人口吻」只能由 `human` 样本喂养。

### §4.3 ⭐ AMR 主动促人手动发（保持口吻鲜活 · 正循环）

AMR **主动调度**人定期进来手动发，持续补真人样本：

- **追踪**（每账号）：最近一条 `human` out 的时间、近窗口 `human` 样本条数。
- **触发**：当 `human` 样本**陈旧**（如 N 天无 human out）或**量低**（窗口内条数低于阈）→ AMR 在「📞 该联系谁 / 主动」面浮出一张**提示卡**：「该进来手动发几条，保持你的口吻鲜活」。
- **形态 = 提示，非强制**（HITL）：它是 Agent 的**建议**，骑在既有主动调度 + outbox 基础上；人愿意就进来发（真人手发，进 HUMAN 态，§1），不愿意也不阻塞。
- **正循环**：真人活动 → 既增口吻样本（Agent 起草越来越像本人）、又增账号可信度（风控更安全）→ 体系长期更值钱。

---

## §5 fullwechat 实现侧（参考，不占 AMR 契约决策）

- 控制态机（AGENT/HUMAN）+ 自动检测人输入（对比 agent 注入 vs X 实际输入；input-idle 监测如 `xprintidle`）。
- HUMAN 态：FSM / PLAN_LOCK 加「人占检查」，拒绝/挂起 GUI 动作；读路径（DB 直读）不受限。
- 界面「有人在操作」提示（noVNC 可见 overlay/标记）。
- `control_state` 暴露进 `/api/control_state`（或 `/api/health/deep` 内含）；event log 记 `human_takeover`/`human_released`；主动推 AMR。
- out 消息打 `authored_by`（进 canonical 信封，§4.1）。

---

## §6 viewonly 新口径 + 能力声明

### §6.1 viewonly 新口径
viewonly **不再是「锁死只读」**，而是：
- **交互可用**：人能进自己微信亲手用（服务于人），不是只读橱窗。
- **人机互斥**：谁驱动界面由 §1 控制态决定（人一动手 Agent 让位）。
- **暴露需 auth**：远程暴露仍需认证（TLS + 认证页 + 订阅关联，fullwechat/ops 侧，原请求 §7）。

### §6.2 能力声明
`/api/capabilities` 增加 `control` 项：
```json
{ "control": { "state": true, "push": true, "authored_by": true } }
```
| 能力 | 含义 | AMR 降级 |
|---|---|---|
| `state` | 支持 `GET /api/control_state` | `false` → AMR 不做人机互斥（回落旧行为：照常对外写）；**不**默认 HUMAN（无此能力即无此语义） |
| `push` | 主动推 `control.event` | `false` → AMR 仅靠轮巡 |
| `authored_by` | out 消息带 `authored_by` | `false` → AMR 口吻样本全部按「未知」，不区分 human/agent（口吻学习降级，但不报错） |

> 注意 §3 的「拉不到默认 HUMAN」只对**声明了 `state:true`** 的账号生效（即该账号本应有控制态，拉失败才保守暂停）；未声明 `state` 的账号无此语义，照常。

---

## §7 数据形态（契约信封）

### §7.1 控制态快照（轮巡）
```
GET /api/control_state?account=<account_id>
Authorization: Bearer <token>
```
```json
{
  "schema": "control.state/1",
  "channel": "wechat",
  "account_id": "wxid_self_example",
  "state": "agent",
  "since": 1750000000,
  "idle_seconds": 0,
  "idle_threshold": 120,
  "seq": 42
}
```
| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | str | 是 | 固定 `"control.state/1"`。 |
| `account_id` | str | 是 | 账号稳定 id（与 message.canonical `self_id` 同源）。 |
| `state` | `"agent"`\|`"human"` | 是 | 当前态。 |
| `since` | int | 是 | 进入当前态的 unix 秒。 |
| `idle_seconds` | int | 否 | HUMAN 态下人输入已空闲秒数（给得出就给，便于 AMR 显示「即将恢复」）。 |
| `idle_threshold` | int | 否 | 恢复阈（默认 120）。 |
| `seq` | int | 否 | 单调递增序号；给得出就给，AMR 用于排序去重（无则用 `since`）。 |

### §7.2 控制态事件（主动推）
```json
{ "schema": "control.event/1", "channel": "wechat",
  "account_id": "wxid_self_example",
  "event": "human_takeover", "at": 1750000123, "seq": 43 }
```
- `event ∈ {"human_takeover","human_released"}`；`at` = unix 秒；`seq` 同上。
- 推送目标 = AMR 侧约定的接收端点（与既有 ingest 同 bearer auth；具体 URL 由 AMR 配置告知 fullwechat，不写死在契约）。

---

## §8 口径与边界

| 事项 | 口径 |
|---|---|
| 多账号 | 控制态/口吻**按账号独立**。A 号 HUMAN 不影响 B 号 AGENT。 |
| 人占闸 vs killswitch vs 自治挡 | 三者**与（AND）**关系，人占闸最外层；任一不许可即不对外写。人占闸由控制态自动驱动，不需人手动拨。 |
| 读永不暂停 | HUMAN 态只停**对外写 / 驱动 GUI**；读（DB 直读）永远照常，保证 AMR 上下文不断、界面不变。 |
| 拉不到 control_state | 对 `state:true` 账号**保守按 HUMAN（暂停对外写）**；恢复需拿到明确 `state:"agent"`。 |
| `authored_by` 判不准 | 省略 → AMR 按「未知」，**不计入口吻 ground truth**（不污染口吻，也不误判为 human）。 |
| 口吻样本红线 | `agent` 产出**绝不**回灌为口吻 ground truth（防漂移）。 |
| 主动促发 = 提示 | 「保持口吻鲜活」是建议卡，非强制、非自动发；人不进来也不阻塞（HITL）。 |
| 远程安全 | TLS/认证/订阅/隔离归 fullwechat/ops（原请求 §7），固化进 deploy kit + 运维手册；本契约只管控制态与口吻。 |
| 公开仓 | 示例全合成（无真实 wxid/token/PII）。 |

---

## 协作约定

fullwechat 实现 §5（检测 / control_state / `authored_by` / 推送），AMR 实现 §2 人占闸 + §4 口吻学习与主动促发。遇不清楚的口径：**提 AMR，AMR 改 spec，后端按新版实现**。AMR 是唯一真相源。

---

*本契约版本：v1 / 2026-06-28。`authored_by` 并入 message-canonical 后，其字段定义以本契约 §4.1 为准。下次修订标 v1.1。*
