<!-- 由 ultraresearch 多智能体研究(6 agents)综合, 2026-06-26. 来源见文末. 知识性综述, 无 PII. -->

# 微信消息 Type 技术综述

## 总述

微信的消息「类型」不是单层枚举，而是**两层结构**：

- **顶层 `MsgType`**——决定消息的大类（文本 1、图片 3、语音 34、视频 43…）。其中 **`49` 是一个万能容器（appmsg）**，链接 / 文件 / 小程序 / 转账 / 红包 / 引用回复 / 合并转发 / 视频号 几乎所有「富消息」都塞进 49。
- **第二层 `<appmsg><type>N</type>`**——当且仅当顶层 = 49 时才有意义，真正语义由这个子 type 决定。

此外还有**结构化系统消息容器**：顶层 `10000`（多为纯中文文案）与 `10002`（`<sysmsg type="...">` 带可解析字段），承载入群 / 撤回 / 拍一拍 / 踢人等事件。

**实现差异是本领域最大的坑**：同一语义在 iPad/Pad 协议、Windows hook（wxhelper / WeChatFerry）、PadLocal、网页版（itchat）、官方开放平台下，落在的 type、是否替你解 XML、解到结构化还是塌成纯文本，都不同。还要警惕**第三套编码**：WeChat 4.x 本地 SQLite 导出工具（LC044/WeChatMsg 等）用「高位复合 type」（如引用 = 822083633、拍一拍 = 922746929、文件 = 1090519089），那是本地库把 `(子类 << 高位) | 主类` 复合编码的结果，**不是实时协议会推给你的 type**，两套不可混用一张映射表。

同一数字在不同层含义完全不同（务必先判顶层是否 = 49 再读 appmsg.type）：

| 数字 | 顶层 MsgType | appmsg 子 type |
|---|---|---|
| 51 | StatusNotify 状态同步/初始化 | 视频号（Channels/Finder） |
| 62 | MicroVideo 小视频 | 拍一拍（部分版本） |
| 2000/2001 | （某些封装层提到顶层）转账/红包 | 转账/红包 |

---

## 顶层 MsgType 主表

| type | 名称 | 含义 / 典型内容 | 备注 / 坑 |
|---|---|---|---|
| 0 | MOMENTS 朋友圈 | wcf 扩展，标「朋友圈消息」 | **仅 WeChatFerry 出现**；wechaty 标准枚举无 0（=Unknown）。SDK 扩展，跨渠道不通用 |
| 1 | TEXT 文本 | 纯文本，content 为明文 | 全实现一致。群消息 content 前缀 `wxid:\n` 发言人需拆分；@ 在 msgsource 的 `<atuserlist>` |
| 3 | IMAGE 图片 | `<img>`（aeskey/cdnthumburl/cdnmidimgurl/md5/length） | 全协议一致。原图常需二次 CDN 下载/解密；网页版给 7 天临时 URL，Pad 给 cdnurl+aeskey，hook 给本地路径——三协议取图路径完全不同 |
| 34 | VOICE 语音 | `<voicemsg>`（voicelength/aeskey/voiceurl/bufid），silk/amr | wechaty 归一为 Audio。需 CDN 下载 + 转码 |
| 37 | VERIFYMSG 好友验证 | 加好友申请，`<msg>` 含验证语 / scene / ticket / encryptusername | wcf 标「好友确认」。**通过好友需用其中 ticket，实战重要**；itchat 早期叫 ADDFRIEND |
| 40 | POSSIBLEFRIEND_MSG 推荐好友 | 可能认识的人（历史 web 协议遗留） | 现代客户端几乎不触发；多为 unhandled |
| 42 | SHARECARD 名片 | 个人/公众号名片，`<msg username= nickname= alias= …>` | wechaty 归一为 Contact。各实现都保留 XML，差异小。企业号名片见 66 争议 |
| 43 | VIDEO 视频 | `<videomsg>`（aeskey/cdnvideourl/cdnthumburl/length/playlength） | 与 62 区分；现代客户端多统一走 43 |
| 47 | EMOTICON 动画表情/sticker | `<emoji>`（md5/cdnurl/aeskey/len），自定义 GIF / 商店表情 | **表情有两个出口：顶层 47（直接发动画表情）vs appmsg 8（分享表情商店条目），统计要两条都收**。自发表情常缺 cdnurl，需用 md5 二次查；网页版抓不到塌成 `[表情]`。wcf 旧文案曾把猜拳/掷骰子也归此 |
| 48 | LOCATION 位置 | `<location>`（x 纬/y 经/label/poiname/scale），静态单点 | 与 49.17 实时位置共享不同。坐标系注意（BD09） |
| 49 | APP / APPMSG 应用消息 | 万能容器，真正语义看 `<appmsg><type>` | **必须解 appmsg XML 取子 type**；Windows PC DB 里正文在 CompressContent（lz4 压缩），只看 StrContent 会拿到空/占位 |
| 50 | VOIPMSG 语音/视频通话 | VoIP 消息事件 | 多 unhandled；与 52/53 构成通话族 |
| 51 | STATUSNOTIFY 状态同步/微信初始化 | 登录初始化下发联系人/会话同步，已读/正在输入等 | **应过滤的协议噪声**。注意与 appmsg 子 type 51（视频号）同号不同义 |
| 52 | VOIPNOTIFY 通话通知 | 未接来电 / 通话结束系统提示 | 通常 unhandled |
| 53 | VOIPINVITE 通话邀请 | 群语音/视频通话邀请 | puppet-xp 里 53 还曾被映射为 GroupNote 之类，实现差异大 |
| 62 | MICROVIDEO 小视频 | 短视频/即拍 | wechaty 归一为 Video；现代客户端渐少见 |
| 66 | （冲突）企业名片 VerifyMsgEnterprise / 红包 | **协议冲突项**：web/padchat = 企业号验证/企业名片；wcf get_msg_types = 「微信红包」 | **高风险冲突**，落库前按目标渠道实测，禁止硬编码。多数富消息（含红包）实际走 49 |
| 9999 | SYSNOTICE 系统通知 | 客户端级提示（区别于 10000） | 老 web 协议常见，hook 端少见，建议过滤 |
| 10000 | SYS 系统消息（纯文本提示） | 入群/退群/被踢、改群名、红包领取、拍一拍、「你已添加 X」、转账被领取/退还等 | **多为自然语言，无稳定字段**，需正则/关键词解析，跨语言/版本文案会变。puppet-xp 把 10000 当 GroupEvent 解析 |
| 10002 | RECALLED / SYSMSG（XML 容器） | `<sysmsg type="...">`：撤回 / 拍一拍 / 踢人 / 群公告等结构化事件 | 与 10000 关键区别 = 带可解析 XML 字段。撤回靠 newmsgid 反查原消息做留痕 |

> 附：wechaty 枚举里还有 `Transfer=2000 / RedEnvelope=2001 / MiniProgram=2002 / File=2004` 等——这些是把 appmsg 子类**归并出的「伪顶层枚举」**，非微信原生即时消息 MsgType（见冲突小节）。

---

## appmsg（49）子类型表

| 49.N | 名称 | 含义 | 关键 XML 字段 |
|---|---|---|---|
| 49.1 | TEXT | 以 appmsg 壳承载的纯文本（少见，多数文本走顶层 1） | `<title>` |
| 49.2 | IMG | appmsg 内嵌图片（少见，图片通常走顶层 3） | — |
| 49.3 | AUDIO/MUSIC 音乐（旧口径） | 音乐/音频链接分享 | `<title>` 曲名 / `<des>` / `<url>` / `<dataurl>` / `<songalbumurl>` |
| 49.4 | VIDEO/URL 链接（部分实现） | 链接分享的一个子值，与 5 常等价 | `<title>` / `<des>` / `<url>` |
| 49.5 | URL 链接·网页·图文 | **最常见**的分享链接/公众号图文/网页卡片 | `<title>` / `<des>` / `<url>` / `<thumburl>` / `<sourcedisplayname>` |
| 49.6 | ATTACH/FILE 文件 | 文件消息 | `<title>` 文件名 / `<appattach>`（`<totallen>` / `<fileext>` / `<attachid>` / `<cdnattachurl>`）/ `<md5>` |
| 49.7 | OPEN 待打开 | 打开 APP/分享回调（罕见） | — |
| 49.8 | EMOJI 自定义表情·GIF | 经 appmsg 通道发的表情/分享表情商店表情 | `<emoji>` 或 appmsg 内 cdnurl/aeskey/md5（与顶层 47 是两条路径） |
| 49.9 | VoiceRemind 语音提醒 | 罕见 | — |
| 49.10 | ScanGood 扫货 | 商品扫码（罕见） | — |
| 49.13 | Good 商品 | 罕见 | — |
| 49.15 | Emotion 表情（商城） | 表情商城类 | — |
| 49.16 | CardTicket 卡券 | 卡包卡券分享 | — |
| 49.17 | RealtimeShareLocation 实时位置共享 | 发起/进行中的实时位置（区别于顶层 48 静态点） | appmsg 内位置节点 |
| 49.19 | ChatHistory 合并转发·聊天记录 | 合并转发卡片 | `<title>` / `<des>` / `<recorditem>`（CDATA 内 `<recordinfo><datalist><dataitem datatype= sourcename= sourcetime=>`）——**需二次/递归解析** |
| 49.24 | NOTE 收藏·笔记 | 收藏/笔记分享，复用 recordinfo（同 19） | `<title>` / `<des>` / `<recorditem>` |
| 49.33 | MiniProgram 小程序 | 小程序卡片 | `<title>` / `<sourcedisplayname>` / `<url>` / `<weappinfo>`（username/appid/pagepath）/ `<thumburl>` |
| 49.36 | MiniProgram2 小程序（新版/另一入口） | 字段同 33 | **33/36 都要当小程序，有的 puppet 只认一个** |
| 49.51 | CHANNEL 视频号（Finder） | 视频号内容分享 | `<finderFeed>`（feedType/desc/nickname/avatar/`<mediaList><media url thumbUrl>`）；旧客户端降级为「当前版本不支持」 |
| 49.53 | GroupNote 接龙/群待办 | 群接龙/群待办类（@wechatferry/core 口径） | — |
| 49.57 | QUOTE/refermsg 引用回复 | 引用回复（带原消息） | `<title>` 本次回复正文 + `<refermsg>`（`<type>` 被引原 type / `<svrid>` / `<fromusr>` / `<chatusr>` / `<displayname>` / `<content>` 被引内容，可能再嵌 XML）——**需按 refermsg.type 递归解析** |
| 49.62 | PAT 拍一拍（appmsg 形态） | 部分版本拍一拍走 appmsg | `<patMsg><records><record fromUser= templete=>`（勿与顶层 62 小视频混淆） |
| 49.63 | CHANNEL_LIVE 视频号直播 | 视频号直播分享 | `<finderLive>`（desc/nickname/media） |
| 49.74 | FILE_SENDING 文件传输中（占位，**存疑**） | 文件「上传中」占位，传完落地为 6 | 字段同 6，但 cdn/attachid 可能未就绪。⚠️ 未用一手源钉死常量，见冲突小节 |
| 49.87 | CHATROOM_NOTICE 群公告 | 群公告，复用 19/24 的 recordInfo | `<recorditem>` |
| 49.92 | MUSIC 音乐（新版） | 现代客户端音乐分享 | `<title>` / `<des>` / `<url>` / `<dataurl>`（与旧口径 3 并存，两者都当音乐） |
| 49.2000 | TRANSFER 转账 | 微信转账 | `<wcpayinfo>`（`<paysubtype>` 收发阶段 / `<feedesc>` 金额如 ¥100.00 / `<pay_memo>` 留言 / `<transferid>`）。paysubtype：1=实时转账(发) 7=非实时(发) 3=实时收钱回执 5=非实时收钱回执 4=退还回执 |
| 49.2001 | RED_ENVELOPE 红包 | 微信红包本体 | `<wcpayinfo>` / `<receivertitle>` / `<sendertitle>` / `<scenetext>` / `<nativeurl>` |
| 49.2003 | RED_ENVELOPE_COVER 红包封面（**冲突**） | 红包封面分享 | 注意：不同枚举体系里 2003 也被用作 GroupInvite，见冲突小节 |
| 49.100001 | ReaderType 公众号阅读类 | wechaty 框架自定义大值，非原生协议字段（罕见） | — |

---

## 系统/通知类（10000 / 10002 sysmsg）

**10000（SYS，纯文本提示）**——多数运营信号（入群/退群/红包领取/改群名/「你已添加 X，现在可以聊天了」/拍一拍）只是一句中文文案，**没有稳定结构化字段**，需关键词/正则解析，且跨语言、跨版本文案会变。撤回在部分老/web 实现也以 10000 纯文案出现。

**10002（SYSMSG，XML 容器）**——`content` 为 `<sysmsg type="...">`，真正事件看 `type` 属性，可解析字段：

| sysmsg type | 事件 | 关键字段 |
|---|---|---|
| `revokemsg` | 撤回 | `<session>` / `<msgid>` 旧本地 id / `<newmsgid>` 新全局 id / `<replacemsg>`（CDATA「"XX" 撤回了一条消息」，群里含撤回人昵称）。防撤回靠 newmsgid 反查 |
| `pat` | 拍一拍 | `<fromusername>` 发起人 / `<chatusername>` 会话 / `<pattedusername>` 被拍者 / `<template>`（可含自定义后缀「拍了拍我的脑袋」） |
| `delchatroommember` | 群成员变更（踢人/邀请） | `<text>` 文案 / `<link><scene>`（invite 等）/ `<memberlist><username>`。既可表移除也可表邀请，看 scene |
| `sysmsgtemplate` | 模板化群通知（「XX 邀请 YY 加入群聊」/群公告/群待办） | `<content_template>` + `<template>`（含 `${...}` 占位符）+ `<link_list>`（占位符→实际昵称/wxid 映射，需回填）。**新版入群/邀请逐步走这个** |
| `multivoip` | 群多人音视频通话提示 | — |

> 入群/退群有**三条并存路径**（版本越新越偏后两者）：10000 纯文案 / 10002 `delchatroommember` / 10002 `sysmsgtemplate`，不能只认一种。

---

## 跨实现差异与陷阱

**谁给结构化、谁给纯文本：**

1. **iPad/Pad 协议（PadLocal、wechatsdk 等商业 Pad）+ Windows hook（wxhelper / WeChatFerry）→ 给结构化**：原样透出 appmsg/emoji 完整 XML，能区分 49 全部子类、能还原引用回复的 refermsg（回复正文 + 被引原文 + 被引人 + 被引原 type）。代价是要自己解 XML、解密 CDN、（Windows）解 CompressContent 的 lz4。子类型最全。
2. **网页版历史协议（itchat / web wx）→ 半结构化偏纯文本**：sticker 默认抓不到塌成 `[表情]` 占位（需抓包注入才显示）；小程序/视频号根本不支持塌成文本；引用回复常塌成普通 TEXT 丢引用关系。该协议已被官方关停，仅历史价值。
3. **官方开放平台/客服 API 及第三方渠道（Sinch 等）→ 最贫**：入站只认 text/media/location，appmsg/引用/sticker/小程序基本不上报或转文本兜底，且有 48h 客服窗口限制。

**陷阱清单：**

- **① 表情两个出口**——顶层 47（动画表情包 `<emoji>`）与 appmsg 8（分享表情），统计/染色要两条都收，否则漏表情。
- **② 49 是大箩筐**——必须解 appmsg 内层 `<type>` 才知真实语义；不解只会看到 `[链接]`/`[文件]` 占位。
- **③ 引用回复 49.57 是「结构化 vs 纯文本」最典型分水岭**——Windows PC DB 正文在 CompressContent（lz4），不解压会以为「没内容」；弱实现把回复正文当 TEXT、丢 refermsg、引用关系彻底丢失。被引 content 若原是图片/文件会再嵌一层 XML，需二级解析。
- **④ 同数字跨层歧义**——51（顶层=状态同步 / appmsg=视频号）、62（顶层=小视频 / appmsg=拍一拍）、2000/2001（既在 appmsg 子类又被某些封装层提到顶层枚举），路由要两边判。
- **⑤ 小程序 33 vs 36 两码并存**，有的 puppet 只认一个。
- **⑥ 撤回/拍一拍落点因实现而异**——主流 hook/gewechat 走 10002 + sysmsg（可解析）；老 web/少数实现走 10000 纯文案。保守做法：10002 优先解 sysmsg.type，兜底再正则扫 10000。
- **⑦ 版本兼容硬墙**——千寻 Pro 文档显示 revoke 等解析在微信 3.9.9.34~3.9.12.56 可用，4.1.2.17 起部分不支持；视频号（appmsg 51/54）旧客户端直接显示「版本不支持」。各协议字段命名略有出入（svrid vs newmsgid、chatusername vs ToUserName）。
- **⑧ 重复计数风险**——红包出现在三处（顶层 66 / 49.2001 / 10000 领取提示）、文件出现在两处（49.6 / DB 高位 1090519089），按接入工具口径择一，勿重复计数。
- **⑨ PadLocal issue #76 证实** appmsg 子类支持是各 puppet 逐步增量补的、覆盖不齐（51/54 等新子类老 puppet 识别为未知 appmsg）。

---

## AMR 现状与 gap

AMR 有**两个微信渠道适配器**，对 type 的处理能力差距很大。

**fullwechat.py（结构化后端，能力较强）**

- **占位（9 个 type）**：`_TYPE_PLACEHOLDER` 把 3/34/42/43/47/48/62/2000/2001 映射为 `[图片]/[语音]/[名片]/[视频]/[表情]/[位置]/[小视频]/[转账]/[红包]`（`src/jl/channels/fullwechat.py:23-26`）。
- **展开（2 个 type）**：
  - type=1 文本直通（`fullwechat.py:36-38, 39`）；
  - type=49 在 `clean_content()` **智能展开**——优先保留后端清洗后的可读文本（引用回复原文/文件名/链接卡），仅在 raw `<appmsg>` XML 时用 `_TITLE_RE` 提取 `<title>`，空值降级为 `[链接/文件]`（`fullwechat.py:42-51`）。
  - 防守逻辑：被误标为 type=1 的媒体 XML（含 `<msg>`/`cdnthumb`/`<img>`）降级为 `[图片]`（`fullwechat.py:52-54`）。
- **type 字段存储**：type=1 → `"text"`；其余 → `str(msg.get("type"))`（`fullwechat.py:113`）。

**powerdata.py（文本导出，永久缺陷）**

- `parse_history()` **无差异处理，硬编码 `type="text"`**（`src/jl/channels/powerdata.py:151`）——PowerData 文本导出丢失结构化 type，只处理 `[HH:MM] 发送者: 内容` 一行格式。
- 仅在 session 预览层用 `_PREVIEW_PREFIX_RE` 剥除 7 个前缀（链接/文件、文本、图片、视频、语音、文件、链接）（`powerdata.py:53`）。
- type=49 app 消息**永久塌成 `[链接/文件]`**，无法区分链接卡/文件/引用回复/小程序，原内容不可恢复——已作为跨账户契约第 5 项提案待 PowerData 侧扩展（`docs/superpowers/specs/2026-06-18-powerdata-multiaccount-contract.md:50-68`）。

**Gap（黑洞）**

- fullwechat 中**除 1/3/34/42/43/47/48/49/62/2000/2001 外的所有 type**，既无占位也无展开，直接 `str(原 type)` 落库（`fullwechat.py:113`）。具体黑洞：
  - **37 好友验证**——未识别，但 ticket 是加好友关键信号，对关系账户 router 价值高。
  - **10000 / 10002 系统消息**——入群/退群/拍一拍/撤回全部未解析，落成原始字符串。对「多渠道 last 互动 audit」这类关系运营信号是实打实的缺口。
  - **49 子类未细分**——fullwechat 虽展开 49，但只取后端清洗文本/title，未按 appmsg.type 区分链接 5 / 文件 6 / 引用 57 / 转账 2000 / 红包 2001（顶层 2000/2001 已占位，但**走 49 通道的转账/红包不会命中顶层占位**）。
  - 视频号 51/63、小程序 33/36、合并转发 19 等均未结构化。

**建议补哪些 type 的展开（按 ROI 排序）**

1. **10002 sysmsg（撤回 revokemsg / 拍一拍 pat / 群成员 delchatroommember）** + **10000 文案兜底解析**——关系互动信号，对 last-互动 audit 与染色直接有用。
2. **37 好友验证**——加好友是关系建立起点，应识别并区分于普通消息。
3. **49.57 引用回复**——还原 refermsg（被引人/被引原文），引用关系对「谁回了谁」的关系建模有价值。
4. **49.6 文件 / 49.5 链接 / 49.33-36 小程序 / 49.19 合并转发**——按 appmsg.type 细分占位（至少给 `[文件]/[链接]/[小程序]/[聊天记录]` 而非笼统 `[链接/文件]`）。
5. **51 顶层（StatusNotify）显式过滤**——避免协议噪声当业务消息入库。

> 注意 AMR CLAUDE.md 纪律：改的是渠道适配器解析路径，应靠**集成跑真机样本**验证（纯 mapper 部分可 TDD），且落库前按各接入工具（fullwechat/PowerData/PadLocal）**各自口径分别建表**，勿用一张映射表硬套（呼应 system 角度 caveat 1）。

---

## 冲突与不确定

1. **code 66 冲突**：wechaty/web/padchat = 企业名片 VerifyMsgEnterprise；WeChatFerry get_msg_types = 「微信红包」。源自不同协议层，**落库前必须按实际渠道抓包确认，禁止硬编码**。
2. **红包出现在三处**：顶层 66（老口径）/ 49.2001（appmsg 本体）/ 10000（领取系统文案）。按接入工具口径择一，勿重复计数。
3. **49.74「文件传输中」存疑**：未用一手源（padlocal/wcf/wxhelper）定位到明确常量，仅社区通识。建议用真实样本验证（同一文件先 74 后 6）。
4. **49.2003 编号冲突**：wetrace = 红包封面 RedEnvelopeCover；另一些枚举体系里 2003 被用作 GroupInvite。同一数字两义，按 SDK 实测。
5. **音乐双口径并存**：旧协议 49.3 = 音乐、现代客户端 49.92 = 音乐，解析时两者都当音乐。
6. **链接 49.4 与 49.5 常等价**，不同客户端把分享链接发成 4 或 5。
7. **撤回/拍一拍落点因实现而异**（10002 结构化 vs 10000 纯文案），追踪需双覆盖。
8. **wechaty 的伪顶层枚举**（Transfer=2000/RedEnvelope=2001/MiniProgram=2002/File=2004/ReaderType=100001）是框架把 appmsg 子类归并出的自定义值，**非微信原生即时消息 MsgType**，跨实现不通用。
9. **第三套「高位复合 type」**（本地 4.x DB 导出：引用 822083633 / 拍一拍 922746929 / 文件 1090519089 / 视频号 754974769）是本地库复合编码，**不是实时协议会推的 type**，与小整数协议 type 不可混用一张映射表。
10. **wxhelper 官方 type 表本轮未直接抓到源码页**，其数值与 wcf 基本同源（都 hook PC 客户端），但 3.9.x 版本间偶有增删，标注为待补。
11. **CompressContent lz4 解压**在部分 Python lz4 库下与原始算法有分块/header 差异，需注意。
12. **DB 列结构（Type/SubType/StrContent/CompressContent）结论部分来自 v2ex/CSDN 二手转述**与 LC044/WeChatMsg，建议落库前用真机 wcf/wxhelper 抓一条 49.57 与一条 47 实测核对。
13. 37/40/9999/50/52/53 在 hook 端覆盖不全或几乎不触发（37 实战重要，40/9999 多为 web 协议遗留）。

---

## 来源

- wechaty/puppet `src/schemas/message.ts`——WechatMessageType + WechatAppMessageType 权威枚举
- WeChatFerry (wcf) `wcferry/wxmsg` 文档 + `get_msg_types` 中文映射（含 zhuanlan.zhihu.com/p/18006455210、blog.csdn.net/qq_47452807/article/details/138536720）
- @wechatferry/core（jsdocs.io）——WechatMessageType / WechatAppMessageType 数字对照（Windows 4.x hook 路线）
- afumu/wetrace `internal/model/message.go`——现代客户端 appmsg 子类型常量 + WCPayInfo/FinderFeed/ReferMsg/RecordInfo/PatMsg XML 结构
- itchat 文档 `intro/messages`——网页 web 协议 MsgType 映射
- opentdp/wechat-rest `wclient`（pkg.go.dev）——顶层 type 与 appmsg 子类枚举
- wechaty/puppet-xp `src/puppet-xp.ts`——onHookRecvMsg 实际分发（10000=GroupEvent、10002=Recalled）
- wechaty/puppet-padlocal issues/76——appmsg 51/54 视频号子类支持讨论
- wechaty.js.org 2019/07/08（room-join data-stream）——sysmsg delchatroommember/revokemsg/sysmsgtemplate/multivoip XML 实例
- jianshu.com/p/dcdae5eb0829——防撤回 10002 + sysmsg revokemsg 字段
- daenmax.github.io/qxpro-doc（千寻 Pro）——revoke 回调字段 + 版本兼容表（3.9.9.34~3.9.12.56、4.1.2.17 不支持）
- wkteam.cn / apifox（E云管家·Gewechat callback）——sysmsg type=pat 结构
- developers.weixin.qq.com 官方社区——引用 appmsg type=57 + refermsg
- ttttupup/wxhelper（Windows hook 逆向）——refermsg 字段、type=49 发送参数
- LC044/WeChatMsg + v2ex.com/t/1000303 + blog.csdn.net/ljc545w/article/details/128499591——PC DB Type/SubType、CompressContent lz4、高位复合 type 来源
- blog.csdn.net/ziyunyang/article/details/81534560——MsgType 全表
- wechatsdk.com（iPad 协议商）——appmsg XML 结构与子 type 发送文档
- geeeeeeeeek/electronic-wechat issues/2——网页版 sticker 塌占位证据
- HalfdogStudio/wechat-user-bot issues/54——MsgType=49 & appmsgtype=6 即文件 佐证
- developers.sinch.com（WeChat channel support）——官方/第三方渠道入站仅 text/media/location
- littlecodersh/ItChat issues/159——GIF = 49 AppMsgType 8 旁证
- **AMR 代码**：`src/jl/channels/fullwechat.py:23-26,36-55,113`、`src/jl/channels/powerdata.py:53,151`、`docs/superpowers/specs/2026-06-18-powerdata-multiaccount-contract.md:50-68`
