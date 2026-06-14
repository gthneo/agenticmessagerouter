# AgenticMessageRouter — `jl` 关系账户 Router

多渠道关系账户健康度 audit。把一个人散落在微信 / 电话 / 飞书 / iMessage / 邮件 的
"最后一次互动" 汇到一处，加权染色，红色清单提示该主动联络谁 —— **联络稿默认给，发不发人决定**。

> 状态：**v0.5 数据层地基**（SQLite 真相源 + 迁移 + dispatch）。渠道目前实现微信 + 电话，
> 其余渠道与 watch/discover/AppleScript/cron 在后续增量。

## 这是什么

从「人」（关系账户）发散到「事」（business）的 **人 → 事 Agent 化软件**。
模仿 Writing ↔ ACP 关系模式：班迪 dogfood → 易链员工用 → 客户盒子。

```
inbound 多渠道 last 互动  →  统一 person 档  →  加权综合 last  →  染色  →  红色清单 + 联络稿(不自动发)
```

## 核心方法（5 步）

1. 统一 person 档（多渠道 ID 集合，存 SQLite）
2. 各渠道 last 互动并行 reach
3. 综合 last = 渠道权重 × recency 加权选取（**不是简单 max**；真实天数保留给阈值）
4. 染色：🟢 < 半阈值 / 🟡 < 阈值 / 🔴 超阈值 / ⚪ 无数据
5. 输出状态卡 + 红色清单 + 联络稿（人在回路，不自动发）

## 用法

```sh
jl              # 全员 sweep + 加权染色 + 红色清单 + 待补
jl <名>         # 单人 deep dive（各渠道 last + 综合）
jl 救补          # 缺 wxid / 电话 的待补队列
jl --migrate    # persons.json → SQLite（幂等）
jl --dump-yaml  # SQLite 真相源的人读视图
jl --tokens     # token / reach 用量反馈
```

姓名可用 id / 全名 / 别名任意一个。

## 架构

```
src/jl/
  schema.sql        5 表：persons / channels / interactions / events / tokens
  db.py             连接 + 初始化 + CRUD（真相源，替代 v0.4 运行时读 json）
  migrate.py        persons.json → SQLite（幂等，json 退化为可编辑种子）
  weighting.py      加权综合 last + 染色（纯函数）
  channels/
    wechat.py       微信 MCP 适配器（parse_history 纯函数已测）
    phone.py        CallHistory + AddressBook 反查（norm/tail_match 已测）
  cli.py            route(纯) + 各子命令 handler
```

- **真相源是 SQLite**（与 sqlite-vec embedding 同栈，为 N8 语义召回铺路）。
- **events 表 = 人在回路审计**：每次 sweep / migration / 干预留痕（谁 / 何时 / 为什么）。
- **jl 从不主动发**：只给红色清单与（后续）联络稿草稿，人决定发否。

## 开发

```sh
python3 -m venv .venv
.venv/bin/pip install -e . pytest
.venv/bin/python -m pytest        # 32 tests
```

`~/bin/jl` 是薄壳，exec 本仓库 venv 的 `jl` 入口。v0.4 单文件原型备份在 `~/bin/jl-v0.4.bak`。

## Roadmap（v0.5 后续增量）

- [x] 数据层：SQLite 5 表 + migration + dispatch（本增量）
- [x] 综合 last 加权染色（非简单 max）
- [ ] 渠道扩展：飞书 / iMessage / 邮件 + WhatsApp / WeCom 打桩
- [ ] AppleScript 自动补 Contacts（🟡jl- 前缀 + 班迪校验）
- [ ] watch 10min 实时盯 + discover 新私聊人自动推选
- [ ] memory-add 工具 + 本地 embedding 语义召回
- [ ] Daily cron + 部署拓扑 + 交付文档（营销/使用/培训/运维）
```
