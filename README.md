# AgenticMessageRouter (AMR) — `jl` 关系账户 Router + 统一消息库

把一个人散落在微信 / 企微 / 飞书 / 电话 / 邮件的**全部会话与消息**汇聚成一个统一消息库，
按人归一、加权染色、可搜索；红色清单提示该主动联络谁 —— **回复/发送默认走人审批，jl 从不自动发**。

> 状态：**v0.6 消息库地基（sub-project A）已落地** —— accounts / conversations / messages /
> media 四表 + FTS5 全文搜索 + 8-bit 多账号 + 派生 last 互动 + HITL 复位(reset)。
> 摄取适配器与 5 分钟轮询（点火 ignite）在 sub-project B；浏览器统一收件箱在 C/D。

## 这是什么

从「人」（关系账户）发散到「事」（business）的 **人 → 事 Agent 化软件**。最终形态是一个
中心化的统一消息库 + 浏览器收件箱：人在浏览器里看到所有接入渠道的消息，并能回复回去
（回复走审批 outbox）。模仿 Writing ↔ ACP 关系模式：班迪 dogfood → 易链员工用 → 客户盒子。

```
        浏览器统一收件箱（看全部 + 回复）              [D]
                     ↓ HTTP/WS
        AMR 后端 API（.156）                          [C]
        └── SQLite: messages + FTS5 (+ 向量 sqlite-vec 后续)  [A] ← 本仓库已落地
                     ↑ 摄取 ingest（点火/5min 轮询）
   边缘采集器/适配器（在数据所在处跑）                  [B]
   fullwechat@.178 REST · lark-cli · wecom-cli · CallHistory(Mac 本地)
```

## 子项目分解（A→B→C→D→E）

| 子项目 | 内容 | 状态 |
|---|---|---|
| **A 消息库地基** | accounts/conversations/messages/media + FTS5 + 8-bit 多账号 + 派生 last + 复位 | ✅ 本仓库 |
| B 摄取 + 5min 轮询 | 四渠道 backfill+增量（fullwechat/lark/wecom/电话）、点火 ignite、mute、边缘 push | 下一步 |
| C 后端 API | .156 暴露 REST/WS：列消息/搜索/会话/回复队列 | 待 |
| D 浏览器统一收件箱 | 看全部 + 回复（回复→审批 outbox） | 待 |
| E 发送 | 各渠道 send + outbox 审批 + 白名单 | 待 |

## 核心方法

1. **统一 person 档** + **8-bit 多账号**（一个人有多个微信/企微/飞书；`account_id` 记"消息从我哪个收件箱进"，`person_id` 记"对端是谁"，两维正交）
2. 各渠道会话/消息**摄取入库**（去重靠 `msg_key`：平台稳定 ID，否则内容哈希）
3. **派生综合 last**：从 messages 按人取各平台最新一条 → 渠道权重 × recency 加权选取（**非简单 max**；真实天数留给阈值）
4. 染色：🟢 < 半阈值 / 🟡 < 阈值 / 🔴 超阈值 / ⚪ 无数据
5. 输出状态卡 + 红色清单（人在回路，不自动发）

## 用法

```sh
jl              # 全员 sweep + 加权染色 + 红色清单（读派生 last）
jl <名>         # 单人 deep dive（各平台 last + 综合）
jl 救补          # 缺 wxid / 电话 的待补队列
jl --migrate    # persons.json → SQLite（幂等）
jl --dump-yaml  # SQLite 真相源的人读视图
jl --tokens     # token / reach 用量反馈
jl reset        # 复位 dry-run（列出将清除的 messages/conversations/media 计数，不删）
jl reset --confirm           # 确认后真正清除（persons 永不受影响）
jl reset --channel wechat --confirm   # 限定单平台
jl reset --all --confirm     # 连 accounts 注册表一起清
```

> sweep 现在读**派生 last**（来自已入库的 messages）。在 sub-project B 的摄取/轮询把消息灌进来之前，
> 全员显示 ⚪（无数据）属正常 —— 这正是"库先建好、摄取后填"的架构。

## 架构

```
src/jl/
  schema.sql   persons / channels / accounts / conversations / messages / media
               / events / tokens + messages_fts(FTS5 trigram) + 同步触发器
  db.py        连接 + 初始化 + CRUD + search_messages + derive_last_interactions + reset_store
  ingest.py    纯：ConvRecord/MsgRecord + IngestAdapter ABC + msg_key/blob_path（B 的适配器复用）
  migrate.py   persons.json → SQLite（幂等，json 退化为可编辑种子）
  weighting.py 加权综合 last + 染色（纯函数）
  channels/    wechat.py / phone.py 渠道适配器（解析纯函数已测；B 接入结构化后端）
  cli.py       route(纯) + 各子命令 handler（含 HITL 复位门）
```

- **真相源是 SQLite**；大文件（PDF/语音/视频）不进库 —— `media` 表只存引用，字节走**内容寻址 blob store**（`blobs/<sha256>`，跨人/账号天然去重）。语音/视频转写、PDF 抽文进 B 层 ASR，落 `media.transcript` 并镜像进 messages 供搜索。
- **搜索**：FTS5 trigram（CJK 子串）；查询 < 3 字自动退化 LIKE 兜底（2 字中文词如"合同/发票"）。向量语义召回（sqlite-vec + Ollama@.156）随 N8 接入，messages 已预留 `embedding_id`。
- **events 表 = 人在回路审计**：sweep / migration / detail / **reset** 全留痕（谁/何时/为什么）；复位在删除**之前**写审计，trace 必存活。
- **jl 从不主动发**：只给红色清单；发送/回复走 sub-project E 的审批 outbox + 白名单。

## 开发

```sh
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python -m pytest        # 76 tests
```

`~/bin/jl` 是薄壳，exec 本仓库 venv 的 `jl` 入口。v0.4 单文件原型备份在 `~/bin/jl-v0.4.bak`。

## 公开仓 · 零真实数据

本仓库 public。真实联系人/凭据**绝不入库**：密钥走 env / `~/.config/jl/` 本地文件（见 `.env.example`），
测试 fixture 全合成（张三/李四/王五、`wxid_test_*`、`+8613000000000` 段）。`scripts/secrets-scan.sh`
作为 pre-commit 钩子拦截真实数据。详见 `CLAUDE.md`。

## Roadmap

- [x] v0.5 数据层：persons/channels + 加权染色 + dispatch
- [x] **v0.6 sub-project A 消息库地基**：accounts/conversations/messages/media + FTS5 + 8-bit 多账号 + 派生 last + HITL 复位
- [ ] **B（下一步）**：四渠道摄取适配器（fullwechat/lark-cli/wecom-cli/CallHistory）+ 点火 ignite + 5min 轮询 + mute + 媒体懒取/转写
- [ ] C 后端 API（.156）+ D 浏览器统一收件箱
- [ ] E 发送：各渠道 send + outbox 审批 + 白名单
- [ ] N8 向量语义召回（sqlite-vec + Ollama@.156）

设计与计划见 `docs/superpowers/specs/` 与 `docs/superpowers/plans/`。
