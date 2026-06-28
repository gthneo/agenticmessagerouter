"""Person-level productized actions — behind `jl person <sub>`.

Today only `refresh-name`. Person display names go stale: ``persons.name`` is
set at seed/link time and does NOT auto-update, but the live conversation name
IS kept fresh by ingest (``db.upsert_conversation`` does ``name=excluded.name``).
This module holds the *pure* diff (name_refresh_plan) plus the side-effecting
applier, so the CLI handler stays thin and the HITL dry-run→commit gate is
testable.

Authoritative new name = the person's primary/linked conversation's roster-fresh
``name`` — the most-recently-active linked conversation (``get_conversations``
returns them ``last_activity_at DESC``, so the first is the freshest one). No
network / no LLM — pure DB read.
"""
from __future__ import annotations

from . import db


def _fresh_name(conn, person_id):
    """The roster-fresh name for a person = its most-recently-active linked
    conversation's ``name``, or None if the person has no linked conversation."""
    convs = db.get_conversations(conn, person_id=person_id)
    return convs[0]["name"] if convs else None


def name_refresh_plan(conn, *, person_id=None):
    """Pure DB read: list the persons whose stored ``name`` differs from their
    primary/linked conversation's roster-fresh ``name``.

    person_id=None: scan ALL persons. Otherwise just that one person id.
    Returns ``[{person_id, old, new}, ...]`` — only where ``old != new`` and the
    fresh name is non-empty (an empty live name is never authoritative)."""
    if person_id is None:
        persons = db.get_persons(conn)
    else:
        p = db.get_person(conn, person_id)
        persons = [p] if p else []
    plan = []
    for p in persons:
        new = _fresh_name(conn, p["id"])
        old = p["name"]
        if new and new != old:
            plan.append({"person_id": p["id"], "old": old, "new": new})
    return plan


def apply_name_refresh(conn, plan):
    """Commit a refresh plan: update each person's ``name`` to its fresh value.
    Returns the list of person ids changed. Caller logs the audit trail."""
    changed = []
    for d in plan:
        conn.execute("UPDATE persons SET name=?, updated_at=? WHERE id=?",
                     (d["new"], db._now(), d["person_id"]))
        changed.append(d["person_id"])
    conn.commit()
    return changed
