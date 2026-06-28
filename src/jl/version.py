"""AMR version — the ONE source of truth for AMR's own version + what it consumes.

Two version axes meet here (see spec
`docs/superpowers/specs/2026-06-28-contract-versioning-and-compat-v1.md`):

  * **AMR 消费侧版本** (`__version__`) — AMR 自己的软件版本, single-sourced here and
    mirrored into `pyproject.toml` (a test keeps them from drifting). Surfaced to the
    three audiences: 用户 (UI badge) / 运维 (`jl account ls`, /api/version) /
    Agent (`/api/version`, the `X-AMR-Version` request header on every outbound call).
  * **消费清单** (`CONSUMES`) — per contract, which version (if any) AMR actually
    consumes today. This is AMR's HALF of the two-sided handshake: the backend declares
    what it PROVIDES (`/api/status.version` + `/api/capabilities.schema`); AMR declares
    what it CONSUMES (here + `/api/version`). Both halves are readable, so either side
    can reconcile.

Keep `CONSUMES` honest: it is the *consumer* truth, not the *provider* capability. A
backend may EXPOSE a contract (e.g. group.canonical roster) that AMR does not yet
CONSUME — that asymmetry is exactly what the live compatibility matrix surfaces.
"""
from __future__ import annotations

__version__ = "0.11.0"

# Per-contract consumer declaration. Value = the contract version AMR consumes today,
# or None when AMR does NOT yet consume that contract (provider may still expose it).
#   "<major>"  — message.canonical/1 style (schema major)
#   "vN"       — doc-versioned contracts (send-target v1, moments v1, …)
#   None       — not yet consumed
CONSUMES = {
    # message.canonical/1 — consumed: adapter `ingest.from_canonical` maps the
    # canonical envelope into MsgRecord (channels/fullwechat.map_message).
    "message.canonical": "1",
    # send-target v1 — consumed: send path relies on the backend auto-opening cold
    # chats (fullwechat.send); AMR drives it but does not yet read `opened` richly.
    "send-target": "v1",
    # moments v1 — consumed: read + like. v2 (publish/broadcast) NOT yet consumed.
    "moments": "v1",
    # group.canonical/1 — NOT yet consumed: group size is still approximated; AMR does
    # not yet read the roster/meta the backend can expose.
    "group.canonical": None,
    # control-and-voice v1 — NOT yet consumed: human/agent mutex + voice feeding not
    # wired into AMR yet.
    "control": None,
}
