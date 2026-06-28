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

from . import db, gate, llm

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


_RISK_SYSTEM = (
    "你是自动回复的安全闸 (SAFETY GATE)。系统准备把一条预设的「确认/寒暄」回复**自动发出**, "
    "由你判定这次自动发送的风险。只输出一个单词: `low` 或 `high`, 不要解释。\n"
    "- 仅当把这条回复自动发给该入站消息是**明显无害且得体**时答 `low`: "
    "纯寒暄/确认收到, 不含任何承诺、金额/付款、情绪/抱怨/投诉、敏感或私密话题。\n"
    "- 入站消息若是抱怨、情绪化、催款、涉钱、纠纷、敏感, 或你有任何疑虑 → 答 `high`。\n"
    "拿不准就答 `high` (保守优先, 把判断交回人)。"
)


def _llm_verdict(conn, draft, *, inbound=None):
    """闸二: LLM 在上下文中判定「这条预设回复能否自动发」→ 'low' | 'high' | None.

    inbound = 我们将要自动回复的那条最新入站消息文本, 让模型**结合上下文**判风险
    (同一句确认, 若入站是投诉/情绪/涉钱, 仍是高风险)。

    返回语义 (保守优先, 见 §6 / HITL 铁律):
        'low'  — 模型明确判低风险, 闸二放行 → 可进入 arm。
        'high' — 模型判高风险或任何非空的模糊回答 → 交人。
        None   — LLM 不可用 / 调用失败 / 空回答 / 抛异常 →「没有 assist」, 等同交人。
                 失败 ≠ 'high': 调用本身失败时不替模型拍 high, 而是回到人 (同不可用)。
    """
    if not llm.available():
        return None
    messages = [
        {"role": "system", "content": _RISK_SYSTEM},
        {"role": "user", "content":
            "【预设回复(将自动发出)】\n" + (draft or "") +
            "\n\n【入站消息(我们要回复的对象)】\n" + (inbound or "(无)") +
            "\n\n请只回答 low 或 high。"},
    ]
    try:
        res = llm.complete(messages, task="risk_classify", conn=conn)
    except Exception:
        return None   # provider 抛异常 → 没 assist → 交人
    if not res.ok or not (res.text or "").strip():
        return None   # 调用失败 / 空回答 → 交人 (不替模型拍 high)
    t = res.text.strip().lower()
    # 明确判低才放行: 文本等于/以 "low" 开头, 或含 "低" 且不含 "高"/"high"
    if t == "low" or t.startswith("low") or ("低" in t and "高" not in t and "high" not in t):
        return "low"
    return "high"   # 其余任何非空回答 → 高风险 → 交人


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

        inbound = recent[-1].get("content")   # 最新入站文本 (供闸二上下文判风险)
        verdict = gate.classify(
            conn, draft, llm_verdict=_llm_verdict(conn, draft, inbound=inbound))
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
