# 朋友圈动作契约 v1 — Moments Action Contract（读 + 点赞）

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`moments/` → AMR/@gthneo）。

> **日期** 2026-06-28 ｜ **状态** 提案（待 fullwechat 后端评估实现）
> **from** AgenticMessageRouter (AMR) ｜ **to** fullwechat 后端
> **真相源** `agentic-contracts` 仓（本文件）—— AMR 定义契约，fullwechat 实现。
> **姊妹契约** `../message/canonical.md`（会话消息通道）/ 发布广播见同目录 `publish.md`
> **适用范围** 微信朋友圈（Moments）：动态读取 + 点赞动作。**不适用于会话消息**（见 §0）。

---

## §0 这份契约是什么 / 不是什么

微信的数据面分两块：

- **消息通道（Message Channel）**：会话消息，存 `message.db`，姊妹契约已覆盖。
- **社交动态面（Moments / 朋友圈）**：一对多广播 feed，存 `sns.db SnsTimeLine`（独立 WebView），**本契约覆盖**。

两者是**平行层**，技术来源不同、消费语义不同，不能混用一份契约。

**本契约是**：朋友圈 read（读动态信封）+ like（点赞写动作）的 AMR↔fullwechat 接口约束。  
**本契约不是**：会话消息（见姊妹契约）；不是评论（v1 YAGNI，§7 留位）；不是广泛的 Moments 社区运营规则。

**真相源**：AMR 仓。后端有疑义 → 提 AMR，AMR 改本 spec → 后端按新版实现。**后端不自行解释契约**。

---

## §1 设计原则

1. **`text` 永远在场**。每条 moment 无论是纯图/纯视频还是分享链接，都带一个人类可读的 `text`（正文文字；无文字时给占位 `[图片]` / `[视频]` / `[链接]`）。这是展示地板，任何消费方只读 `text` 也不丢可用性。
2. **媒体 `ref` = fullwechat 取件端点绝对 URL，非 cdnurl**。微信 cdnurl 加密、AMR 端拿到也无法解密；`ref` 必须指向 fullwechat 已上线的 `/api/media/{chat_id}/{msg_id}` 端点（或同等，返回解密后字节），AMR GET 时带 bearer auth。与姊妹契约 `media.ref` 语义完全一致。
3. **能力声明 + 优雅降级**。后端暴露 capabilities；每项能力（read / like）独立声明；能力不可用时 AMR 侧静默降级（见 §6）。
4. **写动作幂等**。点赞端点对已赞动态再赞 = no-op，正常返回 `"liked":true`。AMR 可安全重试。
5. **HITL 最高准则（王总钦定）**。点赞是写操作、对外动作。**AMR 永不自动赞**。排期/候选由 AMR 决定，人批量审一次后，AMR 才逐条调点赞端点。后端 like 端点是"哑执行"——只管执行，不决定赞不赞。
6. **LLM 无关**。朋友圈面是纯结构化读取 + 确定性写动作，零 LLM 参与。不因模型不可用而降级功能。

---

## §2 Moment Envelope（规范化读形态）

后端对每条 moment emit 一个信封。JSON 形态（合成占位值，无真实 PII）：

```json
{
  "schema": "moment.canonical/1",
  "channel": "wechat",
  "tid": "snsTimeLine_wxid_test_zhangsan_1750000001",
  "author": {
    "name": "张三",
    "wxid": "wxid_test_zhangsan"
  },
  "create_time": 1750000001,
  "text": "今天天气不错，出去走走",
  "media": [
    {
      "kind": "image",
      "placeholder": "[图片]",
      "ref": "https://fullwechat.host/api/media/moments/snsTimeLine_wxid_test_zhangsan_1750000001/0",
      "mime": "image/jpeg"
    }
  ],
  "link": null,
  "liked": false,
  "direction": "in"
}
```

分享链接类 moment（纯分享、无正文）：

```json
{
  "schema": "moment.canonical/1",
  "channel": "wechat",
  "tid": "snsTimeLine_wxid_test_lisi_1750000099",
  "author": { "name": "李四", "wxid": "wxid_test_lisi" },
  "create_time": 1750000099,
  "text": "[链接] 2026年中国制造业白皮书",
  "media": [],
  "link": {
    "title": "2026年中国制造业白皮书",
    "url": "https://example.com/whitepaper",
    "source": "行业观察"
  },
  "liked": true,
  "direction": "in"
}
```

### 2.1 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | str | 是 | 契约版本标识，v1 固定 `"moment.canonical/1"`。 |
| `channel` | str | 是 | 固定 `"wechat"`（v1 仅微信朋友圈）。 |
| `tid` | str | 是 | 动态稳定 id，来自 `sns.db SnsTimeLine` 主键或等效稳定标识。**点赞端点用此字段**，必须在同一后端实例内唯一且稳定（不随登录失效/重新抓取变化）。 |
| `author` | obj | 是 | 发布人。`name`=显示名，`wxid`=稳定 wxid（供 AMR 关系账户归一）。 |
| `create_time` | int | 是 | unix 秒（发布时间）。 |
| `text` | str | 是 | 正文文字，**永远非空**。纯图动态给 `[图片]`；纯视频给 `[视频]`；分享无正文给 `[链接] <链接标题>`。 |
| `media` | arr | 是 | 图片/视频列表，无则空数组 `[]`。字段见 §2.2。 |
| `link` | obj \| null | 是 | 分享链接，仅分享类动态有；纯图文给 `null`。字段见 §2.3。 |
| `liked` | bool | 是 | **我（self）是否已赞**此条动态。AMR 点赞前先读此字段做幂等判断：`liked:true` → 跳过调用（本地幂等）；`liked:false` → 才调端点。后端从 `sns.db` 的赞记录里查。 |
| `direction` | `"in"` \| `"out"` | 是 | `"out"` = 自己发布的动态（self 是 author）；`"in"` = 别人发的动态。判不准时默认 `"in"`（保守）。 |

### 2.2 `media` 元素字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | `"image"` \| `"video"` | 是 | 媒体类型。 |
| `placeholder` | str | 是 | 文字占位：图片给 `"[图片]"`，视频给 `"[视频]"`。AMR 无法渲染媒体时回退显示此字符串。 |
| `ref` | str | 是 | 取件端点绝对 URL（fullwechat 已上线的解密字节端点）。**非微信 cdnurl**。AMR GET 时带 bearer auth（`Authorization: Bearer <token>`）。路径格式建议 `/api/media/moments/{tid}/{index}`（与消息通道 `/api/media/{chat_id}/{msg_id}` 同族）。 |
| `mime` | str | 否 | MIME 类型，如 `"image/jpeg"` / `"video/mp4"`；给得出就给。 |
| `duration` | int | 否 | 视频时长（秒），仅 `kind="video"` 时有意义；给得出就给。 |

### 2.3 `link` 对象字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `title` | str | 是 | 链接标题（分享文章/网页的标题）。 |
| `url` | str | 否 | 链接 URL；给得出就给（部分分享类 sns 条目可能无 url）。 |
| `source` | str | 否 | 来源名称（公众号名 / 站点名等）；给得出就给。 |

### 2.4 口径说明

- **`text` 地板**：结构化字段（`media` / `link`）给得出就给；给不出时，AMR 凭 `text` 也能正常展示动态摘要。`text` 和 `media`/`link` 不互斥——有文字 + 有图 = `text` 给正文文字，`media` 给图片列表。
- **`tid` 稳定性**：`tid` 必须在后端稳定（不随抓取批次变化）；AMR 用它做幂等判断 + 点赞调用。后端若 `tid` 不稳定，点赞端点无法幂等。
- **`liked` 精度**：从 `sns.db` 现有赞记录查，精度取决于本地 DB 同步状态；已知偏差 → 在 capabilities 里标注（见 §6）；AMR 按 `liked` 做本地幂等，但最终幂等由点赞端点保证（§4）。

---

## §3 读接口契约

### 3.1 按人读

```
GET /api/moments?person=<wxid>&limit=<N>
Authorization: Bearer <token>
```

- 返回指定 `wxid` 的人最近 N 条 moment envelopes（按 `create_time` 倒序）。
- AMR 读某人动态、选出候选 `tid` 去赞时走此接口。
- `person` 参数值 = `author.wxid`。
- `limit` 默认 20，上限后端自定（建议 ≤100）。

### 3.2 全 feed 读（可选）

```
GET /api/moments?limit=<N>
Authorization: Bearer <token>
```

- 不带 `person` 参数 → 返回整个朋友圈 feed（所有联系人）近 N 条，按 `create_time` 倒序。
- 此接口是**可选能力**；capabilities 里标 `"feed_all": true/false`（见 §6）。
- 后端若暂不实现，返回 HTTP 400 / capabilities 声明 `feed_all:false`，AMR 静默隐藏。

### 3.3 公共约束

- **只读**，bearer auth，HTTPS（对齐 §14 TLS 方案）。
- 响应体：JSON 数组 `[<moment envelope>, ...]`（空则 `[]`）。
- 后端**按 `create_time` 倒序**返回（最新在前）。
- 无分页 cursor（v1 YAGNI；AMR 按 limit 截取够用）。

---

## §4 点赞动作契约

### 4.1 端点

```
POST /api/moments/{tid}/like
Authorization: Bearer <token>
Content-Type: application/json
```

- `{tid}` = moment envelope 里的 `tid` 字段（URL encode 如含特殊字符）。
- 请求体可为空（`{}`）；v1 无额外参数。

### 4.2 响应

**成功（含已赞幂等）**：

```json
{ "ok": true, "tid": "snsTimeLine_wxid_test_zhangsan_1750000001", "liked": true }
```

**失败**：

```json
{ "ok": false, "tid": "snsTimeLine_wxid_test_zhangsan_1750000001", "error": "moment not found" }
```

- HTTP 状态码：成功 `200`；`tid` 不存在 `404`；token 无效 `401`；后端未实现 `501`（AMR 按 capability 声明判断，不依赖 501 才知道）。

### 4.3 幂等语义

- **已赞再赞 = no-op**，返回 `"ok":true, "liked":true`，不报错、不重复发送点赞动作到微信。
- 后端在执行前先查本地赞记录（`sns.db`）；若已赞，直接返回，不触发 a11y 点击。
- AMR 侧也做本地幂等（读 `liked` 字段跳过），但**最终幂等由后端保证**（网络重试场景）。

### 4.4 v1 不做取消赞

点赞是正向关系交互（王总钦定）。v1 不实现取消赞（unlike）。  
扩展位已留：未来可用 `DELETE /api/moments/{tid}/like` 或 `POST .../unlike`，后端届时在 capabilities 声明 `"unlike":true`。  
AMR v1 不会调取消赞端点，后端无需实现。

### 4.5 HITL 铁律（再次申明）

后端 `/api/moments/{tid}/like` 是**哑执行端点**：收到调用就赞，不做策略判断。  
策略（赞谁 / 何时赞 / 候选优先级）完全在 AMR 侧：

```
AMR 读 feed → 生成候选列表（谁的哪条 tid）
           → 展示给人批量审核
           → 人一次性批准（选 + 确认）
           → AMR 逐条调 POST /api/moments/{tid}/like
```

**系统永不跳过人的批准直接赞**（不管候选看起来多明显）。

---

## §5 AMR 侧「事」模型——点赞 = 一次交互（AMR 消费语义，fullwechat 不实现）

本节是 AMR 内部模型，**fullwechat 后端无需实现**；记录在此是为了让后端理解 AMR 为何这样设计接口。

### 5.1 点赞 = 「维护某人」事下的一次交互

对齐 AMR 仓设计文档 `2026-06-28-supervised-agentic-router-design.md` §2 的事模型：

- **事（matter）**：per-person 的长期维护关系（`type: "关系维护"`，与落地业务事同级别头等对象）。
- **一次点赞 = 该「维护某人」事下的一条 interaction 记录**（commitment event / 互动轨迹）。
- 归一路径：`moment.author.wxid` → 关系账户（person）→ 该 person 的「维护」matter（无则候选新建、人确认）→ 挂 interaction 记录。

### 5.2 点赞把「广播动态」转成「对某人的定向交互」

朋友圈动态本质是广播（一对多）；AMR 点赞后，它在 Router 里变成：  
**「我 → 张三」的一次互动信号**，喂关系账户染色（加权），推高张三的关系温度。

这是 §1.5（群 = 场）逻辑在社交动态面的对称体现——广播面里的信号被 AMR 转换成一对一关系账户的定向交互记录。

### 5.3 HITL = 批量审一次（不逐条、不全自动）

人的介入形态：  
AMR 按染色/关系温度/上次互动时间排出候选列表（"该点赞的人 × 动态"）→ **人在一个 review 界面批量看、打勾或去掉** → 一次确认 → AMR 执行。  
不逐条弹 confirm（太烦），不全自动（违反 HITL）。

### 5.4 排期归 AMR，不归后端

「今天赞哪几个人」的排期策略（频次 / 时间窗 / 关系档位 / 上次点赞距今）完全在 AMR 内部决定，后端只提供读接口和哑执行接口。

---

## §6 能力声明 + 优雅降级

### 6.1 capabilities 声明

后端在 `/api/capabilities`（已上线，姊妹契约）中增加 `moments` 项：

```json
{
  "schema": "message.canonical/1",
  "channel": "wechat",
  "moments": {
    "read": true,
    "feed_all": false,
    "like": true,
    "unlike": false
  }
}
```

| 字段 | 含义 |
|---|---|
| `read` | 是否支持 `GET /api/moments?person=<wxid>` |
| `feed_all` | 是否支持 `GET /api/moments`（不带 person 的全 feed）|
| `like` | 是否支持 `POST /api/moments/{tid}/like` |
| `unlike` | v1 固定 `false`（扩展位） |

若后端尚未实现 moments 任何能力，`"moments"` 项可整体缺失，AMR 按「全部 false」处理。

### 6.2 降级矩阵

| 后端能力 | AMR 行为 |
|---|---|
| `read:false` 或 moments 整体未实现 | AMR 朋友圈面不显示（静默隐藏，非报错）；其他功能不受影响 |
| `read:true, like:false` | AMR 展示动态但不提供点赞动作（人仍可手动去原生微信赞）；`like` 入口灰掉 |
| `read:true, like:true` | 完整功能：读 feed + 批量候选 + 人确认 + 点赞执行 |
| `feed_all:false` | AMR 只支持 per-person 读，不提供全 feed 浏览（功能收敛，非降级） |
| like 端点调用失败（网络/超时） | AMR 标记该 `tid` 失败、保留在待处理队列、人下次可重试；不静默丢弃 |

### 6.3 LLM 无关（再申明）

朋友圈面：读动态 = 纯结构化解析；点赞 = 确定性写动作。**零 LLM**。  
即使所有模型不可用，朋友圈 read + like 正常运行（完美满足 LLM-optional 铁律）。

---

## §7 口径与边界

| 事项 | 口径 |
|---|---|
| `media.ref` 路径 | 复用已上线 `/api/media` 端点族，路径建议 `/api/media/moments/{tid}/{index}`；与消息通道 `/api/media/{chat_id}/{msg_id}` 同族，同一 bearer auth。 |
| `tid` 来源 | `sns.db SnsTimeLine` 的稳定主键或等效稳定字段；具体列名后端确认（AMR 不依赖具体列名，只要求 tid 稳定）。 |
| `direction` 判断 | 后端对照 self wxid 与 `author.wxid` 判定；同一 wxid 设备后缀问题（姊妹契约 §9.8 冲突）同样适用。判不准 → 默认 `"in"`，不阻塞。 |
| `liked` 精度 | 依赖 `sns.db` 本地赞记录同步状态；若后端确认无法可靠读取，声明 `"liked_reliable": false`（扩展字段），AMR 跳过本地幂等、仅依赖端点幂等。 |
| 评论（comments） | v1 不纳入（YAGNI）。扩展位已留：未来 `GET /api/moments/{tid}/comments` + `POST /api/moments/{tid}/comment`，capabilities 另开 `"comment":true`。 |
| 多媒体 moment（9 图）| `media` 数组按序列出所有图片（index 0–N），每项各自有 `ref`。AMR v1 只展示第一张缩略 + 总数（UI 细节，不影响本契约）。 |
| 自己的 moment（`direction:"out"`）| AMR 读取展示、不对自己的动态执行点赞动作（前端屏蔽）。`liked` 字段对 `"out"` 动态意义不大，后端可给 `null`。 |

---

## §8 fullwechat 实现侧（参考，不占 AMR 契约决策）

本节是 AMR 对后端实现路径的了解，记录在此帮助后端定向；**AMR 不 own 此节，后端实现方式可自行调整**，只要暴露 §2–§6 定义的接口形态。

| 项 | 实现参考 |
|---|---|
| 动态读取 | 读 `sns.db SnsTimeLine`；`GET /api/moments?person=<wxid>` 后端已有初始实现，加 `person` 过滤 + `liked` 字段 + 信封格式对齐本契约。 |
| 点赞执行 | 走 a11y（朋友圈 WebView，`--force-renderer-accessibility` spike 进行中）。**a11y spike 未完成 → 声明 `like:false`；AMR 读-only 先上，不因点赞阻塞读端上线**。 |
| 媒体 `ref` | 复用已上线 `/api/media` 端点；朋友圈图片/视频的解密 + 取件逻辑与消息通道同族，统一 bearer auth。 |
| `tid` 稳定性 | 建议使用 `SnsTimeLine.id` 或 `SnsTimeLine.FeedId`（后端核实哪个稳定）；不用 `create_time` 做 tid（时间戳不唯一）。 |
| `liked` 查询 | 从 `sns.db` 赞相关表查 self 是否已赞；若表结构不确定，提 AMR 澄清。 |

---

## 协作约定

后端 Agent / 工程师遇到任何不清楚的口径：**提 AMR，AMR 改 spec，后端按新版实现**。  
不要自行解释契约、不要在后端硬编码推断出来的口径——有疑义的地方对 AMR 提问，让 AMR 写进 spec，再实现。这保证 AMR 是唯一真相源，不让后端各自猜、日后行为漂移。

---

*本契约版本：v1 / 2026-06-28。下次 minor 修订标 v1.1，major 改动另开新文件。*
