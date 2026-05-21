"""Pin liquidity-quality, thesis selector, aggressor_flow, book_state,
and execution_read classifiers in isolation.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    _thesis_liquidity_quality,
    _thesis_select_thesis,
    _thesis_aggressor_flow,
    _thesis_book_state,
    _thesis_execution_read,
    NQ_TICK,
)


def _micro_events(*kind_side_pairs, price=20000.0):
    """Build a fake snap['micro_events'] dict."""
    return {
        "events": [
            {"kind": k, "isBid": b, "price": price, "size": 100, "timeMs": 1}
            for (k, b) in kind_side_pairs
        ],
    }


# -- Liquidity quality ------------------------------------------------------

def test_liquidity_iceberg_defended_at_ask_above():
    me_obj = _micro_events(("ICEBERG", False), price=20000.0)  # ask iceberg
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "ICEBERG_DEFENDED"


def test_liquidity_iceberg_defended_at_bid_below():
    me_obj = _micro_events(("ICEBERG", True), price=19500.0)  # bid iceberg
    lq, _ = _thesis_liquidity_quality(
        side="below", price=19500.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "ICEBERG_DEFENDED"


def test_liquidity_iceberg_on_wrong_side_not_defender():
    # An ASK iceberg at OR-L (below) is NOT a defender of the level.
    me_obj = _micro_events(("ICEBERG", False), price=19500.0)
    lq, _ = _thesis_liquidity_quality(
        side="below", price=19500.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq != "ICEBERG_DEFENDED"


def test_liquidity_spoof_risk_dominates_iceberg():
    me_obj = _micro_events(("SPOOF", False), ("ICEBERG", False), price=20000.0)
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "SPOOF_RISK"


def test_liquidity_real_default():
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq == "REAL"


def test_liquidity_event_outside_window_ignored():
    # Event 10 ticks (2.5 pts) away from level, but band is 4 ticks (1.0 pts)
    me_obj = _micro_events(("ICEBERG", False), price=20002.5)
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=me_obj,
        ps_obj=None, lt_obj=None, tape_obj=None,
    )
    assert lq != "ICEBERG_DEFENDED"


# -- Thesis selector --------------------------------------------------------

def test_thesis_acceptance_long_when_accepted_above():
    t, _ = _thesis_select_thesis(
        state="ACCEPTED_ABOVE", side="above",
        liquidity="REAL", aggressor="WITH",
        me_obj=None, price=20000.0,
    )
    assert t == "ACCEPTANCE_LONG"


def test_thesis_acceptance_short_when_accepted_below():
    t, _ = _thesis_select_thesis(
        state="ACCEPTED_BELOW", side="below",
        liquidity="REAL", aggressor="WITH",
        me_obj=None, price=19500.0,
    )
    assert t == "ACCEPTANCE_SHORT"


def test_thesis_iceberg_defense_blocks_acceptance_long_at_or_h():
    me_obj = _micro_events(("ICEBERG", False), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "ICEBERG_DEFENSE"


def test_thesis_iceberg_defense_blocks_acceptance_short_at_or_l():
    me_obj = _micro_events(("ICEBERG", True), price=19500.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="below",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
        me_obj=me_obj, price=19500.0,
    )
    assert t == "ICEBERG_DEFENSE"


def test_thesis_rejection_short_at_upper_level():
    t, _ = _thesis_select_thesis(
        state="REJECTED", side="above",
        liquidity="REAL", aggressor="AGAINST",
        me_obj=None, price=20000.0,
    )
    assert t == "REJECTION_SHORT"


def test_thesis_rejection_long_at_lower_level():
    t, _ = _thesis_select_thesis(
        state="REJECTED", side="below",
        liquidity="REAL", aggressor="AGAINST",
        me_obj=None, price=19500.0,
    )
    assert t == "REJECTION_LONG"


def test_thesis_stop_sweep_continuation_at_touched_with_sweep_event():
    me_obj = _micro_events(("STOP_SWEEP", False), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="REAL", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "STOP_SWEEP_CONTINUATION"


def test_thesis_stop_sweep_failure_after_failed_break_with_sweep_event():
    me_obj = _micro_events(("STOP_SWEEP", False), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="FAILED_BREAK", side="above",
        liquidity="REAL", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "STOP_SWEEP_FAILURE"


def test_thesis_spoof_risk_yields_none_thesis_not_false_continuation():
    me_obj = _micro_events(("SPOOF", True), price=20000.0)
    t, _ = _thesis_select_thesis(
        state="TOUCHED", side="above",
        liquidity="SPOOF_RISK", aggressor="WITH",
        me_obj=me_obj, price=20000.0,
    )
    assert t == "NONE"


# -- aggressor_flow ----------------------------------------------------------

def test_aggressor_with_for_above_breakout_positive_delta():
    af, _ = _thesis_aggressor_flow(side="above", tape_obj={"deltaScore": 0.45})
    assert af == "WITH"


def test_aggressor_against_for_above_with_negative_delta():
    af, _ = _thesis_aggressor_flow(side="above", tape_obj={"deltaScore": -0.45})
    assert af == "AGAINST"


def test_aggressor_with_for_below_with_negative_delta():
    af, _ = _thesis_aggressor_flow(side="below", tape_obj={"deltaScore": -0.45})
    assert af == "WITH"


def test_aggressor_mixed_when_near_zero():
    af, _ = _thesis_aggressor_flow(side="above", tape_obj={"deltaScore": 0.05})
    assert af == "MIXED"


def test_aggressor_thin_when_no_tape():
    af, _ = _thesis_aggressor_flow(side="above", tape_obj=None)
    assert af == "THIN"


# -- book_state -------------------------------------------------------------

def test_book_untrusted_when_spoof_recent():
    me_obj = _micro_events(("SPOOF", True), price=20000.0)
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=None, me_obj=me_obj)
    assert bs == "UNTRUSTED"


def test_book_stable_with_nothing():
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=None, me_obj=None)
    assert bs == "STABLE"


def test_book_stacking_when_ps_rotation_toward():
    ps = {"rotation": {"direction": "ROTATION_UP"}}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "STACKING"


def test_book_pulling_when_ps_rotation_away():
    ps = {"rotation": {"direction": "ROTATION_DN"}}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "PULLING"


# -- execution_read ---------------------------------------------------------

def test_execution_stand_down_on_iceberg_defense():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="ICEBERG_DEFENSE",
        liquidity="ICEBERG_DEFENDED", aggressor="WITH",
    )
    assert er == "STAND_DOWN"


def test_execution_stand_down_on_spoof_risk():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="NONE",
        liquidity="SPOOF_RISK", aggressor="WITH",
    )
    assert er == "STAND_DOWN"


def test_execution_wait_for_confirm_on_stop_sweep():
    er, _ = _thesis_execution_read(
        state="TOUCHED", thesis="STOP_SWEEP_CONTINUATION",
        liquidity="REAL", aggressor="WITH",
    )
    assert er == "WAIT_FOR_CONFIRM"


def test_execution_pay_for_trade_on_confirmed_acceptance():
    er, _ = _thesis_execution_read(
        state="ACCEPTED_ABOVE", thesis="ACCEPTANCE_LONG",
        liquidity="REAL", aggressor="WITH",
    )
    assert er == "PAY_FOR_TRADE"


def test_execution_pay_for_trade_on_rejection_with_counter_flow():
    er, _ = _thesis_execution_read(
        state="REJECTED", thesis="REJECTION_SHORT",
        liquidity="REAL", aggressor="AGAINST",
    )
    assert er == "PAY_FOR_TRADE"


def test_execution_scratch_ready_on_retest_fail():
    er, _ = _thesis_execution_read(
        state="RETEST_FAIL", thesis="NONE",
        liquidity="REAL", aggressor="AGAINST",
    )
    assert er == "SCRATCH_READY"


def test_execution_scratch_ready_on_invalidated():
    er, _ = _thesis_execution_read(
        state="INVALIDATED", thesis="NONE",
        liquidity="REAL", aggressor="MIXED",
    )
    assert er == "SCRATCH_READY"
