# 请求 AMR 定义「富文件抢救归档契约 v1」(Rich-File Rescue-Archive Contract v1)

> fullwechat 仁德发起 ｜ 2026-07-01 ｜ 班迪钦定 ｜ 契约真相源 = agentic-contracts 仓(AMR 定义,fullwechat 实现)
> 取代早期草案 `docs/rich-file-archive-contract-request.md`(6-28,已被本文并入更新)。
> 术语:provenance /ˈprɒv.ən.əns/ = 数据的来源履历(从哪来/谁发/何时/是否已读)。

## 一、这契约解决什么(一句话)
微信里的业务文档(PDF/DOC/PPT/表格/附件)是**易失资产**——微信 CDN(Content Delivery Network,内容分发网络)只临时中转、到期删,不主动抢救就永久丢。本契约把这些文档**在过期前抢救到本地 + 带 provenance 元数据暴露出去**,让 AMR/AMP/任何消费方能把它们**归到人/事、参与营销·销售·生产**。这是 inbox 方向(读入)的算料抢救。

## 二、地基事实(fullwechat 真机查证,供 AMR 设计参考)
1. **文档明文,不加密**:PDF/DOC/PPT/ZIP 在本地 `msg/file/<年-月>/` 是**明文**(实测头部就是 %PDF/PK/D0CF11E0),直接可读,**无需解密**。(区别:图片 .dat 才加密。)
2. **易失窗口**:每条文件消息 XML 带 `media_expire_at` 到期戳,到期−发送 ≈ **7~14 天**(大文件更短),到期云端永久删。
3. **落盘时机**:桌面版"文件自动下载 ≤N MB"开关(实测 .28 ON, N=20MB)→ **≤20MB 文档自动落盘**(被动抢救已生效);>20MB 或历史件需主动点开(手机版更严:不点开就不下)。
4. **联系人关联**:文档在 `msg/file/年月/`,**文件夹只按年月、不带联系人**;归属靠**数据库**——消息库 `Msg_<md5(会话)>` 表的文件消息行带文件名/大小/md5/路径/发送人/时间/到期戳。图片/视频则在 `msg/attach/<md5(会话)>/年月/`,文件夹路径即带会话。
5. **现状**:.28 历史 38923 条文件消息,本地仅存 ~13.5%(86% 已过期只剩壳)——**抢救有强时效性**。
6. **底座**:Task #4 `media-export.py`(分类导出)+ `/api/media/{chat}/{msg_id}`(单条取件)已具备。

## 三、请 AMR 定义(consumable menu 标准:形态/取法/能力声明,你拍)
### 3.1 富文件 canonical 形态(rich_file item)
建议字段(你定):
- **file_id**(稳定 id,建议 md5)、**name**、**ext**、**size**、**mime**
- **provenance**: `{ chat_id, chat_name, sender_id?, sender_name?, sent_at, is_read, msg_id }`
- **status**: `local`(本地有,可取)/ `recoverable`(本地无但仍在 CDN 窗口内,可主动下载)/ `expired`(壳,已丢)
- **expire_at**(排优先级用)、**retrieval_url**(取件端点绝对 URL)

### 3.2 取法(端点,fullwechat 实现)
- **列举**:`GET /api/files?chat=&since=&status=` → rich_file item 列表(按 chat/时间/状态过滤)。
- **取件**:`GET /api/media/{chat}/{msg_id}`(已有,取解密/明文字节)。
- **主动抢救**:`POST /api/files/rescue {chat, msg_id}` → 对 `recoverable` 件用 XML 的 cdnurl+key 趁窗口期回 CDN 下载到本地,转 `local`。
- **能力声明**:`GET /api/capabilities` 加 `rich_files: {list, rescue, auto_download_mb}`。

### 3.3 选择策略(班迪钦定)
- **业务/小群 → 全量抢救**;**大群 → 只抢"客户认同"的**(需人工/规则筛)。
- **不删微信原文件**:归档=抢救到本地一份,保留时间点风貌(provenance 里 is_read 等)。
- **归类归 AMR**:fullwechat 只抢字节 + 给结构化 provenance,**归到人/事由 AMR 判**(它有人/事模型)。

### 3.4 时效/优先级
- 按 `expire_at` 排:**快到期的先抢**;`recoverable` 的趁窗口主动下载;`expired` 只留元数据(告诉消费方"这曾有个文件但已丢")。
- 前置建议:确保桌面"文件自动下载"开着(被动救 ≤N MB);N 可调高多救大文件。

## 四、与 WPS 等的差异(为什么值得做)
WPS/坚果云/百度网盘只**监听文件夹拷明文**(微信无开放读文件 API)。**我们查库还能给 provenance**(谁发/何时/是否已读/到期戳/属于哪个会话)——纯文件夹监控给不了。这是"file → 可归类可消费 data"的关键。

## 五、给 AMR 仁德的话
请质疑/讨论两轮再定稿(尤其 status 枚举、provenance 字段、选择策略口径),产出到 agentic-contracts 仓。我据此实现列举/取件/主动抢救端点 + capabilities 声明。有歧义提,别让我猜。
