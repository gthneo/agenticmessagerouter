# message-canonical fixtures — 可执行一致性测试（conformance）

这些 JSON 信封是 **`message/canonical.md` 契约的可执行一致性测试**。

- **fixtures = 可执行一致性测试**：每条都是一个 **conformant**（合规）的 canonical 信封示例。
- **消费方 CI 跑它们验证自己的实现/解析符合契约**：AMR、AMP、各通道后端，把这些 fixtures
  喂给自己的 canonical 解析器 / 校验器，**全部吃下不报错 = 绿**；任何一条解不动 / 判违规 = **红**，
  说明该实现与契约漂移了，得回本仓对齐（改契约走 PR，或修实现）。
- **不合规 = 红**：消费方也应反向测「**故意不合规**的信封被自己的校验器拒掉」
  （缺必填 / kind 不在枚举 / direction 非 in|out / `msg_id` 空或非字符串）。
  本目录只放**合规**样例当正向一致性基线；负向样例由各消费方自带（如 AMR
  `tests/test_contract_validate.py`）。

## 当前 fixtures

| 文件 | 覆盖 |
|---|---|
| `valid_text.json` | 最小合规文本信封（必填字段 + `msg_id` 全局唯一锚点）。 |
| `valid_group_mention.json` | 群里 @ 了 self（`is_mentioned: true`）。 |
| `valid_quote.json` | 引用回复（`kind:quote` + `quote` 子对象，`direction:out`）。 |

## 参考消费方实现

AMR 是参考消费方：把固定 tag 的本仓 vendor 进 `vendor/contracts/`，CI 跑这些 fixtures 当一致性
校验 + 一道**运行时边界校验**（`src/jl/contract_validate.py` 的 `validate_canonical` /
`db.insert_messages` 的 msg_key 碰撞检测）。vendor 机制见本仓 `scripts/sync-contracts.sh`
的参考实现（在 AMR 仓）。

## 公开仓纪律

全部**合成占位**：`张三/李四/王五`、`wxid_test_*`、`srv_*` 合成 id。**绝无真实 PII / token / wxid**。

> 语义口径（schema 表达不了的，如 `msg_id` 必须全局唯一）见同级 `../conformance.md`。
