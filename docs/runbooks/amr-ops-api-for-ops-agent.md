# AMR Ops API — 给外部「数字运维工程师 Agent」的对接手册

**适用版本 AMR v0.11.0 ·  更新日 2026-06-28**
**受众**：相邻飞书群里的数字运维工程师 Agent + 现场 FDE（交付工程师）。

这份文档是一份**自包含**的对接说明：让一个**外部监控 Agent** 在**不持有 AMR 主令牌
`JL_WEB_TOKEN`、不接触任何联系人 PII** 的前提下，持续监控 AMR 的运行状态并在异常时**告警给人**。

> **人在回路红线（先读这条）**：监控 Agent 只**观测 + 告警**，**不操作**。所有控制动作
> （killswitch / autonomy 挡位 / outbox 确认发送）是**令牌闸 + 人**的领地，归 FDE / 人，
> 不归外部监控 Agent。本手册只交出**只读、零 PII** 的 `/api/health`，不交主令牌。

---

## Base URL

```
http://<AMR_HOST>:8088
```

`<AMR_HOST>` 为占位符，真实主机地址**线下另行给出**（不写进本仓库文档）。下文示例一律用
`<AMR_HOST>`。

---

## 公开只读端点（无需令牌）

AMR 把这两个端点放在鉴权之前，**公开、无令牌**，专供身份发现与运维监控——它们**不返回任何
PII**（无姓名 / 无 wxid·chat_id / 无消息正文 / 无标签）。其余所有业务端点
（`/api/digest`、`/api/persons`、`/api/conversations` …）都含 PII 且**令牌闸保护**，**不要**
把它们交给外部监控 Agent。

### `GET /api/health` — 运维健康/状态轮询（核心）

返回**纯运维指标 + 状态**：只有计数、布尔、版本号、slot 整数、时间戳。**零 PII**。

示例响应：

```json
{
  "amr_version": "0.11.0",
  "ok": true,
  "ts": 1782600000,
  "killswitch": false,
  "autonomy": {"off": 210, "observe": 0, "supervised": 2},
  "auto_replies": {"armed": 1, "shadow": 0, "human": 3},
  "outbox": {"pending": 0, "failed_recent": 1},
  "backends": [{"slot": 1, "tool": "fullwechat", "reachable": true, "backend_version": "0.12.0"}],
  "events_recent": {"errors_24h": 2},
  "last_event_ts": 1782599000
}
```

**字段逐条解读**（怎么读 / 怎么判异常）：

| 字段 | 含义 | 怎么解读 / 告警条件 |
|---|---|---|
| `amr_version` | AMR 自身软件版本 | 升级核对；与预期版本不符 → 提示 FDE 核对部署 |
| `ok` | 端点自检布尔，恒为 `true`（能返回即健康） | 拿不到响应 / 非 200 / 连接失败 → AMR 服务掉线（见轮询指引） |
| `ts` | 服务端生成本响应的 Unix 秒（`int(time.time())`） | 用它校验响应新鲜度，别用本地时钟 |
| `killswitch` | 全局自动发送急停 | **`true` → 全停**（所有自动发送被禁），属人工干预态；翻转即告警 |
| `autonomy` | 各自治挡位的**会话计数**（`off`/`observe`/`supervised`） | `supervised` 数突增 → 有更多会话被开了监管自动；纯计数，无会话身份 |
| `auto_replies` | 自动回复候选按动作分组的**计数**（`armed`/`shadow`/`human`），或 `null` | **`armed > 0` → 有「已就绪、待人确认」的自动发**，需人留意；`null` = 该指标本次降级（LLM 闸慢/异常），**不代表 AMR 故障**，下次轮询通常恢复 |
| `outbox.pending` | 待发送（待人确认）条数 | 长时间居高不下 → outbox 卡住，提示人处理 |
| `outbox.failed_recent` | 近 24h 发送失败条数 | `> 0` → 有发送失败，多为后端掉线，结合 `backends` 排查 |
| `backends[]` | 各接入后端：`slot`(账号槽整数) / `tool`(fullwechat 等) / `reachable` / `backend_version` | **`reachable:false` → 后端掉线**；`reachable:null` → 本次探测降级（探测慢/异常），非确定掉线，连续多轮 null 才告警 |
| `events_recent.errors_24h` | 近 24h 错误计数（**当前用「近 24h 失败 outbox 数」作代理指标**，见下方说明） | **数值偏高 → 排查**（多半后端/发送链路有问题） |
| `last_event_ts` | 最近一条 HITL 审计事件的 Unix 秒，或 `null` | 长时间不前进 → 系统可能空转/无人操作，按需提示 |

> **`backends[]` 为什么没有 `host` / `self_id`**：故意省略。`host` 可能泄露内网 IP，
> `self_id` 是账号身份——两者都不属于「运维该看的 PII-free 指标」，所以 egress 投影里只留
> `slot` / `tool` / `reachable` / `backend_version`。
>
> **`errors_24h` 的口径说明（实现者诚实声明）**：AMR 的 `events` 审计表**没有显式的
> error/failed 标记**（事件 kind 是 sweep/reach/send/… 这类业务动作）。所以本版用**近 24h
> `status='failed'` 的 outbox 条数**作为错误代理指标，与 `outbox.failed_recent` 同源。它能稳定
> 反映「发送链路出错」，但**不覆盖**非发送类内部错误。后续若 events 引入显式 error 标记，本字段
> 口径会升级（届时更新本手册版本戳）。

### `GET /api/version` — 身份 + 消费清单

```json
{"amr_version": "0.11.0", "consumes": {"message.canonical": "1", "send-target": "v1", "...": "..."}}
```

`amr_version` = AMR 软件版本；`consumes` = AMR 当前**消费**的契约版本清单（双向版本握手的消费侧）。
用于升级前核对兼容性。同样**零 PII、无令牌**。

---

## 轮询指引

- **间隔**：建议 **60s** 轮询一次 `/api/health`（运维监控足够；别更密，无意义压服务）。
- **服务存活判定**：HTTP 请求失败 / 超时 / 非 200 → 视为 **AMR 服务掉线**，立即告警人。
  （`ok` 字段恒 `true`，所以「拿不到 ok」本身就是信号，不要只看 `ok` 的值。）
- **告警条件**（满足任一 → 告警给人，附上原始 JSON 片段，不要自己动手修）：
  1. `killswitch` 从 `false` 翻成 `true`（或反之）——人工干预态变化。
  2. 任一 `backends[].reachable == false`（**连续 ≥2 轮**确认，避免单次抖动误报）。
  3. `events_recent.errors_24h` 超过阈值（建议默认 **>5** 起关注，按现场基线调）。
  4. `outbox.pending` 卡住不降（如连续 >15min 同一非零值）——待确认队列堆积。
  5. `amr_version` 与预期部署版本不符——可能误部署/回滚。
- **降级语义别误报**：`auto_replies == null` 或 `backends[].reachable == null` 是**指标本次
  降级**（探测/LLM 闸慢或异常），**不是 AMR 故障**。仅在**连续多轮**仍为 null 时才升级为告警。

---

## 控制边界（红线 · 不可逾越）

| 动作 | 端点 | 谁能做 |
|---|---|---|
| 观测健康/状态 | `GET /api/health`、`GET /api/version` | **外部监控 Agent ✅**（无令牌） |
| 急停 / 解除急停 | `POST /api/killswitch` | FDE / 人 ❌（令牌闸，外部 Agent 不可） |
| 改会话自治挡位 | `POST /api/autonomy` | FDE / 人 ❌（令牌闸） |
| 确认发送 outbox | `POST /api/outbox/confirm` | FDE / 人 ❌（令牌闸） |
| 任何含 PII 的业务读取 | `/api/digest`、`/api/persons`、`/api/conversations` … | FDE / 人 ❌（令牌闸，含 PII） |

**人在回路**：监控 Agent **只观测、只告警**；写 / 发 / 急停 / 改挡位一律走**令牌闸 + 人**的
确认（dry-run → 人确认 → 执行），每次人工干预留痕（谁 / 何时 / 为什么）。外部 Agent **永远不
持有主令牌 `JL_WEB_TOKEN`**，也就**无法**触达上面任何一个控制/PII 端点——这是设计上的硬隔离。

---

## 如何接入（curl 示例）

```sh
# 健康轮询（无令牌、零 PII）
curl -s http://<AMR_HOST>:8088/api/health

# 身份/版本
curl -s http://<AMR_HOST>:8088/api/version

# 一个最小轮询循环（60s）：靠 jq 取关键告警字段
while true; do
  curl -s http://<AMR_HOST>:8088/api/health \
    | jq '{ok, killswitch, errors_24h: .events_recent.errors_24h,
            pending: .outbox.pending,
            down: [.backends[] | select(.reachable==false) | .slot]}'
  sleep 60
done
```

监控 Agent 把上面这类**只读轮询**接进自己的告警逻辑即可：发现红线条件 → 在飞书群里**@人**
告警并附 JSON 证据，**不要**尝试任何写操作（也无令牌可写）。
