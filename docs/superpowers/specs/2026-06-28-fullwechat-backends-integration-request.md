# 【来自 fullwechat 仁德】请 AMR 配置驱动接入 fullwechat 后端

> 2026-06-28 ｜ 发起:fullwechat 仁德(powerdata-wx,`/Users/neo/as/fullwechet`)｜ 收:AMR 仁德
> 王总钦定:别再手搓 `jl account set …` 一条条接 → 做成**配置驱动**(读注册表 + 接入 Agent)。
> Schema 真相源(fullwechat 仓,绝对路径):`/Users/neo/as/fullwechet/ops/fullwechat-backends.example.json`
> 完整请求(fullwechat 仓):`/Users/neo/as/fullwechet/docs/amr-fullwechat-backends-integration-request.md`
> 本文件已内联全部要点,你在 AMR 仓 cwd 不开那两个文件也能直接干。

## 要你做什么
把"接入 fullwechat 后端"从手工命令升级成**配置驱动**:AMR 读一份**后端注册表 JSON** → 一个**接入 Agent** 逐条接(预检身份→dry-run→人确认→apply)。加客户/加账号只改配置,不重写命令(和运维 ops-targets.json 同构:造一次 N 复用)。

## §1 后端注册表 schema(真相源在 fullwechat 仓)
每个 backend 一条:
```
{ label, amr_account_slot, tool:"fullwechat", host, token_file, self_id, instance_name, customer, enabled }
```
- **真实副本不入仓**(含真 wxid/真 IP):填在 AMR 主机本地,如 `.178:~/.config/jl/fullwechat-backends.json`。仓里只放占位符模板。
- **当前要接的两条**(真实 self_id 由接入 Agent 从 `/api/status/auth` **实时发现并人工确认**,不硬编码进任何入仓文件):
  | label | host | token_file | AMR slot | instance |
  |---|---|---|---|---|
  | 代码班迪 | `http://192.168.31.178:6174` | `~/.config/codebandi-wechat/token` | 你定 | (.178 codebandi) |
  | 仁兄(王立仁) | `http://192.168.31.28:6174` | `~/.config/agent-wechat/token` | 4(你之前提的) | `Desktop-loong-20240628-35909` |
  - 仁兄 base wxid = `wangliren123`(去设备后缀);代码班迪的实时从其 `/api/status/auth` 读。

## §2 接入 Agent 逻辑(配置驱动 + 人在回路)
对注册表里每个 `enabled` backend:
1. **预检身份(关键!今天的血泪教训,务必加)**:`GET {host}/api/status/auth`(带 token)→ 校验 `loggedInUser` 的 base(去设备后缀 `_xxxx`)**等于** config 的 `self_id`(或人工确认是目标账号)。**不等→不接 + 告警**。
   - 今天 .28 就栽这:agent-server 一度绑旧号(代码班迪)、GUI 却是仁兄;那时若盲接,会把代码班迪数据错挂到仁兄 slot、**污染身份图**。fullwechat 侧已加"人 GUI 切号→健康巡检自动重绑"自愈(见 `/Users/neo/as/fullwechet/docs/account-binding-and-one-container-per-account.md`),**§2.1 是 AMR 侧二道闸,两道都要**。
2. **dry-run**:把整条 `jl account set {amr_account_slot} --tool fullwechat --host {host} --token-file {token_file} --self-id {self_id}` 打给人看,等确认。
3. **apply**:确认后执行 → 回读 AMR 侧该 slot 配置确认生效。
4. **能力探测**:`GET {host}/api/capabilities` 记录该后端支持的面(message canonical / moments read+like / group meta…),按能力降级。
5. **留痕**:每条接入(谁/何时/backend/dry-run内容/确认人)记日志。

## §3 验证(接通判据)
- 仁兄(slot 4 @ .28):`/api/status/auth` loggedInUser=`wangliren123_*`、`/api/chats` 返回仁兄名册(液冷/Smile/Peter 叶毓睿/西开全球矿场… 而非代码班迪的 WeChatFerry蛇/能源舆情)。**.28 现已修复并验证为仁兄(2026-06-28)**,可直接接。
- 代码班迪 @ .178:同法预检 → 接。

## §4 话
schema 以 fullwechat 仓 `ops/fullwechat-backends.example.json` 为准,有异议提、我改模板。真实 wxid/IP 带外、只填本地副本不入仓。先把仁兄(slot 4)和代码班迪两条接通。歧义提出来,别猜。
