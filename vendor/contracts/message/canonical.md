# Message Channel 规范化契约 v1 — Canonical Message Contract（通道无关核心 + 通道映射附录）

> 真相源: `agentic-contracts` 仓 · owner 见 CODEOWNERS（`message/` → AMR/@gthneo）。

> **日期** 2026-06-26（含 v1.1 `msg_id` 加严口径，见 §2.1；**v1.2 读空必可区分 / 读覆盖声明 / `ts` 读路径不丢 / 公众号契约化，见 §6.4 + C6**）｜ **状态** 提案（待各通道后端评估实现）
> **v1.2 兼容性**：对**消费成功读**纯加法（`success=数组` 不变、`ts` 本就必填）→ schema 仍 `message.canonical/1`（**minor**）。新增 = 读不可用的 409 信号 + capabilities `read` 块 + C6 口径；消费方应处理新的 409/工具错误。
> ⚠️ **生产侧收紧**：`[]` 的语义被**重定义**——从前「读不到 or 真 0」二义，现在 `[]` 带保证「真 0 + 覆盖完整」。今天任何在密钥缺失时回 `[]` 的后端**即变为不合规**，须改吐 409。判作 minor 仅因本契约仍是**提案**、尚无已部署的合规后端；一旦有后端实现，此为破坏性收紧，须在该后端版本里显式声明。
> **适用范围** **一切消息通道（Message Channels）**：微信 / 电话 / 飞书 / iMessage / 企微 / 未来任意。每个通道后端都遵守本契约。
> **定义方** AgenticMessageRouter (AMR / the Router) —— 契约唯一真相源在 `agentic-contracts` 仓（本文件），AMR(@gthneo) own。
> **实现方** 各通道后端，**各自独立仓库**（如 `gthneo/powerdata`、fullwechat 后端仓 …），引用本契约实现；每通道一份**映射附录**（微信见附录 A）。
> **AMR 侧** 每通道一个**薄适配器**（AMR 仓 `src/jl/channels/*`），把后端的 canonical 输出映成 `MsgRecord`；UI 富渲染。
> **依据** 调研综述（微信通道的 type 全景，附录 A 的来源）维护在 AMR 仓 `docs/reference/wechat-message-types.md`。
> **关系** 本契约**取代并吸收**了 PowerData 多账号契约（AMR 仓 `2026-06-18-powerdata-multiaccount-contract.md`）第 5 项（「`get_chat_history` 展开 app 消息」）——升级为**所有通道统一遵守的规范化信封**，PowerData 第 5 项以本契约为准（见 §8）。

## 0. 这份契约是什么 / 不是什么

不同消息通道各有各的原始协议怪癖（微信是两层枚举 + 三套不兼容编码、解 XML 逐后端不同；电话只有通话记录；飞书有自己的 message type…）。让每个 AMR 适配器各自去认每个通道的原始格式，是把通道复杂度泄漏进 Router。

**本契约把这层复杂度推回后端**：AMR 定义一个**通道无关的规范化消息信封**（canonical envelope），每个通道后端把自己的原始协议**映射**进这个信封（各写各的「通道映射附录」）；AMR 适配器只做**薄映射**（envelope → `MsgRecord`）；UI 富渲染。**核心信封 + kind 枚举 + 能力声明 = 一切通道共守；通道专属的 raw→kind 映射 = 每通道一份附录**（微信 = 附录 A；电话 / 飞书 / … 后补）。

- **是**：一个 read / representation（读 + 表示）层的、**跨通道统一**的归一接口。后端「我这个通道读到的这条消息，规范化后长这样」。
- **不是**：写 / 发送契约（发送能力路由见 `accounts.tool` + `can_send`，与本契约正交）；不是 1c 渐进自治（单独 spec）；不是要求后端实现全部子类（能力声明 + 优雅降级，见 §6）。

**LLM 无关**（CLAUDE.md 铁律）：信封是纯结构化映射，零 LLM 参与。ASR / 语义摘要是可选 assist，绝不是 envelope 的前置 gate。

## 1. 设计原则

1. **`text` 永远在场**。每条消息无论 kind 为何，都带一个人类可读的 `text`（display text / fallback）。纯文本消费方（旧 UI、CLI grep、FTS 索引）只读 `text` 也不丢可用性 → **向后兼容的根基**。
2. **小而封闭的 `kind` 枚举**。15 个 canonical kind 覆盖高 ROI 集；微信几十个原始子类的长尾**映射到最近的 kind，认不出就 `unknown`**（YAGNI，不为冷门子类开洞）。
3. **结构化是可选增量**。`text` 是契约的地板；per-kind 的结构化子对象（`link`/`file`/`quote`…）是天花板。后端给得出就给，给不出就只给 `text` + kind=`unknown`/最近 kind，Router 照样工作。
4. **`direction` 必须由后端标注**。修掉当前 fullwechat 硬编码 `direction="in"` 的缺口（`fullwechat.py:122`）——后端最清楚 self_id，必须判出/入站。
5. **保守优先**。认不准的原始 type（survey 冲突项：66 / 2003 / 高位复合）→ 不猜，落 `unknown` + 原文进 `text`，把判断交回去（见 §9）。

## 2. Canonical Message Envelope

后端对每条消息 emit 一个信封。JSON 形态（示意，合成占位值，无真实 PII）：

```json
{
  "schema": "message.canonical/1",
  "channel": "wechat",
  "direction": "in",
  "kind": "quote",
  "text": "好的，按这个版本改\n↩ 引用 张三: 季度报表初稿",
  "ts": 1750000000,
  "sender": "张三",
  "sender_id": "wxid_test_zhangsan",
  "is_mentioned": false,
  "quote": {
    "author": "张三",
    "refKind": "file",
    "refText": "季度报表初稿"
  }
}
```

### 2.1 顶层字段（所有 kind 通用）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | str | 是 | 契约版本标识，v1 固定 `"message.canonical/1"`（通道无关；见 §6 版本化）。 |
| `channel` | str | 是 | 通道标识：`wechat` / `phone` / `feishu` / `imsg` / `wecom` / …。决定该用哪份映射附录。 |
| `direction` | `"in"` \| `"out"` | 是 | 出/入站。后端按 self_id 判定；**不得**一律 `"in"`。无法判定时见 §6.3 降级。 |
| `kind` | enum（§3） | 是 | 规范化大类。认不出 → `"unknown"`。 |
| `text` | str | 是 | 人类可读 display text，**永远非空**（最差也给占位 `[图片]` / `[链接/文件]` / `[系统消息]`）。 |
| `ts` | int | 是 | unix 秒。 |
| `sender` | str | 是 | 发言人显示名（群里是发言成员名）。 |
| `sender_id` | str | 否 | 发言人稳定 id（wxid）。给得出就给，利于跨账户归一。 |
| `is_mentioned` | bool | 否 | 本条是否 @ 了 self（来自 msgsource `<atuserlist>`）。默认 false。 |
| `msg_id` | str | 否 | 后端的**全局唯一 + 稳定**消息 id（去重锚点）。**必须跨会话不撞、跨重抓不变**——用 `serverId`（WeChat 服务端消息 id）这类全局唯一值。**严禁用会话内 `localId`/位置号**：localId 是会话内位置、会重用/重置，拿它当 `msg_id` → 同一会话内新消息撞老 key、被去重（`UNIQUE(conversation_id, msg_key)`）**静默丢弃**（2026-06-28 实战故障：群消息神秘不入库、自动回无候选）。没有就省略，AMR 退回 content-hash 去重（`ingest.msg_key`）。**口径 v1.1 加严：原文写"localId/serverId"任一即可是错的，必须全局唯一。** |

### 2.2 Per-kind 结构化子对象（可选，按 kind 出现）

| 子对象 | 出现于 kind | 字段 |
|---|---|---|
| `link` | `link` | `{title, url, source?}` — `source` = 公众号/站点名（`sourcedisplayname`）。 |
| `file` | `file` | `{name, ext?, size?}` — `size` 字节数。 |
| `quote` | `quote` | `{author, refKind, refText}` — `author` = 被引消息作者显示名；`refKind` = **被引消息的 canonical kind**；`refText` = 被引内容的 display text。**回复正文走顶层 `text`，quote 子对象内不再放 `text`**（同名易混；AMR UI 只读 `author`/`refText`，顶层 `text` 当正文）。 |
| `miniprogram` | `miniprogram` | `{title, source?, url?}` — `source` = 小程序名（`sourcedisplayname`）。 |
| `chat_history` | `chat_history` | `{title, items?}` — 合并转发；`items` = `[{author, kind, text}]`（可选；解不动就只给 `title`）。 |
| `location` | `location` | `{label?, poi?, lat?, lng?}` — `label` 地名，`poi` POI 名。坐标可省。 |
| `system` | `system` | `{event, actor?, text}` — `event ∈ {revoke, pat, member_change, notice}`；`actor` = 触发者显示名。 |
| `media` | `image` `voice` `video` `sticker` | `{placeholder, ref?, mime?, duration?, transcript?}` — `placeholder` = `[图片]`/`[语音]`… ；**`ref` = 后端可直接 GET 到「解密后字节」的绝对 HTTP URL**（如 fullwechat `GET /api/media/{chat_id}/{msg_id}`），**不是微信 cdnurl**（cdnurl 加密、消费方下不了，真身只有后端能解）。AMR GET `ref` 时**带该通道的 bearer auth**。`mime`/`duration` 给得出就给。**`transcript`（语音转写）= 后端能转就给**（如微信自带语音转文字）；给了 AMR 直接显示、不再转 —— 见 §2.3。 |
| `payment` | `transfer` `red_packet` | `{amount?, memo?, stage?}` — `amount` 如 `"¥100.00"`；`stage` = 收发阶段（survey: paysubtype 1/3/4/5/7）。 |

> 不在表内的字段一律不要求。后端只 emit 它解得出的子对象；缺失即「这一维我没解」，AMR 不报错。

> **口径（2026-06-28 实现后裁定，AMR 钦定）**：
> - **②长尾子对象字段名不阻塞上线**。`transfer`/`red_packet`/名片(42)/位置(48) 当前 AMR UI **退回纯文本气泡吃 `text` 地板**（payment 暂未做卡片），故 XML 字段名按现有就近映射先发即可，**不为真机抽样而阻塞**。唯一例外：**`payment.amount`（钱）**——做金额卡前，后端对 `feedesc`/`scenetext` 做一次真机抽样核对，确认金额字段对，再让 AMR 信它去显示/染色；名片/位置低风险，随手优化、不专门排期。
> - **③`system.actor` 是增量不是闸**。AMR UI 居中灰条只显示 `system.text`（人话地板），**不读 `actor` 也能正常渲**，故 `actor` 留 `None` 可接受、不阻塞。但 `actor` 对 jl 的**关系加权染色**有真实价值（「谁撤回/谁拍了谁」=互动信号）：**`pat.actor` 顺手填**（`fromusername` 现成、近零成本、拍一拍=高频亲密信号），`revoke.actor`（群里要从 `replacemsg` 抽，成本高）可缓到染色真正用它时再补。

### 2.3 语音 / ASR 边界（后端 vs AMR — 重要）

**转写优先后端提供（王总 2026-06-28 钦定）**：微信自带「语音转文字」，让后端转好、连文字一起吐，AMR 直接显示——**无需专门建本地 ASR 引擎**。职责：

- **后端（fullwechat / PowerData）**：语音**优先用自身能力（微信自带语音转文字）转好 → 连 `media.transcript` 一起吐**；同时给 `ref`(取件端点，返回解密原始字节如 silk) + `mime` + `duration`（供播放 / AMR 兜底）。**能转就转，转不了就只给 ref。**
- **AMR**：
  1. **后端给了 `transcript`** → ingest 直接写 `media.transcript`、气泡挂字，**不再转**（首选路径）。
  2. 后端没给 transcript 但有 `ref` **且**配了 ASR 端点 → AMR **兜底**转写（`asr.py`，provider-agnostic、LLM-optional：GET ref 带 bearer auth → 转码 silk→wav → ASR → 写 transcript）。**默认不配 ASR 端点**（首选靠后端/微信转写）。
  3. 都没有 → 停 `[语音]`，人去原生微信听（人在回路兜底）。
- **双向一致**：`in`/`out` 都走 `kind:voice`；transcript 谁给都行，AMR 统一显示。AMR 不"发"语音。
- **双向一致**：`in`（对方语音）/`out`（自己回灌的语音）都走 `kind:voice` + `ref`，AMR 统一转写，与哪个后端无关。
- **AMR 不"发"语音**（发送链路纯文本 outbox→confirm）；要发语音用原生微信。
- ⚠️ 勿与 `assist.py` 里的 `_voice_block`/`VOICE_GUIDE` 混淆——那是「**文字口吻**沉淀」（模仿用户语气），不是语音 ASR。

## 3. Canonical `kind` 枚举（封闭，15 个）

| kind | 含义 | 主子对象 | UI 渲染（气泡已建，见 §7） |
|---|---|---|---|
| `text` | 纯文本 | — | 普通气泡 |
| `image` | 图片 | `media` | 占位/缩略卡 |
| `voice` | 语音 | `media` | 占位（+ transcript 行） |
| `video` | 视频/小视频 | `media` | 占位 |
| `file` | 文件 | `file` | 文件卡（名/大小） |
| `link` | 链接·网页·图文·音乐 | `link` | 链接卡（标题/来源） |
| `quote` | 引用回复 | `quote` | 正文 + 被引块 |
| `miniprogram` | 小程序 | `miniprogram` | 卡片（标题/来源） |
| `chat_history` | 合并转发·聊天记录 | `chat_history` | 卡片（标题 + 条数） |
| `location` | 位置（静态/实时） | `location` | 地点卡 |
| `sticker` | 动画表情/sticker | `media` | 占位 `[表情]` |
| `transfer` | 转账 | `payment` | 金额卡 |
| `red_packet` | 红包 | `payment` | 红包卡 |
| `system` | 系统事件（撤回/拍一拍/群变更/公告） | `system` | 居中灰条 |
| `unknown` | 无法归类 | — | 普通气泡（显示 `text` 占位） |

## 4. 附录 A · 微信通道映射表（`channel:"wechat"`）

> 这是**微信通道专属**的 raw→canonical 映射。**§1–3、§6–9 是通道无关核心，一切通道共守**；本附录只对微信后端（fullwechat / PowerData / 未来 PadLocal）有意义。**新通道（电话 / 飞书 / …）各加一份同构附录（附录 B / C / …）**，把自己的原始 type 映到 §3 的同一套 canonical kind。

后端据此把微信原始协议映射进信封。表分三段：顶层 MsgType、appmsg(49) 子类、10000/10002 sysmsg。所有数字依据 survey 主表/子表/sysmsg 表。

### 4.1 顶层 MsgType → kind

| 原始 type | survey 名称 | → kind | 填充子对象 |
|---|---|---|---|
| 1 | TEXT | `text` | — |
| 3 | IMAGE | `image` | `media{placeholder:[图片], ref}` |
| 34 | VOICE | `voice` | `media{placeholder:[语音], ref, transcript?}` |
| 37 | VERIFYMSG 好友验证 | `system` | `system{event:notice, text:验证语, actor:申请人}` ⚠ ticket 见 §9 |
| 42 | SHARECARD 名片 | `link` | `link{title:名片名, source:"名片"}`（退而求其次；无独立 kind） |
| 43 | VIDEO | `video` | `media{placeholder:[视频], ref}` |
| 47 | EMOTICON 动画表情 | `sticker` | `media{placeholder:[表情], ref?}` |
| 48 | LOCATION 静态位置 | `location` | `location{label, poi}` |
| 49 | APPMSG 容器 | **看 §4.2 子类** | 按 appmsg.type 分流 |
| 50/52/53 | VoIP 通话族 | `system` | `system{event:notice, text:通话提示}`（弱信号，可降级 unknown） |
| 51 | STATUSNOTIFY 状态同步 | **过滤** | 协议噪声，后端**不应 emit**（见 §5）；若 emit 则 `unknown` |
| 62 | MICROVIDEO 小视频 | `video` | `media{placeholder:[小视频]}` |
| 66 | （冲突：企业名片/红包） | `unknown` | 见 §9 冲突①，按实测后端口径择一，默认 unknown |
| 9999 | SYSNOTICE | **过滤**/`system` | 老 web 噪声，建议过滤 |
| 10000 | SYS 纯文本系统提示 | `system` | `system{event, text:原文案}`，event 靠关键词（见 §4.3） |
| 10002 | SYSMSG XML 容器 | `system` | 看 §4.3 sysmsg.type |
| 其它/未知 | — | `unknown` | `text` = 清洗后原文或占位 |

### 4.2 appmsg（49）子类 → kind

| 49.N | survey 名称 | → kind | 填充子对象 |
|---|---|---|---|
| 49.1 | TEXT（壳） | `text` | `text` = `<title>` |
| 49.3 / 49.92 | 音乐 | `link` | `link{title, url, source}`（音乐当链接卡；双口径都收） |
| 49.4 / 49.5 | URL 链接·图文 | `link` | `link{title, url, source:sourcedisplayname}` ⚠ 4≈5（§9 冲突⑥） |
| 49.6 | ATTACH/FILE 文件 | `file` | `file{name:title, ext:fileext, size:totallen}` |
| 49.8 | EMOJI（分享表情） | `sticker` | `media{placeholder:[表情]}`（与顶层 47 同归 sticker，§9 冲突⑤计数） |
| 49.17 | 实时位置共享 | `location` | `location{label}` |
| 49.19 / 49.24 | 合并转发 / 收藏笔记 | `chat_history` | `chat_history{title, items?}`（递归解 recordinfo；解不动只给 title） |
| 49.33 / 49.36 | 小程序 | `miniprogram` | `miniprogram{title, source, url}` ⚠ **33/36 都要认** |
| 49.51 / 49.63 | 视频号 / 视频号直播 | `link` | `link{title:nickname/desc, source:"视频号"}`（无独立 kind，归 link） |
| 49.57 | QUOTE 引用回复 | `quote` | `quote{author:displayname, refKind:map(refermsg.type), refText:被引content display}`；顶层 `text`=回复正文（**quote 子对象内不放 `text`**，对齐 §2.1）。**递归按 refermsg.type 解被引** |
| 49.62 | PAT 拍一拍（appmsg 形态） | `system` | `system{event:pat, actor, text}` |
| 49.87 | CHATROOM_NOTICE 群公告 | `system` | `system{event:notice, text}`（复用 recordinfo） |
| 49.2000 | TRANSFER 转账 | `transfer` | `payment{amount:feedesc, memo:pay_memo, stage:paysubtype}` |
| 49.2001 | RED_ENVELOPE 红包 | `red_packet` | `payment{amount?, memo:scenetext}` |
| 49.其它（7/9/10/13/15/16/53/74/2003…） | 长尾 | **就近或 unknown** | 解得出语义就映到最近 kind，否则 `unknown`（49.2003 见 §9 冲突③；49.74 见 §9 冲突④） |

> **refKind 映射**：`quote.refKind` 把被引消息的原始 refermsg.type 再过一遍本映射表（递归一层即可，被引的被引不展开）。被引是图片/文件 → `refKind:"image"/"file"`，`refText` 给占位。

### 4.3 系统消息 10000 / 10002 → `system{event}`

| 来源 | 触发 | → event | actor / text |
|---|---|---|---|
| 10002 `sysmsg type=revokemsg` | 撤回 | `revoke` | `actor`=撤回人（群里从 replacemsg 取）；`text`=replacemsg 文案；保留 `newmsgid` 进 `raw` 供反查 |
| 10002 `sysmsg type=pat` | 拍一拍 | `pat` | `actor`=fromusername；`text`=template 文案 |
| 10002 `sysmsg type=delchatroommember` | 踢人/邀请 | `member_change` | `text`=文案；scene 区分移除/邀请进 raw |
| 10002 `sysmsg type=sysmsgtemplate` | 模板群通知（入群/邀请/公告/待办） | `member_change` 或 `notice` | 回填 `${...}` 占位符后的 `text`（link_list 映射昵称） |
| 10000 纯文案 | 入群/退群/改群名/红包领取/「你已添加 X」/转账被领取 等 | 关键词归类（`revoke`/`member_change`/`notice`） | `text`=原文案；认不准归 `notice` |

> 入群/邀请有 10000 / `delchatroommember` / `sysmsgtemplate` **三条并存路径**（survey），后端三条都要覆盖，统一归 `system`。撤回保守做法：10002 优先解 sysmsg.type，兜底再正则扫 10000（survey 陷阱⑥）。

## 5. 过滤规则（后端不应 emit 的噪声）

后端**应在源头丢弃**，不要包成信封推给 AMR：

- **51 StatusNotify**（登录初始化/已读/正在输入同步）——纯协议噪声。
- **9999 SYSNOTICE**——老 web 客户端级提示。
- 若后端无法在源头过滤，则 emit `kind:"unknown"` 并在 `raw` 标记，AMR 入库前可二次丢弃。

## 6. 版本化 + 能力声明 + 优雅降级

### 6.1 schema 版本
信封带 `schema:"message.canonical/1"`。后续加 kind / 字段 = minor（向后兼容，旧消费方忽略未知字段）；删 / 改义 = major（bump 到 `/2`）。AMR 适配器读 `schema` 前缀做兼容路由。

### 6.2 后端能力声明
后端暴露一个 **capabilities 声明**（握手时一次性，或随 envelope 流外带），列出它**能 emit 哪些 kind**：

```json
{ "schema": "message.canonical/1",
  "channel": "wechat",
  "kinds": ["text","image","voice","video","file","link","quote","system"],
  "direction": true,
  "read": {
    "coverage": { "coveredShards": 28, "totalShards": 30 },
    "unreadableChats": ["wxid_test_roy"],
    "gh_messages": "out-of-scope"
  } }
```

- `kinds` = 该后端实现了的 canonical kind 子集。**未列出的 kind**，后端遇到对应消息时降级为 `text`（+ 占位）或 `unknown`，**不报错**。
- `direction:true/false` = 该后端能否标出/入站。false → AMR 默认 `in`，并由下游 `db.apply_self_directions` / self_id 后处理补判（对齐现有 powerdata 注释）。
- `read`（**读覆盖声明**，§6.4 配套）= 后端的读可用性，让消费方预判、运维可见：
  - `read.coverage.{coveredShards, totalShards}` = 后端级分片覆盖（已解密分片 / 总分片）。`coveredShards < totalShards` → 存在读不到的会话。
  - `read.unreadableChats[]`（可选）= 已知当前读不到的会话 id 列表（消费方据此不把它们的空当 0 互动）。
  - `read.gh_messages` ∈ `"supported"` \| `"out-of-scope"`（R4）= 公众号 `gh_*` 图文是否可读；`out-of-scope` 即诚实声明「不读」，**好过静默空**。
  > ⚠️ **字段名待与 fullwechat/PDWX 仁德对齐一次**（请求方明确要求）：`coveredShards`/`totalShards`/`unreadableChats`/`gh_messages` 为 AMR 提案，PR review 时与实现方敲定后落定（口径不变，只对名字）。

### 6.3 优雅降级矩阵

| 后端能力 | AMR 行为 |
|---|---|
| 只 emit `text`（如当前 PowerData prose） | 全部落 `kind=text`，UI 普通气泡。**完全可用**，零退化于现状。 |
| emit 部分 kind（如 fullwechat：text/media/link/quote/system） | 已实现的富渲染；未实现的 kind 由后端自己降级为 text/占位。 |
| 后端给了 AMR 不认识的 kind（未来 schema 漂移） | AMR 按 `unknown` 处理，渲染 `text`。前向兼容。 |

> **向后兼容验证点**：一个只会 `{"kind":"text","text":"...","direction":"in",...}` 的后端，喂给 AMR 必须照常入库、FTS、渲染——这是契约的底线，等价于今天 PowerData 的行为。

### 6.4 读调用返回契约 —— 读空必可区分（R1 / R3 / R4）

§2 定义**单条信封**；本节定义**读调用本身**（`read_messages` / `GET /api/messages`）的返回口径。
病根：后端在**分片密钥缺失 / 消息表不可映**时 `return []`，与「真读到 0 条」无法区分 → 下游把
「读不到」误读成「0 互动」→ 对沉默的人**漏报**（家人雷达：最该报警时反而哑了）。这违背 0 号宪法
第 2 条「结果回交给人看」与运维 loud-fail。本节是**写侧** loud-fail 原则（`text` 永不空 / `direction`
不得一律 `in` / 认不准 `unknown`）的**读侧镜像**。同根于本季「哑失败」治理（`msg_id` 撞键 = C1）。

**R1 · 读空必可区分（核心）**
- **读成功（覆盖已确认，0 或多条）→ 数组**，与今天一致（**向后兼容地板**）。**空数组从此带保证：
  「真读到了 0 条、该会话覆盖完整」**——后端只有能确认覆盖时才允许回空数组。
- **读不可用（分片密钥缺失 / 消息表缺失或未映 / 任何"无法确认覆盖"）→ 不得回空数组**，给可区分信号：
  - **REST**（`GET /api/messages`）：HTTP **409** + 结构化体
    ```json
    { "error": {
        "code": "read_unavailable",
        "reason": "key_unavailable",
        "channel": "wechat",
        "chatId": "wxid_test_roy",
        "coverage": { "coveredShards": 28, "totalShards": 30 }
    } }
    ```
  - **MCP**（`read_messages`）：返回**工具错误**（`isError: true`，体同上），**绝不回 `[]`**。
- `reason` 是**封闭**小枚举（`unknown` 即兜底，新增值走本仓 PR + minor；与 `conformance.py`
  `READ_REASONS` 一致）：`key_unavailable`（分片密钥没提）/ `table_unavailable`（密钥已解但该 talker
  消息表缺失/未映——如 2026-06-29 **Roy 孤例**：会话有 `lastMsgLocalId` 却 per-chat 读空）/ `unknown`
  （说不清、但**确实无法确认覆盖**）。**原则压倒细节：后端凡不能确认该会话覆盖完整，就必须给信号、
  不许回裸空数组。**
- 注：`coverage` 出现在两处——§6.2 capabilities 的 `read.coverage` 是**后端级**能力快照，
  本节 `error.coverage` 是**单次信号**里的现场快照；同名、不同粒度，皆可选。
- 为什么选 **409**（而非 `200`+对象）：非 2xx **逼**消费方分支处理（多数 HTTP client 对非 2xx 抛错），
  这才是 loud-fail；`200`+非数组体会被老消费方静默误迭代 → 又制造一种哑失败，且破坏「success=数组」。
  409 = 已知、reseed 后可重试的命名状态。

**R3 · `ts` 不可在读路径丢**
§2.1 已规定 `ts` 必填；**读调用返回的每条信封必须携带 `ts`**，禁止 MCP / 序列化层省略
（2026-06-29 实战：底层 `/api/messages` 行有 `ts`，但 MCP `server.mjs` 默认未透出 → 仁德本地补丁）。
见 conformance C6。

**R4 · 公众号（`gh_*`）行为契约化**
公众号图文推送不在普通消息表。后端**二选一，禁静默空**：
- **支持读** → 吐 canonical 信封（`kind:"link"`，`link{title,summary,url}` + `ts`）；或
- **不支持** → 在 §6.2 capabilities 声明 `read.gh_messages:"out-of-scope"`，消费方据此预判、不误读为 0。

## 7. AMR 侧职责（与后端分离）

### 7.1 薄适配器映射（envelope → MsgRecord）
AMR 适配器（`fullwechat.py` / `powerdata.py`）的 `map_message` 改为**薄映射**：直接读信封字段填 `ingest.MsgRecord`，不再在适配器里塞 type 判断逻辑（`clean_content` 的 placeholder/XML 启发式逻辑随后端实现契约而**退役**或仅作旧后端兜底）。

| MsgRecord 字段 | ← envelope |
|---|---|
| `content` | `text`（始终非空） |
| `direction` | `direction`（不再硬编码 `"in"`）✅ 修掉 `fullwechat.py:122` 缺口 |
| `type` | `kind`（**语义变更**：从「原始数字字符串」改为「canonical kind 字符串」） |
| `sender` / `sender_id` / `ts` / `is_mentioned` | 同名直填 |
| `media_ref` | `media.ref`（若有） |
| `raw` | 整个信封（含未映射的结构化子对象，供审计/二次解析） |

### 7.2 schema 改动（最小化）
现 `messages.type TEXT DEFAULT 'text'` 列**直接复用**承载 canonical kind——无需加列（`type` 今天已存 `"text"`/原始数字串，改存 canonical kind 是**值域收敛**，更干净）。

结构化子对象（`link`/`file`/`quote`…）**不新增专列**，整条信封进现有 `messages.raw TEXT DEFAULT '{}'`（JSON）。UI / 检索从 `raw` 按需取结构化字段。媒体走已有 `media` 表（`source_ref` ← `media.ref`，`filename`/`ext`/`size` ← `file` 子对象）。

> **迁移**：`type` 列值域从「数字串」迁到「canonical kind」是数据迁移（旧库 `type='49'` → 按 raw 重判 kind，或保守留旧值）。建议出一支幂等迁移：旧值能映射的映射，映射不动的留 `unknown`。无 DDL 变更，纯数据 backfill。**YAGNI：不为本契约加任何新列。**

### 7.3 UI 渲染（气泡已建，简述）
对齐 `2026-06-26-amr-chat-ui-redesign-design.md`，按 kind 取渲染分支：
- `quote` → 正文 + 被引块（`quote.author`/`refText`）。
- `file` → 文件卡（`file.name`/`size`）；`link` / `miniprogram` → 链接卡（`title`/`source`）。
- `system` → 居中灰条（`text`）。
- `image`/`voice`/`video`/`sticker` → 占位（+ voice 的 `transcript`）。
- 缺结构化子对象时一律回退渲染 `text` —— 与气泡现状无缝。

## 8. 取代 / 吸收 PowerData 契约第 5 项

`2026-06-18-powerdata-multiaccount-contract.md` §5 要求 PowerData 把 type-49 展开成「可读摘要 + 可选 `app_type`/`title`/`url`/`filename` 字段」。**本契约取代它**：

- 那里的「可读摘要」= 本契约的顶层 `text`（必填）。
- 那里的可选结构化字段 = 本契约的 `link`/`file`/`quote`/`miniprogram` 子对象（规范化、跨后端统一命名）。
- PowerData 实现本契约即自动满足旧第 5 项；旧 spec §5 标注「已并入 canonical contract」。PowerData 契约其余项（`list_accounts` / `account` 参数 / 游标隔离 / 出站 `account_wxid`）**不受影响**，仍独立有效。

## 9. 冲突 / 口径（王总 2026-06-26 已决；余项交后端实测）

均来自 survey 冲突小节，**禁止硬编码**，由各后端按目标渠道实测，认不准一律 `unknown`（保守 §1.5）。**三项口径已由王总 2026-06-26 钦定**（标 ✅决）：

1. **type 66 双义**（企业名片 vs 红包）——wechaty/web=企业名片，wcf=红包。**✅决：默认 `unknown` + 纯交各后端按渠道实测，AMR 不给统一兜底、不硬编码**（红包本体走 49.2001 更可靠）。
2. **红包三处**（顶层 66 / 49.2001 / 10000 领取文案）——**✅决：以 49.2001 为本体 `red_packet`（计 1），10000 领取文案归 `system`（不另计红包）**，避免一个红包计三次。
3. **49.2003 双义**（红包封面 vs GroupInvite）——按 SDK 实测，默认 `unknown`。
4. **49.74「文件传输中」存疑**——未钉死常量；若后端能确认「先 74 后 6 同文件」，74 可映 `file`（标 `stage:sending`），否则 `unknown`。
5. **表情两出口**（顶层 47 + appmsg 8 都归 `sticker`）——**✅决：两条各算一条消息(不去重)，染色给低权重**。
6. **链接 49.4≈49.5、音乐 49.3≈49.92** 双口径——本契约都归 `link`，无歧义，仅记录。
7. **高位复合 type / wechaty 伪顶层枚举**（本地 4.x DB 导出：引用 822083633 等）——**不进本契约的映射表**。若某后端走本地 DB 路线，它**自己**先把高位复合解回 (主类, 子类) 再映本契约，AMR 不认高位数字。
8. **direction 判定的 self_id 设备后缀**——fullwechat self wxid 带设备后缀，与消息 sender 的 base wxid 不一致（`fullwechat.py:119-121` 注）。后端若标不准 direction，声明 `direction:false`，交 AMR 后处理（§6.2）——**这是 fullwechat 当前缺口的正式出口**。

---

## 附：实现优先级（给后端 Agent，按 ROI，对齐 survey 建议）

1. **direction 标注**（修最痛的缺口）+ `text` 永远在场 + capabilities 声明 — 契约地基。
2. **system**：10002 sysmsg（`revoke`/`pat`/`member_change`）+ 10000 文案兜底 — 关系互动信号。
3. **quote**（49.57，含 refKind 递归一层）— 「谁回了谁」关系建模。
4. **link / file / miniprogram / chat_history**（49.5/6/33-36/19）— 富消息细分，告别笼统 `[链接/文件]`。
5. **51 过滤** — 去噪。
6. 长尾子类 → 就近或 `unknown`（YAGNI，不追全）。
