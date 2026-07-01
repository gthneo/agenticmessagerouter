# 【来自 fullwechat 仁德】能力更新广播:send 现在 build 无关(任何微信 build 都能发)

> 2026-07-01 ｜ 发起:fullwechat 仁德 ｜ 收:**所有 fullwechat 后端的数据消费方**(AMR / AMP / MCP 消费方如 Claude Code awx-mcp / Selling 会话)
> 版本:fullwechat v0.13.4 ｜ 缩写:a11y = accessibility(无障碍树)/ build = 微信客户端构建版本

## 一句话
fullwechat 后端的 **send(发消息)能力从"部分微信 build 可用"升级为"任何 build 都可用"**。之前某些 build(如 .28 仁兄的 7b3f07cc)因缺 frida 内存偏移 profile,send 对冷会话报 `Unknown BuildID prefix` 失败;现在有 **a11y 降级**(搜索开会话,不依赖 frida),**build 无关**。

## 消费方要知道的两件事
1. **能力声明变了**:`GET /api/capabilities` 的 `send.supported` **现在恒 true**(所有后端实例)。之前 .28 报 false(旧逻辑探 frida),会让消费方误判"这个号发不了"。**请消费方重新读一次 capabilities,别再缓存旧的 false。**
2. **send 行为不变**:接口还是 `POST /api/messages/send {chatId, text}`,回执 `{ok, opened, ...}`。冷会话会自动搜索开会话(可能慢几秒,a11y 走界面)。HITL 不变:对外发送仍应人审。

## 背景(为何之前失败)
- send 底层开会话靠 chat-select(frida 读内存会话向量),需每个微信 build 一份内存偏移 profile。.28 的 build 7b3f07cc 不在已知 profile 里 → send 全挂。
- 修法:build 无 profile 时,send 改走 a11y(wxid→contact.db 备注名→搜索框→点唯一精确匹配联系人→发)。一劳永逸(frida 偏移每次微信升级要重逆向;a11y 稳)。
- 真机验证:.28 给一个冷联系人发送成功。

## 对各消费方的具体影响
- **AMR**:接入 .28(仁兄 slot)后可正常 send;之前若因 send.supported=false 跳过发送,现在应放开。ingest(读)一直正常,不受影响。
- **AMP / 营销发布**:朋友圈发布是另一条路(不受此变更影响);普通 send 现在全 build 可用。
- **MCP 消费方(Claude Code awx-mcp)**:`send_text` 工具在任何后端 build 都能用了。
- **Selling 会话**:bd仁德 的 "班迪2727 发不出" P0 已修(见 handback:`RunEl/handoff/2026-07-01-awx-send-buildid-FIXED-handback.md`)。

## 边角
- filehelper(文件传输助手)是系统账号,走最近会话列表特例(需在最近会话里)。
- 真正逆向 7b3f07cc 的 frida profile 留 backlog,不阻塞。

---
*能力更新广播,供所有消费方对齐。有疑问提。真相源:fullwechat capabilities 端点 + 本文件。*
