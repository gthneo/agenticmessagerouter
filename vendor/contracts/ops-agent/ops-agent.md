# 【来自 fullwechat】运维 Agent 契约 v1 — 供 AMR知悉

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`ops-agent/` → **fullwechat 持有**，是本仓唯一非 AMR-owned 的契约）。
> 本目录收录的是 fullwechat 完整运维契约的 AMR 知悉摘要；fullwechat 完整契约维护在其本仓 `~/as/fullwechet/docs/ops-agent-contract-v1.md`。运维契约的变更由 fullwechat（ops-agent reviewer）批，不由 AMR 批。

> 2026-06-28 ｜ 发起:fullwechat ｜ 收:AMR ｜ 已 dogfood 真实验证
> **真相源(完整契约,绝对路径)**:`~/as/fullwechet/docs/ops-agent-contract-v1.md`
> 注意:运维契约**由 fullwechat 起草并持有**(它约束"怎么运维 fullwechat 后端自己",区别于 message/send/group/moments 那些**由 AMR 持有**的业务契约)。本文件让你(AMR)**知悉边界**,不用你实现——但 AMR 侧接入/发送时要据此对齐(尤其 §对外动作失败的回执语义)。

## 为什么现在给你看(班迪2727 真实失败触发)
今天 AMR 收件箱给「班迪2727」发消息 `发送失败: timed out`。借这次**真实生产失败**把运维契约验证 + 补全了,几个点 AMR 侧要对齐:

1. **失败是可观测的**:fullwechat `GET /api/events` 把这次失败结构化记下了:
   `{"kind":"action","action":"send","ok":false,"errorCode":"Command timed out","opened":false,"chatHash":"…"}`。
   → AMR 侧若要展示/重试/告警发送失败,**可以读后端 event log 拿到结构化原因**,不用猜。
2. **`opened` 字段是分诊关键**:
   - `opened:false` = 后端**连会话都没打开就超时**(开会话/定位卡死,偏系统性,底层常是 frida/GUI/PLAN_LOCK)。
   - `opened:true` + 失败 = 开了会话但发不出(单次输入/点击层)。
   → **AMR 的重试策略应据此分流**:`opened:false` 连续出现 = 后端发送链路整体故障,**别无脑快重试**(浪费 + 可能加重),应升级/退避;`opened:true` 的单次失败可短重试。
3. **`SKIPPED_HUMANLIKE` 不是失败**(点赞拟人故意跳过)——见 moments 契约,AMR 别当错误重试。
4. **`account_rebind` 事件**:后端检测到有人 GUI 切号会自动重绑并发此事件;AMR 若缓存了 self_id,见此事件应重新校验身份(呼应 fullwechat-backends 接入的"预检身份")。

## 与业务契约的边界
- 本契约 = fullwechat 后端的**可观测面/可动作面/红线/告警出口**(给"数字运维工程师 Agent"+FDE 人)。
- AMR 侧只需:① 发送失败时可选地读 `/api/events` 拿结构化原因做更聪明的重试/提示;② 对齐 §2、§6.1 的回执语义(尤其 `opened` 分诊 + `SKIPPED_HUMANLIKE` 非失败)。
- 运维告警(飞书群)由 fullwechat 侧运维 poller/Agent 出,不占 AMR 业务通道。

## 当前 .178 班迪2727 失败的处置(FYI)
根因 = 发送 FSM 开会话步超时(`opened:false`),偏后端系统性(疑 frida/GUI 卡)。fullwechat 侧排障中;AMR 侧此刻**重试大概率仍超时**(同一后端同一症状),建议先等后端修复信号(event log 不再出 `opened:false`)再重发。

---
*完整契约见 fullwechat 仓绝对路径(上)。本文件=供 AMR知悉 + 对齐回执语义;有异议提,我改契约(fullwechat 持有)。*
