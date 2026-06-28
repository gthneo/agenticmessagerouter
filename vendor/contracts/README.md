# vendor/contracts — vendored copy of agentic-contracts (READ-ONLY)

契约真相源 = `agentic-contracts` 仓 (https://github.com/gthneo/agentic-contracts)。
这是 **v0.1.0** 的 vendored 只读副本, **别在这里改** —— 改走那个仓的 PR (CODEOWNERS 走 owner review)。
`scripts/sync-contracts.sh` 升级版本 (pin 在仓根 `CONTRACTS_VERSION`)。

AMR 是这套契约的一个**消费方**: 把固定 tag 的契约拉进来 commit, 构建/测试时零网络、可复现、可 diff 审。
运行时的契约边界校验 (msg_key 碰撞检测 + canonical 结构校验) 见 `src/jl/contract_validate.py` 与
`src/jl/db.py:insert_messages`; 一致性 fixtures 见本目录 `fixtures/` (随契约 tag 一起 vendored)。
