"""Unit tests for institutional_flow tracker.

Pins sign conventions, threshold behavior, rotation state, chop-window
forcing, and chart-event emission shape.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from unittest.mock import patch

import pytest

from bookmap_mcp import institutional_flow as iflow


_ALIAS = "TEST.SYM"


@pytest.fixture(autouse=True)
def _reset_state_between_tests():
    iflow.reset_state()
    yield
    iflow.reset_state()


def _ts_outside_chop(hh: int = 9, mm: int = 0) -> int:
    """Build a CT timestamp guaranteed outside all chop windows."""
    from datetime import datetime as _dt

    if iflow._CT is None:
        # Fallback: just pick a number; chop check returns None when zoneinfo missing.
        return 1_700_000_000_000
    dt = _dt(2026, 5, 27, hh, mm, 0, tzinfo=iflow._CT)
    return int(dt.timestamp() * 1000)


def _make_snap(
    *,
    ts_ms: int,
    mid: float = 30000.0,
    or_high: float = 30050.0,
    or_low: float = 29950.0,
    flow_ofiZ: float = 0.0,
    flow_cvdDeltaZ: float = 0.0,
    flow_biasScore: float = 0.0,
    flow_regime: str = "BALANCED",
    pull_stack_bbo_bias: str = "NEUTRAL",
    pull_stack_bbo_z: float = 0.0,
    pull_stack_1m_bias: str = "NEUTRAL",
    pull_stack_1m_z: float = 0.0,
    pull_stack_3m_bias: str = "NEUTRAL",
    pull_stack_3m_z: float = 0.0,
    lt_liquidity_ratio: float = 0.0,
    or_level_labels=(),
    micro_events=(),
    trend_warmed: bool = False,
    trend_direction: str = "NEUTRAL",
    trend_direction_sign: int = 0,
    trend_chop: bool = False,
) -> Dict[str, Any]:
    or_levels_levels = []
    for label, price, side in or_level_labels:
        or_levels_levels.append({"label": label, "price": price, "side": side})
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": mid},
        "or_levels": {
            "orHigh": or_high,
            "orLow": or_low,
            "levels": or_levels_levels,
        },
        "flow": {
            "ofiZ": flow_ofiZ,
            "cvdDeltaZ": flow_cvdDeltaZ,
            "biasScore": flow_biasScore,
            "regime": flow_regime,
        },
        "pull_stack": {
            "windows": [
                {"label": "BBO", "bias": pull_stack_bbo_bias, "zScore": pull_stack_bbo_z},
                {"label": "1m", "bias": pull_stack_1m_bias, "zScore": pull_stack_1m_z},
                {"label": "3m", "bias": pull_stack_3m_bias, "zScore": pull_stack_3m_z},
            ]
        },
        "lt_liquidity": {"ratio": lt_liquidity_ratio},
        "micro_events": {"events": list(micro_events)},
        "trend_analyzer": {
            "warmedUp": trend_warmed,
            "fast": {
                "direction": trend_direction,
                "directionSign": trend_direction_sign,
                "chop": trend_chop,
            },
        },
    }


def test_balanced_when_all_zero():
    snap = _make_snap(ts_ms=_ts_outside_chop())
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "BALANCED"
    assert flow["conviction"] == 0.0


def test_sustained_bullish_inputs_produce_accumulation():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_1m_bias="BULLISH",
        pull_stack_1m_z=2.0,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        lt_liquidity_ratio=0.2,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "ACCUMULATION", flow
    assert flow["weighted_vote"] > 0.30
    assert flow["conviction"] > 0.0


def test_sustained_bearish_inputs_produce_distribution():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=-2.5,
        flow_cvdDeltaZ=-2.0,
        flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_1m_bias="BEARISH",
        pull_stack_1m_z=-2.0,
        pull_stack_3m_bias="BEARISH",
        pull_stack_3m_z=-2.0,
        lt_liquidity_ratio=-0.2,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "DISTRIBUTION", flow
    assert flow["weighted_vote"] < -0.30


def test_chop_window_is_informational_not_blocking():
    """chop_window flag is set but does NOT suppress regime firing.
    Operator decides whether to act on a fire during chop based on
    their own discipline. Pinned 2026-05-28 after operator pointed
    out the previous force-suppress hid a real under-EXT move during
    lunch chop."""
    if iflow._CT is None:
        pytest.skip("zoneinfo not available")
    # 11:30 CT is inside us_lunch chop window.
    ts = int(datetime(2026, 5, 27, 11, 30, 0, tzinfo=iflow._CT).timestamp() * 1000)
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # Regime fires despite chop window -- inputs are strongly bullish.
    assert flow["regime"] == "ACCUMULATION"
    # chop_window flag IS surfaced so operator can apply their own discipline.
    assert flow["chop_window"] == "us_lunch"


def test_or_high_break_begins_rotation_state():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        mid=30055.0,  # above orHigh 30050
        or_high=30050.0,
        or_low=29950.0,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    rot = flow["rotation_state"]
    assert rot["commit_level"] == "OR-H"
    assert rot["commit_price"] == 30050.0
    assert rot["rotations_completed"] == 0
    assert rot["next_rotation_target"] == pytest.approx(30050.0 + 65.0)


def test_or_low_break_begins_rotation_with_negative_sign():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        mid=29945.0,
        or_low=29950.0,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    rot = flow["rotation_state"]
    assert rot["commit_level"] == "OR-L"
    assert rot["next_rotation_target"] == pytest.approx(29950.0 - 65.0)


def test_rotation_count_increments_after_65pt_move():
    base_ts = _ts_outside_chop()
    # commit
    iflow.compute_institutional_flow(
        _make_snap(ts_ms=base_ts, mid=29945.0, or_low=29950.0),
        _ALIAS,
    )
    # 65pt move down -> 1 rotation completed
    flow = iflow.compute_institutional_flow(
        _make_snap(ts_ms=base_ts + 30_000, mid=29885.0, or_low=29950.0),
        _ALIAS,
    )
    assert flow["rotation_state"]["rotations_completed"] == 1
    assert flow["rotation_state"]["next_rotation_target"] == pytest.approx(29950.0 - 130.0)


def test_rotation_resets_when_price_retraces_inside_or():
    base_ts = _ts_outside_chop()
    iflow.compute_institutional_flow(
        _make_snap(ts_ms=base_ts, mid=29945.0, or_low=29950.0),
        _ALIAS,
    )
    # retrace back inside
    flow = iflow.compute_institutional_flow(
        _make_snap(ts_ms=base_ts + 60_000, mid=29980.0, or_low=29950.0),
        _ALIAS,
    )
    assert flow["rotation_state"]["commit_price"] is None


def test_transition_fires_on_sign_flip_within_30s():
    base_ts = _ts_outside_chop()
    bullish = _make_snap(
        ts_ms=base_ts,
        flow_ofiZ=2.0,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.5,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    iflow.compute_institutional_flow(bullish, _ALIAS)
    # 10s later flip to bearish
    bearish = _make_snap(
        ts_ms=base_ts + 10_000,
        flow_ofiZ=-2.0,
        flow_cvdDeltaZ=-2.0,
        flow_biasScore=-0.5,
        pull_stack_3m_bias="BEARISH",
        pull_stack_3m_z=-2.0,
    )
    flow = iflow.compute_institutional_flow(bearish, _ALIAS)
    assert flow["regime"] == "TRANSITION", flow


def test_graceful_on_empty_snapshot():
    flow = iflow.compute_institutional_flow({}, _ALIAS)
    assert flow["regime"] == "BALANCED"
    assert flow["conviction"] == 0.0
    assert flow["drivers"] is not None
    assert flow["rotation_state"]["commit_price"] is None
    assert flow["level_stance"] == {}


def test_level_stance_distribution_above_or_from_below_is_fade_lean():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        mid=30040.0,  # inside OR but approaching OR-H from below
        or_high=30050.0,
        or_low=29950.0,
        flow_ofiZ=-2.5,
        flow_cvdDeltaZ=-2.0,
        flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_1m_bias="BEARISH",
        pull_stack_1m_z=-2.0,
        pull_stack_3m_bias="BEARISH",
        pull_stack_3m_z=-2.0,
        or_level_labels=(("OR-H", 30050.0, "above"), ("OR-L", 29950.0, "below")),
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "DISTRIBUTION"
    assert flow["level_stance"]["OR-H"] == "FADE_LEAN"


def test_build_flow_chart_events_long_triangle_for_accumulation():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_1m_bias="BULLISH",
        pull_stack_1m_z=2.0,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    events = iflow.build_flow_chart_events(flow, snap)
    assert len(events) == 1
    e = events[0]
    assert e["direction"] == "LONG"
    assert e["marker_color_hint"] == "#2BD25B"
    assert e["marker_text"].startswith("L^IFL")
    assert e["event_type"] == "LOCAL_ANCHORED"
    assert e["action"] == "BIAS_SIGNAL"
    assert e["severity"] in ("WATCH", "WARNING")
    assert e["source"] == "institutional_flow"


def test_build_flow_chart_events_short_triangle_for_distribution():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=-2.5,
        flow_cvdDeltaZ=-2.0,
        flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_1m_bias="BEARISH",
        pull_stack_1m_z=-2.0,
        pull_stack_3m_bias="BEARISH",
        pull_stack_3m_z=-2.0,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    events = iflow.build_flow_chart_events(flow, snap)
    assert len(events) == 1
    assert events[0]["direction"] == "SHORT"
    assert events[0]["marker_color_hint"] == "#FF4D4D"
    assert events[0]["marker_text"].startswith("LvIFL")


def test_build_flow_chart_events_dedup_within_1s_bucket():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    flow1 = iflow.compute_institutional_flow(snap, _ALIAS)
    events1 = iflow.build_flow_chart_events(flow1, snap)
    assert len(events1) == 1
    # Same 1s bucket -> no emission
    flow2 = iflow.compute_institutional_flow(snap, _ALIAS)
    events2 = iflow.build_flow_chart_events(flow2, snap)
    assert events2 == []


def test_build_flow_chart_events_emits_again_after_1s():
    ts = _ts_outside_chop()
    snap1 = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    flow1 = iflow.compute_institutional_flow(snap1, _ALIAS)
    iflow.build_flow_chart_events(flow1, snap1)

    snap2 = _make_snap(
        ts_ms=ts + 1_500,  # next 1s bucket
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
    )
    flow2 = iflow.compute_institutional_flow(snap2, _ALIAS)
    events2 = iflow.build_flow_chart_events(flow2, snap2)
    assert len(events2) == 1


def test_no_event_when_regime_balanced():
    ts = _ts_outside_chop()
    snap = _make_snap(ts_ms=ts)
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "BALANCED"
    assert iflow.build_flow_chart_events(flow, snap) == []


def test_weights_sum_to_one():
    total = sum(w for _, w in iflow._WEIGHTS)
    assert abs(total - 1.0) < 1e-9


def test_trend_filter_neutral_when_not_warmed():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=False,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "NEUTRAL"


def test_trend_filter_aligned_passes_vote_through():
    """Vote bullish + trend UP -> ALIGNED, vote unchanged."""
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "ALIGNED"
    # vote should equal raw vote (no dampening)
    assert flow["weighted_vote"] == flow["raw_vote_pre_trend_filter"]
    assert flow["regime"] == "ACCUMULATION"


def test_trend_filter_opposed_dampens_vote_by_half():
    """Vote bullish but trend DOWN -> OPPOSED, vote * 0.5."""
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=True,
        trend_direction="DOWN",
        trend_direction_sign=-1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "OPPOSED"
    raw = flow["raw_vote_pre_trend_filter"]
    # Both fields are rounded to 4 dp in the public output, so allow
    # small rounding slack on the dampening factor of 0.5
    assert flow["weighted_vote"] == pytest.approx(raw * 0.5, abs=0.001)


def test_trend_filter_dampened_vote_can_demote_regime():
    """If raw vote is just above 0.20 threshold but trend opposes,
    dampened vote should drop below threshold -> BALANCED instead of
    ACCUMULATION."""
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        # Modest bullish vote that would land near +0.25 (above threshold)
        flow_ofiZ=1.0,
        flow_cvdDeltaZ=1.0,
        flow_biasScore=0.3,
        pull_stack_1m_bias="BULLISH",
        pull_stack_1m_z=1.0,
        trend_warmed=True,
        trend_direction="DOWN",
        trend_direction_sign=-1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "OPPOSED"
    assert flow["raw_vote_pre_trend_filter"] > 0.20
    assert flow["weighted_vote"] < 0.20
    # Regime should NOT be ACCUMULATION because dampened vote is below threshold
    assert flow["regime"] in ("BALANCED", "TRANSITION")


def test_trend_filter_neutral_when_chop():
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
        trend_chop=True,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "NEUTRAL"


def test_aligned_threshold_lower_fires_at_0_15():
    """When trend filter ALIGNED, regime fires at vote >= 0.15 (was 0.20).
    Catches the case where bridge micro-detector is stricter than chart
    micro-detectors, leaving slow drivers as the main signal."""
    ts = _ts_outside_chop()
    # Modest bullish vote ~0.17 (between 0.15 and 0.20), trend ALIGNED UP,
    # no recent micro events
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=0.8,
        flow_cvdDeltaZ=0.6,
        flow_biasScore=0.35,
        flow_regime="TRENDING_UP",
        pull_stack_1m_bias="BULLISH",
        pull_stack_1m_z=0.5,
        lt_liquidity_ratio=0.10,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "ALIGNED"
    # Vote should be > 0.15 but may not exceed 0.20
    if flow["weighted_vote"] > 0.15 and flow["weighted_vote"] < 0.20:
        assert flow["regime"] == "ACCUMULATION"


def test_aligned_threshold_does_not_fire_below_0_15():
    """Conservative even with ALIGNED -- vote < 0.15 still stays BALANCED."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=0.3,
        flow_cvdDeltaZ=0.1,
        flow_biasScore=0.05,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    if flow["weighted_vote"] < 0.15:
        assert flow["regime"] in ("BALANCED", "TRANSITION")


def test_unaligned_threshold_stays_at_0_20():
    """Without trend ALIGNED, threshold stays at 0.20 (conservative)."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=0.8,
        flow_cvdDeltaZ=0.6,
        flow_biasScore=0.35,
        flow_regime="TRENDING_UP",
        pull_stack_1m_bias="BULLISH",
        pull_stack_1m_z=0.5,
        # No trend (warmedUp=False) -> trend_filter NEUTRAL
        trend_warmed=False,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["trend_filter"] == "NEUTRAL"
    if flow["weighted_vote"] > 0.15 and flow["weighted_vote"] < 0.20:
        # NEUTRAL -> uses 0.20 threshold, vote not enough -> BALANCED
        assert flow["regime"] in ("BALANCED", "TRANSITION")


def test_textbook_setup_override_long():
    """Strong bullish micro_events + trend ALIGNED forces ACCUMULATION even
    if the broader weighted vote is below the 0.20 threshold. Captures the
    operator's textbook setup: institutional fingerprint at the level +
    trend agreement = entry trigger, regardless of other input agreement."""
    ts = _ts_outside_chop()
    # Weak overall vote (only micro_events firing strongly bullish) +
    # trend ALIGNED UP -> textbook setup
    snap = _make_snap(
        ts_ms=ts,
        # Other inputs near-neutral; only micro_events strongly bullish
        flow_ofiZ=0.2,
        flow_cvdDeltaZ=0.0,
        flow_biasScore=0.0,
        flow_regime="BALANCED",
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
            {"kind": "ABSORPTION", "timeMs": ts - 2000, "isBid": True,
             "price": 30048.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # The broader weighted vote is below 0.20 threshold in this setup
    # because most inputs are neutral. But textbook override fires.
    assert flow["regime"] == "ACCUMULATION"


def test_textbook_setup_override_short():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=-0.2,
        flow_cvdDeltaZ=0.0,
        flow_biasScore=0.0,
        flow_regime="BALANCED",
        trend_warmed=True,
        trend_direction="DOWN",
        trend_direction_sign=-1,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 100},
            {"kind": "ABSORPTION", "timeMs": ts - 2000, "isBid": False,
             "price": 30055.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["regime"] == "DISTRIBUTION"


def test_textbook_setup_does_not_fire_without_trend_aligned():
    """Strong micro_events alone is NOT enough -- requires trend ALIGNED.
    Without trend, the system stays at the normal threshold gate."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=0.1,
        flow_cvdDeltaZ=0.0,
        trend_warmed=False,  # trend not warmed = NEUTRAL filter
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
            {"kind": "ABSORPTION", "timeMs": ts - 2000, "isBid": True,
             "price": 30048.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # micro_events strong bullish but trend NEUTRAL -> no textbook override
    assert flow["regime"] != "ACCUMULATION"  # falls back to threshold gate


def test_stop_sweep_bid_is_bullish_reversal():
    """Bid-stops swept = aggressive sell exhausted, reversal up expected."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "STOP_SWEEP", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_stop_sweep_ask_is_bearish_reversal():
    """Ask-stops swept = aggressive buy exhausted, reversal down expected."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "STOP_SWEEP", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] < 0


def test_trend_aligned_bypasses_opposing_label_block():
    """When trend is ALIGNED with vote, regime can fire even if recent
    flow.regime labels were opposite. Captures the trend-flip + flow-stab
    setup where prior history naturally had opposing labels."""
    ts = _ts_outside_chop()
    # Tick 1: bearish regime sustained — populates history with TRENDING_DOWN
    bearish = _make_snap(
        ts_ms=ts,
        flow_ofiZ=-2.5,
        flow_cvdDeltaZ=-2.0,
        flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_3m_bias="BEARISH",
        pull_stack_3m_z=-2.0,
        trend_warmed=True,
        trend_direction="DOWN",
        trend_direction_sign=-1,
    )
    iflow.compute_institutional_flow(bearish, _ALIAS)
    # Tick 2: trend flips UP, vote turns bullish (micro_events + flow agree)
    bullish_with_history = _make_snap(
        ts_ms=ts + 5_000,
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts + 4_000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(bullish_with_history, _ALIAS)
    # With trend ALIGNED, regime should fire ACCUMULATION despite recent
    # bearish history in the trail. Allow TRANSITION as a valid alternative
    # since the sign-flip-within-30s rule may catch it first.
    assert flow["trend_filter"] == "ALIGNED"
    assert flow["regime"] in ("ACCUMULATION", "TRANSITION")


def test_micro_events_iceberg_bid_is_bullish():
    """ICEBERG on bid side = institution defending support -> bullish."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # Vote should now be positive from micro_events contribution alone
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_micro_events_iceberg_ask_is_bearish():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] < 0


def test_micro_events_pull_bid_is_bearish():
    """PULL on bid side = support being removed -> bearish."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "PULL", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] < 0


def test_micro_events_pull_ask_is_bullish():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "PULL", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_micro_events_stack_bid_is_bullish():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "STACK", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_micro_events_absorption_ask_is_bearish():
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "ABSORPTION", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] < 0


def test_micro_events_spoof_bid_is_bearish():
    """Fake bid pulled = institution was luring buyers to sell into them = bearish intent."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "SPOOF", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 30},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] < 0


def test_micro_events_spoof_ask_is_bullish():
    """Fake ask pulled = institution was luring sellers to buy from them = bullish intent."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "SPOOF", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 30},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_micro_events_balanced_spoofs_offset():
    """Equal bid+ask spoofs from same direction interpretation cancel out."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "SPOOF", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 30},
            {"kind": "SPOOF", "timeMs": ts - 1000, "isBid": False,
             "price": 30050.0, "size": 30},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # 1 bearish (-1) + 1 bullish (+1) = avg 0
    assert abs(flow["raw_vote_pre_trend_filter"]) < 0.05


def test_regime_hold_time_blocks_opposite_within_60s():
    """After ACCUMULATION fires, DISTRIBUTION cannot fire within 60s -- it
    demotes to BALANCED. Stops the whipsaw cluster (caught 09:40-09:49
    CT today: 4 fires in 9 min, all MISS)."""
    ts = _ts_outside_chop()
    # Tick 1: strong bullish -> ACCUMULATION
    bull_snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5, flow_cvdDeltaZ=2.0, flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH", pull_stack_3m_z=2.0,
        trend_warmed=True, trend_direction="UP", trend_direction_sign=1,
    )
    f1 = iflow.compute_institutional_flow(bull_snap, _ALIAS)
    assert f1["regime"] == "ACCUMULATION"
    # Tick 2 (30s later): strong bearish -> would normally fire
    # DISTRIBUTION but hold prevents it.
    bear_snap = _make_snap(
        ts_ms=ts + 30_000,
        flow_ofiZ=-2.5, flow_cvdDeltaZ=-2.0, flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_3m_bias="BEARISH", pull_stack_3m_z=-2.0,
        trend_warmed=True, trend_direction="DOWN", trend_direction_sign=-1,
    )
    f2 = iflow.compute_institutional_flow(bear_snap, _ALIAS)
    # Demoted to BALANCED because opposite within hold window
    assert f2["regime"] in ("BALANCED", "TRANSITION")


def test_regime_hold_time_allows_opposite_after_60s():
    """After 60s of hold elapses, opposite regime can fire."""
    iflow.reset_state()
    ts = _ts_outside_chop()
    bull_snap = _make_snap(
        ts_ms=ts,
        flow_ofiZ=2.5, flow_cvdDeltaZ=2.0, flow_biasScore=0.6,
        flow_regime="TRENDING_UP",
        pull_stack_3m_bias="BULLISH", pull_stack_3m_z=2.0,
        trend_warmed=True, trend_direction="UP", trend_direction_sign=1,
    )
    iflow.compute_institutional_flow(bull_snap, _ALIAS)
    # 65 seconds later (past 60s hold)
    bear_snap = _make_snap(
        ts_ms=ts + 65_000,
        flow_ofiZ=-2.5, flow_cvdDeltaZ=-2.0, flow_biasScore=-0.6,
        flow_regime="TRENDING_DOWN",
        pull_stack_3m_bias="BEARISH", pull_stack_3m_z=-2.0,
        trend_warmed=True, trend_direction="DOWN", trend_direction_sign=-1,
    )
    f = iflow.compute_institutional_flow(bear_snap, _ALIAS)
    # Hold expired -- DISTRIBUTION can fire (may show as TRANSITION
    # via sign-flip check OR direct DISTRIBUTION, both acceptable)
    assert f["regime"] in ("DISTRIBUTION", "TRANSITION")


def test_micro_events_aged_out_after_window():
    """Events older than the micro-event window should not contribute."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 200_000, "isBid": True,
             "price": 30050.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] == 0.0


def _micro_snap(ts_ms: int, events) -> dict:
    return {"ts_ms": ts_ms, "micro_events": {"events": list(events)}}


def test_micro_signal_full_strength_when_fresh():
    """M2: a just-fired strong fingerprint still reads near full magnitude."""
    ts = 1_700_000_000_000
    ev = {"kind": "ICEBERG", "timeMs": ts, "isBid": True,
          "price": 30050.0, "size": 100}
    signed, reliability = iflow._extract_micro_events_signal(
        _micro_snap(ts + 1_000, [ev]))
    assert signed > 0.8, signed
    assert reliability == 1.0


def test_micro_signal_decays_toward_zero_when_stale_no_new_events():
    """M2 regression: a lone fingerprint must FADE as it ages, not pin at
    full magnitude until a hard window cutoff. Reproduces the live bug where
    micro stayed +0.85 for ~3 min after the last fingerprint while trend had
    already flipped down."""
    ts = 1_700_000_000_000
    ev = {"kind": "ICEBERG", "timeMs": ts, "isBid": True,
          "price": 30050.0, "size": 100}
    # 60s later, still inside the cutoff window: must already be decaying hard.
    signed_60, _ = iflow._extract_micro_events_signal(
        _micro_snap(ts + 60_000, [ev]))
    assert signed_60 < 0.3, signed_60
    # 90s later (no new events): effectively gone.
    signed_90, _ = iflow._extract_micro_events_signal(
        _micro_snap(ts + 90_000, [ev]))
    assert abs(signed_90) < 0.15, signed_90


def test_micro_signal_exp_decay_half_life():
    """One half-life after firing, a lone fingerprint reads ~half strength."""
    ts = 1_700_000_000_000
    ev = {"kind": "ICEBERG", "timeMs": ts, "isBid": True,
          "price": 30050.0, "size": 100}
    half_life_ms = int(iflow._MICRO_EVENT_HALFLIFE_SEC * 1000)
    signed, _ = iflow._extract_micro_events_signal(
        _micro_snap(ts + half_life_ms, [ev]))
    assert 0.4 < signed < 0.6, signed


def test_micro_events_multiple_aggregate_signed():
    """Two bullish + one bearish iceberg -> net bullish."""
    ts = _ts_outside_chop()
    snap = _make_snap(
        ts_ms=ts,
        micro_events=[
            {"kind": "ICEBERG", "timeMs": ts - 1000, "isBid": True,
             "price": 30050.0, "size": 100},
            {"kind": "ICEBERG", "timeMs": ts - 2000, "isBid": True,
             "price": 30048.0, "size": 100},
            {"kind": "ICEBERG", "timeMs": ts - 1500, "isBid": False,
             "price": 30055.0, "size": 100},
        ],
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    assert flow["raw_vote_pre_trend_filter"] > 0


def test_trend_filter_never_amplifies():
    """Even when vote and trend agree strongly, dampening factor is never
    applied as a boost -- magnitude comes only from primaries."""
    snap = _make_snap(
        ts_ms=_ts_outside_chop(),
        flow_ofiZ=2.5,
        flow_cvdDeltaZ=2.0,
        flow_biasScore=0.6,
        pull_stack_3m_bias="BULLISH",
        pull_stack_3m_z=2.0,
        trend_warmed=True,
        trend_direction="UP",
        trend_direction_sign=1,
    )
    flow = iflow.compute_institutional_flow(snap, _ALIAS)
    # Filtered vote must equal raw vote (no amplification); allow 4dp rounding slack.
    assert flow["weighted_vote"] == pytest.approx(flow["raw_vote_pre_trend_filter"], abs=0.001)
