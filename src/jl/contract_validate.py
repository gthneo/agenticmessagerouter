"""Structural validation of a canonical message envelope (契约 message.canonical/1).

A pure, LLM-free boundary check: given a canonical envelope dict, return a list of
human-readable violation strings ([] = conformant). This is the *structural* half of
AMR's runtime contract teeth (the *semantic* msg_key-collision half lives in
db.insert_messages). It catches drift where a backend emits a non-conformant envelope —
a missing required field, an out-of-enum kind/direction, a malformed msg_id — so the ops
Agent sees contract drift instead of it silently corrupting the timeline.

The closed `kind` enum + required fields mirror message/canonical.md §2.1 + §3. Keep this
in sync with the vendored contract (vendor/contracts/message/canonical.md); the contract
repo is the truth source.

Validate-and-WARN only: callers never drop/break ingest on a violation — they log it.
"""
from __future__ import annotations

# Closed kind enum — message/canonical.md §3 (15 kinds). Drift here = update the contract
# first (PR to agentic-contracts), re-vendor, then mirror the new kind here.
CANONICAL_KINDS = frozenset({
    "text", "image", "voice", "video", "file", "link", "quote", "miniprogram",
    "chat_history", "location", "sticker", "transfer", "red_packet", "system", "unknown",
})

# Top-level fields every envelope must carry (§2.1, 必填 rows).
REQUIRED_FIELDS = ("schema", "channel", "kind", "text", "ts", "direction")

DIRECTIONS = frozenset({"in", "out"})


def check_and_log(conn, raw_msgs, *, channel="", enabled=None) -> int:
    """OPT-IN ingest-boundary check: validate each CANONICAL envelope in `raw_msgs`
    (a list of raw backend message dicts) and, on a structural violation, log a LOUD
    PII-free `contract_violation` event. **Validate-and-WARN only — never drops or
    breaks ingest** (the caller still maps + inserts every message). Non-canonical raw
    dicts (legacy fullwechat, no kind/schema) are skipped — this check is for backends
    that claim to speak canonical. Returns the number of violating envelopes.

    Gated by the `contract_validate_enabled` setting (default ON); pass `enabled` to
    override (tests). Reads the setting lazily so it can be toggled live.
    """
    from . import db, ingest
    if enabled is None:
        enabled = db.get_setting(conn, "contract_validate_enabled", "1") != "0"
    if not enabled:
        return 0
    n = 0
    for m in raw_msgs or []:
        if not ingest.is_canonical(m):
            continue
        viol = validate_canonical(m)
        if viol:
            n += 1
            db.log_event(conn, kind="contract_violation", actor="ingest",
                         detail={"type": "schema", "channel": channel or m.get("channel", ""),
                                 "violations": viol})  # PII-free: field names / enum values only
    return n


def validate_canonical(env) -> list[str]:
    """Return a list of violation messages for a canonical envelope ([] = ok).

    Pure + total: never raises. A non-dict input is itself one violation.
    """
    if not isinstance(env, dict):
        return [f"envelope is not a dict (got {type(env).__name__})"]

    viol: list[str] = []

    for f in REQUIRED_FIELDS:
        if f not in env or env[f] in (None, ""):
            viol.append(f"missing required field: {f}")

    kind = env.get("kind")
    if kind is not None and kind not in CANONICAL_KINDS:
        viol.append(f"kind {kind!r} not in canonical enum")

    direction = env.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        viol.append(f"direction {direction!r} not in {{in,out}}")

    # msg_id is OPTIONAL, but if present it MUST be a non-empty string. This encodes
    # half of the 2026-06-28 lesson at the structural boundary: a numeric / empty msg_id
    # is exactly the localId-as-id smell (the runtime uniqueness teeth are in db).
    if "msg_id" in env:
        mid = env["msg_id"]
        if not isinstance(mid, str) or not mid:
            viol.append(f"msg_id must be a non-empty string (got {mid!r})")

    return viol
