"""Combined-freshness weighting + coloring.

The v0.4 prototype used a simple max (most-recent interaction across channels).
That over-rewards weak signals — a missed call counts the same as a deep WeChat
conversation. Here each channel kind carries a weight, and the combined "last"
is the channel maximizing weight x recency. Real days are preserved (so the
threshold comparison stays in real calendar days), only the *choice* of which
channel defines freshness is weighted.
"""
from __future__ import annotations

import time

# Default per-channel weights. Richer/more-intentional channels rank higher;
# low-signal channels (a missed call) need to be much more recent to dominate.
DEFAULT_WEIGHTS = {
    "wechat": 1.0,
    "feishu": 1.0,
    "imsg": 0.9,
    "phone": 0.8,
    "gmail": 0.6,
    "whatsapp": 0.7,
    "wecom": 0.9,
}


def days_since(ts, now=None):
    """Days between ts (unix) and now. Returns None for the 0 sentinel (no data)."""
    if not ts:
        return None
    now = time.time() if now is None else now
    return (now - ts) / 86400.0


def _recency(days):
    # Monotonically decreasing in days; bounded so weight differences matter.
    return 1.0 / (days + 1.0)


def combine(signals, weights=None, now=None):
    """Pick the channel that defines combined freshness.

    signals: iterable of {"kind": str, "ts": int}. ts == 0 means no interaction.
    Returns {"kind", "ts", "days"} for the winner, or None if all empty.
    """
    weights = weights or DEFAULT_WEIGHTS
    now = time.time() if now is None else now
    best = None
    best_score = -1.0
    for s in signals:
        ts = s.get("ts") or 0
        if not ts:
            continue
        days = days_since(ts, now)
        weight = weights.get(s["kind"], 0.5)
        score = weight * _recency(days)
        # strict '>' means exact-score ties keep the first signal seen
        # (callers pass channels ordered by kind, so ties resolve alphabetically)
        if score > best_score:
            best_score = score
            best = {"kind": s["kind"], "ts": ts, "days": days}
    return best


def color(days, threshold_days=7):
    """🟢 fresh / 🟡 aging / 🔴 overdue / ⚪ no data."""
    if days is None:
        return "⚪"
    if days < threshold_days * 0.5:
        return "🟢"
    if days < threshold_days:
        return "🟡"
    return "🔴"
