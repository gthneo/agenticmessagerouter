# 微信消息规范化契约 v1 — WeChat Canonical Message Contract — Design / Contract

> **日期** 2026-06-26 ｜ **状态** 提案（待 fullwechat / PowerData 两侧后端评估实现）
> **定义方** AgenticMessageRouter (AMR / the Router)。**实现方** 各微信后端（fullwechat、PowerData，未来 PadLocal 等）。
> **依据** 调研综述 `docs/reference/wechat-message-types.md`（顶层 MsgType + appmsg(49) 子类 + 10000/10002 sysmsg + 跨实现差异）。
> **关系** 本契约**取代并吸收**了 `docs/superpowers/specs/2026-06-18-powerdata-multiaccount-contract.md` 第 5 项（「`get_chat_history` 展开 app 消息」）——那一项的「可读摘要 + 可选结构化字段」诉求，在这里被升级为一个**所有后端统一遵守的规范化信封**，PowerData 第 5 项以本契约为准（见 §8）。

## 0. 这份契约是什么 / 不是什么

微信原始协议是**两层枚举 + 三套不兼容编码**（实时协议小整数 / wechaty 伪顶层枚举 / 本地 4.x DB 高位复合 type），且「谁解 XML、解到结构化还是塌成纯文本」逐后端不同（survey §跨实现差异）。让每个 AMR 适配器各自去认这堆原始 type，是把协议复杂度泄漏进 Router。

**本契约把这层复杂度推回后端**：AMR 定义一个**规范化消息信封**（canonical envelope），每个微信后端把自己的原始协议**映射**进这个信封；AMR 适配器只做**薄映射**（envelope → `MsgRecord`）；UI 富渲染。

- **是**：一个 read / representation（读 + 表示）层的归一接口。后端「我读到的这条微信消息，规范化后长这样」。
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
  "schema": "wechat.canonical/1",
  "direction": "in",
  "kind": "quote",
  "text": "好的，按这个版本改\n↩ 引用 张三: 季度报表初稿",
  "ts": 1750000000,
  "sender": "张三",
  "sender_id": "wxid_test_zhangsan",
  "is_mentioned": false,
  "quote": {
    "author": "张三",
    "text": "季度报表初稿",
    "refKind": "file",
    "refText": "Q2-report.pdf"
  }
}
```

### 2.1 顶层字段（所有 kind 通用）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | str | 是 | 契约版本标识，v1 固定 `"wechat.canonical/1"`（见 §6 版本化）。 |
| `direction` | `"in"` \| `"out"` | 是 | 出/入站。后端按 self_id 判定；**不得**一律 `"in"`。无法判定时见 §6.3 降级。 |
| `kind` | enum（§3） | 是 | 规范化大类。认不出 → `"unknown"`。 |
| `text` | str | 是 | 人类可读 display text，**永远非空**（最差也给占位 `[图片]` / `[链接/文件]` / `[系统消息]`）。 |
| `ts` | int | 是 | unix 秒。 |
| `sender` | str | 是 | 发言人显示名（群里是发言成员名）。 |
| `sender_id` | str | 否 | 发言人稳定 id（wxid）。给得出就给，利于跨账户归一。 |
| `is_mentioned` | bool | 否 | 本条是否 @ 了 self（来自 msgsource `<atuserlist>`）。默认 false。 |
| `msg_id` | str | 否 | 后端的稳定消息 id（如 fullwechat localId/serverId）。没有就省略，AMR 退回 content-hash 去重（`ingest.msg_key`）。 |

### 2.2 Per-kind 结构化子对象（可选，按 kind 出现）

| 子对象 | 出现于 kind | 字段 |
|---|---|---|
| `link` | `link` | `{title, url, source?}` — `source` = 公众号/站点名（`sourcedisplayname`）。 |
| `file` | `file` | `{name, ext?, size?}` — `size` 字节数。 |
| `quote` | `quote` | `{author, text, refKind, refText}` — `refKind` = **被引消息的 canonical kind**；`refText` = 被引内容的 display text。`text` 顶层字段 = 本次回复正文。 |
| `miniprogram` | `miniprogram` | `{title, source?, url?}` — `source` = 小程序名（`sourcedisplayname`）。 |
| `chat_history` | `chat_history` | `{title, items?}` — 合并转发；`items` = `[{author, kind, text}]`（可选；解不动就只给 `title`）。 |
| `location` | `location` | `{label?, poi?, lat?, lng?}` — `label` 地名，`poi` POI 名。坐标可省。 |
| `system` | `system` | `{event, actor?, text}` — `event ∈ {revoke, pat, member_change, notice}`；`actor` = 触发者显示名。 |
| `media` | `image` `voice` `video` `sticker` | `{placeholder, ref?, transcript?}` — `placeholder` = `[图片]`/`[语音]`… ；`ref` = CDN/本地引用（供后续取媒体，对齐 `media` 表 `source_ref`）；`transcript` = 语音转写（可选 ASR assist）。 |
| `payment` | `transfer` `red_packet` | `{amount?, memo?, stage?}` — `amount` 如 `"¥100.00"`；`stage` = 收发阶段（survey: paysubtype 1/3/4/5/7）。 |

> 不在表内的字段一律不要求。后端只 emit 它解得出的子对象；缺失即「这一维我没解」，AMR 不报错。

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

## 4. 映射表：微信原始 type/subtype → canonical kind

后端据此把原始协议映射进信封。表分三段：顶层 MsgType、appmsg(49) 子类、10000/10002 sysmsg。所有数字依据 survey 主表/子表/sysmsg 表。

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
| 49.57 | QUOTE 引用回复 | `quote` | `quote{author:displayname, text:被引content, refKind:map(refermsg.type), refText}`；顶层 `text`=回复正文。**递归按 refermsg.type 解被引** |
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
信封带 `schema:"wechat.canonical/1"`。后续加 kind / 字段 = minor（向后兼容，旧消费方忽略未知字段）；删 / 改义 = major（bump 到 `/2`）。AMR 适配器读 `schema` 前缀做兼容路由。

### 6.2 后端能力声明
后端暴露一个 **capabilities 声明**（握手时一次性，或随 envelope 流外带），列出它**能 emit 哪些 kind**：

```json
{ "schema": "wechat.canonical/1",
  "kinds": ["text","image","voice","video","file","link","quote","system"],
  "direction": true }
```

- `kinds` = 该后端实现了的 canonical kind 子集。**未列出的 kind**，后端遇到对应消息时降级为 `text`（+ 占位）或 `unknown`，**不报错**。
- `direction:true/false` = 该后端能否标出/入站。false → AMR 默认 `in`，并由下游 `db.apply_self_directions` / self_id 后处理补判（对齐现有 powerdata 注释）。

### 6.3 优雅降级矩阵

| 后端能力 | AMR 行为 |
|---|---|
| 只 emit `text`（如当前 PowerData prose） | 全部落 `kind=text`，UI 普通气泡。**完全可用**，零退化于现状。 |
| emit 部分 kind（如 fullwechat：text/media/link/quote/system） | 已实现的富渲染；未实现的 kind 由后端自己降级为 text/占位。 |
| 后端给了 AMR 不认识的 kind（未来 schema 漂移） | AMR 按 `unknown` 处理，渲染 `text`。前向兼容。 |

> **向后兼容验证点**：一个只会 `{"kind":"text","text":"...","direction":"in",...}` 的后端，喂给 AMR 必须照常入库、FTS、渲染——这是契约的底线，等价于今天 PowerData 的行为。

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
