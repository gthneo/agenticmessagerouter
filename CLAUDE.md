# AgenticMessageRouter — project instructions

Routing layer for agentic communication: inbound messages from many sources → routed to
the right handler (agent / human / pipeline).

## Project conventions

- **Human-in-the-loop is the highest rule.** Any write / send / publish / destructive
  action goes through an explicit confirmation gate first (dry-run → confirm → act). No
  silent fire-and-forget. Every human intervention leaves a trace (who / when / why).
- **Conservative when unsure** — hand the decision back to the human rather than guessing.
- Keep routing decisions **explainable and logged**, not black-box.
- **LLM-optional core (王总 2026-06-14 钦定).** Every human-communication capability —
  read, search, browse, reply, send — MUST work with zero LLM. The LLM is an optional
  *assist* layer (draft suggestions, summaries, auto-routing, merge candidates, ASR,
  semantic search), never a *gate*: if every model is unreachable, the human stays fully
  in the loop and can still communicate. LLM features degrade gracefully to manual.
  This is human-in-the-loop taken to its end — the human can operate without any AI.
- **Multi-LLM behind a thin abstraction.** When AI assist is introduced, it goes through
  a provider-agnostic `llm` layer + a router that picks by task / cost / availability
  (Claude API primary; local Ollama for cheap/offline fallback). No feature hard-codes a
  single provider.

## Status

**v0.5 数据层地基已落地** — 本仓库是 `jl` 关系账户 router 的工程化产品化家。
多渠道 last 互动 audit；当前实现微信 + 电话渠道，SQLite 真相源 + 加权染色 + dispatch。
（产品全貌与 roadmap 见 `README.md`；设计源头 handoff:
`~/as/changethepeoples/RunEl/handoff/2026-06-13-jl-engineering-handoff.md`）

## Stack / 命令

- Python 3.10+，无第三方运行时依赖（stdlib `sqlite3` + `urllib`）。venv 在 `.venv/`。
- 包：`src/jl/`（`db` / `migrate` / `weighting` / `cli` / `channels/`）。schema：`src/jl/schema.sql`。
- 真相源 DB：`~/.config/jl/jl.db`；可编辑种子：`~/.config/jl/persons.json`。

```sh
.venv/bin/python -m pytest          # 测试（32 passing）
.venv/bin/pip install -e .          # 装 jl 入口到 venv
jl --migrate                        # persons.json → SQLite（幂等）
jl                                  # 全员 sweep
```

`~/bin/jl` 是薄壳 exec venv 入口；v0.4 原型备份 `~/bin/jl-v0.4.bak`。

## 契约依赖（vendored）

契约真相源 = `agentic-contracts` 仓；`vendor/contracts/` 是 pin 在 `CONTRACTS_VERSION` 的只读副本，
**别在这里改，改走那个仓的 PR**；`scripts/sync-contracts.sh` 升级版本。运行时边界校验（msg_key
碰撞检测 + canonical 结构校验）是这套契约的「牙齿」，见 `src/jl/contract_validate.py` 与
`db.insert_messages`，并在 `/api/health` 暴露 `contract_violations_24h`。

## 契约治理铁律 / 开工先读

> 单账号(gthneo)下无天然身份分权，治理靠**机器强制 + agent 级分权**。治理总章在契约仓
> `agentic-contracts/GOVERNANCE.md`，本节是 AMR 这个**消费方**侧的落地纪律。

1. **开工先读** `vendor/contracts/00-CONSTITUTION.md`（0 号宪法·根契约）+ 相关技术契约
   （`vendor/contracts/message/canonical.md` 等）。任何碰对外动作/自动挡/人审窗/Agent 决策权的
   改动，都要能追溯到 0 号宪法、不踩红线。
2. **改契约只走 agentic-contracts 仓 PR**——口径有疑义/要改，**回那个仓提 PR 改契约**，
   **绝不在 AMR 本地打补丁绕契约**（本地硬编码私自解释 = 隐蔽漂移）。
3. **绝不手改 `vendor/contracts/`**——它是 pin 在 `CONTRACTS_VERSION` 的**只读副本**；
   升级版本走 `scripts/sync-contracts.sh`（拉新 tag → 人审 diff → commit），不在副本里就地改。
4. **merge 契约相关改动前跑 conformance**——`pytest tests/test_conformance_fixtures.py`
   （AMR 实现持续对着 `vendor/contracts/fixtures/` 自检，红 = 实现漂离契约）+
   `tests/test_vendor_drift.py`（vendored 副本没被本地手改）。
5. **宪法敏感改动须独立 agent 审**——契约变更 / 碰宪法红线的功能，须由一个**非作者的 fresh
   Claude Code agent** 对着 `vendor/contracts/00-CONSTITUTION.md` 做对抗式审查（作者 agent ≠
   审查 agent），结论贴进 PR，再由人 merge。

## How to work here

- Read this file and `README.md` first.
- **TDD**：纯逻辑（db/migrate/weighting/route/parse）先写失败测试再实现；渠道适配器的活系统路径靠集成跑验证。
- 改 schema 时 `src/jl/schema.sql` 是唯一真相源，`db.py` 读它，勿在两处各写一份 DDL。
- Match surrounding code style.

## Public repo — no real data, ever

This repo is (or will be) **public**. Real contacts and credentials must never be
committed:

- **Secrets via env / local files only** — never literals. WeChat read token:
  `$WX_MCP_TOKEN` or `~/.config/jl/wechat_mcp_token`. fullwechat: `~/.config/agent-wechat/token`.
  See `.env.example`. Real data (persons/db) lives under `~/.config/jl/`, outside the repo.
- **Test fixtures are synthetic**: placeholder names `张三/李四/王五`, `wxid_test_*`,
  numbers in the `+8613000000000` range. Tests assert logic, not real values.
- **Guardrail**: `scripts/secrets-scan.sh` runs as a pre-commit hook (install:
  `printf '#!/bin/sh\nexec "$(git rev-parse --show-toplevel)/scripts/secrets-scan.sh"\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit`).
  Run `scripts/secrets-scan.sh --all` to scan the whole tree.
