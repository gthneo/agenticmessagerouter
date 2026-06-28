"""事生命周期引擎 v1 — 确定性、只读的「待推进提议」+ 人确认的 advance。零 LLM、HITL。

propose() 只读出提议(不改库);advance() 是人确认后才调的执行(改 status + 留痕)。
设计 §3:确定性信号(承诺未结/闲置)冒出提议,人批准,系统才动。
"""
from __future__ import annotations

from . import db

IDLE_DAYS_DEFAULT = 7


def propose(conn, now, idle_days=IDLE_DAYS_DEFAULT):
    """只读:扫 open 的事,按确定性信号给「待推进提议」。不改库(HITL — 人确认才 advance)。
    返回 [{matter_id, title, status, signal, reason, suggestion}]。"""
    out = []
    for m in db.get_matters(conn, status="open"):
        opens = [c for c in (m.get("commitments") or []) if c.get("status") == "open"]
        idle = (now - (m.get("updated_at") or now)) / 86400.0
        if opens:
            out.append({
                "matter_id": m["id"],
                "title": m.get("title", ""),
                "status": m["status"],
                "signal": "承诺未结",
                "reason": f"{len(opens)} 条承诺未办",
                "suggestion": "跟进",
            })
        elif idle > idle_days:
            out.append({
                "matter_id": m["id"],
                "title": m.get("title", ""),
                "status": m["status"],
                "signal": "闲置",
                "reason": f"闲置 {int(idle)} 天",
                "suggestion": "推进或办结",
            })
    return out


def advance(conn, matter_id, to_status, actor="user"):
    """人确认后的执行:改 status + 留痕。HITL — 只有显式调用才动(propose 永不自动调它)。"""
    db.set_matter_status(conn, matter_id, to_status)
    db.log_event(conn, kind="lifecycle", actor=actor,
                 detail={"matter_id": matter_id, "to_status": to_status})
    return {"ok": True, "matter_id": matter_id, "to_status": to_status}
