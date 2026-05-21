"""Pin the per-level touch-state machine in isolation.

Exercises _thesis_classify_touch_state with synthesized poll histories —
no full snapshot needed.

Sign convention: dist_pts = mid - level_price. For side='above', breakout
direction is positive distance; for side='below' breakout direction is
negative distance.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    _thesis_classify_touch_state,
    _TOUCH_TICKS,
    _ACCEPT_HOLD_POLLS,
    _APPROACH_PROX_PTS,
    _REJECT_BACKOFF_PTS,
    NQ_TICK,
)


def _hist(*entries):
    return list(entries)


def test_far_from_level_returns_empty_state():
    state, reasons = _thesis_classify_touch_state(
        side="above", curr_dist_pts=50.0, prior_history=[],
    )
    assert state == ""
    assert isinstance(reasons, list)


def test_approaching_within_prox_but_no_touch_yet():
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-10.0, prior_history=[],
    )
    assert state == "APPROACHING"


def test_touched_when_within_one_tick():
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=0.10, prior_history=[],
    )
    assert state == "TOUCHED"


def test_accepted_above_requires_consecutive_polls_past_level():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched
        {"ts_ms": 2000, "dist_pts": 2.50},   # one poll past
        {"ts_ms": 3000, "dist_pts": 3.50},   # second poll past
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=4.50, prior_history=history,
    )
    assert state == "ACCEPTED_ABOVE"


def test_accepted_above_not_yet_when_only_one_poll_past():
    history = _hist({"ts_ms": 1000, "dist_pts": 0.10})  # touched last poll
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=2.50, prior_history=history,
    )
    # one poll past is not yet acceptance.
    assert state in ("TOUCHED", "APPROACHING", "FAILED_BREAK")
    assert state != "ACCEPTED_ABOVE"


def test_accepted_below_for_below_side_level():
    # For OR-L (side='below') price moving DOWN past the level
    history = _hist(
        {"ts_ms": 1000, "dist_pts": -0.10},   # touched
        {"ts_ms": 2000, "dist_pts": -2.50},   # one poll past
        {"ts_ms": 3000, "dist_pts": -3.50},   # second poll past
    )
    state, _ = _thesis_classify_touch_state(
        side="below", curr_dist_pts=-4.50, prior_history=history,
    )
    assert state == "ACCEPTED_BELOW"


def test_rejected_when_touched_then_reverses_back_into_range():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched OR-H
    )
    # current poll: well inside the range
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-40.0, prior_history=history,
    )
    assert state == "REJECTED"


def test_failed_break_when_pushed_past_then_reverses_before_accept():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},   # touched
        {"ts_ms": 2000, "dist_pts": 2.50},   # one poll past
        # current poll reverses to negative but not past the REJECT backoff
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-5.0, prior_history=history,
    )
    assert state == "FAILED_BREAK"


def test_retest_hold_after_acceptance():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},
        {"ts_ms": 2000, "dist_pts": 4.00},
        {"ts_ms": 3000, "dist_pts": 4.00},
        {"ts_ms": 4000, "dist_pts": 0.20},   # came back to retest
    )
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=2.50, prior_history=history,
        prior_state="ACCEPTED_ABOVE",
    )
    assert state == "RETEST_HOLD"


def test_retest_fail_after_acceptance_then_cross_back():
    history = _hist(
        {"ts_ms": 1000, "dist_pts": 0.10},
        {"ts_ms": 2000, "dist_pts": 4.00},
        {"ts_ms": 3000, "dist_pts": 4.00},
        {"ts_ms": 4000, "dist_pts": 0.20},
    )
    # current poll: well inside the range, below the REJECT backoff
    state, _ = _thesis_classify_touch_state(
        side="above", curr_dist_pts=-50.0, prior_history=history,
        prior_state="ACCEPTED_ABOVE",
    )
    assert state == "RETEST_FAIL"


def test_constants_are_sane():
    assert _TOUCH_TICKS == 1.0
    assert _ACCEPT_HOLD_POLLS == 2
    assert _APPROACH_PROX_PTS > 0
    assert _REJECT_BACKOFF_PTS > 0
    assert NQ_TICK == 0.25
