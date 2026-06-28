"""监管下自动回复编排器 (Phase 1) — **propose-only, 绝不发送**.

只算「该不该/能不能自动回」的候选；真发由 outbox/confirm + 人 countdown 否决窗 (Task 4),
不在此。

设计 §7.5: 默认观察、人拨挡、双闸+时间窗+人本限频+killswitch、Agent 提议人决策。

铁律(非协商):
- 本模块绝不写 outbox、绝不调任何 send 路径、绝不改数据库状态。
- 只读 + 返回候选列表。测试 test_propose_never_sends 保障这一点。
- LLM 不可用/失败 → verdict=None → 双闸不过 → action=human。保守优先。
"""
from __future__ import annotations

import time

from . import db, gate

WORK_HOURS = (9, 21)   # 默认时间窗 (得体发送区间, 本地时间)；后续可每账户配
DAILY_CAP = 8          # 人本限频: 每会话每天自动回上限 (小, 模仿真人节奏)


# ---------------------------------------------------------------------------
# Internal helpers (all read-only)
# ---------------------------------------------------------------------------

def _recent(conn, cid, n=6):
    """取最近 n 条消息, 时间正序返回."""
    rows = conn.execute(
        "SELECT direction, content, ts, sender FROM messages "
        "WHERE conversation_id=? ORDER BY ts DESC LIMIT ?",
        (cid, n),
    ).fetchall()
    return [dict(r) for r in rows][::-1]   # reverse → 时间正序


def _needs_reply(recent):
    """保守: 最后一条是对方发的 (direction='in') 且其后我没回过 → 该回.
    空消息列表 → False."""
    if not recent:
        return False
    return recent[-1].get("direction") == "in"


def _draft_ack(conn, recent):
    """拟一条安全区寒暄/确认话术草稿.

    保守策略:
    - 入站消息长 (>40 字) → None (正文, 要思考, 交人)
    - 包含疑问词 → None (提问, 需答复, 交人)
    - 否则从话术库取第一条 确认/寒暄 类 pattern → 返回

    返回 None 时调用方将 action='human'.
    """
    if not recent:
        return None
    last = recent[-1].get("content", "")
    if not last:
        return None
    # 长内容 / 明确提问 → 交人
    if len(last) > 40:
        return None
    _QUESTION_SIGNALS = ("?", "？", "为什么", "怎么", "多少", "行吗", "可以吗",
                         "如何", "怎样", "什么时候", "谁", "哪", "能否", "是否")
    if any(q in last for q in _QUESTION_SIGNALS):
        return None
    # 从话术库取安全确认/寒暄
    acks = [p["pattern"] for p in db.get_safe_phrases(conn)
            if p.get("kind") in ("确认", "寒暄")]
    return acks[0] if acks else None


def _in_window(now):
    """True if `now` (unix seconds) falls inside WORK_HOURS on the local clock."""
    h = time.localtime(now).tm_hour
    return WORK_HOURS[0] <= h < WORK_HOURS[1]


def _under_rate(conn, cid, now):
    """每会话每天 < DAILY_CAP 条自动回 (人本节流).

    v1 占位: 返回 True (保守: 宁可放过多于节流).
    TODO v2: 统计当天 outbox 行中 created_by='auto' 且 conversation_id=cid 的数量,
             若 >= DAILY_CAP 则返回 False (阻止), 限保护真人节奏.
    钩子在此 — 实现者替换 `return True` 行.
    """
    # v1 placeholder — see TODO above
    return True   # noqa: SIM110


def _llm_verdict(conn, draft):
    """获取 LLM 风险判定: 'low' | 'high' | None.

    v1 保守实现: 直接返回 None (等同 LLM 不可用).
    效果: 双闸闸二不过 → action 不会是 'arm' (→ needs_llm → human).
    这是 Phase 1 的刻意选择: Phase 2 在此接入真实 LLM 风险打分.

    如果将来接入 LLM:
        from . import llm
        if not llm.available():
            return None
        try:
            res = llm.complete([...], task="risk_classify")
            if res.ok:
                return "low" if "低" in res.text or "low" in res.text.lower() else "high"
        except Exception:
            pass
        return None
    """
    return None   # Phase 1: conservative — no LLM risk check, everything goes to human


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def propose_replies(conn, now):
    """对 observe/supervised 会话产出自动回候选 (**不发**).

    killswitch 开 → 空列表.

    每条候选 dict:
        conversation_id : int
        mode            : 'observe' | 'supervised'
        draft           : str | None
        verdict         : gate.classify 结果 dict | None
        in_window       : bool  (时间窗判定)
        under_rate      : bool  (日频上限判定)
        action          : 'shadow' | 'arm' | 'human'
        reason          : str   (可选, action='human' 时说明原因)

    action 语义:
        shadow — observe 模式: 候选仅供展示/日志, 不走 countdown, 不发.
        arm    — supervised 模式: 双闸全过 + 时间窗 + 限频 → 可交 Task 4 countdown.
        human  — 任何条件不满足 → 交人处理.
    """
    if db.killswitch_on(conn):
        return []

    out = []
    for cv in db.get_conversations(conn):
        mode = db.get_autonomy(conn, cv["id"])
        if mode not in ("observe", "supervised"):
            continue

        recent = _recent(conn, cv["id"])
        if not _needs_reply(recent):
            continue

        draft = _draft_ack(conn, recent)
        if not draft:
            out.append({
                "conversation_id": cv["id"],
                "mode": mode,
                "draft": None,
                "verdict": None,
                "in_window": _in_window(now),
                "under_rate": _under_rate(conn, cv["id"], now),
                "action": "human",
                "reason": "无合适安全话术",
            })
            continue

        verdict = gate.classify(conn, draft, llm_verdict=_llm_verdict(conn, draft))
        okw = _in_window(now)
        okr = _under_rate(conn, cv["id"], now)

        if mode == "observe":
            action = "shadow"
        elif verdict["allow_auto"] and okw and okr:
            action = "arm"
        else:
            action = "human"

        entry = {
            "conversation_id": cv["id"],
            "mode": mode,
            "draft": draft,
            "verdict": verdict,
            "in_window": okw,
            "under_rate": okr,
            "action": action,
        }
        out.append(entry)

    return out
