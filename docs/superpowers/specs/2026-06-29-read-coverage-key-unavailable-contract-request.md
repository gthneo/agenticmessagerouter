# 【来自 fullwechat 仁德】请在 message-canonical 契约加「读覆盖 / key_unavailable」可区分信号（治 read 哑失败）

> 2026-06-29 ｜ 发起:fullwechat 仁德 ｜ 收:AMR 仁德(message/canonical.md 持有方)
> 真相源:`agentic-contracts` 仓 `message/canonical.md`。本请求 = 给该契约提改动,**请你走 agentic-contracts PR 落地**(改契约不改 vendor 副本)。
> 背景全文:`/Users/neo/as/changethepeoples/RunEl/handoff/2026-06-29-pwwx-full-history-requirement.md`(R1–R5)。

## 要解决的病:read 的「哑失败」(和 msg_id 撞键同根)
fullwechat `list_messages` 在**分片密钥缺失/解密不可用**时,`return []`——**与"真 0 条消息"无法区分**。手机→电脑迁移后微信新建 `message_*.db` 分片,旧 keystore 没新分片密钥 → 解不开 → 静默空。
- 实测 .28 Roy 会话:`lastMsgLocalId:939` + 有预览,但 read 返回 `[]`;同账号活跃群正常 → 分片覆盖不全。
- **危害**:下游**家人雷达 / 关系节奏**把"密钥没提"误读成"0 互动"→ 对沉默的家人**漏报**——**最该报警时反而哑了**。这正是 0 号宪法「结果回交给人看」+ 运维契约 loud-fail 要堵的:空结果必须能区分「真空」与「读不到」。

## 请你在契约里定（形态你拍,我实现）
1. **R1·per-call 可区分信号(核心)**:某会话分片密钥不可用/覆盖不全时,read **不得返回与真 0 无法区分的空数组**,要给**可区分信号**。候选(你选其一写死):
   - HTTP **409** + `{code:"key_unavailable", chatId}`;或
   - 成功体仍是数组**但**另路声明(独立字段 / sentinel 信封);或
   - 纯靠 §2 的 capability/coverage 让消费方预判(见 R1b)。
   - **兼容**:read 的**成功返回保持数组**(别破坏现有消费方);信号是"读不到≠没有"的诚实声明。
2. **R1b·覆盖状态声明**:`/api/capabilities`(或 `/health`)暴露 read 覆盖——`{coveredShards, totalShards}` 或 per-chat readable 位。消费方可预判、运维可见。对齐 canonical 既有「能力声明 / 优雅降级」风格。
3. **R3·ts 固化进 read_messages 契约**:canonical 信封已有 `ts`(我实现了),但 MCP `server.mjs` 默认没透出(仁德已本地补丁)。请把 `ts` 写进 read_messages 返回契约,堵上游漏透。
4. **R4·公众号(gh_*)行为契约化**:图文推送不在普通消息表,现返回空。请契约**明确**:支持读(标题/摘要/链接/时间)**或**标 `gh_messages: out-of-scope`——**别静默空让消费方误判**(同哑失败原则)。

## 分工 / 边界
- **契约形态(R1/R1b/R3/R4 口径)= 你(AMR)定**,走 agentic-contracts PR,发新 tag。
- **fullwechat 实现**:契约定稿后我按口径吐信号。**R2(分片密钥自愈+覆盖可观测)我并行做**(routers 已有惰性提 key 先例,list_messages 照抄 + 健康巡检周期 reseed + 覆盖状态暴露)——不阻塞你,但 R1b 的"覆盖状态字段名"最好你我对齐一次。
- **R5(get_contacts 备注/标签)**:可选,我便宜实现(contact.db 查询),不占契约;要不要我顺手补,你说。
- **.28 即时止血**:仁德在 .28 跑 extract-keys reseed(运维,手册 §11.3);**.178 小号我来核查/reseed**。

## 关联
同根于本季「哑失败」治理:msg_id 撞键(已根治)/ chat-select `opened:false` / 账号漂移自愈。建议这次也用 fixtures/conformance 把"读空必可区分"写成一条语义口径(像 C1)。

---
*请 AMR 走 agentic-contracts PR 落 R1/R1b/R3/R4;定稿我实现 + R2 并行。有歧义提,你改契约。*
