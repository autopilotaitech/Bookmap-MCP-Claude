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


def test_level_approach_re_emits_while_in_proximity():
    """LEVEL_APPROACH is a STATE condition, not an edge event. The chip
    must stay visible on every poll while ticks_away <= prox_ticks. The
    prior dedup-based behavior caused the chip to vanish on the second
    poll -- exactly the "flicker" bug the user reported."""
    snap = _live_snap_base()
    snap["or_levels"]["levels"][0]["distance"] = 1.0
    t1 = triggers.compute_triggers(snap, snap_age_ms=100)
    t2 = triggers.compute_triggers(snap, snap_age_ms=100)
    t3 = triggers.compute_triggers(snap, snap_age_ms=100)
    assert any(x["kind"] == "LEVEL_APPROACH" for x in t1)
    assert any(x["kind"] == "LEVEL_APPROACH" for x in t2), "must re-emit while in proximity"
    assert any(x["kind"] == "LEVEL_APPROACH" for x in t3), "must re-emit while in proximity"


def test_level_approach_disappears_when_price_leaves_proximity():
    snap = _live_snap_base()
    snap["or_levels"]["levels"][0]["distance"] = 1.0
    t1 = triggers.compute_triggers(snap, snap_age_ms=100)
    snap["or_levels"]["levels"][0]["distance"] = 50.0   # well outside 8 ticks
    t2 = triggers.compute_triggers(snap, snap_age_ms=100)
    assert any(x["kind"] == "LEVEL_APPROACH" for x in t1)
    assert all(x["kind"] != "LEVEL_APPROACH" for x in t2), \
        "must disappear once condition no longer holds"


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
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0,
                          "eventMsSource": "trend_analyzer"}
    t = triggers.compute_triggers(s, 100)
    fire = next((x for x in t if x["kind"] == "TREND_SIGNAL_FIRE"), None)
    assert fire is not None
    assert fire["severity"] == "HIGH"


def test_trend_signal_weak_bear_is_med_severity():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "WEAK_BEAR", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 2000, "mid": 21330.0}
    t = triggers.compute_triggers(s, 100)
    fire = next((x for x in t if x["kind"] == "TREND_SIGNAL_FIRE"), None)
    assert fire is not None and fire["severity"] == "MED"


def test_trend_signal_not_eligible_does_not_fire():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": False,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t)


def test_trend_signal_no_change_flag_does_not_fire():
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": False,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t)


def test_trend_signal_lingers_after_fire_even_when_change_flag_clears():
    """Edge trigger: once it fires, the chip must stay visible across
    subsequent polls where changedSinceLastTick=False. This was the
    "flicker like HFT 2 times" bug the user reported."""
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    # The dashboard typically sets changedSinceLastTick back to False on
    # the very next poll, while bucketEnteredMs stays at 1000.
    s["trend_signal"]["changedSinceLastTick"] = False
    t2 = triggers.compute_triggers(s, 100)
    t3 = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t1), "initial fire"
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t2), (
        "must linger across the change-flag-false poll")
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t3), "still lingering"


def test_trend_signal_linger_expires_after_window(monkeypatch):
    """The linger cache must EVENTUALLY drop the chip. We monkeypatch
    time.time so the test runs deterministically without sleeping."""
    import pax_ai.triggers as trig_mod
    fake_now = [1_000_000.0]
    monkeypatch.setattr(trig_mod.time, "time", lambda: fake_now[0])
    triggers.reset_state_for_tests()

    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t1)

    # Advance wall clock past the linger window (default 15 s).
    s["trend_signal"]["changedSinceLastTick"] = False
    fake_now[0] += 16.0
    t_expired = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t_expired), \
        "linger must expire after LINGER_MS"


def test_trend_signal_new_bucket_refires_within_60s():
    """Dashboard re-entry (NONE -> BULL past cooldown) emits a new bucket
    with changedSinceLastTick=True. The detector MUST fire again because
    the (kind, bucket) dedup key is fresh -- this is the exact regression
    the user reported in the screenshot."""
    s = _live_snap_base()
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 16000, "mid": 21331.0}
    t2 = triggers.compute_triggers(s, 200)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t1)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t2), (
        "new bucket must re-fire; this is the screenshot bug")


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


# ---------------------------------------------------------------------------
# Alias scoping (audit fix 3): trigger state must be partitioned by
# snap["alias"] so switching instruments never inherits / suppresses
# triggers that belong to a different instrument.
# ---------------------------------------------------------------------------

def test_alias_same_alias_dedups_normally():
    """Re-iterating the same alias must still dedup edge fires."""
    s = _live_snap_base()
    s["alias"] = "NQM6.CME@RITHMIC"
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    # Same bucket on the same alias -> no fresh fire on the second poll.
    s["trend_signal"]["changedSinceLastTick"] = True   # defensive
    t2 = triggers.compute_triggers(s, 100)
    # Both polls should surface the (lingering) chip but the cache key is
    # the same -- firstSeenMs must NOT advance.
    fires_t1 = [x for x in t1 if x["kind"] == "TREND_SIGNAL_FIRE"]
    fires_t2 = [x for x in t2 if x["kind"] == "TREND_SIGNAL_FIRE"]
    assert fires_t1 and fires_t2
    assert fires_t1[0]["firstSeenMs"] == fires_t2[0]["firstSeenMs"]


def test_alias_different_alias_same_bucket_fires_independently():
    """Same bucketEnteredMs on alias B must produce its own fresh fire,
    not inherit / be suppressed by alias A's state."""
    s = _live_snap_base()
    s["alias"] = "NQM6.CME@RITHMIC"
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    fires_a = [x for x in t1 if x["kind"] == "TREND_SIGNAL_FIRE"]
    assert fires_a, "NQ must fire on the first poll"

    # Different alias; identical bucket / kind. The detector must see
    # prev_trend_bucket_ms == 0 (fresh alias) and emit.
    s["alias"] = "MNQM6.CME@RITHMIC"
    t2 = triggers.compute_triggers(s, 100)
    fires_b = [x for x in t2 if x["kind"] == "TREND_SIGNAL_FIRE"]
    assert fires_b, ("MNQ must fire independently of NQ even though the "
                       "bucketEnteredMs and kind happen to match")
    # Both aliases should now be tracked in state.
    aliases = triggers.known_aliases_for_tests()
    assert "NQM6.CME@RITHMIC" in aliases
    assert "MNQM6.CME@RITHMIC" in aliases


def test_alias_middle_lock_transitions_are_independent():
    """middleLock ENTER / EXIT edges are per-alias."""
    s = _live_snap_base()
    # Alias A starts middleLock=False (initial state, no prev), then
    # transitions to True.
    s["alias"] = "NQM6.CME@RITHMIC"
    s["or_levels"]["middleLock"] = False
    triggers.compute_triggers(s, 100)
    s["or_levels"]["middleLock"] = True
    t_a_enter = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "MIDDLE_LOCK_ENTER" for x in t_a_enter)

    # Alias B (fresh state) -- middleLock=True on the first poll must
    # NOT produce a transition (no prior value to compare).
    s["alias"] = "MNQM6.CME@RITHMIC"
    s["or_levels"]["middleLock"] = True
    t_b_first = triggers.compute_triggers(s, 100)
    assert all(x["kind"] not in ("MIDDLE_LOCK_ENTER", "MIDDLE_LOCK_EXIT")
                 for x in t_b_first)


def test_alias_conviction_flip_is_independent():
    """Conviction sign flip on alias A must not pre-flip alias B."""
    s = _live_snap_base()
    s["alias"] = "NQM6.CME@RITHMIC"
    s["conviction"]["score"] = 0.40
    triggers.compute_triggers(s, 100)
    s["conviction"]["score"] = -0.40   # flip on NQ
    t_a = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "CONVICTION_FLIP" for x in t_a)

    # Alias B sees a positive score for the first time -- no transition
    # because there is no prior value, NOT because NQ already flipped.
    s["alias"] = "MNQM6.CME@RITHMIC"
    s["conviction"]["score"] = 0.40
    t_b = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "CONVICTION_FLIP" for x in t_b)


def test_alias_regime_change_is_independent():
    """A's prev_regime must not leak into B's transition detection."""
    s = _live_snap_base()

    # Alias A: TRENDING_UP -> ABSORPTION_BID fires (existing test).
    s["alias"] = "NQM6.CME@RITHMIC"
    s["flow"]["regime"] = "TRENDING_UP"
    triggers.compute_triggers(s, 100)
    s["flow"]["regime"] = "ABSORPTION_BID"
    s["flow"]["regimeConfidence"] = 0.8
    t_a = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "REGIME_CHANGE" for x in t_a)

    # Alias B with a non-absorption baseline -- no transition yet.
    s["alias"] = "ESM6.CME@RITHMIC"
    s["flow"]["regime"] = "TRENDING_UP"
    t_b_first = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "REGIME_CHANGE" for x in t_b_first), (
        "fresh alias seeing TRENDING_UP must not produce a REGIME_CHANGE")

    # Alias B then enters absorption -- fires ON B (independent of A).
    s["flow"]["regime"] = "ABSORPTION_BID"
    s["flow"]["regimeConfidence"] = 0.7
    t_b_enter = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "REGIME_CHANGE" for x in t_b_enter), (
        "B must transition independently of A's state")

    # And A's prior REGIME_CHANGE chip is in A's linger cache, not B's.
    b_edges = [x for x in t_b_enter if x["kind"] == "REGIME_CHANGE"]
    # Exactly one fresh chip for B; A's chip is in A's bucket.
    assert len(b_edges) == 1


def test_alias_edge_linger_does_not_bleed_across_aliases():
    """A TREND_SIGNAL_FIRE on NQ must not appear in MNQ's chip list."""
    s = _live_snap_base()
    s["alias"] = "NQM6.CME@RITHMIC"
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    triggers.compute_triggers(s, 100)

    # Switch to MNQ with no eligible trend signal of its own; the chip
    # from NQ must NOT appear.
    s["alias"] = "MNQM6.CME@RITHMIC"
    s["trend_signal"] = {"kind": "NONE", "eligible": False,
                          "changedSinceLastTick": False,
                          "bucketEnteredMs": 0}
    t = triggers.compute_triggers(s, 100)
    assert all(x["kind"] != "TREND_SIGNAL_FIRE" for x in t)


def test_alias_reset_state_clears_all_aliases():
    s = _live_snap_base()
    s["alias"] = "A"; s["trend_signal"] = {
        "kind": "STRONG_BULL", "eligible": True,
        "changedSinceLastTick": True, "bucketEnteredMs": 10, "mid": 1.0}
    triggers.compute_triggers(s, 100)
    s["alias"] = "B"
    triggers.compute_triggers(s, 100)
    assert set(triggers.known_aliases_for_tests()) >= {"A", "B"}
    triggers.reset_state_for_tests()
    assert triggers.known_aliases_for_tests() == []


def test_alias_missing_falls_back_to_default_bucket():
    """A snapshot with no alias key must still be processed; consecutive
    no-alias polls share the same '__default__' bucket so dedup works."""
    s = _live_snap_base()
    s.pop("alias", None)
    s["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True,
                          "changedSinceLastTick": True,
                          "bucketEnteredMs": 1000, "mid": 21330.0}
    t1 = triggers.compute_triggers(s, 100)
    assert any(x["kind"] == "TREND_SIGNAL_FIRE" for x in t1)
    # The fallback bucket must exist in alias registry.
    aliases = triggers.known_aliases_for_tests()
    assert triggers.ALIAS_DEFAULT in aliases
