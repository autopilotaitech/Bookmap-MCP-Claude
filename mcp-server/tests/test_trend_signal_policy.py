"""Quant-quality entry-marker policy for compute_trend_signal.

Pins:
  * eligibility gate (kind renderable + mid valid + eventMs > 0)
  * eventMs source labeling (trend_analyzer vs wall_clock_fallback)
  * NONE-flicker debounce: BULL -> NONE -> BULL within cooldown preserves bucket
  * cooldown-exhausted re-entry advances bucket
  * legitimate renderable transitions (WEAK→STRONG, BULL→BEAR) advance immediately
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def _reset():
    d._LAST_TREND_SIGNAL.clear()


def _snap(trend_label, *, alias="NQ", mid=21800.0, ta_event_ms=None,
          score=0.5, trajectory="RISING"):
    snap = {
        "alias": alias,
        "conviction": {"trend": trend_label, "score": score, "trajectory": trajectory},
        "book": {"mid": mid},
    }
    if ta_event_ms is not None:
        snap["trend_analyzer"] = {"eventMs": ta_event_ms}
    return snap


def _freeze_time(monkeypatch, t_ms):
    """Pin time.time() so the state machine's wall-clock reads are deterministic."""
    monkeypatch.setattr(d.time, "time", lambda: t_ms / 1000.0)


# ─── Eligibility gate ────────────────────────────────────────────────────────

def test_bullish_trend_valid_yields_eligible_strong_bull(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND", mid=21800.25, ta_event_ms=1_700_000_000_000))
    assert out["kind"] == "STRONG_BULL"
    assert out["eligible"] is True
    assert out["blockedReason"] is None
    assert out["eventMsSource"] == "trend_analyzer"
    assert out["changedSinceLastTick"] is True


def test_bearish_trend_valid_yields_eligible_strong_bear(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BEARISH_TREND", mid=21800.25, ta_event_ms=1_700_000_000_000))
    assert out["kind"] == "STRONG_BEAR"
    assert out["eligible"] is True
    assert out["blockedReason"] is None


def test_renderable_conviction_with_invalid_mid_downgrades_to_none(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    # No book.mid → invalid_mid.
    snap = {
        "alias": "NQ",
        "conviction": {"trend": "BULLISH_TREND", "score": 0.5, "trajectory": "RISING"},
        "book": {"mid": None},
    }
    out = d.compute_trend_signal(snap)
    assert out["kind"] == "NONE", f"expected NONE, got {out['kind']}"
    assert out["eligible"] is False
    assert out["blockedReason"] == "invalid_mid"


def test_renderable_conviction_with_nan_mid_downgrades_to_none(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND", mid=float("nan")))
    assert out["kind"] == "NONE"
    assert out["eligible"] is False
    assert out["blockedReason"] == "invalid_mid"


def test_renderable_conviction_with_zero_mid_downgrades_to_none(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND", mid=0.0))
    assert out["kind"] == "NONE"
    assert out["eligible"] is False
    assert out["blockedReason"] == "invalid_mid"


# ─── eventMs source labeling ──────────────────────────────────────────────────

def test_event_ms_source_trend_analyzer_when_provided(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND", ta_event_ms=1_700_000_000_000))
    assert out["eventMs"] == 1_700_000_000_000
    assert out["eventMsSource"] == "trend_analyzer"


def test_event_ms_source_wall_clock_fallback_when_trend_analyzer_missing(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out["eventMs"] == 1_000_000
    assert out["eventMsSource"] == "wall_clock_fallback"


def test_event_ms_source_falls_back_when_ta_event_ms_invalid(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    snap = _snap("BULLISH_TREND")
    snap["trend_analyzer"] = {"eventMs": 0}     # sentinel for invalid
    out = d.compute_trend_signal(snap)
    assert out["eventMsSource"] == "wall_clock_fallback"


# ─── Debounce: same-kind re-entry within cooldown ────────────────────────────

def test_strong_bull_to_none_to_strong_bull_within_cooldown_preserves_bucket(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out1 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out1["kind"] == "STRONG_BULL"
    assert out1["changedSinceLastTick"] is True
    first_bucket = out1["bucketEnteredMs"]

    # Brief NONE flicker 2s later.
    _freeze_time(monkeypatch, 1_002_000)
    out2 = d.compute_trend_signal(_snap("CHOP"))
    assert out2["kind"] == "NONE"
    assert out2["bucketEnteredMs"] == first_bucket, "NONE must NOT reset the bucket"
    assert out2["changedSinceLastTick"] is False

    # Return to STRONG_BULL 5s after the original emit — still within
    # COOLDOWN (15s). Bucket MUST stay the same; no new triangle.
    _freeze_time(monkeypatch, 1_005_000)
    out3 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out3["kind"] == "STRONG_BULL"
    assert out3["bucketEnteredMs"] == first_bucket, (
        f"re-entry within cooldown must preserve bucket; got {out3['bucketEnteredMs']} vs {first_bucket}")
    assert out3["changedSinceLastTick"] is False


def test_strong_bull_to_none_to_strong_bull_after_cooldown_advances_bucket(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out1 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    first_bucket = out1["bucketEnteredMs"]

    # NONE 5s later.
    _freeze_time(monkeypatch, 1_005_000)
    d.compute_trend_signal(_snap("CHOP"))

    # Return to STRONG_BULL AFTER cooldown (>= 15s since first emit).
    _freeze_time(monkeypatch, 1_020_000)   # 20s after first emit
    out3 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out3["bucketEnteredMs"] > first_bucket, (
        f"re-entry past cooldown must advance bucket; got {out3['bucketEnteredMs']} == {first_bucket}")
    assert out3["changedSinceLastTick"] is True


# ─── Immediate advance on legitimate transitions ─────────────────────────────

def test_weak_bull_to_strong_bull_advances_bucket_immediately(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out1 = d.compute_trend_signal(_snap("BULL_LEAN"))
    assert out1["kind"] == "WEAK_BULL"
    first_bucket = out1["bucketEnteredMs"]

    # Next tick, conviction strengthens. No dwell, no cooldown.
    _freeze_time(monkeypatch, 1_001_000)
    out2 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out2["kind"] == "STRONG_BULL"
    assert out2["bucketEnteredMs"] > first_bucket, (
        "renderable-kind change must advance bucket immediately")
    assert out2["changedSinceLastTick"] is True


def test_bull_to_bear_reversal_advances_bucket_immediately(monkeypatch):
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out1 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    first_bucket = out1["bucketEnteredMs"]

    _freeze_time(monkeypatch, 1_001_000)
    out2 = d.compute_trend_signal(_snap("BEARISH_TREND"))
    assert out2["kind"] == "STRONG_BEAR"
    assert out2["bucketEnteredMs"] > first_bucket
    assert out2["changedSinceLastTick"] is True


def test_none_flicker_does_not_reset_renderable_state(monkeypatch):
    """Multiple NONE ticks in a row must not corrupt renderable tracking."""
    _reset()
    _freeze_time(monkeypatch, 1_000_000)
    out1 = d.compute_trend_signal(_snap("BULLISH_TREND"))
    first_bucket = out1["bucketEnteredMs"]

    # Three NONE ticks.
    for t in (1_001_000, 1_002_000, 1_003_000):
        _freeze_time(monkeypatch, t)
        out = d.compute_trend_signal(_snap("CHOP"))
        assert out["kind"] == "NONE"
        assert out["bucketEnteredMs"] == first_bucket, (
            f"NONE tick at {t} must preserve bucket {first_bucket}")
        assert out["changedSinceLastTick"] is False

    # Return to renderable within cooldown — still the same bucket.
    _freeze_time(monkeypatch, 1_004_000)
    out_back = d.compute_trend_signal(_snap("BULLISH_TREND"))
    assert out_back["bucketEnteredMs"] == first_bucket
    assert out_back["changedSinceLastTick"] is False
