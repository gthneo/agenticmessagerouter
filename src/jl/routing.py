"""Endpoint routing: pick the best reachable endpoint for a person. Pure — the
sendability predicate is injected (the live channel check lives in the caller), so
selection is testable without network. Realizes 一人多渠道每渠道多号 routing."""
from __future__ import annotations

import time

from . import weighting


def _recency(last_ts, now):
    days = max(0.0, (now - last_ts) / 86400.0) if last_ts else 1e9
    return 1.0 / (days + 1.0)


def score(endpoint, now):
    w = weighting.DEFAULT_WEIGHTS.get(endpoint["kind"], 0.5)
    return w * _recency(endpoint.get("last_ts") or 0, now)


def best_endpoint(endpoints, *, sendable, now=None):
    """Return the best SENDABLE endpoint, or None. A pinned endpoint wins if sendable;
    otherwise the highest weight×recency among sendable endpoints. `sendable(e)->bool`."""
    now = time.time() if now is None else now
    usable = [e for e in endpoints if sendable(e)]
    if not usable:
        return None
    pinned = [e for e in usable if e.get("pinned")]
    pool = pinned or usable
    return max(pool, key=lambda e: score(e, now))
