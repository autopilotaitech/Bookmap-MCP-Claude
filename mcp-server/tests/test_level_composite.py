"""V5 per-magnet composite tests.

Covers the new `_level_composite` and its helpers (_book_at_level,
_vwap_or_at_level, _vp_at_level, _conviction_at_level,
_vwap_stretch_directional). Builds synthetic snapshots that exercise
each driver and the FOLLOW/FADE/WAIT mapping per row side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as dash    # noqa: E402


@pytest.fixture(autouse=True)
def _reset_conviction_cache():
    """Tests must not leak conviction state between cases."""
    dash._LAST_CONVICTION.clear()
    yield
    dash._LAST_CONVICTION.clear()


# ──────────────────────────────────────────────────────────────────────
# Helpers — build synthetic snap pieces.
# ──────────────────────────────────────────────────────────────────────

def _bullish_snap(price: float, mid: float):
    """Bullish flow across multiple drivers at a given level price."""
    return {
        "alias": "NQM6",
        "tape_flow": {
            "deltaScore": 0.8, "deltaLabel": "STRONG_BUY",
            "totalPrints30s": 50, "largePrints30s": 12,
            "deltaReason": "test bull"},
        "lt_liquidity": {"bidSize": 1000, "askSize": 200,
                          "halfLifeMillis": 30000,
                          "bestBid": price - 0.5, "bestAsk": price + 0.5},
        "book": {
            "bestBid": price - 0.25, "bestAsk": price + 0.25,
            "mid": mid, "spread": 0.5,
            "bids": [{"price": price - 0.25, "size": 100},
                     {"price": price - 0.50, "size": 80},
                     {"price": price - 0.75, "size": 60}],
            "asks": [{"price": price + 0.25, "size": 10},
                     {"price": price + 0.50, "size": 8}],
        },
        "micro_events": {"events": [
            {"kind": "ICEBERG", "isBid": True, "price": price,
             "size": 100, "timeMs": 0}]},
    }


def _bearish_snap(price: float, mid: float):
    return {
        "alias": "NQM6",
        "tape_flow": {
            "deltaScore": -0.8, "deltaLabel": "STRONG_SELL",
            "totalPrints30s": 50, "largePrints30s": 12,
            "deltaReason": "test bear"},
        "lt_liquidity": {"bidSize": 200, "askSize": 1000,
                          "halfLifeMillis": 30000,
                          "bestBid": price - 0.5, "bestAsk": price + 0.5},
        "book": {
            "bestBid": price - 0.25, "bestAsk": price + 0.25,
            "mid": mid, "spread": 0.5,
            "bids": [{"price": price - 0.25, "size": 10},
                     {"price": price - 0.50, "size": 8}],
            "asks": [{"price": price + 0.25, "size": 100},
                     {"price": price + 0.50, "size": 80},
                     {"price": price + 0.75, "size": 60}],
        },
        "micro_events": {"events": [
            {"kind": "ICEBERG", "isBid": False, "price": price,
             "size": 100, "timeMs": 0}]},
    }


# ──────────────────────────────────────────────────────────────────────
# 1-4: Direction mapping by row side
# ──────────────────────────────────────────────────────────────────────

def test_above_level_with_bullish_flow_maps_to_follow_long():
    px = 20100.0
    out = dash._level_composite("above", px, mid=20050.0, snap=_bullish_snap(px, 20050.0))
    assert out["score"] > 0.20, f"expected positive composite, got {out['score']}"
    assert out["direction"] == "FOLLOW_LONG"
    assert 0.0 <= out["confidence"] <= 1.0


def test_above_level_with_bearish_flow_maps_to_fade_short():
    px = 20100.0
    out = dash._level_composite("above", px, mid=20050.0, snap=_bearish_snap(px, 20050.0))
    assert out["score"] < -0.20
    assert out["direction"] == "FADE_SHORT"


def test_below_level_with_bearish_flow_maps_to_follow_short():
    px = 19900.0
    out = dash._level_composite("below", px, mid=19950.0, snap=_bearish_snap(px, 19950.0))
    assert out["score"] < -0.20
    assert out["direction"] == "FOLLOW_SHORT"


def test_below_level_with_bullish_flow_maps_to_fade_long():
    px = 19900.0
    out = dash._level_composite("below", px, mid=19950.0, snap=_bullish_snap(px, 19950.0))
    assert out["score"] > 0.20
    assert out["direction"] == "FADE_LONG"


# ──────────────────────────────────────────────────────────────────────
# 5: VWAP stretch directional
# ──────────────────────────────────────────────────────────────────────

def test_vwap_extreme_upper_stretch_is_bearish_directional():
    """At +2.5σ above VWAP, the directional vwap score is bearish (fade
    short favored). At -2.5σ below VWAP, it is bullish."""
    upper_s, upper_rel, upper_r = dash._vwap_stretch_directional(
        {"vwap": 20000.0, "stddev": 10.0}, 20025.0)   # +2.5σ
    assert upper_s < 0, f"upper stretch should be bearish, got {upper_s}"
    assert upper_rel > 0
    lower_s, lower_rel, lower_r = dash._vwap_stretch_directional(
        {"vwap": 20000.0, "stddev": 10.0}, 19975.0)   # -2.5σ
    assert lower_s > 0
    fair_s, fair_rel, fair_r = dash._vwap_stretch_directional(
        {"vwap": 20000.0, "stddev": 10.0}, 20002.0)   # +0.2σ
    assert fair_s == 0.0


def test_vwap_extreme_stretch_reduces_long_follow_confidence_at_above_level():
    """An above-side level with bullish tape but vwap blowoff still maps
    to FOLLOW_LONG, but with lower confidence than the same snap at fair value."""
    px = 20100.0
    base = _bullish_snap(px, 20050.0)
    base["vwap_obj"] = {"vwap": px - 1.0, "stddev": 10.0}     # fair-value-ish
    fair = dash._level_composite("above", px, 20050.0, base)
    stretched = dict(base)
    stretched["vwap_obj"] = {"vwap": 20000.0, "stddev": 10.0}   # +10σ blowoff at px=20100
    stretched_out = dash._level_composite("above", px, 20050.0, stretched)
    assert fair["direction"] == "FOLLOW_LONG"
    # Either still FOLLOW_LONG with lower confidence, OR demoted toward WAIT/FADE.
    assert stretched_out["confidence"] <= fair["confidence"] + 1e-9, (
        f"expected stretched confidence <= fair; fair={fair['confidence']}, "
        f"stretched={stretched_out['confidence']}")


# ──────────────────────────────────────────────────────────────────────
# 6: VP HVN/LVN bounded scores
# ──────────────────────────────────────────────────────────────────────

def test_vp_at_level_hvn_returns_bounded_score_and_reason():
    # 4 bins; the one at price 100.0 is the heaviest → HVN.
    vp = {
        "totalVolume": 1000,
        "vpoc": 100.0, "vah": 102.0, "val": 98.0,
        "levels": [
            {"price": 98.0,  "volume": 50,  "buyVolume": 25, "sellVolume": 25},
            {"price": 99.0,  "volume": 100, "buyVolume": 50, "sellVolume": 50},
            {"price": 100.0, "volume": 800, "buyVolume": 400, "sellVolume": 400},
            {"price": 101.0, "volume": 50,  "buyVolume": 25, "sellVolume": 25},
        ],
    }
    above = dash._vp_at_level(vp, 100.0, "above")
    below = dash._vp_at_level(vp, 100.0, "below")
    # Bounded
    assert -1.0 <= above[0] <= 1.0
    assert -1.0 <= below[0] <= 1.0
    # HVN at above-level → mild bearish; below-level → mild bullish.
    assert above[0] < 0
    assert below[0] > 0
    assert "HVN" in above[2]


def test_vp_at_level_lvn_is_neutral_with_reduced_reliability():
    vp = {
        "totalVolume": 1000,
        "vpoc": 100.0, "vah": 102.0, "val": 98.0,
        "levels": [
            {"price": 98.0,  "volume": 800, "buyVolume": 400, "sellVolume": 400},
            {"price": 99.0,  "volume": 50,  "buyVolume": 25,  "sellVolume": 25},
            {"price": 100.0, "volume": 50,  "buyVolume": 25,  "sellVolume": 25},
            {"price": 101.0, "volume": 800, "buyVolume": 400, "sellVolume": 400},
        ],
    }
    out = dash._vp_at_level(vp, 100.0, "above")
    assert out[0] == 0.0
    assert "LVN" in out[2]
    assert 0.0 <= out[1] < 1.0   # reduced reliability


# ──────────────────────────────────────────────────────────────────────
# 7: Orderbook helper missing/empty without exception
# ──────────────────────────────────────────────────────────────────────

def test_book_at_level_handles_missing_book():
    for book in (None, {}, {"_error": "boom"}, {"bids": [], "asks": []}):
        s, r, reason = dash._book_at_level(book, 100.0, "above")
        assert -1.0 <= s <= 1.0
        assert 0.0 <= r <= 1.0
        assert isinstance(reason, str)


# ──────────────────────────────────────────────────────────────────────
# 8: Missing optional sources → composite still computed, WAIT/low confidence
# ──────────────────────────────────────────────────────────────────────

def test_missing_sources_yields_low_confidence_wait_composite():
    snap = {"alias": "NQM6"}    # every source missing
    out = dash._level_composite("above", 20100.0, 20050.0, snap)
    assert out["score"] == 0.0
    assert out["direction"] == "WAIT"
    assert out["confidence"] == 0.0
    assert any("thin coverage" in w for w in out["warnings"]) or \
           any("unavailable" in w for w in out["warnings"])


# ──────────────────────────────────────────────────────────────────────
# 9: compute_or_levels backward-compat fields + composite presence
# ──────────────────────────────────────────────────────────────────────

def test_compute_or_levels_preserves_existing_fields_and_adds_composite():
    snap = _bullish_snap(20100.0, 20050.0)
    snap["or_row"] = {"orHigh": "20100.00", "orLow": "20000.00"}
    out = dash.compute_or_levels(snap)
    assert out is not None
    # Top-level legacy fields stay.
    for key in ("anchor", "orHigh", "orLow", "orWidthPts", "rungPts", "mid",
                "proxTicks", "proxPts", "inProximity", "middleLock", "levels"):
        assert key in out, f"missing top-level key {key}"
    assert len(out["levels"]) == 8
    for lvl in out["levels"]:
        # Existing per-level fields preserved.
        for key in ("label", "price", "side", "distance", "proximity",
                    "score", "decision", "components", "reasons", "confidence"):
            assert key in lvl, f"level {lvl.get('label')} missing legacy {key}"
        # New composite block present + shape.
        comp = lvl["composite"]
        assert {"score", "direction", "confidence", "drivers", "warnings"} \
            <= set(comp.keys())
        assert -1.0 <= comp["score"] <= 1.0
        assert 0.0 <= comp["confidence"] <= 1.0
        assert comp["direction"] in (
            "FOLLOW_LONG", "FOLLOW_SHORT", "FADE_LONG", "FADE_SHORT", "WAIT")


# ──────────────────────────────────────────────────────────────────────
# 10: Bounds — composite clamping
# ──────────────────────────────────────────────────────────────────────

def test_composite_score_and_confidence_are_clamped():
    px = 20100.0
    out = dash._level_composite("above", px, 20050.0, _bullish_snap(px, 20050.0))
    assert -1.0 <= out["score"] <= 1.0
    assert 0.0 <= out["confidence"] <= 1.0
    out_bear = dash._level_composite("below", px - 200, 20050.0,
                                       _bearish_snap(px - 200, 20050.0))
    assert -1.0 <= out_bear["score"] <= 1.0
    assert 0.0 <= out_bear["confidence"] <= 1.0


# ──────────────────────────────────────────────────────────────────────
# Bonus: vwap_or gate as bullish/bearish context
# ──────────────────────────────────────────────────────────────────────

def test_vwap_or_gate_contributes_directionally():
    bull_gate = {"state": "ALLOW_LONG", "reason": "OR > VWAP, mid > VWAP"}
    bear_gate = {"state": "ALLOW_SHORT", "reason": "OR < VWAP, mid < VWAP"}
    blocked   = {"state": "BLOCKED", "reason": "mixed"}
    unknown   = {"state": "UNKNOWN", "reason": "no live mid"}
    assert dash._vwap_or_at_level(bull_gate, "above")[0] > 0
    assert dash._vwap_or_at_level(bear_gate, "above")[0] < 0
    assert dash._vwap_or_at_level(blocked, "above")[0] == 0
    assert dash._vwap_or_at_level(unknown, "above")[1] == 0   # reliability 0


# ──────────────────────────────────────────────────────────────────────
# Bonus: conviction reliability ramps in over duration
# ──────────────────────────────────────────────────────────────────────

def test_conviction_reliability_is_zero_in_first_5_minutes():
    early = {"score": 0.6, "trend": "BULLISH_TREND", "durationSec": 60}
    s, r, _ = dash._conviction_at_level(early, "above")
    assert r == 0.0
    mature = {"score": 0.6, "trend": "BULLISH_TREND", "durationSec": 1800}
    s, r, _ = dash._conviction_at_level(mature, "above")
    assert r == 1.0
    chop = {"score": 0.6, "trend": "CHOP", "durationSec": 1800}
    s, r, _ = dash._conviction_at_level(chop, "above")
    assert r <= 0.3


def test_conviction_slow_prior_uses_last_poll_cache():
    """When snap['conviction'] is absent but _LAST_CONVICTION has an entry
    for the alias, the composite should still factor in conviction."""
    px = 20100.0
    snap = _bullish_snap(px, 20050.0)
    # No conviction in this poll.
    snap.pop("conviction", None)
    # Seed previous-poll conviction in the cache.
    dash._LAST_CONVICTION["NQM6"] = {
        "score": 0.7, "trend": "BULLISH_TREND", "durationSec": 1800}
    out = dash._level_composite("above", px, 20050.0, snap)
    conv_driver = next(d for d in out["drivers"]
                       if d["name"] == "session_conviction")
    assert conv_driver["weight"] > 0, "conviction should pull from _LAST_CONVICTION"
