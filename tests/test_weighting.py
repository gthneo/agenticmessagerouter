"""Weighting + coloring — combined freshness is weighted, not a simple max."""
from jl import weighting as w


def test_days_since_converts_unix_to_days():
    now = 1_000_000
    assert w.days_since(now - 2 * 86400, now=now) == 2.0
    assert w.days_since(0, now=now) is None  # 0 == no interaction


def test_combine_picks_weighted_recency_not_simple_max():
    # phone is 1 day old, wechat is 2 days old. Simple max → phone (1d).
    # But wechat outweighs phone, so a 2d wechat beats a 1d phone.
    now = 1_000_000
    signals = [
        {"kind": "phone", "ts": now - 1 * 86400},
        {"kind": "wechat", "ts": now - 2 * 86400},
    ]
    weights = {"wechat": 1.0, "phone": 0.3}
    chosen = w.combine(signals, weights=weights, now=now)
    assert chosen["kind"] == "wechat"
    # real days are preserved (for threshold comparison), not a blended number
    assert chosen["days"] == 2.0


def test_combine_simple_max_when_weights_equal():
    now = 1_000_000
    signals = [
        {"kind": "phone", "ts": now - 1 * 86400},
        {"kind": "wechat", "ts": now - 2 * 86400},
    ]
    weights = {"wechat": 1.0, "phone": 1.0}
    chosen = w.combine(signals, weights=weights, now=now)
    assert chosen["kind"] == "phone"   # equal weight → most recent wins
    assert chosen["days"] == 1.0


def test_combine_ignores_empty_channels():
    now = 1_000_000
    signals = [
        {"kind": "phone", "ts": 0},          # no interaction
        {"kind": "wechat", "ts": now - 5 * 86400},
    ]
    chosen = w.combine(signals, now=now)
    assert chosen["kind"] == "wechat"
    assert chosen["days"] == 5.0


def test_combine_returns_none_when_all_empty():
    now = 1_000_000
    signals = [{"kind": "phone", "ts": 0}, {"kind": "wechat", "ts": 0}]
    assert w.combine(signals, now=now) is None


def test_color_thresholds():
    assert w.color(1.0, threshold_days=7) == "🟢"   # < 50% of threshold
    assert w.color(5.0, threshold_days=7) == "🟡"   # between 50% and threshold
    assert w.color(9.0, threshold_days=7) == "🔴"   # over threshold
    assert w.color(None, threshold_days=7) == "⚪"  # no data
