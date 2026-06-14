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

ABROOT = os.path.expanduser("~/Library/Application Support/AddressBook")
CALLDB = os.path.expanduser(
    "~/Library/Application Support/CallHistoryDB/CallHistory.storedata")
APPLE_OFFSET = 978307200  # Apple epoch (2001-01-01) -> unix


def norm_phone(s):
    return re.sub(r"\D", "", s or "")


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
