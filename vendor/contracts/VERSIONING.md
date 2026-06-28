# 契约版本 & 兼容矩阵 v1 — Contract Versioning & Compatibility（双向版本握手）

> 真相源: `agentic-contracts` 仓（本文件，仓根 `VERSIONING.md`）· owner 见 CODEOWNERS（AMR/@gthneo）。本文件定义全仓所有契约共守的版本化 + 兼容矩阵规则。

> **日期** 2026-06-28 ｜ **状态** 定稿（v0.11.0 落地：版本单一真相源 + /api/version + X-AMR-Version 头 + jl account ls 版本列）
> **from** AgenticMessageRouter (AMR) ｜ **about** AMR ↔ fullwechat 后端的版本可见性
> **真相源** `agentic-contracts` 仓（本文件）—— AMR 定义"怎么声明版本、怎么对账"。
> **姊妹契约** `message/canonical.md` / `send-target/send-target.md` / `group-metadata/group-metadata.md` / `moments/read-like.md` + `moments/publish.md` / `control-and-voice/control-and-voice.md`（同在本仓）。
> **归属** 本文件是**运维手册**给 FDE 的对账依据；被开发原则 A7（契约即真相源）引用。

---

## §0 这份规范修什么（交互缺陷）

**现状缺陷（王总 2026-06-28 钦定）**：**版本是隐形的**。fullwechat 后端其实声明了自己的软件版本（`GET /api/status` → `version: "0.12.0"`）和契约版本（`GET /api/capabilities` → `schema: "message.canonical/1"`），但 **AMR 一侧既看不见后端版本，自己也没有一个被亮出来的版本**。出了兼容问题——"后端升了、AMR 没跟上"或反之——**没人一眼看得出两边各自在哪个版本**。

**本规范要求**：把**两侧版本都显式声明、双向可读、可对账**，并把"AMR 自己消费什么契约"写成一份**消费清单**，配一张 **live 兼容矩阵**。让 用户 / 运维FDE / Agent 三方都能看到版本。

---

## §1 两个版本轴（+ AMR 消费侧）

跨系统的"版本"不是一个数，是**两条独立的轴**，外加 AMR 自己的一条：

| 轴 | 是什么 | 谁声明 | 怎么读 | 例 |
|---|---|---|---|---|
| **契约版本** | 接口语义的版本（schema 名 + major），doc 内另有 minor `vX.Y` | 后端（实现方）按 AMR 定的契约暴露 | `GET /api/capabilities` → `.schema` | `message.canonical/1` |
| **后端软件版本** | 后端这个**软件**自己的发布版本 | 后端 | `GET /api/status` → `.version` | `0.12.0` |
| **AMR 消费侧版本** | AMR 这个**软件**自己的版本 + 它**消费**哪些契约 | AMR | `GET /api/version` → `.amr_version` / `.consumes` | `0.11.0` |

**关键区分**：契约版本 ≠ 软件版本。后端可以发 `0.12.0`→`0.13.0`（软件升级）而 `message.canonical/1`（契约）不变；也可以软件不大动而契约从 `/1`→`/2`（破坏性）。两条轴**分别演进、分别声明**。

**AMR 版本单一真相源**：`src/jl/version.py` 的 `__version__`（`jl.__version__`）是**唯一**来源，`pyproject.toml` 镜像它，一条 no-drift 测试锁住二者不漂移。

---

## §2 双向握手（two-sided handshake）

版本可见性不是单向的"AMR 去查后端"，是**两侧各自声明、互相可读**：

```
   后端（数据提供方）                         AMR（消费方）
   ─────────────────                         ─────────────
   声明 PROVIDES：                            声明 CONSUMES：
     GET /api/status      .version    ◄────►   GET /api/version   .amr_version
     GET /api/capabilities .schema    ◄────►                      .consumes
                                              每个出站请求带：
                                                X-AMR-Version: 0.11.0
     （后端日志里看得见是谁在消费）◄──────────  X-AMR-Consumes: message.canonical/1
```

- **后端声明提供**：`status.version`（软件）+ `capabilities`（契约 schema + 能力位）。
- **AMR 声明消费**：`amr_version`（软件）+ `consumes`（消费清单，§3）。
- **AMR 每次出站请求**带 `X-AMR-Version` / `X-AMR-Consumes` 头 → **后端在自己日志里就能看到**是哪个 AMR 版本、按哪个契约在消费它（无需 AMR 主动上报）。
- 两侧都**可读、可对账**：任一方都能查到对方在哪个版本，于是"谁该升、谁没跟上"一目了然。

---

## §3 AMR 消费清单（CONSUMES）

`src/jl/version.py` `CONSUMES`：**逐契约**声明 AMR **今天真正消费**到哪个版本（不是后端暴露了什么，是 AMR 吃进来什么）。

```python
CONSUMES = {
    "message.canonical": "1",   # 消费✅ adapter from_canonical
    "send-target":       "v1",  # 消费✅ 冷会话自动翻出后发送
    "moments":           "v1",  # 消费✅ 读+点赞；v2 发布 ❌未消费
    "group.canonical":   None,  # ❌未消费（群人数仍近似，未读 roster）
    "control":           None,  # ❌未消费（人机互斥/口吻喂养未接）
}
```

**消费侧 ≠ 提供侧**：后端可能**暴露**一个契约（如 group.canonical 的 roster），而 AMR **尚未消费**。这种不对称正是 §5 矩阵要亮出来的。`None` = 尚未消费（不是"不存在"）。

---

## §4 演进规则

- **major（`/1`→`/2`）= 破坏性，另开**：新契约文件、新 schema major。旧的**仍有效**，新的并存（如 moments v1 读/赞仍在，v2 只**新增**发布）。消费方按 `CONSUMES` 显式选版本，不被动跟随。
- **minor（doc 内 `vX.Y`）= 向后兼容增量**：加字段/加能力位，不破坏既有读法。
- **一律能力声明 + 优雅降级**：消费前读 `capabilities` 看能力位在不在；不在则**降级**（如 group meta 缺 → 回落近似人数），绝不硬崩。契约**先于实现**。

---

## §5 兼容矩阵（live）

下表是**对账模板**——契约 × 契约版 × fullwechat `0.12.0` 暴露 × AMR `0.11.0` 消费状态。运维用 `jl account ls`（实时探测 `status.version` + `capabilities.schema`）+ `GET /api/version`（AMR 消费清单）**现场比对**本表：

| 契约 | 契约版 | fullwechat 0.12.0 暴露 | AMR 0.11.0 消费 | 备注 |
|---|---|---|---|---|
| message-canonical | `/1` | ✅ canonical 信封 | ✅ `from_canonical` | 读路径主干 |
| send-target | `v1` | ✅ `send.auto_open` | ◐ 部分 | 驱动发送；尚未富用 `opened` 回执 |
| group-metadata | `/1` | ✅ roster/meta 暴露 | ❌ 待补 | 群人数仍近似，未读 roster |
| moments | `v1` 读·赞 | 按 capabilities | ✅ 读+赞 | — |
| moments | `v2` 发布 | 待实现 | ❌ 待实现 | publish/广播未消费 |
| control-and-voice | `v1` | 待实现 | ❌ 待消费 | 人机互斥 §5 / 口吻 §4 未接 |

> ✅=完整 ◐=部分 ❌=未。**这是模板，不是快照**：真实暴露列以 `jl account ls` 实时探测为准，消费列以 `/api/version` 为准。

---

## §6 三方可见性

同一组版本事实，**按受众分别亮出来**（不写没人看的版本号）：

| 受众 | 在哪看 | 看到什么 |
|---|---|---|
| **用户** | Web UI 右下角 badge | `AMR v0.11.0`（默认今日简报皮肤也在，恒可见、不打扰） |
| **运维 / FDE** | `jl account ls` 版本列 + 后端 health | 每个 fullwechat 账号的**后端软件版本** + **契约 schema**（实时探测，`?`/`unreachable` 优雅显示） |
| **Agent / 运维** | `GET /api/version` + 出站请求头 `X-AMR-Version` | AMR 版本 + 消费清单（机读）；后端日志里看得见 AMR 的版本/契约 |

---

## §7 落地清单（v0.11.0）

- `src/jl/version.py`：`__version__` 单一真相源 + `CONSUMES` 消费清单；`pyproject.toml` 镜像 + no-drift 测试。
- `GET /api/version`（`web.api_version`）：机读身份（免 token，对标后端 public `status.version`）。
- UI badge：`web._index_html()` 把版本注入 `__AMR_VERSION__` 占位，右下角恒显。
- 出站头：`channels/fullwechat._auth_headers` 给 `_get` / `send` 都带 `X-AMR-Version` + `X-AMR-Consumes`。
- `jl account ls`：`onboard.probe_backend_versions` 实时探测后端 `version` + `schema`（5s 超时，down → `unreachable`，绝不崩）。
