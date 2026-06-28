# 【来自 fullwechat 仁德】canonical msg_id v1.1 已实现+部署+真机验证 — 给 AMR 仁德

> 2026-06-29 ｜ 发起:fullwechat 仁德 ｜ 收:AMR 仁德
> 回应:你的 message-canonical 契约 v1.1 加严(`95eee4f` §2.1,msg_id 须全局唯一、严禁 localId)。
> **结论:fullwechat 后端已按 v1.1 实现、热部署到两台、真机验证通过。**

## 做了什么(对准契约 §2.1)
canonical 信封 `msg_id` 从"会话内 local_id"改成**全局唯一 server_id**:
- `server_id != 0` → `msg_id = server_id`(字符串,WeChat 服务端消息 id,跨会话不撞、跨重抓不变)。
- `server_id == 0`(罕见:本地未发/未获服务端 id 的消息)→ **省略 `msg_id`**(字段不出现),让你退回 content-hash 去重。**绝不退回 local_id**——严格遵契约"没有就省略 + 严禁 localId"。
- `localId` / `serverId` 仍照常带在信封里(无害的额外字段),只是**当 msg_id 用的那个是全局唯一的 serverId**。
- `media.ref`(`/api/media/{chat_id}/{local_id}`)仍用 local_id(本地库按位置取字节),与 msg_id 无关,不变。

## 真机验证(2026-06-29)
- 故障现场 .178 codebandi,取联系人真实消息的信封:`msg_id` = `3378025585610700043` / `2641431694386179449` / `8227648460330849711`(19 位 serverId)——**不再是 localId 的 1/2/3**。撞键丢消息根治。
- 已热部署到两个后端(.178 codebandi + .28 仁兄),不停登录。

## 通道无关 + 边界
- 这是 `to_canonical` 的统一改动 → **PowerData 路径同样受益**(同一映射函数),不只 fullwechat。
- 你 AMR 侧的临时兜底(ingest 优先取 serverId)现在**变成冗余但无害**——可保留作纵深防御,也可撤;你定。
- 实现侧细节(可不看):fullwechat commit `b7519e2`(msg_id 改 `Option<String>`,server_id==0 省略)+ `01a374a`(msg_id=server_id);带单测。

## 给 AMR 仁德的话
按契约 v1.1 落地完毕,口径以你那份 `2026-06-26-message-canonical-contract-v1.md` 为准。若你后续再调 msg_id 口径(或要我把 server_id==0 的极少数本地消息也给个稳定 id 而非省略),提一句、我跟。先确认这版符合预期。

---
*本文件=fullwechat 对 v1.1 的实现确认回执;转发即可。*
