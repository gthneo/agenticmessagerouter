# 发送目标契约 v1 — Send Target Contract（冷会话自动翻出）

> **日期** 2026-06-28 ｜ **状态** 提案（待 fullwechat 后端实现）
> **from** AgenticMessageRouter (AMR) ｜ **to** fullwechat 后端
> **真相源** AMR 仓（本文件）—— AMR 定义契约，fullwechat 实现。
> **姊妹契约** `2026-06-26-message-canonical-contract-v1.md`（读）/ `2026-06-28-moments-action-contract-v1.md`（朋友圈）

---

## §0 这份契约修什么（交互缺陷）

**现状缺陷（王总 2026-06-28 钦定为契约 bug）**：fullwechat 发送只能发给**当前在微信"近期会话列表"里、能选中**的会话；目标若不在（冷会话），AMR 适配器（`fullwechat.send`，`_live_chat_ids` 预检）**拒发并要人去 VNC 手工打开**那个对话。

这把**后端的 UI 局限甩给了人**——像「王珺熙」这种**已知联系人**（AMR 早有她稳定的 `chat_id`/`wxid`），系统理应**自己把她翻出来**（搜索→打开会话→发），不该让人手工去 VNC 翻。

**本契约要求**：发送端对**任何已知 `chat_id`**，在目标不在近期列表时**自动解析 + 打开会话**再发，把"先手动开聊天"这步**自动化掉**。

---

## §1 硬性要求

1. **可发任意已知会话**：发送端接受一个稳定 `chat_id`（AMR 从读契约/入库数据里早已持有），**不要求它当前可选中**。
2. **不可选中 → 自动翻出**：目标不在近期列表时，后端**自动搜索联系人 + 打开其会话**（进入可选中态）后再发。这一步是**机械动作（找到正确窗口），不是决策**——人已经在 AMR 侧确认过发什么（HITL 不变）。
3. **按稳定 id 解析，绝不模糊猜**：解析以 `chat_id`/`wxid`（稳定标识）为准。**严禁按显示名模糊匹配后误发给同名/相近的人**。若 `chat_id` 解析不到唯一会话 → **返回可执行错误**（`ok:false` + 原因），**绝不瞎发**（保留现有 "Never guesses a target" 安全底线）。
4. **回执标明是否自动翻出**：成功响应里带 `opened`（bool）—— `true` = 后端为这次发送自动打开了冷会话；`false` = 本就可选中。供 AMR 留痕/可观测。

---

## §2 端点形态

复用现有发送端点（AMR 经 `fullwechat.send_text(chat_id, body)` 调）。语义升级为：

**请求**：发送某 `chat_id`（不再前置要求它在近期列表）。

**成功**：
```json
{ "ok": true, "chat_id": "wxid_test_wangjunxi", "opened": true }
```
（`opened:true` = 后端自动翻出并打开了该冷会话）

**失败（解析不到唯一会话，不猜不发）**：
```json
{ "ok": false, "chat_id": "wxid_test_wangjunxi", "error": "chat_id 解析不到唯一会话（联系人不存在/多匹配）" }
```

---

## §3 能力声明 + 优雅降级

后端在 `/api/capabilities` 增加 `send` 项：
```json
{ "send": { "auto_open": true } }
```

| `send.auto_open` | AMR 行为 |
|---|---|
| `true` | AMR **直接发任意已知 chat_id**，撤掉 `_live_chat_ids` 预检拒发；冷会话由后端自动翻出，**人不再去 VNC 手工开**。 |
| `false`（或缺失） | AMR 保留现状：`_live_chat_ids` 预检 + 冷会话给"先在微信打开 TA 的对话再发"的可执行提示（过渡期 fallback，不阻塞）。 |

这样后端**未实现前**AMR 不报错（现状 fallback），**实现并声明后**AMR 自动启用直发——平滑切换。

---

## §4 HITL + 安全（不破底线）

- **HITL 不变**：发送仍只在**人在 AMR 侧确认后**才发（选 1/3 话术 → countdown → 发）。后端"自动打开会话"是**机械寻址**，不替人决定发不发。
- **不误发**：解析以稳定 id 为锚；模糊/多匹配 → 报错不发（保留 "Never guesses a target"）。**自动翻出 ≠ 自动乱发**。
- **留痕**：`opened` 字段进 AMR 审计（这次是否触发了自动翻出）。

---

## §5 AMR 侧职责

- 收到 `send.auto_open:true` 能力声明后：**放宽** `src/jl/channels/fullwechat.py` 的 `send()` —— 不再用 `_live_chat_ids()` 预先拒发，直接把 `chat_id` 交后端（后端负责翻出）。失败仍按 `ok:false` 的 error 展示 + 「重试」。
- 能力为 `false`/缺失时：维持现状（预检 + 手动 fallback 提示）。
- AMR 永远按**稳定 chat_id** 发，不向后端传模糊名。

---

## 协作约定

后端 Agent / 工程师遇到不清楚的口径：**提 AMR，AMR 改 spec，后端按新版实现**。不自行解释契约、不硬编码推断口径。AMR 是唯一真相源。

---

*本契约版本：v1 / 2026-06-28。修订标 v1.1。*
