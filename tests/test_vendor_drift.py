"""vendor-漂移检查 —— 防"本地手改 vendored 副本"这一漂移口。

`vendor/contracts/` 是 pin 在 `CONTRACTS_VERSION` 的**只读副本**。如果有人/agent 在 AMR 本地
**就地改了 vendored 契约**（而不是回 agentic-contracts 仓提 PR + 重 sync），实现看起来"符合契约"，
其实那份契约已和真相源对不上 —— 这是一种隐蔽漂移。本测试守的就是这个口。

两档校验，自动择稳：

* **强校验（首选，真相源在手时）**：在本地 agentic-contracts 检出处对 `CONTRACTS_VERSION` 指定的
  tag 跑 `git archive`，把 tag 的 tracked 树逐文件**字节比对** `vendor/contracts/`。任何差异 = 红
  —— 这能逮到任意本地手改。**唯一豁免** `vendor/contracts/README.md`：`scripts/sync-contracts.sh`
  按设计用 AMR 自己的 vendoring 说明覆盖它（见脚本 step 2），故不参与字节比对。

* **弱校验（离线/无真相源时退化）**：仍然有用 —— 断言 `CONTRACTS_VERSION` 是合法 `v*` pin、
  `vendor/contracts/` 在、根契约 `00-CONSTITUTION.md` 与关键契约文件都在、且 vendored 根 README
  申明的就是只读副本。这逮不到"内容被手改"，但逮得到"vendor 结构被破坏/版本 pin 丢失"。

CI 里若把 agentic-contracts 也检出在 `~/as/agentic-contracts`（或 `AGENTIC_CONTRACTS_LOCAL`），
就跑强校验；否则自动退弱校验，永不因无网而假绿。全合成内容，public-safe。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_VENDOR_DIR = _REPO_ROOT / "vendor" / "contracts"
_VERSION_FILE = _REPO_ROOT / "CONTRACTS_VERSION"

# sync-contracts.sh 按设计用 AMR 自己的 vendoring 说明覆盖这个文件 —— 强校验里豁免它。
_VENDOR_OVERRIDE = {"README.md"}

# 本地真相源检出（与 sync-contracts.sh 的 LOCAL_FALLBACK 同口径）。
_LOCAL_CONTRACTS = pathlib.Path(
    os.environ.get("AGENTIC_CONTRACTS_LOCAL", str(pathlib.Path.home() / "as" / "agentic-contracts"))
)


def _pinned_version() -> str:
    assert _VERSION_FILE.is_file(), "缺 CONTRACTS_VERSION —— vendor 没 pin 在任何 tag"
    return _VERSION_FILE.read_text(encoding="utf-8").strip()


# ----- 弱校验（永远跑，离线也跑）---------------------------------------------

def test_contracts_version_is_pinned_to_a_tag():
    """CONTRACTS_VERSION 必须存在且是合法的 v* tag pin。"""
    v = _pinned_version()
    assert v.startswith("v") and len(v) > 1, f"CONTRACTS_VERSION={v!r} 不是合法 v* tag"


def test_vendor_dir_and_key_contracts_present():
    """vendored 副本结构在 + 根契约/关键契约文件都在（结构没被破坏）。"""
    assert _VENDOR_DIR.is_dir(), f"缺 vendored 副本目录 {_VENDOR_DIR}"
    for rel in (
        "00-CONSTITUTION.md",       # 0 号宪法·根契约
        "message/canonical.md",     # 核心技术契约
        "VERSIONING.md",
        "CODEOWNERS",
    ):
        assert (_VENDOR_DIR / rel).is_file(), f"vendored 副本缺关键契约文件：{rel}"


def test_vendor_readme_declares_readonly():
    """vendored 根 README 必须申明这是只读副本（防有人把它当可改文档）。"""
    readme = _VENDOR_DIR / "README.md"
    assert readme.is_file(), "缺 vendored 根 README（vendoring 说明）"
    text = readme.read_text(encoding="utf-8")
    assert "READ-ONLY" in text or "只读" in text, "vendored README 未申明只读副本"


# ----- 强校验（真相源在手时）：逐文件字节比对 vendor 树 vs pinned tag --------

def _have_truth_source() -> bool:
    return (_LOCAL_CONTRACTS / ".git").is_dir()


def _export_tag_tree(version: str, dest: pathlib.Path) -> bool:
    """把 _LOCAL_CONTRACTS 在 `version` tag 的 tracked 树导出到 dest。tag 不存在则 False。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(_LOCAL_CONTRACTS), "archive", "--format=tar", version],
            capture_output=True, check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    tar_path = dest / "_tag.tar"
    tar_path.write_bytes(proc.stdout)
    with tarfile.open(tar_path) as tf:
        tf.extractall(dest)  # noqa: S202 — trusted local git archive of our own contracts repo
    tar_path.unlink()
    return True


@pytest.mark.skipif(not _have_truth_source(),
                    reason=f"无本地真相源 {_LOCAL_CONTRACTS} —— 退化为弱校验（上面那几条）")
def test_vendor_matches_pinned_tag_byte_for_byte():
    """强校验：vendored 每个文件都与 CONTRACTS_VERSION 指定 tag 的内容**字节一致**
    （唯一豁免 README.md —— sync 脚本按设计覆盖它）。任何差异 = 本地手改了 vendored 副本 = 红。"""
    version = _pinned_version()
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="vendor-drift-"))
    try:
        if not _export_tag_tree(version, tmp):
            pytest.skip(f"本地真相源没有 tag {version} —— 无法强校验，弱校验已覆盖结构")

        tag_files = {
            p.relative_to(tmp).as_posix()
            for p in tmp.rglob("*") if p.is_file()
        }
        vendor_files = {
            p.relative_to(_VENDOR_DIR).as_posix()
            for p in _VENDOR_DIR.rglob("*") if p.is_file()
        }

        # 集合一致（豁免 README.md）：vendor 不能多/少文件。
        cmp_tag = tag_files - _VENDOR_OVERRIDE
        cmp_vendor = vendor_files - _VENDOR_OVERRIDE
        assert cmp_vendor == cmp_tag, (
            "vendored 文件集合与 tag 不一致（有人增删了 vendored 文件）：\n"
            f"  仅 vendor 有：{sorted(cmp_vendor - cmp_tag)}\n"
            f"  仅 tag 有：{sorted(cmp_tag - cmp_vendor)}"
        )

        # 逐文件字节比对。
        drifted = []
        for rel in sorted(cmp_tag):
            if (tmp / rel).read_bytes() != (_VENDOR_DIR / rel).read_bytes():
                drifted.append(rel)
        assert not drifted, (
            "vendored 副本被本地手改（与 pinned tag 字节不一致）：\n  "
            + "\n  ".join(drifted)
            + f"\n→ 别在 vendor/contracts/ 就地改；改契约走 agentic-contracts 仓 PR，再 "
            f"`bash scripts/sync-contracts.sh {version}` 重 sync。"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
