# PowerData 多账户 MCP 契约 — 提案 (给 powerdata 维护者)

> 适用：`gthneo/powerdata`（微信4.0 解密器 + MCP server）。消费方：AgenticMessageRouter (AMR)。
> 日期：2026-06-18。状态：提案，待 powerdata 侧评估。

## 背景 / 问题

PowerData 的**配置层**已支持「一机多号」(微信多开)：`config.json` 的 `accounts[]` 每个登录账号一套
`db_dir` / `keys_file`，解密到 `decrypted/<wxid>/`。

但 PowerData 的 **MCP 表面层**（8765 上的 11 个只读工具：`get_recent_sessions` /
`get_chat_history` / `search_messages` / `get_contacts` / `get_contact_tags` /
`get_tag_members` / `get_new_messages` / `get_chat_images` / `decode_image` /
`get_date_stats` / `health`）**没有任何 `account` 参数**——一个 MCP server 只服务其 config
指向的那一个账号。

后果：要在**同一台机**上经 MCP 读 N 个号，得起 N 个 server 实例 / N 个端口 / N 份 config。
**配置层支持多号、接口层不暴露多号**，这是内部不一致。AMR 现在只能用「一个 PowerData URL = 一个号」
来区分账号（已据此修掉适配器里无效的 `account` kwarg）。

## 提案：薄、可选、向后兼容的多账户契约

### 1. 新增 `list_accounts() -> str`
返回本机已登录、可服务的账号清单，供消费方发现一机多号：
```
账号 (2):
  wxid_AAAA  label: 主号A   active: true   db_ready: true
  wxid_BBBB  label: 主号B   active: true   db_ready: true
```
（结构对齐 config 的 `accounts[]`；`db_ready` = 该号的解密库就绪可查。）

### 2. 账号作用域工具加可选参数 `account: str = ""`
给以下「按账号取数」的工具加可选 `account`（取 **wxid**，base wxid，不带设备后缀）：
`get_recent_sessions` / `get_chat_history` / `search_messages` / `get_contacts` /
`get_contact_tags` / `get_tag_members` / `get_new_messages` / `get_chat_images` /
`decode_image` / `get_date_stats`。

- **空 / 省略 → 首账号**（= 现行行为，完全向后兼容）。
- 传入未知 wxid → **明确报错**，不要静默退回首账号（避免读错号还不自知）。

### 3. `get_new_messages` 游标按账号隔离
其「自上次调用以来的 diff」游标必须按 `(client, account)` 维护，
否则多账号轮询会互相串扇区、丢消息或重复。

### 4. 出站 ingest webhook 带账号标识
`notify_filter` 的出站事件 envelope 增加 `account_wxid` 字段，
让消费方（AMR / OpenClaw bridge）把推来的消息路由到正确的账号。
（与 SCHEMA_VERSION 对齐；保持 customer-bridge 兼容。）

### 5. `get_chat_history` 展开 app 消息（type 49），不要塌成「[链接/文件]」
**现状问题**：`get_chat_history` 的文本导出把 app 消息（微信 type 49：链接卡 / 文件 /
引用回复 / 小程序）统一塌成占位符 `[链接/文件]`，**真内容丢失**——消费方既存不下、
也无从恢复（无结构化 raw）。AMR 实测：account 4(mwin) 有 48 条会话/feed 消息因此只剩占位符。

**对比**：fullwechat 后端对同类 type-49 已给出可读文本（引用回复原文 / 文件名 /
`[Link] 标题\n<url>`），AMR 直接可用。PowerData 这一层是短板。

**期望**：`get_chat_history`（及 `get_recent_sessions` 预览、`get_new_messages`）对 app 消息
至少给出**可读摘要**，按子类型：
- **链接卡**：`标题` + `url`（如 `[链接] <title>\n<url>`）。
- **文件**：`文件名`（如 `[文件] 季度报表.pdf`）。
- **引用回复**：回复正文（可附 `↩ 引用:<被引摘要>`）。
- **小程序/卡片**：`卡片标题`。

**更优（可选）**：在结构化输出里附 `app_type` / `title` / `url` / `filename` 字段，
让消费方既能展示又能入库检索（与「Structured Output」AI-Native 套路一致）。

**向后兼容**：纯文本消费方仍能读到一行可读文本；无法解析的 app 消息再退回 `[链接/文件]`。

## 兼容性

全部可选：单账号用户、现有 stdio / SSE 客户端零改动；`account` 省略即旧行为。
建议 minor 版本号 +1，并在 `docs/08-mcp-interface.md` 补签名与示例。

## AMR 侧落地（契约就绪后）

- `accounts` 表的 `account_id ↔ self wxid` 天然对应 `account` 参数。
- 适配器重新引入 `account`（这次对齐真实契约 = base wxid），**一机一 URL + 每号一参数**，
  取代「一号一 URL / 一机 N 服务」。
- `list_accounts` 用于自发现一机多号；出站 `account_wxid` 用于落库路由。

## 不做（YAGNI）

- 不要求 PowerData 做账号级**写/发**（仍只读；发送走可发渠道）。
- 不要求跨机聚合（多机仍是多 URL；契约只解决「一机多号」）。
