"""Orchestration: drive an IngestAdapter to fill the store (ignite / poll)."""
from __future__ import annotations

from . import db


def account_for(conn, account_id):
    for a in db.get_accounts(conn):
        if a["account_id"] == account_id:
            return a
    return {"account_id": account_id}


def ignite(conn, adapter, *, account_id, recent_limit=30, actor="cli"):
    """One-shot recent pull: ingest the recent messages of every active conversation.
    Groups arrive muted (the adapter sets ConvRecord.muted). Returns messages inserted."""
    inserted = 0
    convs = 0
    # OPT-IN runtime contract boundary check: hand the adapter the conn so it validate-
    # and-warns each canonical envelope it fetches (gated by contract_validate_enabled,
    # default on). Adapters without this attr (lark…) are unaffected.
    if hasattr(adapter, "validate_conn") and adapter.validate_conn is None:
        if db.get_setting(conn, "contract_validate_enabled", "1") != "0":
            adapter.validate_conn = conn
    for conv, msgs in adapter.pull_new(account_for(conn, account_id), recent_limit=recent_limit):
        _, n = db.ingest_records(conn, account_id=account_id, platform=adapter.platform,
                                 conv=conv, msgs=msgs)
        inserted += n
        convs += 1
    db.log_event(conn, kind="ignite", actor=actor,
                 detail={"account_id": account_id, "conversations": convs,
                         "inserted": inserted})
    # 契约 §6.4：把后端报「读不到」的会话响亮记下来（一条 read_unavailable 事件/会话），
    # 让它在 /api/health 计数、不被当成「0 互动」静默吞掉（家人雷达漏报的根因）。
    if hasattr(adapter, "drain_unreadable"):
        for u in adapter.drain_unreadable():
            db.log_event(conn, kind="read_unavailable", actor=actor,
                         detail={"account_id": account_id, "chat_id": u.get("chat_id", ""),
                                 "reason": u.get("reason", ""), "coverage": u.get("coverage", {})})
    # scoped auto-draft on freshly ingested inbound + proactive openers for
    # watched/🔴 relationships (both LLM-optional; never block ingest, never send)
    try:
        from . import assist, llm
        if llm.available():
            assist.auto_draft_sweep(conn)
            assist.proactive_sweep(conn)
    except Exception:
        pass
    return inserted
