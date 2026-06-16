"""Endpoint routing — pure selection over endpoints + an injected sendable predicate."""
from jl import routing


def _ep(kind, ident, last_ts, pinned=0):
    return {"kind": kind, "identifier": ident, "last_ts": last_ts, "pinned": pinned}


def test_best_endpoint_prefers_weight_times_recency():
    eps = [_ep("phone", "13800000000", 100), _ep("wechat", "wxid_a", 100)]
    best = routing.best_endpoint(eps, sendable=lambda e: True, now=100)
    assert best["identifier"] == "wxid_a"   # equal recency → higher channel weight wins


def test_best_endpoint_pin_overrides_score():
    eps = [_ep("wechat", "wxid_a", 999), _ep("phone", "13800000000", 1, pinned=1)]
    best = routing.best_endpoint(eps, sendable=lambda e: True, now=1000)
    assert best["identifier"] == "13800000000"   # human pin wins regardless of score


def test_best_endpoint_falls_back_to_sendable():
    eps = [_ep("wechat", "wxid_stale", 999), _ep("wechat", "wxid_live", 1)]
    best = routing.best_endpoint(eps, sendable=lambda e: e["identifier"] == "wxid_live", now=1000)
    assert best["identifier"] == "wxid_live"   # top score unsendable → next sendable


def test_best_endpoint_none_when_no_sendable():
    eps = [_ep("wechat", "wxid_x", 5)]
    assert routing.best_endpoint(eps, sendable=lambda e: False) is None
