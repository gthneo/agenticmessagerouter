#!/usr/bin/env python3
"""conformance.py — CI 闸的可执行一致性校验（机器，不可绕）。

把 `fixtures/conformance.md` 里**能自动化**的语义口径，变成跑得起来的检查：
对 `fixtures/message-canonical/*.json` 里每条 canonical 信封做**结构 + 语义**校验，
任一条不合规 → 非 0 退出 → PR 的 CI 红 → merge 不了。

这是单账号生态下的**机器闸**：实现/契约示例一旦漂离 canonical 契约（缺必填 / kind 不在
枚举 / direction 非 in|out / msg_id 空或非字符串 / msg_id 跨 fixtures 撞车），CI 立刻变红，
不靠人眼、不可绕。与 AMR 消费方侧 `tests/test_conformance_fixtures.py` 同口径（双向自检）。

无第三方依赖（纯 stdlib），public-safe（只读合成 fixtures）。

退出码：0 = 全合规；1 = 有违规（细节打到 stderr）。
"""
from __future__ import annotations

import json
import pathlib
import sys

# --- canonical 口径（与 message/canonical.md §2.1 + §3 一致；漂移先改契约）-----------

# kind 封闭枚举 —— message/canonical.md §3（15 个值，conformance.md C4）。
CANONICAL_KINDS = frozenset({
    "text", "image", "voice", "video", "file", "link", "quote", "miniprogram",
    "chat_history", "location", "sticker", "transfer", "red_packet", "system", "unknown",
})

# 每条信封必填的顶层字段（§2.1）。
REQUIRED_FIELDS = ("schema", "channel", "kind", "text", "ts", "direction")

DIRECTIONS = frozenset({"in", "out"})

SCHEMA_PREFIX = "message.canonical/"  # conformance.md C5：schema 前缀决定兼容路由。


def validate_envelope(env: object) -> list[str]:
    """对单条 canonical 信封返回违规字符串列表（[] = 合规）。纯函数，永不抛。

    覆盖 conformance.md 里能在**单条**上自动化的口径：
      C3 text 非空 / C4 kind 落枚举 / C5 schema 前缀 / direction ∈ {in,out} /
      C1 的结构地板（msg_id 若存在则为非空字符串）。
    """
    if not isinstance(env, dict):
        return [f"envelope is not an object (got {type(env).__name__})"]

    viol: list[str] = []

    # 必填字段（含 C3 text 非空：值为 None/"" 也算缺）。
    for f in REQUIRED_FIELDS:
        if f not in env or env[f] in (None, ""):
            viol.append(f"missing required field: {f}")

    schema = env.get("schema")
    if isinstance(schema, str) and not schema.startswith(SCHEMA_PREFIX):
        viol.append(f"schema {schema!r} must start with {SCHEMA_PREFIX!r} (C5)")

    kind = env.get("kind")
    if kind is not None and kind not in CANONICAL_KINDS:
        viol.append(f"kind {kind!r} not in canonical enum (C4)")

    direction = env.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        viol.append(f"direction {direction!r} not in {{in,out}}")

    # C1 的结构地板：msg_id 可选，但存在即必须是非空字符串（数值/空 = localId-as-id 嫌疑）。
    if "msg_id" in env:
        mid = env["msg_id"]
        if not isinstance(mid, str) or not mid:
            viol.append(f"msg_id must be a non-empty string (got {mid!r}) (C1)")

    return viol


# C6（conformance.md）：读不可用信号体的结构闸。读调用读不到时不得回空数组，要回
# {error:{code:"read_unavailable", reason, chatId, ...}}（REST 409 / MCP 工具错误的 body）。
READ_REASONS = frozenset({"key_unavailable", "table_unavailable", "unknown"})


def validate_read_signal(obj: object) -> list[str]:
    """对单个「读不可用」信号体返回违规列表（[] = 合规）。纯函数，永不抛。

    口径见 message/canonical.md §6.4 + conformance.md C6：必须有 error.code=="read_unavailable"
    + reason 落 READ_REASONS + 非空 chatId。coverage 可选。
    """
    if not isinstance(obj, dict):
        return [f"read-signal is not an object (got {type(obj).__name__})"]
    err = obj.get("error")
    if not isinstance(err, dict):
        return ["read-signal missing object field: error (C6)"]
    viol: list[str] = []
    if err.get("code") != "read_unavailable":
        viol.append(f'error.code must be "read_unavailable" (got {err.get("code")!r}) (C6)')
    if err.get("reason") not in READ_REASONS:
        viol.append(f"error.reason {err.get('reason')!r} not in {sorted(READ_REASONS)} (C6)")
    if not (isinstance(err.get("chatId"), str) and err.get("chatId")):
        viol.append("error.chatId must be a non-empty string (C6)")
    return viol


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parent.parent
    fixtures_dir = root / "fixtures" / "message-canonical"
    files = sorted(fixtures_dir.glob("*.json"))

    if not files:
        print(f"conformance: NO fixtures found under {fixtures_dir} — fail-closed", file=sys.stderr)
        return 1

    total_viol = 0
    seen_msg_ids: dict[str, str] = {}  # C1：fixtures 自洽 —— msg_id 跨文件全局唯一。

    for f in files:
        try:
            env = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [FAIL] {f.name}: cannot parse JSON: {e}", file=sys.stderr)
            total_viol += 1
            continue

        viol = validate_envelope(env)
        for v in viol:
            print(f"  [FAIL] {f.name}: {v}", file=sys.stderr)
        total_viol += len(viol)

        mid = env.get("msg_id") if isinstance(env, dict) else None
        if isinstance(mid, str) and mid:
            if mid in seen_msg_ids:
                print(f"  [FAIL] {f.name}: msg_id {mid!r} collides with {seen_msg_ids[mid]} "
                      f"(C1: msg_id 必须全局唯一)", file=sys.stderr)
                total_viol += 1
            else:
                seen_msg_ids[mid] = f.name

        if not viol:
            print(f"  [ok]   {f.name}")

    # C6：读不可用信号体（另一套校验，单独目录，不与信封混淆）。
    read_dir = root / "fixtures" / "read-contract"
    read_files = sorted(read_dir.glob("*.json"))
    for f in read_files:
        try:
            obj = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"  [FAIL] {f.name}: cannot parse JSON: {e}", file=sys.stderr)
            total_viol += 1
            continue
        viol = validate_read_signal(obj)
        for v in viol:
            print(f"  [FAIL] {f.name}: {v}", file=sys.stderr)
        total_viol += len(viol)
        if not viol:
            print(f"  [ok]   {f.name} (read-signal)")

    print(f"\nconformance: {len(files)} envelope + {len(read_files)} read-signal fixtures, "
          f"{total_viol} violation(s)")
    if total_viol:
        print("conformance: RED — fixtures drifted from message.canonical contract", file=sys.stderr)
        return 1
    print("conformance: GREEN — all fixtures conform to message.canonical")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
