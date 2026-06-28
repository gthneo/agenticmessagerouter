"""双闸安全区分类器 (设计 §6). 只分类「这条草稿能不能自动发」, **绝不发送**.

闸一 (确定性·零 LLM): 命中话术库 (safe_phrases).
闸二 (LLM assist): 风险判定由调用方传入 verdict ('low'/'high'/None) ——
    本模块不调 LLM, verdict 由外层 LLM 层传入后注入.

allow_auto = 闸一命中 ∧ 闸二判低.

铁律:
- LLM 只能往人那边拦 (更保守), 绝不越白名单授权发出.
- 无 verdict (LLM 不可用/未跑) → 不自动发 (交人), 保守优先.
- 本模块不 import llm, 不发送, 不写 outbox. 纯分类.
"""
from __future__ import annotations

from . import db


def _norm(s: str) -> str:
    """规范化: 去空格 + 小写. 使话术匹配对空格变体宽容."""
    return "".join((s or "").split()).lower()


def matches_whitelist(conn, draft: str):
    """闸一: 草稿是否命中已批准话术库 (规范化后, 话术库条目作为草稿子串). 零 LLM.

    Returns (命中bool, 命中的条目dict或None).
    空草稿直接返回 (False, None).
    """
    nd = _norm(draft)
    if not nd:
        return False, None
    for p in db.get_safe_phrases(conn):
        np = _norm(p.get("pattern", ""))
        if np and np in nd:
            return True, p
    return False, None


def classify(conn, draft: str, *, person_id=None, purpose: str = "reply",
             llm_verdict=None) -> dict:
    """分类草稿的自动发安全等级.

    参数:
        conn          — db 连接.
        draft         — 待发草稿文本.
        person_id     — 可选, 供上层日志/审计用 (本模块不用).
        purpose       — 可选标注 (reply/opener 等), 供上层审计用.
        llm_verdict   — 调用方传入的 LLM 风险判定: 'low' | 'high' | None.
                        None = LLM 未跑或不可用 (→ 保守, 交人).

    Returns dict:
        tier        : 'auto' | 'needs_llm' | 'human'
        allow_auto  : bool  (只有 auto 时为 True)
        gate1       : bool  (话术库命中)
        gate2       : bool | None  (LLM 判低=True, 判高=False, 无判定=None)
        matched     : str | None  (命中的话术库 pattern)
        reasons     : list[str]
    """
    g1, matched = matches_whitelist(conn, draft)
    # 闸二: None 表示无 LLM 判定(未跑/挂了); 只有 'low' 才算通过
    g2: bool | None = None if llm_verdict is None else (llm_verdict == "low")

    allow_auto = bool(g1 and g2 is True)

    if allow_auto:
        tier, reasons = "auto", ["命中话术库", "LLM 风险低"]
    elif g1 and g2 is None:
        tier = "needs_llm"
        reasons = ["命中话术库,但无 LLM 风险判定 → 交人(保守)"]
    elif not g1:
        tier = "human"
        reasons = ["未命中话术库 → 交人(出安全区)"]
    else:
        # g1 and g2 is False  (LLM 判高风险)
        tier = "human"
        reasons = ["LLM 判风险高 → 交人"]

    return {
        "tier": tier,
        "allow_auto": allow_auto,
        "gate1": g1,
        "gate2": g2,
        "matched": (matched["pattern"] if matched else None),
        "reasons": reasons,
    }
