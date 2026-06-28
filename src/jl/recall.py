"""记忆层 recall — 显著性检索(纯 SQL、零 LLM)。

recall() 默认出「此刻该记得的」显著上下文包(每项带 handle 可钻取);
expand() 按 handle 钻全量(只读)。

设计 §4:像人地有分寸地想起,不是 dump。
可插拔显著性策略(v1 确定性;日后接 semantic)。
"""
from __future__ import annotations

from . import db, weighting, assist


def recall(conn, person_id, *, now, channel=None, purpose="reply", budget=8):
    """显著上下文包(只读、零 LLM)。budget=近端消息条数上限。

    purpose 调权(v1 调排序/limit):
      '撩'  → 事/承诺优先;
      'reply'→ 近端消息优先。
    每项带 handle 供 expand 钻取。
    """
    person = next(
        (p for p in db.get_persons(conn) if str(p.get("id")) == str(person_id)),
        None,
    )

    # ---- 近端消息 (时间正序) ---------------------------------------------------
    rows = conn.execute(
        "SELECT m.id, m.ts, m.sender, m.content, m.direction "
        "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
        "WHERE c.person_id=? ORDER BY m.ts DESC LIMIT ?",
        (person_id, budget),
    ).fetchall()
    recent = [dict(r) for r in rows][::-1]  # 时间正序

    # ---- 开放的「事」 ----------------------------------------------------------
    matters = db.get_matters(conn, person_id=person_id, status="open")

    # ---- 开放承诺 (从所有 open 事里展平) ---------------------------------------
    due = [
        c
        for m in matters
        for c in (m.get("commitments") or [])
        if c.get("status") == "open"
    ]

    # ---- 关系温度 --------------------------------------------------------------
    days = assist._person_days(conn, person_id)
    color = weighting.color(days, (person or {}).get("threshold_days"))

    return {
        "person": {
            "id": person_id,
            "name": (person or {}).get("name", ""),
            "handle": {"type": "person", "id": person_id},
        },
        "recent": recent,
        "open_matters": [
            {
                "id": m["id"],
                "title": m.get("title", ""),
                "status": m["status"],
                "handle": {"type": "matter", "id": m["id"]},
            }
            for m in matters
        ],
        "due_commitments": [
            {
                "text": c.get("text", ""),
                "due": c.get("due", ""),
                "matter_id": c.get("matter_id"),
            }
            for c in due
        ],
        "temperature": {
            "days": round(days, 1) if days is not None else None,
            "color": color,
        },
        "purpose": purpose,
    }


def expand(conn, handle, *, limit=200):
    """按 handle 钻全量(只读)。handle={'type','id'}。

    type='person' → 该人全量消息(时间正序)。
    type='matter' → 事详情 + 承诺列表。
    """
    t, i = handle.get("type"), handle.get("id")

    if t == "person":
        rows = conn.execute(
            "SELECT m.* FROM messages m JOIN conversations c ON c.id=m.conversation_id "
            "WHERE c.person_id=? ORDER BY m.ts ASC LIMIT ?",
            (i, limit),
        ).fetchall()
        return {"type": "person", "messages": [dict(r) for r in rows]}

    if t == "matter":
        m = next((x for x in db.get_matters(conn) if x.get("id") == i), None)
        return {
            "type": "matter",
            "matter": m,
            "commitments": db.get_commitments(conn, i),
        }

    return {"type": t, "error": "unknown handle"}
