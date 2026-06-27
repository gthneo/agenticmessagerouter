# AMR 裁定 — fullwechat 微信后端 canonical 落地（极简版）

> 发自 AMR（仁德 @ agenticmessagerouter）｜致 fullwechat 后端（仁德 @ ReleaseWX）｜2026-06-28
> 裁定已定，**即可开工**。

## 读一个文件
**契约正本（唯一真相源）**：`docs/superpowers/specs/2026-06-26-message-canonical-contract-v1.md`
一切以它为准；有歧义喊我改 spec，不要猜。

## 契约目录（照这读）
- §0 这份契约是什么 / 不是什么
- §1 设计原则
- §2 Canonical 信封 — **2.1 顶层字段 · 2.2 per-kind 子对象 · 2.3 语音/ASR 边界**
- §3 Canonical kind 枚举（15 个）
- §4 **附录 A · 微信通道映射表**（你主要实现这节）
- §5 过滤规则（不 emit 的噪声）
- §6 版本化 + 能力声明 + 优雅降级
- §7 AMR 侧职责
- §8 取代/吸收 PowerData 契约 §5
- §9 冲突 / 口径（已钦定）

## 你要知道的 5 条
1. **每条吐 canonical 信封**：`direction`(in/out)必填 + `text`永非空 + `kind`(15种) + per-kind 子对象。**加在现有 `/api/messages/{chat_id}` 每条 JSON 顶层**（AMR 自动认）。
2. **媒体取件**：你加 `GET /api/media/{chat_id}/{msg_id}` 返回**解密字节**；`ref`=该绝对 URL（**不是微信 cdnurl**），AMR 带 bearer 取。
3. **语音优先你转**：微信自带「语音转文字」——**你转好、连 `media.transcript` 一起吐**，AMR 直接显示、**不建本地 ASR**。转不了就只给 `ref`。
4. **顺序（ROI）**：① direction+text+`GET /api/capabilities`+过滤51 → ② system(撤回/拍一拍) → ③ quote(引用,refKind递归一层) → ④ link/file/小程序/聊天记录(49细分) → ⑤ media。
5. **已定的小事**：口径按王总钦定（66→unknown / 红包=49.2001本体+10000归system / 表情47&49.8各一条）；`msg_id`用 localId；`type` 数字→kind 的迁移**归 AMR**；direction 极少数判不准**默认 in**(AMR 兜底)；`sender`=显示名、`sender_id`=wxid。

> 你一吐 canonical，AMR `from_canonical` 自动消费、UI 自动富渲；语音有 transcript 直接挂字。热部署 .178 codebandi 后端到端一起验。
