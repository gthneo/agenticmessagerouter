# AMR 裁定 + 实现提示词 — fullwechat 微信后端 canonical 落地

> **发自** AMR 契约定义方（仁德 @ agenticmessagerouter）｜**致** fullwechat 后端实现者（仁德 @ ReleaseWX）+ 王总
> **日期** 2026-06-28 ｜ **状态** ✅ 裁定已定，契约正本已更新并推 master，**即可开工**
> **回应** `fullwechet/docs/canonical-contract-wechat-backend-feedback.md`（后端反馈与决策清单）
> **契约正本（唯一真相源，以 AMR 仓为准）** `docs/superpowers/specs/2026-06-26-message-canonical-contract-v1.md`

---

## 目录
- [0. 一句话](#0-一句话)
- [1. 必读（三份）](#1-必读三份)
- [2. 裁定 · 三个决策](#2-裁定--三个决策)
  - [D1 ⭐ media.ref 形态 + 语音转写边界（王总改了）](#d1--mediaref-形态--语音转写边界王总改了)
  - [D2 capabilities 投递](#d2-capabilities-投递)
  - [D3 direction 判不准](#d3-direction-判不准)
- [3. 小项确认（S1–S5 / C1–C4）](#3-小项确认s1s5--c1c4)
- [4. 实现指引（按 ROI）](#4-实现指引按-roi)
- [5. 交付与端到端验证](#5-交付与端到端验证)
- [6. canonical 信封速查（照此实现）](#6-canonical-信封速查照此实现)
- [7. 本轮契约更新记录](#7-本轮契约更新记录)

---

## 0. 一句话
跨双方真正要拍的 **D1 已定 = (a)**；D2/D3 + 所有小项均确认。**唯一新增的边界变化：语音转写优先你用「微信自带语音转文字」转好、连文字一起吐，AMR 不建本地 ASR 引擎**（王总 2026-06-28 钦定）。契约 §2.2/§2.3 已据此更新。**你即可按 ROI 开工。**

## 1. 必读（三份）
1. **契约正本**（唯一真相源）：`docs/superpowers/specs/2026-06-26-message-canonical-contract-v1.md`（微信映射见**附录 A**；envelope 见 §2；kind 枚举 §3；能力/降级 §6；冲突口径 §9）。
2. **类型调研**：`docs/reference/wechat-message-types.md`（附录 A 的依据）。
3. 本文（裁定 + 提示词）。

> 有歧义**直接照契约正本**（已更新），或喊 AMR 改 spec——**我以你仓为准之前，spec 以 AMR 仓为准**。

## 2. 裁定 · 三个决策

### D1 ⭐ media.ref 形态 + 语音转写边界（王总改了）
**裁定 = (a) ✅**：fullwechat 加 `GET /api/media/{chat_id}/{msg_id}` 取件端点（复用已打通的 media-export 解密），**`ref` = 绝对 HTTP URL**（如 `http://<host>:6174/api/media/<chat>/<msgid>`），返回**解密后字节**。
- **鉴权**：AMR GET `ref` 时**带 fullwechat bearer auth**（与你其余 API 同一鉴权，端点照常校验即可）。
- **不是** 微信 cdnurl（加密、消费方下不了）。

**⚠️ 语音转写边界——王总 2026-06-28 钦定（相对你清单 D1 子确认的反转）：**
- **语音优先你转**：微信自带「语音转文字」，**你能转就转好、连 `media.transcript` 一起吐**（同时给 `ref`+`mime`+`duration`）。**AMR 直接显示、不再转。**
- **目的：不在本地专门建 ASR 引擎**——复用微信/后端已有的转写能力，最省。
- **AMR 的 ASR 降为兜底**：你给不了 transcript 时 + AMR 配了 ASR 端点才转；**默认不配端点**。
- 故语音吐 `media.{ref, mime, duration, transcript?}`：**transcript 能转就给；转不了只给 ref**（AMR 兜底或停 `[语音]`）。

### D2 capabilities 投递
**裁定 = (a) ✅**：你加 `GET /api/capabilities`，返回 `{schema, channel:"wechat", kinds:[...], direction:true}`，AMR 握手拉一次。
- 说明：AMR 优雅降级**本不依赖**它（未知 kind 自动落 `text`），所以这是锦上添花、**AMR 消费优先级低**；你先把端点实现即可。

### D3 direction 判不准
**裁定 = ✅**：capability 级 `direction:true` + per-message 极少数（sender 解不出）**默认 `in`**，AMR 的 `apply_self_directions`（已挂进 poll/ingest）兜底补判。**不要**一律 `in`——绝大多数你用 `is_self` 判得出，照判。

## 3. 小项确认（S1–S5 / C1–C4）
| # | 项 | 裁定 |
|---|---|---|
| S1 | 落点端点 | ✅ canonical 字段加在**现有 `/api/messages/{chat_id}` 每条 JSON 顶层**（保留 localId/timestamp 等旧字段）。AMR `is_canonical` 认 `schema` 或 `kind+text`。**不**新开端点。 |
| S2 | sender 语义 | ✅ `sender`=显示名、`sender_id`=wxid；保留旧字段；awx 你自己同步。 |
| S3 | 过滤 51/9999 | ✅ 源头不 emit。 |
| S4 | text 永非空 | ✅ 媒体/系统给占位（`[图片]`/`[语音]`/`[系统消息]`）。 |
| S5 | §9 口径 | ✅ 王总钦定：66→`unknown`、红包=49.2001 本体(计1)+10000 归 `system`、表情 47&49.8 各算一条；49.2003/49.74 默认 `unknown`。 |
| C1 | **msg_id = localId** | ✅ **用 localId**。理由：AMR 现有路径就是 `fullwx:{localId}`，**切换时 dedup 连续、不会大面积重入库**；serverId 跨安装更稳但切换即全重灌、不划算。容器迁移致 localId 变的罕见重入库可接受。 |
| C2 | type 迁移 | ✅ 归 AMR（`jl migrate-kinds` 已建，dry-run→confirm），你只管新消息吐 kind。 |
| C3 | 灰度共存 | ✅ AMR 逐条 `is_canonical`，新旧并存无碍（前向兼容设计）。 |
| C4 | quote refKind 递归 | ✅ **一层**够：被引 `refermsg.type` 过一遍映射表得 `refKind`，`refText`=被引 display/占位；被引的被引不展开。 |

## 4. 实现指引（按 ROI）
1. **地基**：`direction`(in/out) + `text` 永非空 + `GET /api/capabilities` + 过滤 51/9999。
2. **system**：10002 sysmsg（`revoke` 撤回 / `pat` 拍一拍 / `member_change`）+ 10000 文案兜底。`system.{event, actor?, text}`。
3. **quote**：49.57（`quote.{author, text, refKind, refText}`，refKind 递归一层）。
4. **富消息细分**：`link`(49.5/4) / `file`(49.6) / `miniprogram`(49.33/36) / `chat_history`(49.19)，各自结构化子对象见契约 §2.2。
5. **media**：图片/视频/语音吐 `media.{ref, mime, duration, transcript?}` + 同时交付 `GET /api/media/...` 端点。**语音把「微信自带转文字」接进来吐 `transcript`**（王总新增、首选）。
6. **长尾**：就近映射或 `unknown`（YAGNI，不追全）。

## 5. 交付与端到端验证
- **你侧**：worktree 实现 → `cargo check` → `scripts/update-server-on-178.sh` 热部署到 .178 codebandi（不重启微信）。
- **AMR 侧（已就绪，零改自动消费）**：
  - `ingest.is_canonical` / `ingest.from_canonical`：你一吐 canonical，AMR 自动映成内部记录、写 `messages.type`(=kind) + `messages.raw`(=结构化)。
  - UI 自动按 kind 富渲（链接/文件/引用/系统/小程序卡，已上线）。
  - **语音**：你给 `media.transcript` → AMR ingest 直接写库（`status=done`）、气泡在 `[语音]` 下挂字，**无需 ASR**；你只给 `ref` 且 AMR 配了端点 → AMR 兜底转。
- **端到端**：你热部署后，AMR 这边一起验（direction 标对、各 kind 富渲、语音挂字）。

## 6. canonical 信封速查（照此实现）
每条消息一个信封（加在 `/api/messages/{chat_id}` 每条 JSON 顶层）：
```json
{
  "schema": "message.canonical/1",
  "channel": "wechat",
  "direction": "in",
  "kind": "quote",
  "text": "好的，按这版改",
  "ts": 1750000000,
  "sender": "张三",
  "sender_id": "wxid_test_zhangsan",
  "is_mentioned": false,
  "msg_id": "<localId>",
  "quote": { "author": "张三", "text": "好的", "refKind": "file", "refText": "Q2-report.pdf" }
}
```
per-kind 子对象（按 kind 出现，缺失即"这维我没解"，AMR 不报错）：
- `link {title,url,source?}` · `file {name,ext?,size?}` · `quote {author,text,refKind,refText}`
- `miniprogram {title,source?,url?}` · `chat_history {title,items?}` · `location {label?,poi?,lat?,lng?}`
- `system {event,actor?,text}`（event∈revoke|pat|member_change|notice）
- `media {placeholder,ref?,mime?,duration?,transcript?}`（image/voice/video/sticker；**voice 优先给 transcript**）
- `payment {amount?,memo?,stage?}`（transfer/red_packet）

15 个 canonical `kind`：`text image voice video file link quote miniprogram chat_history location sticker transfer red_packet system unknown`。

## 7. 本轮契约更新记录（因你反馈 + 王总钦定而改）
- **§2.2 media 子对象**：`ref` 明确为「后端可直接 GET 到解密字节的绝对 URL（非 cdnurl）+ AMR 带 bearer auth」；**新增 `transcript?` = 后端能转就给**。
- **§2.3 语音/ASR 边界**：从"转写归 AMR"改为 **"转写优先后端（微信自带语音转文字），AMR 直接显示；AMR 的 ASR 降为兜底、默认不配端点"**。
- 提交：`de4adc3`（master 已推）。
