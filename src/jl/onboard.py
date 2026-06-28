"""Per-account onboarding — productized surface behind `jl account add|set|ls`.

Onboarding a new/changed WeChat backend = upsert one accounts row + place one
token file at a standardized cred path. This module holds the *pure* pieces
(cred-path convention, dry-run plan builder) plus the side-effecting token
writer (copy file->file + chmod 600) and plan applier, so the CLI handler stays
thin and the HITL dry-run→commit gate is testable.

NEVER print or log the token contents — write_token only moves bytes.
"""
from __future__ import annotations

import os

from . import db

CRED_DIR = os.path.expanduser("~/.config/jl/cred")

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
    A token copy is planned iff --token-file was given (key "token_file" in flags).
    """
    flags = dict(flags or {})
    token_file = flags.pop("token_file", None)

    if op == "add":
        account_id = db.next_account_id(conn)
        before = None
        after = {k: "" for k in _FIELDS}
    elif op == "set":
        existing = {a["account_id"]: a for a in db.get_accounts(conn)}
        if account_id not in existing:
            raise ValueError(f"account {account_id} not found — use `account add`")
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
