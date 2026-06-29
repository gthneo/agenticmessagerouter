# fixtures/read-contract — 读调用返回契约 fixtures（非单条信封）

这些 fixture 校验的是**读调用本身**的返回口径（`canonical.md` §6.4 / conformance C6），
不是单条 canonical 信封 —— 故**单独成目录**，`scripts/conformance.py` 用**另一套校验**
（`validate_read_signal`）跑它们，不与 `message-canonical/` 的信封校验混淆。

- `read_unavailable.json` —— R1「读不可用」可区分信号体（REST 409 / MCP 工具错误的 `body`）。
  必含 `error.code=="read_unavailable"` + `error.reason` + `error.chatId`；`coverage` 可选。

> 合成占位（公开仓铁律）：`wxid_test_*` 占位 id，无真实 PII。
