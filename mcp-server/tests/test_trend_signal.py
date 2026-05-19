"""Tests for compute_trend_signal — the conviction → triangle projection.

trend_signal is ONLY a projection of snap["conviction"]. It never reads
snap["trend_analyzer"] for its scoring (it can read eventMs from there for
chart anchoring, nothing else). This is the architectural barrier that keeps
TrendAnalyzer from becoming a decision engine.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def _reset():
    d._LAST_TREND_SIGNAL.clear()


def _snap(trend_label, *, alias="NQ", mid=21800.0, ta_event_ms=None):
    snap = {
        "alias": alias,
        "conviction": {"trend": trend_label, "score": 0.42,
                        "trajectory": "RISING"},
        "book": {"mid": mid},
    }
    if ta_event_ms is not None:
        snap["trend_analyzer"] = {"eventMs": ta_event_ms}
    return snap


# ─── Label → kind mapping ────────────────────────────────────────────────────

@pytest.mark.parametrize("trend_label,expected_kind", [
    ("BULLISH_TREND", "STRONG_BULL"),
    ("BULL_LEAN",     "WEAK_BULL"),
    ("BEARISH_TREND", "STRONG_BEAR"),
    ("BEAR_LEAN",     "WEAK_BEAR"),
    ("CHOP",          "NONE"),
    ("MIXED",         "NONE"),
    ("BULL_FADING",   "NONE"),
    ("BEAR_FADING",   "NONE"),
    (None,            "NONE"),
])
def test_kind_mapping(trend_label, expected_kind):
    _reset()
    out = d.compute_trend_signal(_snap(trend_label))
    assert out["kind"] == expected_kind, (
        f"trend={trend_label} should map to {expected_kind}, got {out['kind']}"
    )


def test_missing_conviction_yields_none_kind():
    _reset()
    out = d.compute_trend_signal({"alias": "NQ"})
    assert out["kind"] == "NONE"


def test_missing_alias_returns_none():
    _reset()
    out = d.compute_trend_signal({"conviction": {"trend": "BULLISH_TREND"}})
    assert out is None


# ─── Bucket transition detection ─────────────────────────────────────────────

def test_same_kind_clears_changed_flag_and_freezes_bucket():
    _reset()
    a = d.compute_trend_signal(_snap("BULLISH_TREND"))
    b = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert a["changedSinceLastTick"] is True       # first tick is always "new"
    assert b["changedSinceLastTick"] is False
    assert b["bucketEnteredMs"] == a["bucketEnteredMs"]


def test_kind_transition_sets_changed_flag_and_advances_bucket():
    _reset()
    a = d.compute_trend_signal(_snap("BULL_LEAN"))
    # Advance wall-clock minimally so bucketEnteredMs can move.
    time.sleep(0.002)
    b = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert b["changedSinceLastTick"] is True
    assert b["bucketEnteredMs"] >= a["bucketEnteredMs"]
    assert b["kind"] == "STRONG_BULL"


def test_per_alias_state_isolation():
    _reset()
    a = d.compute_trend_signal(_snap("BULLISH_TREND", alias="NQ"))
    # A new alias starts its own bucket — should be "changed".
    b = d.compute_trend_signal(_snap("BEAR_LEAN", alias="ES"))
    assert b["changedSinceLastTick"] is True
    # Original alias still in its bucket.
    c = d.compute_trend_signal(_snap("BULLISH_TREND", alias="NQ"))
    assert c["changedSinceLastTick"] is False


# ─── eventMs sourcing ────────────────────────────────────────────────────────

def test_event_ms_from_trend_analyzer_when_present():
    _reset()
    out = d.compute_trend_signal(_snap("BULLISH_TREND", ta_event_ms=1_700_000_000_000))
    assert out["eventMs"] == 1_700_000_000_000


def test_event_ms_falls_back_to_wall_clock_when_trend_analyzer_missing():
    _reset()
    before = int(time.time() * 1000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND"))
    after = int(time.time() * 1000)
    assert before <= out["eventMs"] <= after


def test_event_ms_falls_back_when_trend_analyzer_event_ms_invalid():
    _reset()
    snap = _snap("BULLISH_TREND")
    snap["trend_analyzer"] = {"eventMs": 0}   # invalid sentinel
    before = int(time.time() * 1000)
    out = d.compute_trend_signal(snap)
    assert out["eventMs"] >= before


# ─── Projection independence from trend_analyzer score ───────────────────────

def test_kind_does_not_change_with_strong_trend_analyzer_when_conviction_is_chop():
    """Architectural barrier: even with a maxed-out trend_analyzer in the
    snap, if the composite conviction says CHOP, the trend signal stays NONE.
    """
    _reset()
    now_ms = int(time.time() * 1000)
    snap = _snap("CHOP")
    snap["trend_analyzer"] = {
        "warmedUp": True, "updatedAtMs": now_ms, "eventMs": now_ms,
        "fast": {"direction": "UP", "directionSign": 1, "confidence": 100, "chop": False},
        "slow": {"direction": "UP", "directionSign": 1, "confidence": 100, "chop": False},
        "score": 1.0,
    }
    out = d.compute_trend_signal(snap)
    assert out["kind"] == "NONE"
