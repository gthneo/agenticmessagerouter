"""AMR 实现持续对着契约 fixtures 自检 —— 一致性闸（机器，不可绕）。

把 `vendor/contracts/fixtures/message-canonical/*.json`（随契约 tag 一起 vendored 进来的
一致性 fixtures）喂给 AMR 自己的 canonical 校验器 `jl.contract_validate.validate_canonical`，
**全部 0 violation = 绿**。任一条解不动 / 判违规 = **红** —— 说明 AMR 的实现已经漂离了契约
（要么修实现对齐，要么改契约走 agentic-contracts 仓 PR 再重 sync）。

这是契约仓 `scripts/conformance.py` 的**消费方侧镜像**：同一批 fixtures，两边都跑，双向自检。
全部为合成占位（张三/李四/王五、wxid_test_*、srv_* 合成 id），public-safe。
"""
from __future__ import annotations

import json
import pathlib

import pytest

from jl import contract_validate

# vendor/contracts/ 是 pin 在 CONTRACTS_VERSION 的只读副本（fixtures 随契约 tag vendored）。
_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_FIXTURES_DIR = _REPO_ROOT / "vendor" / "contracts" / "fixtures" / "message-canonical"


def _fixture_files() -> list[pathlib.Path]:
    return sorted(_FIXTURES_DIR.glob("*.json"))


def test_fixtures_dir_is_vendored():
    """vendored fixtures 目录必须在（否则 v0.1.0 没 fixtures —— 先 sync 到 ≥v0.2.0）。"""
    assert _FIXTURES_DIR.is_dir(), (
        f"缺 {_FIXTURES_DIR} —— 跑 `bash scripts/sync-contracts.sh v0.2.0` 把 fixtures vendored 进来"
    )
    assert _fixture_files(), "vendored fixtures 目录为空 —— 契约 tag 可能 < v0.2.0"


@pytest.mark.parametrize("fx", _fixture_files(), ids=lambda p: p.name)
def test_vendored_fixture_conforms_to_canonical(fx: pathlib.Path):
    """每条 vendored 合规 fixture 经 AMR 校验器必须 0 violation（实现 ⊨ 契约）。"""
    env = json.loads(fx.read_text(encoding="utf-8"))
    viol = contract_validate.validate_canonical(env)
    assert viol == [], f"{fx.name} 不被 AMR canonical 校验器接受（实现漂离契约）：{viol}"


def test_all_vendored_fixtures_conform_in_aggregate():
    """聚合断言：全部 vendored fixtures 0 violation（即便上面的 parametrize 被跳过也兜底）。"""
    files = _fixture_files()
    assert files, "no vendored fixtures to check"
    total = 0
    for fx in files:
        env = json.loads(fx.read_text(encoding="utf-8"))
        total += len(contract_validate.validate_canonical(env))
    assert total == 0, f"vendored fixtures 共 {total} 处 violation —— 实现与契约漂移了"
