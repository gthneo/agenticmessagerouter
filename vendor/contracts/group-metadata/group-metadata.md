# 群组元数据契约 v1 — Group Metadata Contract（成员名册 + 群 meta）

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`group-metadata/` → AMR/@gthneo）。

> **日期** 2026-06-28 ｜ **状态** 提案（待 fullwechat 后端实现）
> **from** AgenticMessageRouter (AMR) ｜ **to** fullwechat 后端
> **真相源** `agentic-contracts` 仓（本文件）—— AMR 定义契约，fullwechat 实现。
> **姊妹契约** `../message/canonical.md`（消息）/ `../moments/read-like.md`（朋友圈）/ `../send-target/send-target.md`（发送）

---

## §0 这份契约是什么 / 不是什么

`message.canonical/1` 已覆盖**群消息逐条**（会话 `type=group` + 每条带发言成员 `sender`/`sender_id` + `is_mentioned` + 群系统事件 `system`）。但它**不提供群的会话级元数据**——尤其**完整成员名册**。AMR 目前只能**从见过的消息 sender 累积推断**成员，看不到没发言的人、拿不到群主/角色。

**本契约补这个缺口**：后端**显式吐群的 meta + 成员名册**，AMR 不再靠推断。

- **本契约是**：群（场）的**只读**会话级元数据 —— 群名/群主/成员列表/角色/公告。
- **本契约不是**：群消息（见 message.canonical）；不是加群/退群/改群名等**写动作**（YAGNI，那些作为 `member_change` system 事件在 message.canonical 里**读**即可）；不是阵营/组织建模（那是 AMR 侧 overlay，§5）。

**真相源**：AMR 仓。后端有疑义 → 提 AMR，AMR 改 spec → 后端按新版实现。

---

## §1 设计原则（同 canonical）

1. **稳定 id 为锚**：成员以 `wxid` 标识（与消息 `sender_id` 同源、可跨账户归一）；群以 `chat_id`（chatroom id）标识。
2. **给得出就给**：群昵称/备注/角色/公告等字段，后端解得出就给，解不出省略，AMR 不报错。
3. **能力声明 + 优雅降级**：roster/meta 各自独立声明；**拿不到 roster → AMR 回落到"从消息 sender 推断成员"（现状）**，不阻塞（见 §4）。
4. **大群可懒**：营销大群（20–500 人）全名册可能不实际 → 后端可只给 `member_count` + 声明 `roster:false`，AMR 用活跃发言者近似。**不强求全量**。
5. **LLM 无关**：纯结构化读，零 LLM。
6. **只读**：本契约无写动作。

---

## §2 群元数据读形态（group envelope）

```json
{
  "schema": "group.canonical/1",
  "channel": "wechat",
  "chat_id": "12345678@chatroom",
  "name": "甲乙丙项目群",
  "owner_id": "wxid_test_owner",
  "member_count": 8,
  "members": [
    { "wxid": "wxid_test_owner", "name": "张三", "alias": "张总", "role": "owner" },
    { "wxid": "wxid_test_lisi",  "name": "李四", "role": "member" }
  ],
  "announcement": "本周五前各自交初稿"
}
```

### §2.1 字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | str | 是 | 固定 `"group.canonical/1"`。 |
| `channel` | str | 是 | 固定 `"wechat"`。 |
| `chat_id` | str | 是 | 群稳定 id（chatroom id），与 message.canonical 会话 `chat_id` 同值。**snake_case**。 |
| `name` | str | 是 | 群名（权威值，优先于 AMR 从消息推断的）。 |
| `owner_id` | str | 否 | 群主 `wxid`；给得出就给。 |
| `member_count` | int | 是 | 成员总数（即便 `members` 只给部分，也给真实总数）。 |
| `members` | arr | 是 | 成员列表（见 §2.2）；大群可只给一部分（配合 `roster` 能力声明，§4）。空给 `[]`。 |
| `announcement` | str | 否 | 群公告正文；给得出就给。 |

### §2.2 `members` 元素

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `wxid` | str | 是 | 成员稳定 id（**归一锚点**，与消息 `sender_id` 同源）。 |
| `name` | str | 是 | 群内显示名（群昵称优先；无则微信昵称）。 |
| `alias` | str | 否 | 我给该成员的**备注名**；给得出就给（对关系账户识别有用）。 |
| `role` | str | 否 | `"owner" \| "admin" \| "member"`；缺省按 `"member"`。**v1 只要 `owner`/`member` 足够,`admin` 可缓到 v1.1**(需真机 roomdata 标志验证;在那之前 admin 当 `member` 处理,不阻塞)。 |

---

## §3 读接口

### 3.1 取单群元数据（必选）
```
GET /api/group/{chat_id}
Authorization: Bearer <token>
```
返回 §2 的 group envelope。`{chat_id}` = message.canonical 里群会话的 `chat_id`（URL encode）。

### 3.2 列出所在群（可选）
```
GET /api/groups?limit=<N>
Authorization: Bearer <token>
```
返回该账号所在群的轻量列表 `[{chat_id, name, member_count}]`（不含完整 members）。可选能力（§4 `list`）；未实现 → AMR 从已入库会话里取 `type=group` 的群,不依赖此接口。

### 3.3 公共约束
- **只读**，bearer auth，HTTPS。
- 找不到群 / 非群会话 → `{ "ok": false, "code": "NOT_A_GROUP", "error": "..." }`（错误形态对齐 send-target §2.1 的 `code`+`error`）。

---

## §4 能力声明 + 优雅降级

`/api/capabilities` 增加 `group` 项：
```json
{ "group": { "meta": true, "roster": true, "list": false } }
```

| 能力 | 含义 | AMR 降级 |
|---|---|---|
| `meta` | 支持 `GET /api/group/{chat_id}`（名/群主/公告/member_count） | `false` → AMR 用 message.canonical 会话的 `name`/`type` 兜底 |
| `roster` | `members` 给**全量** | `false` 或部分 → AMR **回落到从消息 sender 累积推断**成员（现状），用 `member_count` 知道"还有没见过的人" |
| `list` | 支持 `GET /api/groups`（列所在群） | `false` → AMR 从已入库 `type=group` 会话取 |

**大群口径**：`member_count` 大（如 >100）时，后端可声明 `roster:false` 或只给前 N + 总数；AMR 接受部分名册 + 推断补全，不报错。

---

## §5 AMR 侧消费（群=场 / 关系账户，fullwechat 不实现）

对齐设计 spec §1.5（群 = 场 arena）+ §2（事 M:N 挂 persons）：
- **roster → 场的成员**：每个 `member.wxid` → 关系账户(person)（经 wxid 归一）→ M:N 挂到该群(场)。**没发言的人也能进场**（这正是 roster 补的缺口）。
- **阵营/组织**：`member.alias`/`name` + person 的「所属组织」标签（AMR 侧）→ 群内阵营分组（§1.5）。本契约只给原料(wxid/name/role)，阵营是 AMR overlay。
- **@ 解析 / 多方事**：roster 让 AMR 知道"这群里有谁"，支撑 @mention 归人、多方事挂相关 persons。
- **HITL/只读**：roster 是只读信息，无写动作；加群/拉人是对外动作、不在本契约。

---

## §6 口径与边界

| 事项 | 口径 |
|---|---|
| `wxid` 设备后缀 | 同 message.canonical §9.8：成员 wxid 与消息 `sender_id` 同源，设备后缀比对交 AMR 后处理。 |
| 群昵称 vs 备注 vs 微信昵称 | `name`=群内显示名（群昵称优先）；`alias`=我的备注；都给得出就给，AMR 自行取舍展示。 |
| 群名权威性 | 本契约 `name` 为权威；与 message.canonical 会话 `name` 冲突时以本契约为准。 |
| 成员变动 | 实时增减作为 `member_change` system 事件在 message.canonical 里**读**；本契约给的是**当前快照**，AMR 按需重拉。 |
| 退群/解散 | `GET /api/group/{chat_id}` 返回 `{ok:false, code:"NOT_A_GROUP"}` 即可,AMR 标记该场失效。 |
| `member_count` 权威性 | `roster:true` 时为权威值；**`roster:false` 时为 best-effort 参考值**（有权威列用列、否则解析数）。AMR 把它当**近似**「还有多少人没见过」的提示,不做精确依赖。**确认接受参考值**。 |
| `announcement` 格式 | **必须是纯文本**(text 地板)。微信若存成 XML/protobuf 而非纯文本列 → 后端**解析成纯文本再吐;解不出就省略**(announcement 可选),**绝不把 raw XML/protobuf 透传给 AMR**。后端"只在纯文本列时吐"的现状=对。 |
| 头像 | v1 不纳入（YAGNI）。 |

---

## §7 fullwechat 实现侧参考（不占 AMR 契约决策）

| 项 | 实现参考 |
|---|---|
| 成员名册 | 读微信群成员表（`chatroom`/contact.db 的 roomdata 或等效），映射 wxid→群昵称/备注/角色。 |
| 群 meta | 群名/群主/公告从 chatroom 记录取。 |
| 大群 | 成员多时可分页/截断 + 声明 `roster:false`/部分；AMR 接受。 |
| 媒体 | 群头像 v1 不做。 |

---

## 协作约定

后端遇不清楚的口径：**提 AMR，AMR 改 spec，后端按新版实现**。不自行解释契约、不硬编码推断口径。AMR 是唯一真相源。

---

*本契约版本：v1 / 2026-06-28。修订标 v1.1。*
