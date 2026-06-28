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
GROUP_SMALL_THRESHOLD = 10   # 群规模分挡: < 此值 = 小群(主动); >= = 大群(@我才回)。
                             # 可被 app_settings['group_small_threshold'] 覆盖。


# ---------------------------------------------------------------------------
# Internal helpers (all read-only)
# ---------------------------------------------------------------------------

def _recent(conn, cid, n=6):
    """取最近 n 条消息, 时间正序返回."""
    rows = conn.execute(
        "SELECT direction, content, ts, sender, is_mentioned FROM messages "
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


# ---------------------------------------------------------------------------
# 群组规模分挡闸 (SIZE-TIERED group auto-engagement gate)
# ---------------------------------------------------------------------------
# 私聊 → 不变。小群(<阈值) 或 自己是群主 → 主动(不需 @我)。大群/未知 → @我才自动回。
#
# 数据保真度 (2026-06-28 现状, 见群组元数据契约 v1):
#   - is_mentioned: 全链路已落地 (schema/ingest/fullwechat/db)，真值可用。
#   - member_count / owner_id: fullwechat 尚未吐 → 无群 meta 表。规模用「会话内
#     DISTINCT sender_id 计数」近似 (会**低估**: 没发言的成员看不到)。群主检测暂为
#     no-op。待 group.canonical/1 落地后, group_size_estimate / _is_owner 可换成读
#     权威 member_count / owner_id, 上层逻辑不变。

def group_small_threshold(conn) -> int:
    """小群阈值: app_settings['group_small_threshold'] 优先, 否则默认常量。
    (镜像 WORK_HOURS/DAILY_CAP 的常量定义方式 + app_settings 可覆盖。)"""
    raw = db.get_setting(conn, "group_small_threshold", "")
    if raw:
        try:
            v = int(raw)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return GROUP_SMALL_THRESHOLD


def group_size_estimate(conn, conversation_id) -> int:
    """群成员数的**近似** = 该会话里见过的 DISTINCT 非空 sender_id 数。

    保真度: 这是下界 (没发言的成员看不到 → 低估)。真权威值要等 fullwechat 按
    群组元数据契约吐 member_count；届时此函数改读真值即可, 调用方不变。
    """
    row = conn.execute(
        "SELECT COUNT(DISTINCT sender_id) FROM messages "
        "WHERE conversation_id=? AND sender_id!=''",
        (conversation_id,)).fetchone()
    return int(row[0] or 0)


def _is_group(conn, conversation_id) -> bool:
    cv = db.get_conversation(conn, conversation_id)
    return bool(cv) and cv.get("type") == "group"


def _self_is_owner(conn, conversation_id) -> bool:
    """自己是否群主。**当前为 no-op (恒 False)**: fullwechat 尚未吐 owner_id, 无群
    meta 表。待群组元数据契约落地 (owner_id ∈ SELF wxid) 后在此实现, 不阻塞。"""
    return False


def is_small_group(conn, conversation_id, *, threshold=None) -> bool:
    """True 当: 会话是群 且 (估算成员 < 阈值 OR 自己是群主)。

    私聊 → False (本闸不适用于私聊, 私聊走原路径)。
    阈值缺省 → group_small_threshold(conn) (app_settings 可覆盖)。
    估算成员见 group_size_estimate (近似, 低估)。保守: 估算 >= 阈值 = 大群。
    """
    if not _is_group(conn, conversation_id):
        return False
    if _self_is_owner(conn, conversation_id):
        return True
    if threshold is None:
        threshold = group_small_threshold(conn)
    return group_size_estimate(conn, conversation_id) < threshold


def _group_reply_gate(conn, conversation_id, recent):
    """群自动回闸判定 → (proceed: bool, reason: str|None)。

    - 小群 / 群主 → 主动: (True, None) → 上层照常走双闸/挡位。
    - 大群 / 未知规模 → @我才回:
        最后入站 is_mentioned 真 → (True, None);
        否则 → (False, "大群非@我·交人") → 上层交人 (action='human', 可见于观察)。

    is_mentioned 数据现已可用 (全链路落地)；未来若某渠道暂不吐, 缺省存 0 → 大群
    保守落 human, 待 is_mentioned 到位后自动放行, 无需改本逻辑。
    """
    if is_small_group(conn, conversation_id):
        return True, None
    last = recent[-1] if recent else {}
    if last.get("is_mentioned"):
        return True, None
    return False, "大群非@我·交人"


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

        # 群组规模分挡闸 (私聊不受影响): 大群非@我 → 交人 (可见于观察, 不静默丢弃)。
        if cv.get("type") == "group":
            proceed, greason = _group_reply_gate(conn, cv["id"], recent)
            if not proceed:
                out.append({
                    "conversation_id": cv["id"],
                    "mode": mode,
                    "draft": None,
                    "verdict": None,
                    "in_window": _in_window(now),
                    "under_rate": _under_rate(conn, cv["id"], now),
                    "action": "human",
                    "reason": greason,
                })
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
