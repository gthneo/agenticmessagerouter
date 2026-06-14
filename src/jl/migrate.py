"""Migrate the v0.4 persons.json into the SQLite source of truth.

Idempotent: re-running upserts persons and channels in place (no duplicates).
The legacy JSON stays as a human-editable seed; SQLite is now authoritative.
"""
from __future__ import annotations

import json
import os

from . import db

DEFAULT_JSON = os.path.expanduser("~/.config/jl/persons.json")


def migrate_persons_json(conn, json_path: str = DEFAULT_JSON) -> int:
    with open(json_path, encoding="utf-8") as f:
        cfg = json.load(f)
    persons = cfg.get("persons", [])
    for p in persons:
        db.upsert_person(
            conn,
            id=p["id"],
            name=p["name"],
            category=p.get("category", ""),
            threshold_days=p.get("threshold_days", 7),
            aliases=p.get("aliases", []),
        )
        _migrate_wechat(conn, p)
        _migrate_phones(conn, p)
    db.log_event(conn, kind="migration", actor="migrate",
                 detail={"source": json_path, "count": len(persons)})
    return len(persons)


def _migrate_wechat(conn, p):
    wx = p.get("wechat") or {}
    wxid = wx.get("wxid")
    chat_name = wx.get("chat_name", "")
    # identifier prefers the stable wxid; falls back to chat_name when absent.
    identifier = wxid or chat_name
    if not identifier:
        return
    db.upsert_channel(conn, person_id=p["id"], kind="wechat",
                      identifier=identifier, label=chat_name,
                      meta={"wxid": wxid, "chat_name": chat_name})


def _migrate_phones(conn, p):
    for phone in p.get("phone") or []:
        if not phone:
            continue
        db.upsert_channel(conn, person_id=p["id"], kind="phone",
                          identifier=phone, label="")
