"""Tests for flow.lt_signal_quality (T2 dial-in change 2026-05-28).

Pins the contract:
- RELIABLE: normal-volume tape + LT sign stable -> LT contributes normally.
- LIKELY_SPOOFED: LT sign flips >= 3 times in 5 min -> LT contribution
  forced to NULL OPINION (signed=0, reliability preserved). Dilutes the
  weighted vote toward zero; does NOT amplify other inputs.
- DEAD: 30s total tape volume below noise floor -> same NULL OPINION
  treatment.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from bookmap_mcp import institutional_flow as iflow


_ALIAS = "LTQ.TEST"


@pytest.fixture(autouse=True)
def _reset_state():
    iflow.reset_state()
    yield
    iflow.reset_state()


def _ts_outside_chop(hh: int = 9, mm: int = 0) -> int:
    from datetime import datetime as _dt
    if iflow._CT is None:
        return 1_700_000_000_000
    dt = _dt(2026, 5, 27, hh, mm, 0, tzinfo=iflow._CT)
    return int(dt.timestamp() * 1000)


def _snap(
    *, ts_ms: int, lt_ratio: float = 0.0,
    total_vol_30s: int = 1000,
    extra_inputs: Dict[str, Any] = None,
) -> Dict[str, Any]:
    """Snapshot with controllable LT ratio + tape volume, and just enough
    structure to drive compute_institutional_flow without producing a
    directional regime by accident."""
    # Build a tape_buckets payload with the requested 30s total volume,
    # distributed evenly buy/sell so it doesn't bias other inputs.
    half = total_vol_30s // 2
    buckets = [
        {"label": "1-10",  "buyVol30s": half, "sellVol30s": half,
         "buyVol5m": 0, "sellVol5m": 0},
        {"label": "11-25", "buyVol30s": 0, "sellVol30s": 0,
         "buyVol5m": 0, "sellVol5m": 0},
        {"label": "26-50", "buyVol30s": 0, "sellVol30s": 0,
         "buyVol5m": 0, "sellVol5m": 0},
        {"label": "51-99", "buyVol30s": 0, "sellVol30s": 0,
         "buyVol5m": 0, "sellVol5m": 0},
        {"label": "100+",  "buyVol30s": 0, "sellVol30s": 0,
         "buyVol5m": 0, "sellVol5m": 0},
    ]
    base: Dict[str, Any] = {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": 30000.0},
        "or_levels": {"orHigh": 30050.0, "orLow": 29950.0, "levels": []},
        "flow": {"ofiZ": 0.0, "cvdDeltaZ": 0.0, "biasScore": 0.0,
                 "regime": "BALANCED"},
        "pull_stack": {"windows": [
            {"label": "BBO", "bias": "NEUTRAL", "zScore": 0.0},
            {"label": "1m",  "bias": "NEUTRAL", "zScore": 0.0},
            {"label": "3m",  "bias": "NEUTRAL", "zScore": 0.0},
        ]},
        "lt_liquidity": {"ratio": lt_ratio},
        "micro_events": {"events": []},
        "tape_buckets": {"buckets": buckets},
        "trend_analyzer": {"warmedUp": False, "fast": {
            "direction": "NEUTRAL", "directionSign": 0, "chop": False,
        }},
    }
    if extra_inputs:
        base.update(extra_inputs)
    return base


# ─── DEAD condition ───────────────────────────────────────────────────


def test_no_tape_buckets_is_dead():
    snap = _snap(ts_ms=_ts_outside_chop())
    del snap["tape_buckets"]
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "DEAD"


def test_zero_volume_is_dead():
    snap = _snap(ts_ms=_ts_outside_chop(), total_vol_30s=0)
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "DEAD"


def test_below_threshold_volume_is_dead():
    snap = _snap(ts_ms=_ts_outside_chop(),
                 total_vol_30s=iflow._LT_QUALITY_DEAD_VOL_30S - 1)
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "DEAD"


def test_at_threshold_volume_not_dead():
    snap = _snap(ts_ms=_ts_outside_chop(),
                 total_vol_30s=iflow._LT_QUALITY_DEAD_VOL_30S)
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] != "DEAD"


def test_missing_lt_ratio_with_volume_is_dead():
    snap = _snap(ts_ms=_ts_outside_chop(), total_vol_30s=1000)
    snap["lt_liquidity"] = {}   # no ratio
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "DEAD"


# ─── LIKELY_SPOOFED condition ─────────────────────────────────────────


def _drive_lt_history(ratios: List[float], spacing_sec: float = 30.0) -> Dict[str, Any]:
    """Drive the engine through a series of LT ratios spaced spacing_sec apart
    and return the final output."""
    ts0 = _ts_outside_chop(hh=10)
    out = None
    for i, r in enumerate(ratios):
        ts = ts0 + int(i * spacing_sec * 1000)
        snap = _snap(ts_ms=ts, lt_ratio=r, total_vol_30s=1000)
        out = iflow.compute_institutional_flow(snap, _ALIAS)
    return out  # type: ignore[return-value]


def test_stable_lt_sign_is_reliable():
    out = _drive_lt_history([0.10, 0.12, 0.08, 0.15, 0.11])
    assert out["lt_signal_quality"] == "RELIABLE"


def test_three_sign_flips_in_window_is_spoofed():
    # 0.20 -> -0.20 -> 0.20 -> -0.20 = 3 flips
    out = _drive_lt_history([0.20, -0.20, 0.20, -0.20], spacing_sec=30.0)
    assert out["lt_signal_quality"] == "LIKELY_SPOOFED"


def test_two_sign_flips_is_reliable():
    out = _drive_lt_history([0.20, -0.20, 0.20], spacing_sec=30.0)
    assert out["lt_signal_quality"] == "RELIABLE"


def test_deadzone_swings_do_not_count_as_flip():
    # 0.005 is inside deadzone so doesn't count as a sign change.
    out = _drive_lt_history(
        [0.20, 0.005, -0.20, 0.005, 0.20, 0.005, -0.20],
        spacing_sec=30.0,
    )
    # Real flips: +0.20 -> -0.20 -> +0.20 -> -0.20 = 3 flips (zeros skipped)
    assert out["lt_signal_quality"] == "LIKELY_SPOOFED"


def test_history_outside_window_does_not_count():
    """Flips that age outside the 5-min window should drop from the count."""
    ts0 = _ts_outside_chop(hh=10)
    # Three flips spread 2 min apart -> all within 5 min window
    for i, r in enumerate([0.20, -0.20, 0.20, -0.20]):
        ts = ts0 + i * 2 * 60_000
        iflow.compute_institutional_flow(
            _snap(ts_ms=ts, lt_ratio=r, total_vol_30s=1000), _ALIAS)
    # Now jump 10 min forward with a single new ratio -> old flips age out.
    ts_later = ts0 + 14 * 60_000
    out = iflow.compute_institutional_flow(
        _snap(ts_ms=ts_later, lt_ratio=0.20, total_vol_30s=1000), _ALIAS)
    assert out["lt_signal_quality"] == "RELIABLE"


# ─── effect on weighted vote ──────────────────────────────────────────


def test_spoofed_lt_does_not_amplify_other_inputs():
    """When LT is forced to null opinion (signed=0, reliability preserved),
    its weight remains in the denominator and other inputs cannot ride a
    smaller denominator into the regime threshold."""
    # Trigger spoofed by flipping a few times first.
    ts0 = _ts_outside_chop(hh=10)
    for i, r in enumerate([0.20, -0.20, 0.20, -0.20]):
        ts = ts0 + i * 30_000
        iflow.compute_institutional_flow(
            _snap(ts_ms=ts, lt_ratio=r, total_vol_30s=1000), _ALIAS)

    # Now feed a snap with strong bullish ofi but stay below the natural
    # threshold; verify SPOOFED LT does not push it over.
    ts = ts0 + 5 * 60_000 + 1
    snap = _snap(ts_ms=ts, lt_ratio=0.20, total_vol_30s=1000)
    snap["flow"]["ofiZ"] = 0.5    # mild bullish ofi
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "LIKELY_SPOOFED"
    # Vote should be small because mild input + spoofed LT diluted.
    assert abs(out["weighted_vote"]) < 0.20


def test_reliable_lt_with_strong_bias_contributes():
    """Sanity: when LT is RELIABLE and bullish, it should add weight to a
    bullish regime fire."""
    ts = _ts_outside_chop(hh=10)
    snap = _snap(ts_ms=ts, lt_ratio=0.40, total_vol_30s=1000)
    out = iflow.compute_institutional_flow(snap, _ALIAS)
    assert out["lt_signal_quality"] == "RELIABLE"
    # LT-only at +0.40 ratio -> clamped to 0.40*4 = 1.0, weight 0.15 -> +0.15
    # contribution before normalization. Vote should be measurably positive.
    assert out["weighted_vote"] > 0.0


# ─── return-dict shape ────────────────────────────────────────────────


def test_lt_signal_quality_field_always_present():
    out = iflow.compute_institutional_flow(_snap(ts_ms=_ts_outside_chop()), _ALIAS)
    assert "lt_signal_quality" in out
    assert out["lt_signal_quality"] in ("RELIABLE", "LIKELY_SPOOFED", "DEAD")
