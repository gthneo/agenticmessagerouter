# GOVERNANCE — 治理铁律（先读）

> 本仓是 agentic 生态所有契约的**唯一真相源**。契约一旦漂移，整个生态对不上话。
> 本文件规定**怎么防止漂移**——而且要防到「即便全是同一个账号在干活，漂移在结构上也不可能」。
> 配套：根契约 [`00-CONSTITUTION.md`](00-CONSTITUTION.md)、版本规则 [`VERSIONING.md`](VERSIONING.md)、
> 工作流总览见 [`README.md`](README.md)。

---

## 0. 开宗明义：单账号 = 无身份分权 → 必须机器强制 + agent 级分权

传统软件治理靠**两个人**：你写、另一个人审你。这套分权的前提是「作者 ≠ 审查者」这两个
**不同的人格/身份**天然存在。

但本生态的现实是：**契约仓、消费方仓、CI、写代码的 Claude Code agent——全在同一个 GitHub +
Claude Code 账号（`gthneo`）下**。没有「另一个人来审你」这种天然的身份分权。如果什么都不做，
治理就退化成「自己审自己」=「没有审查」。

所以本仓的治理铁律建立在两条腿上，缺一条都不成立：

1. **机器强制（CI，不可绕）**——把能判的规则交给机器判，机器不讲人情、不会"这次先放过"。
   人/agent 想绕也绕不动（红就 merge 不了）。
2. **★ agent 级分权（AI 时代的分权）**——把"作者 ≠ 审查者"这条分权，从**人的身份**层
   下沉到 **agent 的身份**层：**写契约的 agent ≠ 审契约的 agent**。一个独立的、非作者的
   fresh Claude Code agent 对着 0 号宪法做**对抗式审查**。这是单账号下**重建分权的唯一办法**。

> 一句话：**人不够分权，就让机器和 agent 来补分权。**

---

## 1. 改契约只走 PR（main 已 branch-protected）

契约**绝不**被某一方在自己仓里悄悄改，也**绝不**直推 `main`。

- `main` 已开启分支保护：**require PR + 含管理员（enforce_admins）+ 禁直推 / 禁 force-push**。
  即便是仓主、即便是管理员，也只能走 PR——这关掉了「凭权限旁路 PR」这个漂移口。
- 改契约 = 在分支上改对应 spec → 开 PR → 写清「改了什么 / 为什么 / 影响哪些消费方」。
- owner 由根 `CODEOWNERS` 自动派（业务契约 = AMR，运维契约 = fullwechat）。

---

## 2. ★ agent 级分权（AI 时代的分权）——本治理的命门

**哪些 PR 必须过 agent 级分权审查：**

- **契约变更 PR**（动了 `message/` `send-target/` `moments/` 等任何 spec 或 `00-CONSTITUTION.md`）；
- **宪法敏感的功能 PR**（消费方仓里新增/改动一个**碰到对外动作、自动挡、人审窗、Agent 决策权**的功能）。

**怎么审（硬约束）：**

> 由**一个独立的、非作者的 agent**——一个 **fresh Claude Code agent**，**不是**写这个变更的同一个
> agent——对着 [`00-CONSTITUTION.md`](00-CONSTITUTION.md) + 相关技术契约 + **跑一遍 conformance**，
> 做**对抗式审查**（adversarial review）。逐条核：

1. **有没有旁路人**——这个变更有没有引入「甩手全自动、人被旁路」的路径？对外动作（send /
   publish / like）是否仍走人审 / 否决窗？自动挡是否仍要人手拨、系统不自升？结果是否回交给人看？
   （0 号宪法红线）
2. **有没有偷改语义**——有没有在不动 schema 的情况下，悄悄改了某字段的**语义口径**
   （如 `msg_id` 唯一性 / `direction` 判定 / `kind` 枚举）？这种漂移 schema 抓不住，必须人/agent 核。
3. **有没有过 conformance**——`scripts/conformance.py` 是否绿？fixtures 是否仍自洽？
4. **是否违宪**——有没有任何一条踩 `00-CONSTITUTION.md` 的红线？

**审查 agent ≠ 作者 agent**——这是单账号下重建分权的**唯一办法**。同一个 agent 自审自，
等于没审。必须另起一个干净上下文的 agent，立场是「挑毛病」，不是「确认通过」。

**审查结论贴进 PR**（用 PR 模板里的"独立 agent 对抗审查结论"段），**再由人 merge**——
HITL 最后一闸。CI 绿 + 独立 agent 审过 + 人按下 merge，三者齐了才算数。

---

## 3. CI 闸（机器，不可绕）

PR 必须过 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)，红就 merge 不了。三步：

- **(a) lint**——关键治理/契约文件在不在 + markdown 基本健全（非空 / UTF-8）。
- **(b) conformance**——跑 [`scripts/conformance.py`](scripts/conformance.py)：校验
  `fixtures/message-canonical/*.json` 每条都符合 canonical 契约（必填字段 / `kind` 落 15 值枚举 /
  `direction` ∈ {in,out} / `schema` 前缀 / `msg_id` 若存在则非空字符串），且 fixtures 之间
  **`msg_id` 全局唯一**（自洽）。这把 `fixtures/conformance.md` 里能自动化的口径变成跑得起来的检查。
- **(c) compat-gate**——占位的兼容闸（按 `VERSIONING.md`：major 须**另开新文件 + bump**）。
  现在先留住这道 step，逻辑后续接。

> **给分支保护用的 required status check 名 = `conformance`。** 在 GitHub branch protection
> 里把它设为 required，PR 不绿就不能 merge——这是机器闸真正"长牙"的地方。

---

## 4. vendor 不手改（消费方侧的漂移口）

消费方（AMR / AMP / fullwechat / PowerData …）vendor 一份本仓某 tag 的**只读副本**
（如 AMR `vendor/contracts/`，pin 在 `CONTRACTS_VERSION`）。

- **`vendor/contracts/` 是只读副本，绝不手改。** 想改契约 → **回本仓提 PR + 重新 sync**
  （跑消费方的 `scripts/sync-contracts.sh`），不在 vendored 副本里就地改。
- **就地改 vendored 副本 = 一种隐蔽漂移**：实现看起来"符合契约"，其实那份契约已被本地偷改，
  和真相源对不上。
- **消费方 CI 必须有 vendor-漂移检查**：校验 `vendor/contracts/` 没被本地手改（比对 vendored
  内容与 `CONTRACTS_VERSION` 指定 tag 的内容是否一致；离线时退化为结构 + 版本一致性检查）。
  参考实现：AMR `tests/test_vendor_drift.py`。

---

## 5. 每条变更可追溯到 0 号宪法 —— 违宪不予采纳

[`00-CONSTITUTION.md`](00-CONSTITUTION.md) 是契约树的**根**。下面每份技术契约的设计与评审，
都以它为最终裁决依据。它的红线（引 §红线）：

- 不得设计"甩手全自动、人被旁路"的路径；
- 不得让 Agent 替人做不可逆决策；
- 不得为了自动化牺牲人的决策权。

**违背本宪法的契约 / 实现，无论多高效，不予采纳。** 任何一条变更，PR 里都要能说出它如何
**可追溯到 0 号宪法**、没踩红线——这正是第 2 节独立 agent 对抗审查要逐条核的事。

---

## 速查：一个契约变更 PR 的闸序

```
作者 agent 在分支改 spec
        │
        ▼
开 PR（填 PR 模板：改了什么/治理清单/独立审查结论）
        │
        ▼
机器闸：CI ci.yml 绿（lint + conformance + compat-gate）   ← 不可绕
        │
        ▼
agent 级分权：独立 fresh agent 对着 00-CONSTITUTION 对抗审，结论贴 PR   ← 作者 agent ≠ 审查 agent
        │
        ▼
人 merge（HITL 最后一闸）                                   ← 系统永不自动合并
```

> 三道闸——**机器（CI）+ agent 分权（独立审查）+ 人（merge）**——任一缺位，这条 PR 不算合规。
