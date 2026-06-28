"""Per-account onboarding — productized surface behind `jl account add|set|ls`.

Onboarding a new/changed WeChat backend = upsert one accounts row + place one
token file at a standardized cred path. This module holds the *pure* pieces
(cred-path convention, dry-run plan builder) plus the side-effecting token
writer (copy file->file + chmod 600) and plan applier, so the CLI handler stays
thin and the HITL dry-run→commit gate is testable.

NEVER print or log the token contents — write_token only moves bytes.
"""
from __future__ import annotations

import json
import os
import urllib.request

from . import db

CRED_DIR = os.path.expanduser("~/.config/jl/cred")

# default config-driven registry of fullwechat backends (real copy, NOT in repo —
# holds real wxid/IP/token paths). Repo ships only ops/fullwechat-backends.example.json.
DEFAULT_REGISTRY = os.path.expanduser("~/.config/jl/fullwechat-backends.json")

# the per-account account row fields a plan can carry (besides account_id)
_FIELDS = ("platform", "tool", "host", "self_id", "label", "cred_ref")


def cred_path_for(tool: str, account_id) -> str:
    """Standardized cred path for one account's token:
    ``~/.config/jl/cred/<tool>_<account_id>.token``. Pure — does not touch disk."""
    return os.path.join(CRED_DIR, f"{tool}_{account_id}.token")


def write_token(src: str, dest: str) -> str:
    """Copy the token at ``src`` to ``dest`` (creating ``dest``'s dir), chmod 600.
    Bytes move file->file; contents are never returned or printed. Returns dest."""
    src = os.path.expanduser(src)
    dest = os.path.expanduser(dest)
    if not os.path.exists(src):
        raise FileNotFoundError(f"token-file not found: {src}")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(src, "rb") as f:
        data = f.read()
    # create with owner-only perms from the start (don't briefly expose the token)
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.chmod(dest, 0o600)   # in case the file pre-existed with looser perms
    return dest


def build_plan(conn, *, op, account_id=None, flags=None):
    """Pure-ish (reads DB only): compute the before→after of an add/set, including
    the allocated account_id and the cred path the token would land at. Drives the
    dry-run summary; nothing is written here.

    op="add": allocate next account_id, before=None.
    op="set": account_id required & must exist; overwrite only the given flags.
    op="onboard": account_id required at a FIXED slot; before=existing row if any,
      else None (config-driven onboarding targets a chosen slot that may be new).
      Unlike "set" it does NOT require the row to pre-exist and writes nothing here.
    A token copy is planned iff --token-file was given (key "token_file" in flags).
    """
    flags = dict(flags or {})
    token_file = flags.pop("token_file", None)

    if op == "add":
        account_id = db.next_account_id(conn)
        before = None
        after = {k: "" for k in _FIELDS}
    elif op in ("set", "onboard"):
        existing = {a["account_id"]: a for a in db.get_accounts(conn)}
        if account_id not in existing:
            if op == "set":
                raise ValueError(f"account {account_id} not found — use `account add`")
            before = None                                # onboard a brand-new slot
            after = {k: "" for k in _FIELDS}
        else:
            before = existing[account_id]
            after = {k: before.get(k, "") for k in _FIELDS}
    else:
        raise ValueError(f"unknown op: {op}")

    for k in _FIELDS:
        if k in flags and flags[k] is not None:
            after[k] = flags[k]

    copy_token = token_file is not None
    cred_dest = None
    if copy_token:
        cred_dest = cred_path_for(after["tool"], account_id)
        after["cred_ref"] = cred_dest

    return {
        "op": op,
        "account_id": account_id,
        "before": dict(before) if before is not None else None,
        "after": after,
        "copy_token": copy_token,
        "token_file": token_file,
        "cred_dest": cred_dest,
    }


def apply_plan(conn, plan):
    """Commit a plan: copy the token (if any) then upsert the accounts row.
    Token is copied first so a write failure aborts before the row points at a
    missing cred file."""
    if plan.get("copy_token"):
        write_token(plan["token_file"], plan["cred_dest"])
    a = plan["after"]
    db.upsert_account(
        conn,
        account_id=plan["account_id"],
        platform=a["platform"],
        tool=a["tool"],
        host=a["host"],
        self_id=a["self_id"],
        label=a["label"],
        cred_ref=a["cred_ref"],
    )
    return plan["account_id"]


# ----- config-driven onboarding (registry + identity preflight) -------------
#
# Adding a customer/account should be a config edit (one registry entry), not a
# hand-typed `jl account set`. We read the registry, and for each enabled entry
# run an identity PREFLIGHT before building a plan: ask the backend who it is
# logged in as and refuse to bind if that disagrees with the configured self_id.
# This is the AMR-side second gate against the wrong-account-binding bug (a GUI
# showing account A while the REST API still reports account B → blind onboarding
# would pollute the identity graph).


def load_registry(path):
    """Parse the backend registry JSON at ``path`` → list of entry dicts. Accepts
    either ``{"backends": [...]}`` or a bare top-level list. Missing file raises
    FileNotFoundError with the path (a clear, actionable error)."""
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        raise FileNotFoundError(f"registry not found: {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    backends = data.get("backends", []) if isinstance(data, dict) else data
    return list(backends)


def _get_json(host, path, token, timeout=10):
    """GET ``{host}{path}`` with a bearer token, parse JSON. The HTTP seam tests
    monkeypatch / pass a fake ``fetch`` for — no real network in tests. May raise
    (URLError/OSError/JSON errors); callers wrap it so one bad backend can't crash
    the run."""
    req = urllib.request.Request(host.rstrip("/") + path,
                                 headers={"Authorization": "Bearer " + (token or "")})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _read_token_file(token_file):
    """Read a bearer token from a path on the AMR host. Returns the stripped token
    or None if the path is empty/missing (never raises — preflight degrades)."""
    if not token_file:
        return None
    p = os.path.expanduser(token_file)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read().strip() or None


def _base_wxid(logged_in, self_id):
    """Best-effort device-suffix-stripped base of ``logged_in`` for the identity
    comparison. Reuses db's wxid canonicalizer (strips a PowerData/multi-open
    ``_xxxx`` device suffix); when ``logged_in`` is exactly ``self_id`` plus a
    device suffix, the base IS ``self_id`` (covers multi-underscore bases the
    conservative graph canonicalizer leaves alone)."""
    if self_id and logged_in.startswith(self_id + "_"):
        return self_id
    return db._canon_identifier("wechat", logged_in)


def preflight_identity(entry, *, fetch=_get_json, timeout=10):
    """The safety gate. Ask the backend who it's logged in as and compare to the
    configured ``self_id``. Returns a dict; NEVER raises (one bad backend must not
    abort the run):
      ok=True                          → identity matches, safe to onboard
      ok=False reason=no_token         → token_file empty/missing
      ok=False reason=unreachable      → fetch raised (host down / refused)
      ok=False reason=no_user          → response lacked loggedInUser
      ok=False reason=mismatch         → backend reports a DIFFERENT account
    Carries logged_in / base / self_id for the warning and the audit trail.
    """
    self_id = (entry.get("self_id") or "").strip()
    token = _read_token_file(entry.get("token_file"))
    if token is None:
        return {"ok": False, "reason": "no_token", "self_id": self_id,
                "logged_in": None, "base": None}
    try:
        resp = fetch(entry["host"], "/api/status/auth", token, timeout=timeout)
    except Exception as e:                       # noqa: BLE001 — graceful by design
        return {"ok": False, "reason": "unreachable", "self_id": self_id,
                "logged_in": None, "base": None, "error": str(e)}
    logged_in = (resp or {}).get("loggedInUser")
    if not logged_in:
        return {"ok": False, "reason": "no_user", "self_id": self_id,
                "logged_in": None, "base": None}
    base = _base_wxid(logged_in, self_id)
    ok = bool(self_id) and (
        logged_in == self_id or base == self_id or logged_in.startswith(self_id + "_"))
    return {"ok": ok, "reason": None if ok else "mismatch", "self_id": self_id,
            "logged_in": logged_in, "base": base}


def probe_capabilities(entry, *, fetch=_get_json, timeout=10):
    """Best-effort GET /api/capabilities → a one-line human summary + the raw dict.
    Never fails the onboarding if the backend lacks the endpoint; returns
    summary=None when unavailable."""
    token = _read_token_file(entry.get("token_file"))
    try:
        caps = fetch(entry["host"], "/api/capabilities", token, timeout=timeout)
    except Exception:                            # noqa: BLE001 — capabilities are optional
        return {"summary": None, "caps": None}
    if not isinstance(caps, dict):
        return {"summary": None, "caps": caps}
    parts = []
    if caps.get("schema"):
        parts.append(f"schema={caps['schema']}")
    grp = caps.get("group") or {}
    if grp.get("meta") or grp.get("roster"):
        parts.append("group(" + "/".join(
            k for k in ("meta", "roster") if grp.get(k)) + ")")
    send = caps.get("send") or {}
    if send.get("auto_open"):
        parts.append("send.auto_open")
    kinds = caps.get("kinds")
    if isinstance(kinds, (list, tuple)):
        parts.append(f"kinds={len(kinds)}")
    return {"summary": ", ".join(parts) if parts else "(无明细)", "caps": caps}


def probe_backend_versions(host, token, *, fetch=_get_json, timeout=5):
    """Live-probe a fullwechat backend for BOTH version axes — the PROVIDER half of the
    two-sided version handshake (mirror of AMR's own /api/version):
      * software version  ← GET /api/status      `.version`
      * contract schema    ← GET /api/capabilities `.schema`
    Never raises (a down backend must not crash `jl account ls`): unreachable / missing
    fields degrade to a sentinel string. Returns {"version": str, "schema": str}."""
    def _one(path, key):
        try:
            resp = fetch(host, path, token, timeout=timeout)
        except Exception:                        # noqa: BLE001 — graceful by design
            return "unreachable"
        if not isinstance(resp, dict):
            return "?"
        v = resp.get(key)
        return str(v) if v else "?"
    return {"version": _one("/api/status", "version"),
            "schema": _one("/api/capabilities", "schema")}


def onboard_entry(conn, entry, *, fetch=_get_json):
    """Process ONE registry entry up to (but not including) the write: run the
    identity preflight, and only on a match build the dry-run plan (op='set' onto
    the entry's amr_account_slot, copying the backend's token_file). Returns a dict
    with preflight, capabilities, and plan (plan=None when preflight fails — we
    NEVER build a plan for a mismatched/unreachable backend)."""
    pre = preflight_identity(entry, fetch=fetch)
    caps = probe_capabilities(entry, fetch=fetch) if pre["ok"] else {"summary": None, "caps": None}
    plan = None
    if pre["ok"]:
        flags = {
            "platform": entry.get("platform", "wechat"),
            "tool": entry.get("tool", "fullwechat"),
            "host": entry["host"],
            "self_id": entry["self_id"],
            "label": entry.get("label", ""),
            "token_file": entry["token_file"],
        }
        # op='onboard': writes nothing here; tolerates a brand-new slot (dry-run safe).
        plan = build_plan(conn, op="onboard",
                          account_id=entry["amr_account_slot"], flags=flags)
    return {"preflight": pre, "capabilities": caps, "plan": plan}
