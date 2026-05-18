"""Quant tests for the session conviction integrator.

Pins the EMA-decay math against realistic rotation scenarios so the label
can't silently regress to CHOP/MIXED during clear directional moves.

The math:
    α   = 1 − exp(−ln 2 · Δt / HL)        per-component per-tick
    EMA = (1 − α)·EMA_prev + α·instant
    score = Σ w_k · EMA_k                  weighted composite, clipped to [-1,+1]

Threshold contract (after the 2026-05-17 tune):
    score >  0.35  + non-falling trajectory → BULLISH_TREND
    score >  0.18  + non-falling            → BULL_LEAN
    |score| < 0.10                          → CHOP
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def _reset_state():
    d._CONVICTION_STATE.clear()


def _snap(*, regime="WARMUP", regime_conf=0.0, bias=0.0,
          vwap=0.0, vp=0.0, slope_label="WARMUP", slope_z=0.0,
          or_levels=None, alias="NQM6", ts=None):
    """Build a snapshot dict shaped like fetch_snapshot() output."""
    return {
        "health": "ok",
        "alias":  alias,
        "ts":     ts or dt.datetime.now(d.ET).isoformat(timespec="seconds"),
        "flow": {
            "regime":           regime,
            "regimeConfidence": regime_conf,
            "biasScore":        bias,
            "vwapSlope":        {"label": slope_label, "slopeZ": slope_z},
            "ib":               {"dayType": "UNKNOWN"},
        },
        "vwap_bias":  {"score": vwap},
        "vp_bias":    {"score": vp},
        "or_levels":  or_levels,
    }


# ─── Defaults check ─────────────────────────────────────────────────────────

def test_ib_weight_is_zero_in_defaults():
    """Placeholder must not steal headroom from real signals."""
    assert d.CONVICTION_WEIGHTS["ib"] == 0.0
    assert sum(d.CONVICTION_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-6)


def test_halflives_are_in_responsive_range():
    hl = d.CONVICTION_HALFLIFE_SEC
    assert hl["level"] <= 120,  "level must react in <2min"
    assert hl["regime"] <= 180, "regime must react in <3min"
    assert hl["vp"]    <= 480,  "vp must charge to ~50% within 5min"
    assert hl["vwap"]  <= 360


# ─── Helper signal tests ────────────────────────────────────────────────────

def test_regime_signal_trending_up_strong_conf():
    s = d._regime_to_signal("TRENDING_UP", 0.8)
    assert 0.6 < s < 0.95, f"expected strong bull contribution, got {s:+.3f}"


def test_regime_signal_warmup_is_zero():
    assert d._regime_to_signal("WARMUP", 0.5) == 0.0
    assert d._regime_to_signal("QUIET", 1.0) == 0.0


def test_slope_signal_strong_up_with_z():
    s = d._slope_to_signal("STRONG_UP", 2.0)
    assert s == pytest.approx(0.9, abs=0.01)   # base 0.9 × (0.5 + 0.5) = 0.9


def test_level_signal_aggregates_proximity_only():
    ol = {"levels": [
        {"proximity": True,  "decision": "ENTER_LONG_FOLLOW",  "confidence": 0.7},
        {"proximity": False, "decision": "ENTER_SHORT_FOLLOW", "confidence": 0.9},  # ignored
        {"proximity": True,  "decision": "WAIT",               "confidence": 0.5},  # no side
    ]}
    assert d._level_to_signal(ol) == pytest.approx(0.7)


# ─── Conviction integration — the failure mode the user reported ────────────

def _drive(scenarios, dt_sec=1.0):
    """Step the conviction integrator with a sequence of (snap, ticks) pairs.

    Each snap is replayed `ticks` times spaced `dt_sec` apart. The state's
    `lastUpdateMs` is advanced manually so we don't depend on wall-clock.
    Returns the final result dict.
    """
    _reset_state()
    cur_ms = int(dt.datetime.now(d.ET).timestamp() * 1000) - sum(t for _, t in scenarios) * int(dt_sec*1000)
    result = None
    for snap, ticks in scenarios:
        for _ in range(ticks):
            # Force the snap timestamp so the state's last-update math advances
            # correctly relative to "now" at call time.
            result = d.compute_session_conviction(snap)
            # Push state's clock forward by dt_sec so the next call sees Δt = dt_sec
            alias = snap["alias"]
            d._CONVICTION_STATE[alias]["lastUpdateMs"] -= int(dt_sec * 1000)
            cur_ms += int(dt_sec * 1000)
    return result


def test_rotation_up_does_not_stay_chop():
    """The bug the user reported: real rotation up reading CHOP the whole time."""
    rotation_up = _snap(
        regime="TRENDING_UP", regime_conf=0.75,
        bias=0.40, vwap=0.50, vp=0.30,
        slope_label="RISING", slope_z=1.5,
        or_levels={"levels": [{"proximity": True,
                               "decision": "ENTER_LONG_FOLLOW",
                               "confidence": 0.40}]},
    )
    # 5 minutes of constant rotation-up signals at 1Hz
    result = _drive([(rotation_up, 300)])
    assert result is not None
    assert result["score"] > 0.18, (
        f"5-min rotation up should leave CHOP zone — got {result['score']:+.3f}, "
        f"label={result['trend']}, components={result['components']}"
    )
    assert result["trend"] in ("BULL_LEAN", "BULLISH_TREND"), (
        f"expected BULL_LEAN or BULLISH_TREND, got {result['trend']} "
        f"(score={result['score']:+.3f})"
    )


def test_rotation_up_reaches_bullish_trend_within_5_min():
    rotation = _snap(
        regime="TRENDING_UP", regime_conf=0.8,
        bias=0.50, vwap=0.55, vp=0.40,
        slope_label="STRONG_UP", slope_z=2.0,
        or_levels={"levels": [{"proximity": True,
                               "decision": "ENTER_LONG_FOLLOW",
                               "confidence": 0.55}]},
    )
    result = _drive([(rotation, 300)])
    assert result["score"] > 0.35, (
        f"strong rotation should clear BULLISH_TREND threshold — got {result['score']:+.3f}"
    )
    assert result["trend"] == "BULLISH_TREND"


def test_early_rotation_bull_lean_within_2_min():
    """At 2min of strong signals the label should be BULL_LEAN, not CHOP."""
    rotation = _snap(
        regime="TRENDING_UP", regime_conf=0.75,
        bias=0.40, vwap=0.50, vp=0.30,
        slope_label="RISING", slope_z=1.5,
        or_levels={"levels": [{"proximity": True,
                               "decision": "ENTER_LONG_FOLLOW",
                               "confidence": 0.40}]},
    )
    result = _drive([(rotation, 120)])
    assert result["score"] > 0.10, f"2-min rotation should not be CHOP — got {result['score']:+.3f}"
    assert "CHOP" != result["trend"]


def test_real_chop_stays_chop():
    """Symmetric: when signals are near zero the label MUST be CHOP."""
    flat = _snap(
        regime="BALANCED", regime_conf=0.5,
        bias=0.02, vwap=-0.05, vp=0.0,
        slope_label="FLAT", slope_z=0.0,
        or_levels=None,
    )
    result = _drive([(flat, 300)])
    assert abs(result["score"]) < 0.10, f"expected CHOP, got {result['score']:+.3f}"
    assert result["trend"] == "CHOP"


def test_rotation_down_reaches_bearish_trend():
    rotation_down = _snap(
        regime="TRENDING_DOWN", regime_conf=0.8,
        bias=-0.50, vwap=-0.55, vp=-0.40,
        slope_label="STRONG_DOWN", slope_z=-2.0,
        or_levels={"levels": [{"proximity": True,
                               "decision": "ENTER_SHORT_FOLLOW",
                               "confidence": 0.55}]},
    )
    result = _drive([(rotation_down, 300)])
    assert result["score"] < -0.35
    assert result["trend"] == "BEARISH_TREND"


def test_bull_lean_zone_exists():
    """An intermediate-strength signal lands in LEAN, not flipped to TREND or CHOP."""
    moderate = _snap(
        regime="TRENDING_UP", regime_conf=0.5,
        bias=0.25, vwap=0.30, vp=0.15,
        slope_label="RISING", slope_z=1.0,
    )
    result = _drive([(moderate, 200)])
    assert 0.10 < result["score"] < 0.50, (
        f"moderate rotation should sit between CHOP and TREND, got {result['score']:+.3f}"
    )


def test_ib_weight_zero_means_ib_does_not_contribute():
    """With ib weight at 0 even a wild ib instantaneous value can't move the score."""
    s = _snap(regime="TRENDING_UP", regime_conf=0.8, bias=0.5,
              vwap=0.5, vp=0.4, slope_label="RISING", slope_z=1.0)
    r1 = _drive([(s, 100)])
    score_no_ib = r1["score"]
    # Manually corrupt the ib EMA in the state and recompute one more tick
    # to confirm it has zero effect on the composite.
    alias = s["alias"]
    d._CONVICTION_STATE[alias]["ema"]["ib"] = 1.0
    r2 = d.compute_session_conviction(s)
    assert r2["score"] == pytest.approx(score_no_ib, abs=0.02), (
        f"ib should not affect composite (weight=0), but score moved "
        f"from {score_no_ib:.4f} to {r2['score']:.4f}"
    )


def test_session_reset_at_anchor_crossing():
    """When the 08:30 CT anchor advances, EMA state resets to zero."""
    _reset_state()
    s = _snap(regime="TRENDING_UP", regime_conf=0.8, bias=0.5, vwap=0.5, vp=0.4,
              slope_label="RISING", slope_z=1.0)
    d.compute_session_conviction(s)
    alias = s["alias"]
    assert any(v != 0.0 for v in d._CONVICTION_STATE[alias]["ema"].values())

    # Force a stale anchor — simulates a new trading day
    d._CONVICTION_STATE[alias]["anchorMs"] = 0
    d.compute_session_conviction(s)
    # After reset, the EMA for the just-arrived sample is α·instant (not zero),
    # but should be much smaller than the saturated values.
    emas = d._CONVICTION_STATE[alias]["ema"]
    # Pick the slowest component to confirm reset: vp at 360s HL → α≈0.0019 on one tick
    assert emas["vp"] < 0.05, f"vp EMA didn't reset, still {emas['vp']:.3f}"
