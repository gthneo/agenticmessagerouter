"""Phone channel adapter — local CallHistory DB + AddressBook reverse lookup.

`norm_phone` / `tail_match` are pure and unit-tested; `last()` and
`resolve_contact()` read local macOS SQLite stores at runtime.
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import time

from .. import ingest

ABROOT = os.path.expanduser("~/Library/Application Support/AddressBook")
CALLDB = os.path.expanduser(
    "~/Library/Application Support/CallHistoryDB/CallHistory.storedata")
APPLE_OFFSET = 978307200  # Apple epoch (2001-01-01) -> unix


def norm_phone(s):
    return re.sub(r"\D", "", s or "")


def canon_phone(s):
    """Canonical comparable id: digits only, mainland country code 86 and the leading
    domestic-trunk 0 stripped, so '+86 136…' / '8613…' / '013…' / '136…' all collapse
    to one id. Non-CN numbers are left intact. One person can still hold several of
    these (distinct numbers stay distinct); only format-variants of the SAME number merge."""
    d = norm_phone(s)
    if len(d) > 11 and d.startswith("86"):
        d = d[2:]
    if len(d) == 12 and d.startswith("0"):
        d = d[1:]
    return d


# Minimum trailing digits that must coincide before two numbers are deemed the
# same contact — blocks short-fragment suffix collisions while staying tolerant
# of country-code prefixes (a CN mobile shares 11 digits).
_MIN_TAIL = 7


def tail_match(a, b):
    """True if the two normalized numbers share a suffix (country-code tolerant)."""
    na, nb = norm_phone(a), norm_phone(b)
    if not na or not nb:
        return False
    if not (na.endswith(nb) or nb.endswith(na)):
        return False
    # the shorter number must itself be long enough to be a real number,
    # otherwise a 6-digit fragment would falsely match a full number it ends.
    return min(len(na), len(nb)) >= _MIN_TAIL


def last(channel):
    """Return (ts, summary) of the most recent call for a phone channel row."""
    target = norm_phone(channel.get("identifier"))
    if not target:
        return (0, "")
    last4 = target[-4:] if len(target) >= 4 else target
    best = (0, "")
    try:
        conn = sqlite3.connect(f"file:{CALLDB}?mode=ro", uri=True, timeout=3)
        for zdate, zaddr, zdur, zorig in conn.execute(
            """SELECT ZDATE, ZADDRESS, ZDURATION, ZORIGINATED FROM ZCALLRECORD
               WHERE ZADDRESS LIKE ? ORDER BY ZDATE DESC LIMIT 5""",
            (f"%{last4}",),
        ):
            if not tail_match(str(zaddr), target):
                continue
            unix = int(zdate) + APPLE_OFFSET
            if unix > best[0]:
                dur = f"{int(zdur)}s" if zdur and zdur >= 1 else "miss"
                dir_ = "out→" if zorig else "→in"
                tstr = time.strftime("%m-%d %H:%M", time.localtime(unix))
                best = (unix, f"{tstr} {dir_} {zaddr} {dur}")
        conn.close()
    except Exception:
        pass
    return best


def resolve_contact(phone):
    """Reverse-lookup a phone number to contact name(s) in AddressBook."""
    target = norm_phone(phone)
    if not target or len(target) < 4:
        return ""
    last4 = target[-4:]
    hits = set()
    pattern = os.path.join(ABROOT, "Sources", "*", "AddressBook-v22.abcddb")
    for dbf in glob.glob(pattern):
        try:
            conn = sqlite3.connect(f"file:{dbf}?mode=ro", uri=True, timeout=3)
            for fn, nk, org, raw in conn.execute(
                """SELECT COALESCE(r.ZFIRSTNAME,'')||COALESCE(r.ZLASTNAME,'') AS fn,
                          COALESCE(r.ZNICKNAME,''), COALESCE(r.ZORGANIZATION,''),
                          p.ZFULLNUMBER
                   FROM ZABCDPHONENUMBER p
                   LEFT JOIN ZABCDRECORD r ON p.ZOWNER=r.Z_PK
                   WHERE p.ZLASTFOURDIGITS = ?""",
                (last4,),
            ):
                if tail_match(raw, target):
                    parts = [x for x in (fn, nk, org) if x]
                    if parts:
                        hits.add("|".join(parts))
            conn.close()
        except Exception:
            pass
    return "/".join(sorted(hits)) if hits else ""


def map_call(row):
    """One CallHistory row -> MsgRecord."""
    ts = int(row["ZDATE"]) + APPLE_OFFSET
    out = bool(row.get("ZORIGINATED"))
    dur = int(row.get("ZDURATION") or 0)
    if dur >= 1:
        body = f"[通话] {dur}s {'拨出' if out else '接听'}"
    else:
        body = "[通话] 未接通" if out else "[通话] 未接"
    return ingest.MsgRecord(
        msg_key=f"phone:{row['Z_PK']}",
        ts=ts,
        content=body,
        sender="me" if out else (row.get("ZADDRESS") or ""),
        sender_id=row.get("ZADDRESS") or "",
        direction="out" if out else "in",
        type="call",
        raw={k: row.get(k) for k in ("Z_PK", "ZADDRESS", "ZDATE", "ZDURATION", "ZORIGINATED")},
    )


def conversations_from_calls(rows, name_resolver=resolve_contact):
    """Group call rows by number into [(ConvRecord, [MsgRecord])]."""
    groups = {}
    for r in rows:
        num = r.get("ZADDRESS") or ""
        groups.setdefault(canon_phone(num) or num, []).append((num, r))
    out = []
    for cid, grp in groups.items():
        msgs = [map_call(r) for _, r in grp]
        last_ts = max((m.ts for m in msgs), default=None)
        raw_num = grp[0][0]  # resolve the contact name from an original (formatted) number
        conv = ingest.ConvRecord(chat_id=cid, name=name_resolver(raw_num) or "",
                                 type="private", last_activity_at=last_ts)
        out.append((conv, msgs))
    return out


class PhoneAdapter(ingest.IngestAdapter):
    platform = "phone"
    tool = "callhistory"
    can_send = False

    def _rows(self, limit):
        conn = sqlite3.connect(f"file:{CALLDB}?mode=ro", uri=True, timeout=3)
        try:
            cur = conn.execute(
                """SELECT Z_PK, ZADDRESS, ZDATE, ZDURATION, ZORIGINATED
                   FROM ZCALLRECORD ORDER BY ZDATE DESC LIMIT ?""", (limit,))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()

    def list_conversations(self, account, **kw):
        return [c for c, _ in conversations_from_calls(self._rows(2000))]

    def backfill(self, account, conv, cursor):
        return [], ""

    def pull_new(self, account, recent_limit=500):
        return conversations_from_calls(self._rows(recent_limit))
