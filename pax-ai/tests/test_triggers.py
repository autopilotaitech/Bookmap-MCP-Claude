"""Trigger engine tests - parametric fixtures + dedup matrix."""

from __future__ import annotations

import time

import pytest

from pax_ai import triggers


@pytest.fixture(autouse=True)
def _reset_state():
    triggers.reset_state_for_tests()
    yield
    triggers.reset_state_for_tests()


def _live_snap_base():
    return {
        "health": "ok",
        "alias":  "NQM6.CME@RITHMIC",
        "book":   {"mid": 21326.00, "spread": 0.25},
        "conviction": {"score": 0.10, "trend": "RISING", "anchorMode": "LIVE"},
        "trend_signal": {"kind": "NONE", "eligible": False, "mid": 21326.00},
        "flow":   {"regime": "TRENDING_UP", "regimeConfidence": 0.5,
                    "biasScore": 0.10, "biasTrajectory": "FLAT"},
        "or_levels": {
            "middleLock":  False, "inProximity": True,
            "orHigh": 21340.0, "orLow": 21320.0, "orWidthPts": 20.0,
            "levels": [
                {"label": "+1", "price": 21391.0, "side": "above", "distance": 12.50,
                 "proximity": True, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.62},
            ],
        },
        "micro_events": {"events": []},
        "gates": {
            "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
            "news":    {"blocked": False, "label": None},
        },
        "session": {"anchorMode": "LIVE"},
        "pax": {"decision": "WAIT", "size_tier": "NONE"},
        "vwap_bias": {"label": "BULLISH"},
        "vp_bias":   {"label": "INSIDE_VA"},
    }


# ---------------------------------------------------------------------------
# LEVEL_APPROACH
# ---------------------------------------------------------------------------

def test_level_approach_fires_when_within_prox_ticks():
    snap = _live_snap_base()
    # Bring nearest level within 8 ticks (= 2 NQ pts at tick=0.25)
    snap["or_levels"]["levels"][0]["distance"] = 1.5
    t = triggers.compute_triggers(snap, snap_age_ms=100)
    kinds = [x["kind"] for x in t]
    assert "LEVEL_APPROACH" in kinds
    la = next(x for x in t if x["kind"] == "LEVEL_APPROACH")
    assert la["severity"] == "HIGH"   # conf >= 0.5
    assert la["label"] == "+1"


def test_level_approach_severity_med_below_confidence_threshold():
    snap = _live_snap_base()
    snap["or_levels"]["levels"][0]["distance"] = 1.0
    snap["or_levels"]["levels"][0]["confidence"] = 0.30
    t = triggers.compute_triggers(snap, snap_age_ms=100)
    la = next(x for x in t if x["kind"] == "LEVEL_APPROACH")
    assert la["severity"] == "MED"


def test_level_approach_does_not_fire_when_far():
    snap = _live_snap_base()
    snap["or_levels"]["levels"][0]["distance"] = 50.0
    t = triggers.compute_triggers(snap, snap_age_ms=100)
    assert all(x["kind"] != "LEVEL_APPROACH" for x in t)


def test_level_approach_dedups_within_bucket():
    snap = _live_snap_base()
    snap["or_levels"]["levels"][0]["distance"] = 1.0
    t1 = triggers.compute_triggers(snap, snap_age_ms=100)
    t2 = triggers.compute_triggers(snap, snap_age_ms=100)
    assert any(x["kind"] == "LEVEL_APPROACH" for x in t1)
    assert all(x["kind"] != "LEVEL_APPROACH" for x in t2), "dedup should suppress second fire"


# ---------------------------------------------------------------------------
# MIDDLE_LOCK
# ---------------------------------------------------------------------------

def test_middle_lock_enter_fires_on_transition():
    s = _live_snap_base(); s["or_levels"]["middleLock"] = False
    triggers.compute_triggers(s, 100)
    s["or_levels"]["middleLock"] = True
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "MIDDLE_LOCK_ENTER" for x in t)


def test_middle_lock_exit_fires_on_transition():
    s = _live_snap_base(); s["or_levels"]["middleLock"] = True
    triggers.compute_triggers(s, 100)
    s["or_levels"]["middleLock"] = False
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "MIDDLE_LOCK_EXIT" for x in t)


def test_middle_lock_no_fire_on_steady_state():
    s = _live_snap_base(); s["or_levels"]["middleLock"] = True
    triggers.compute_triggers(s, 100)
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] not in ("MIDDLE_LOCK_ENTER", "MIDDLE_LOCK_EXIT") for x in t)


# ---------------------------------------------------------------------------
# TREND_SIGNAL_FIRE
# ---------------------------------------------------------------------------

def test_trend_signal_strong_bull_eligible_fires():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True, "mid": 21330.0,
                          "eventMsSource": "trend_analyzer"}
    t = triggers.compute_triggers(s, 100)
    fire = next((x for x in t if x["kind"] == "TREND_SIGNAL_FIRE"), None)
    assert fire is not None
    assert fire["severity"] == "HIGH"


def test_trend_signal_not_eligible_does_not_fire():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": False, "mid": 21330.0}
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t)


def test_trend_signal_same_kind_does_not_fire_twice():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    t2 = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t1)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t2)


# ---------------------------------------------------------------------------
# CONVICTION_FLIP
# ---------------------------------------------------------------------------

def test_conviction_flip_fires_on_sign_cross():
    s = _live_snap_base()
    s["conviction"]["score"] = 0.30   # initial positive
    triggers.compute_triggers(s, 100)
    s["conviction"]["score"] = -0.30  # cross to negative
    t = triggers.compute_triggers(s, 100)
    flip = next((x for x in t if x["kind"] == "CONVICTION_FLIP"), None)
    assert flip is not None


def test_conviction_flip_hysteresis_band_no_fire():
    s = _live_snap_base()
    s["conviction"]["score"] = 0.30
    triggers.compute_triggers(s, 100)
    s["conviction"]["score"] = -0.05   # within hysteresis band, no flip
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "CONVICTION_FLIP" for x in t)


# ---------------------------------------------------------------------------
# REGIME_CHANGE
# ---------------------------------------------------------------------------

def test_regime_change_into_absorption_fires():
    s = _live_snap_base()
    s["flow"]["regime"] = "TRENDING_UP"
    triggers.compute_triggers(s, 100)
    s["flow"]["regime"] = "ABSORPTION_BID"
    s["flow"]["regimeConfidence"] = 0.71
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "REGIME_CHANGE" and x["label"] == "ABSORPTION_BID" for x in t)


def test_regime_change_into_trending_does_not_fire():
    s = _live_snap_base()
    s["flow"]["regime"] = "BALANCED"
    triggers.compute_triggers(s, 100)
    s["flow"]["regime"] = "TRENDING_UP"   # NOT in ABSORPTION/EXHAUSTION set
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "REGIME_CHANGE" for x in t)


# ---------------------------------------------------------------------------
# MICRO_EVENT
# ---------------------------------------------------------------------------

def test_micro_event_recent_fires():
    s = _live_snap_base()
    now_ms = int(time.time() * 1000)
    s["micro_events"]["events"] = [
        {"type": "STOP_SWEEP", "side": "ask", "ts": now_ms - 2000, "price": 21341.0},
    ]
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "MICRO_EVENT" and "STOP_SWEEP" in x["label"] for x in t)


def test_micro_event_stale_does_not_fire():
    s = _live_snap_base()
    now_ms = int(time.time() * 1000)
    s["micro_events"]["events"] = [
        {"type": "SPOOF", "side": "ask", "ts": now_ms - 30_000, "price": 21341.0},
    ]
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "MICRO_EVENT" for x in t)


# ---------------------------------------------------------------------------
# BRIDGE_DEGRADED
# ---------------------------------------------------------------------------

def test_bridge_degraded_offline_fires_in_stale_path():
    t = triggers.compute_triggers(None, snap_age_ms=10_000)
    assert any(x["kind"] == "BRIDGE_DEGRADED" for x in t)


def test_bridge_degraded_anchor_not_live_fires():
    s = _live_snap_base()
    s["gates"]["session"]["anchorMode"] = "LAST_KNOWN_STALE"
    s["session"]["anchorMode"] = "LAST_KNOWN_STALE"
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "BRIDGE_DEGRADED" and x["label"] == "LAST_KNOWN_STALE" for x in t)


# ---------------------------------------------------------------------------
# EOD_RISK
# ---------------------------------------------------------------------------

def test_eod_risk_in_close_window():
    s = _live_snap_base()
    s["gates"]["session"]["code"] = "CLOSE_RISK"
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "EOD_RISK" and x["label"] == "CLOSE_RISK" for x in t)


def test_eod_risk_not_in_active_window():
    s = _live_snap_base()
    s["gates"]["session"]["code"] = "ACTIVE"
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "EOD_RISK" for x in t)


# ---------------------------------------------------------------------------
# NEWS_T_MINUS_5
# ---------------------------------------------------------------------------

def test_news_blackout_fires():
    s = _live_snap_base()
    s["gates"]["news"] = {"blocked": True, "label": "CPI 08:30"}
    t = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "NEWS_T_MINUS_5" for x in t)


# ---------------------------------------------------------------------------
# Sorting + cap
# ---------------------------------------------------------------------------

def test_severity_sort_and_cap():
    """HIGH before MED before LOW, and at most 5 items returned."""
    s = _live_snap_base()
    # Stack: LEVEL_APPROACH (HIGH), MIDDLE_LOCK_EXIT (MED), EOD_RISK (LOW),
    # NEWS_T_MINUS_5 (HIGH), STRONG_BULL (HIGH).
    s["or_levels"]["middleLock"] = True
    triggers.compute_triggers(s, 100)
    s["or_levels"]["middleLock"] = False
    s["or_levels"]["levels"][0]["distance"] = 1.0
    s["gates"]["news"] = {"blocked": True, "label": "CPI"}
    s["gates"]["session"]["code"] = "CLOSE_RISK"
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True, "mid": 21330.0}
    t = triggers.compute_triggers(s, 100)
    severities = [x["severity"] for x in t]
    # HIGH must come first
    assert severities[0] == "HIGH"
    # Result capped at 5
    assert len(t) <= 5


# ---------------------------------------------------------------------------
# Stale gate
# ---------------------------------------------------------------------------

def test_stale_snapshot_only_bridge_degraded():
    s = _live_snap_base()
    s["or_levels"]["levels"][0]["distance"] = 1.0
    # Age above default stale_snapshot_ms=5000
    t = triggers.compute_triggers(s, snap_age_ms=10_000)
    # LEVEL_APPROACH would normally fire, but stale gate suppresses it.
    assert all(x["kind"] == "BRIDGE_DEGRADED" or x["kind"] == "BRIDGE_DEGRADED" for x in t)
    assert all(x["kind"] != "LEVEL_APPROACH" for x in t)
