"""今日简报聚合(L0 落地页)。纯逻辑、零 LLM、只读现有 db 数据。

每份报告 = {counts, items|nudge|..., narrative, pending_backend}。
narrative 留空字符串——LLM 可后续填(assist),无 LLM 也出数字/清单。
尚无后端的维度(营销线索)标 pending_backend=True,不假数据。
"""
from __future__ import annotations

from . import db, weighting, assist, lifecycle


def _sales(conn):
    matters = db.get_matters(conn)
    by = {}
    for m in matters:
        by[m["status"]] = by.get(m["status"], 0) + 1
    return {"counts": {"total": len(matters), "by_status": by},
            "items": matters[:8], "narrative": "", "pending_backend": False}


def _relationship(conn):
    red = amber = green = 0
    nudge = []
    for p in db.get_persons(conn):
        days = assist._person_days(conn, p["id"])
        c = weighting.color(days, p.get("threshold_days"))
        if c == "🔴":
            red += 1
        elif c == "🟡":
            amber += 1
        else:
            green += 1
        if p.get("watch") or c == "🔴":
            nudge.append({"person_id": p["id"], "name": p["name"],
                          "days": round(days, 1) if days is not None else None, "color": c})
    return {"counts": {"red": red, "amber": amber, "green": green},
            "nudge": nudge[:12], "narrative": "", "pending_backend": False}


def _progress(conn):
    import time
    matters = db.get_matters(conn)
    open_m = [m for m in matters if m["status"] not in ("完结", "丢弃")]
    proposals = lifecycle.propose(conn, now=time.time())
    return {"counts": {"open": len(open_m), "proposals": len(proposals)},
            "items": open_m[:8], "narrative": "", "pending_backend": False}


def _meta(conn):
    try:
        sent = len(db.get_outbox(conn, status="sent"))
    except Exception:
        sent = 0
    pending = len(db.get_outbox(conn, status="pending"))
    return {"counts": {"sent": sent, "pending": pending},
            "narrative": "", "pending_backend": False}


def _marketing(conn):
    # 营销/线索后端(大群浮线索)尚未实现 → 显式占位,不假数据。
    return {"counts": {}, "items": [], "narrative": "",
            "pending_backend": True, "note": "营销线索后端待实现(设计 §1.5)"}


def build(conn):
    """组装今日简报。gate = 需你拍板清单(从各报告浮显著项,带可执行 action 标记)。"""
    reports = {"sales": _sales(conn), "marketing": _marketing(conn),
               "relationship": _relationship(conn), "progress": _progress(conn),
               "meta": _meta(conn)}
    gate = []
    for row in db.get_outbox(conn, status="pending"):
        gate.append({"kind": "send_draft", "actionable": True,
                     "outbox_id": row["id"],
                     "text": f"待发给会话 {row['conversation_id']}：{row['body'][:40]}"})
    for n in reports["relationship"]["nudge"][:5]:
        gate.append({"kind": "nudge", "actionable": True,
                     "person_id": n["person_id"],
                     "text": f"{n['name']} 已 {n['days']} 天未联系，建议主动撩"})
    return {"reports": reports, "gate": gate}
