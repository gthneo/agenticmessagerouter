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
    for conv, msgs in adapter.pull_new(account_for(conn, account_id), recent_limit=recent_limit):
        _, n = db.ingest_records(conn, account_id=account_id, platform=adapter.platform,
                                 conv=conv, msgs=msgs)
        inserted += n
        convs += 1
    db.log_event(conn, kind="ignite", actor=actor,
                 detail={"account_id": account_id, "conversations": convs,
                         "inserted": inserted})
    return inserted
