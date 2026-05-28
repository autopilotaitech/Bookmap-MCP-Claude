"""Tests for the whipsaw-lockout feature in institutional_flow.

Pins the contract:
- 3 directional eps closed with peak_favorable < 5pt within 15 min ->
  10-min lockout triggers.
- During lockout, ACCUMULATION/DISTRIBUTION candidates are demoted to
  BALANCED.
- After lockout window expires, directional fires resume.
- Eps with peak_favorable >= 5pt do NOT count as failed.
- Eps older than 15 min do NOT count.

Operator's 2026-05-28 12:55-13:13 CT cluster (5 STALE/MISS eps with peaks
0.125, 0, 0, 1.0, 0.125) is the production scenario this prevents.
"""

from __future__ import annotations

from collections import deque
from typing import Dict, Any

import pytest

from bookmap_mcp import institutional_flow as iflow


_ALIAS = "WHIP.TEST"


@pytest.fixture(autouse=True)
def _reset_state():
    iflow.reset_state()
    yield
    iflow.reset_state()


def _state() -> Dict[str, Any]:
    return iflow._get_state(_ALIAS)


def _push_closed_ep(end_ms: int, peak_fav: float, regime: str = "ACCUMULATION") -> None:
    """Append a synthetic closed episode directly into state for unit-level
    testing of the lockout-trigger logic without driving full snapshots."""
    _state()["whipsaw_closed_eps"].append({
        "regime": regime,
        "start_ms": end_ms - 30_000,
        "end_ms": end_ms,
        "peak_favorable_pts": peak_fav,
    })


def _drive_track(regime: str, mid: float, now_ms: int) -> None:
    iflow._track_episode_for_whipsaw(_state(), regime, mid, now_ms)


# ─── helper-level unit tests ──────────────────────────────────────────


def test_clean_session_has_no_lockout():
    s = _state()
    assert s["whipsaw_lockout_until_ms"] == 0
    assert s["whipsaw_lockout_reason"] == ""
    assert s["whipsaw_open_ep"] is None
    assert list(s["whipsaw_closed_eps"]) == []


def test_three_failed_eps_within_15min_triggers_lockout():
    now_ms = 100_000_000  # arbitrary epoch ms
    _push_closed_ep(now_ms - 10 * 60_000, 0.125)
    _push_closed_ep(now_ms -  5 * 60_000, 0.0)
    _push_closed_ep(now_ms -  1 * 60_000, 1.0)
    # Drive tracker with BALANCED (non-directional) at now_ms so it runs
    # the lockout check on existing closed eps.
    _drive_track("BALANCED", 30000.0, now_ms)

    s = _state()
    assert s["whipsaw_lockout_until_ms"] == now_ms + int(iflow._WHIPSAW_LOCKOUT_MIN * 60_000)
    assert "3 directional eps" in s["whipsaw_lockout_reason"]
    assert "peak<5" in s["whipsaw_lockout_reason"]


def test_two_failed_eps_does_not_trigger_lockout():
    now_ms = 100_000_000
    _push_closed_ep(now_ms - 5 * 60_000, 0.125)
    _push_closed_ep(now_ms - 1 * 60_000, 0.5)
    _drive_track("BALANCED", 30000.0, now_ms)
    assert _state()["whipsaw_lockout_until_ms"] == 0


def test_three_eps_with_peak_at_or_above_5pt_do_not_trigger():
    now_ms = 100_000_000
    _push_closed_ep(now_ms - 10 * 60_000, 8.0)
    _push_closed_ep(now_ms -  5 * 60_000, 5.0)   # exactly threshold = NOT failed
    _push_closed_ep(now_ms -  1 * 60_000, 12.0)
    _drive_track("BALANCED", 30000.0, now_ms)
    assert _state()["whipsaw_lockout_until_ms"] == 0


def test_mixed_failed_and_successful_eps_uses_failed_count_only():
    now_ms = 100_000_000
    _push_closed_ep(now_ms - 12 * 60_000, 0.0)
    _push_closed_ep(now_ms - 10 * 60_000, 8.0)   # success - not counted
    _push_closed_ep(now_ms -  5 * 60_000, 1.0)
    _push_closed_ep(now_ms -  1 * 60_000, 0.25)
    _drive_track("BALANCED", 30000.0, now_ms)
    # 3 failed (0, 1.0, 0.25) within 15 min - lockout triggers.
    assert _state()["whipsaw_lockout_until_ms"] == now_ms + int(iflow._WHIPSAW_LOCKOUT_MIN * 60_000)


def test_eps_outside_15min_lookback_do_not_count():
    now_ms = 100_000_000
    _push_closed_ep(now_ms - 16 * 60_000, 0.0)   # OUTSIDE lookback
    _push_closed_ep(now_ms - 10 * 60_000, 0.0)
    _push_closed_ep(now_ms -  5 * 60_000, 0.0)
    _drive_track("BALANCED", 30000.0, now_ms)
    # Only 2 within lookback - no lockout.
    assert _state()["whipsaw_lockout_until_ms"] == 0


def test_lockout_does_not_retrigger_while_active():
    now_ms = 100_000_000
    s = _state()
    # Pretend a lockout was already set 1 min ago
    s["whipsaw_lockout_until_ms"] = now_ms + 9 * 60_000
    s["whipsaw_lockout_reason"] = "existing"
    _push_closed_ep(now_ms - 5 * 60_000, 0.0)
    _push_closed_ep(now_ms - 4 * 60_000, 0.0)
    _push_closed_ep(now_ms - 3 * 60_000, 0.0)
    _drive_track("BALANCED", 30000.0, now_ms)
    # Should NOT extend or replace existing lockout.
    assert s["whipsaw_lockout_until_ms"] == now_ms + 9 * 60_000
    assert s["whipsaw_lockout_reason"] == "existing"


# ─── episode-tracking behavior ────────────────────────────────────────


def test_directional_tick_opens_episode():
    _drive_track("ACCUMULATION", 30000.0, 1_000_000)
    ep = _state()["whipsaw_open_ep"]
    assert ep is not None
    assert ep["regime"] == "ACCUMULATION"
    assert ep["start_mid"] == 30000.0
    assert ep["peak_favorable_pts"] == 0.0


def test_same_direction_tick_updates_peak_favorable():
    _drive_track("ACCUMULATION", 30000.0, 1_000_000)
    _drive_track("ACCUMULATION", 30010.0, 1_001_000)   # +10pt favorable
    _drive_track("ACCUMULATION", 30003.0, 1_002_000)   # peak should retain 10
    ep = _state()["whipsaw_open_ep"]
    assert ep["peak_favorable_pts"] == 10.0


def test_distribution_peak_uses_negative_sign():
    _drive_track("DISTRIBUTION", 30000.0, 1_000_000)
    _drive_track("DISTRIBUTION", 29985.0, 1_001_000)   # -15pt = +15 favorable for SHORT
    ep = _state()["whipsaw_open_ep"]
    assert ep["peak_favorable_pts"] == 15.0


def test_balanced_closes_episode():
    _drive_track("ACCUMULATION", 30000.0, 1_000_000)
    _drive_track("ACCUMULATION", 30002.0, 1_001_000)
    _drive_track("BALANCED", 30002.0, 1_002_000)
    s = _state()
    assert s["whipsaw_open_ep"] is None
    closed = list(s["whipsaw_closed_eps"])
    assert len(closed) == 1
    assert closed[0]["regime"] == "ACCUMULATION"
    assert closed[0]["peak_favorable_pts"] == 2.0


def test_opposite_directional_closes_then_opens_new():
    _drive_track("ACCUMULATION", 30000.0, 1_000_000)
    _drive_track("DISTRIBUTION", 30000.0, 1_001_000)
    s = _state()
    closed = list(s["whipsaw_closed_eps"])
    assert len(closed) == 1
    assert closed[0]["regime"] == "ACCUMULATION"
    assert s["whipsaw_open_ep"]["regime"] == "DISTRIBUTION"


# ─── end-to-end via compute_institutional_flow ────────────────────────


def _strong_long_inputs(ts_ms: int, mid: float = 30000.0) -> Dict[str, Any]:
    """Snapshot strong enough to produce ACCUMULATION."""
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": mid},
        "or_levels": {"orHigh": 30050.0, "orLow": 29950.0, "levels": []},
        "flow": {"ofiZ": 2.0, "cvdDeltaZ": 2.0, "biasScore": 1.0, "regime": "BULLISH"},
        "pull_stack": {"windows": [
            {"label": "BBO", "bias": "BULLISH", "zScore": 2.0},
            {"label": "1m",  "bias": "BULLISH", "zScore": 2.0},
            {"label": "3m",  "bias": "BULLISH", "zScore": 2.0},
        ]},
        "lt_liquidity": {"ratio": 0.5},
        "micro_events": {"events": []},
        "trend_analyzer": {"warmedUp": True, "fast": {
            "direction": "BULLISH", "directionSign": 1, "chop": False,
        }},
    }


def _flat_inputs(ts_ms: int, mid: float = 30000.0) -> Dict[str, Any]:
    snap = _strong_long_inputs(ts_ms, mid)
    snap["flow"] = {"ofiZ": 0.0, "cvdDeltaZ": 0.0, "biasScore": 0.0, "regime": "BALANCED"}
    snap["pull_stack"] = {"windows": [
        {"label": "BBO", "bias": "NEUTRAL", "zScore": 0.0},
        {"label": "1m",  "bias": "NEUTRAL", "zScore": 0.0},
        {"label": "3m",  "bias": "NEUTRAL", "zScore": 0.0},
    ]}
    snap["lt_liquidity"] = {"ratio": 0.0}
    snap["trend_analyzer"] = {"warmedUp": True, "fast": {
        "direction": "NEUTRAL", "directionSign": 0, "chop": False,
    }}
    return snap


def test_return_dict_has_lockout_fields_default_zero():
    snap = _flat_inputs(1_700_000_000_000)
    out = iflow.compute_institutional_flow(snap, alias=_ALIAS)
    assert out["whipsaw_lockout_until_ms"] == 0
    assert out["whipsaw_lockout_reason"] == ""


def test_lockout_demotes_directional_regime_to_balanced():
    s = _state()
    base_ms = 1_700_000_000_000
    # Hand-set the lockout 10 min into the future to simulate an active lockout.
    s["whipsaw_lockout_until_ms"] = base_ms + 10 * 60_000
    s["whipsaw_lockout_reason"] = "test-suppression"

    # Strong bullish snap that would otherwise produce ACCUMULATION.
    snap = _strong_long_inputs(base_ms)
    out = iflow.compute_institutional_flow(snap, alias=_ALIAS)
    assert out["regime"] == "BALANCED"
    assert out["whipsaw_lockout_until_ms"] == base_ms + 10 * 60_000
    assert out["whipsaw_lockout_reason"] == "test-suppression"


def test_lockout_expires_after_window():
    s = _state()
    base_ms = 1_700_000_000_000
    # Lockout that EXPIRED 1 ms ago.
    s["whipsaw_lockout_until_ms"] = base_ms - 1
    s["whipsaw_lockout_reason"] = "stale-lockout"

    snap = _strong_long_inputs(base_ms)
    out = iflow.compute_institutional_flow(snap, alias=_ALIAS)
    # Lockout no longer suppresses; regime can fire normally.
    assert out["regime"] == "ACCUMULATION"


def test_lockout_does_not_block_already_balanced():
    s = _state()
    base_ms = 1_700_000_000_000
    s["whipsaw_lockout_until_ms"] = base_ms + 5 * 60_000

    snap = _flat_inputs(base_ms)
    out = iflow.compute_institutional_flow(snap, alias=_ALIAS)
    # Already BALANCED; lockout doesn't change it.
    assert out["regime"] == "BALANCED"


def test_three_failed_directional_eps_via_compute_triggers_lockout():
    """End-to-end: drive 3 short directional eps that each close STALE
    via mid not moving, then verify lockout fires on the 4th BALANCED
    tick that closes the third ep."""
    base_ms = 1_700_000_000_000

    # Helper: open an ACCUM via strong-long snap, close via flat snap.
    def drive_one_failed_ep(ep_start_ms: int) -> None:
        snap_open = _strong_long_inputs(ep_start_ms, mid=30000.0)
        iflow.compute_institutional_flow(snap_open, alias=_ALIAS)
        # Close 5s later with BALANCED and mid unchanged (peak_fav stays 0)
        snap_close = _flat_inputs(ep_start_ms + 5_000, mid=30000.0)
        iflow.compute_institutional_flow(snap_close, alias=_ALIAS)

    # Three failed eps spaced 5 min apart well within 15 min lookback.
    drive_one_failed_ep(base_ms)
    drive_one_failed_ep(base_ms + 5 * 60_000)
    drive_one_failed_ep(base_ms + 10 * 60_000)

    s = _state()
    # After the 3rd close, lockout should be set.
    assert s["whipsaw_lockout_until_ms"] > base_ms + 10 * 60_000
    assert "3 directional eps" in s["whipsaw_lockout_reason"]
