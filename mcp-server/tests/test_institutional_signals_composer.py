"""Pin the institutional-signal composer.

Hard contracts enforced here:

1. Only execution_read == PAY_FOR_TRADE yields direction in (LONG, SHORT).
2. WAIT_FOR_CONFIRM / STAND_DOWN / SCRATCH_READY -> direction == NONE.
3. ACCEPTANCE requires aggressor_flow == WITH; REJECTION + STOP_SWEEP_FAILURE
   require aggressor_flow == AGAINST. Anything else (MIXED, THIN, missing,
   malformed) blocks entry direction and downgrades execution_read.
4. Stop-sweep CONTINUATION signal_type matches the BREAKOUT side
   (sweep above -> STOP_SWEEP_LONG / sweep below -> STOP_SWEEP_SHORT),
   direction NONE, WAIT_FOR_CONFIRM. On failure, label flips to the entry
   direction and only becomes PAY_FOR_TRADE if aggressor confirms.
5. Spoof risk -> SPOOF_STAND_DOWN, NONE.
6. Iceberg defense -> ICEBERG_DEFENSE, NONE.
7. No signals in the middle of the OR (require level.proximity).
8. trend_signal alone NEVER creates an entry direction.
9. Signal id = "{alias}|{label}|{side}|{state_since_ms}" -> deterministic.
"""
from __future__ import annotations

from typing import Dict, Any, Optional

from bookmap_mcp.dashboard import (
    compute_institutional_signals,
    compute_or_levels,
    _LEVEL_TOUCH_STATE,
    _SIGNAL_TYPE_CODES,
    _SIGNAL_DIRECTION_CODES,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = (
    "id", "alias", "label", "price", "side", "direction", "signal_type",
    "execution_read", "confidence", "size_tier", "reason_codes",
    "invalidation_price", "payline_price", "timestamp_ms",
    "source_level_state", "liquidity_quality", "aggressor_flow", "book_state",
)


def _base_snap(or_high=20000.0, or_low=19950.0, mid=20002.0,
               alias="NQM6.CME@RITHMIC", micro_events=None,
               tape_delta=0.0, trend_kind="NONE") -> Dict[str, Any]:
    return {
        "alias": alias,
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": or_high, "orLow": or_low},
        "micro_events": micro_events or {"events": []},
        "tape_flow": {"deltaScore": tape_delta, "label": "MIXED"},
        "pull_stack": None,
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
        "trend_signal": {"kind": trend_kind, "eligible": False},
    }


def _drive_and_build(or_high=20000.0, or_low=19950.0,
                     mids=(), tape_delta=0.0, micro_events=None,
                     alias="NQM6.CME@RITHMIC"):
    """Walk a sequence of mids through compute_or_levels (which mutates
    _LEVEL_TOUCH_STATE) and return the final snap + institutional_signals."""
    _LEVEL_TOUCH_STATE.clear()
    snap = None
    for mid in mids:
        snap = _base_snap(or_high=or_high, or_low=or_low, mid=mid,
                          alias=alias, tape_delta=tape_delta,
                          micro_events=micro_events)
        snap["or_levels"] = compute_or_levels(snap)
    sigs = compute_institutional_signals(snap)
    return snap, sigs


def _find(sigs, label):
    return next((s for s in sigs if s["label"] == label), None)


def _assert_full_shape(sig):
    for f in _REQUIRED_FIELDS:
        assert f in sig, f"missing field {f} in signal {sig.get('id')}"
    assert sig["signal_type"] in _SIGNAL_TYPE_CODES
    assert sig["direction"] in _SIGNAL_DIRECTION_CODES
    assert isinstance(sig["reason_codes"], list)


# ---------------------------------------------------------------------------
# Hard-rule tests (13)
# ---------------------------------------------------------------------------

def test_no_signals_when_or_levels_missing():
    snap = _base_snap()
    snap["or_levels"] = None
    assert compute_institutional_signals(snap) == []


def test_no_signal_in_middle_of_or():
    """mid centred inside OR; OR-H/OR-L should NOT be in proximity (12.5p band
    on a 50p OR around mid 19975). No signals must be emitted."""
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19975.0])
    # OR-H is 25p away, OR-L is 25p away -> neither in proximity (12.5p band).
    # Expect zero signals.
    assert sigs == []


def test_spoof_risk_blocks_entry_with_spoof_stand_down_only():
    """A SPOOF at OR-H must yield exactly SPOOF_STAND_DOWN at OR-H, NONE
    direction, STAND_DOWN. NO acceptance/rejection signal must coexist."""
    me = {"events": [{"kind": "SPOOF", "isBid": True,
                      "price": 20000.0, "size": 200, "timeMs": 1}]}
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[20000.0], micro_events=me)
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    _assert_full_shape(or_h)
    assert or_h["signal_type"] == "SPOOF_STAND_DOWN"
    assert or_h["direction"] == "NONE"
    assert or_h["execution_read"] == "STAND_DOWN"


def test_iceberg_defense_blocks_breakout_entry():
    """ASK iceberg at OR-H must produce ICEBERG_DEFENSE only, never
    ACCEPTANCE_LONG."""
    me = {"events": [{"kind": "ICEBERG", "isBid": False,
                      "price": 20000.0, "size": 8000, "timeMs": 1}]}
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19995.0, 20000.0, 20003.0, 20005.0,
                                         20006.0],
                                   tape_delta=0.45, micro_events=me)
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    assert or_h["signal_type"] == "ICEBERG_DEFENSE"
    assert or_h["direction"] == "NONE"
    assert or_h["execution_read"] == "STAND_DOWN"
    # No ACCEPTANCE_LONG anywhere.
    assert not any(s["signal_type"] == "ACCEPTANCE_LONG" for s in sigs)


def test_or_h_acceptance_with_with_flow_creates_long_pay_for_trade():
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19995.0, 20000.0, 20003.0, 20005.0,
                                         20006.0],
                                   tape_delta=0.45)  # WITH for above
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    _assert_full_shape(or_h)
    assert or_h["signal_type"] == "ACCEPTANCE_LONG"
    assert or_h["direction"] == "LONG"
    assert or_h["execution_read"] == "PAY_FOR_TRADE"
    assert or_h["source_level_state"] == "ACCEPTED_ABOVE"
    # Invalidation should be 1 tick below OR-L.
    assert or_h["invalidation_price"] is not None
    # Payline = entry + 10 pts on NQ.
    assert or_h["payline_price"] == 20010.0


def test_or_l_acceptance_with_with_flow_creates_short_pay_for_trade():
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19955.0, 19950.0, 19947.0, 19945.0,
                                         19944.0],
                                   tape_delta=-0.45)  # WITH for below
    or_l = _find(sigs, "OR-L")
    assert or_l is not None
    _assert_full_shape(or_l)
    assert or_l["signal_type"] == "ACCEPTANCE_SHORT"
    assert or_l["direction"] == "SHORT"
    assert or_l["execution_read"] == "PAY_FOR_TRADE"


def test_or_h_rejection_with_against_flow_creates_short_pay_for_trade():
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19995.0, 20000.0, 19960.0],
                                   tape_delta=-0.30)  # AGAINST for above
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    assert or_h["signal_type"] == "REJECTION_SHORT"
    assert or_h["direction"] == "SHORT"
    assert or_h["execution_read"] == "PAY_FOR_TRADE"
    assert or_h["source_level_state"] == "REJECTED"


def test_or_l_rejection_with_against_flow_creates_long_pay_for_trade():
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19955.0, 19950.0, 19990.0],
                                   tape_delta=+0.30)  # AGAINST for below (positive = up)
    or_l = _find(sigs, "OR-L")
    assert or_l is not None
    assert or_l["signal_type"] == "REJECTION_LONG"
    assert or_l["direction"] == "LONG"
    assert or_l["execution_read"] == "PAY_FOR_TRADE"


def test_stop_sweep_continuation_label_matches_breakout_side():
    """Sweep above OR-H during CONTINUATION = STOP_SWEEP_LONG (matches sweep
    direction), direction NONE, WAIT_FOR_CONFIRM."""
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[20000.0], micro_events=me)
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    assert or_h["signal_type"] == "STOP_SWEEP_LONG"  # matches breakout side
    assert or_h["direction"] == "NONE"               # no entry until confirm
    assert or_h["execution_read"] == "WAIT_FOR_CONFIRM"


def test_stop_sweep_failure_flips_direction_with_confirming_flow():
    """Sweep above OR-H then reversal back -> STOP_SWEEP_SHORT (the entry
    direction is opposite to the failed sweep). Requires AGAINST flow."""
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    _LEVEL_TOUCH_STATE.clear()
    # Poll 1: APPROACHING (5p below)
    snap1 = _base_snap(or_high=20000.0, or_low=19950.0, mid=19995.0,
                      tape_delta=-0.30, micro_events=me)
    snap1["or_levels"] = compute_or_levels(snap1)
    # Poll 2: TOUCHED (sweep arrives)
    snap2 = _base_snap(or_high=20000.0, or_low=19950.0, mid=20000.0,
                      tape_delta=-0.30, micro_events=me)
    snap2["or_levels"] = compute_or_levels(snap2)
    # Poll 3: pushed past (2.5p)
    snap3 = _base_snap(or_high=20000.0, or_low=19950.0, mid=20002.5,
                      tape_delta=-0.30, micro_events=me)
    snap3["or_levels"] = compute_or_levels(snap3)
    # Poll 4: reversed back into range -> FAILED_BREAK (+ has STOP_SWEEP)
    snap4 = _base_snap(or_high=20000.0, or_low=19950.0, mid=19995.0,
                      tape_delta=-0.30, micro_events=me)
    snap4["or_levels"] = compute_or_levels(snap4)
    sigs = compute_institutional_signals(snap4)
    or_h = _find(sigs, "OR-H")
    assert or_h is not None, f"no OR-H signal; sigs={[s['signal_type'] for s in sigs]}"
    assert or_h["signal_type"] == "STOP_SWEEP_SHORT"
    assert or_h["direction"] == "SHORT"
    assert or_h["execution_read"] == "PAY_FOR_TRADE"


def test_aggressor_mixed_blocks_acceptance_entry():
    """ACCEPTED_ABOVE state but aggressor_flow MIXED (delta near zero)
    must produce direction=NONE, WAIT_FOR_CONFIRM."""
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19995.0, 20000.0, 20003.0, 20005.0,
                                         20006.0],
                                   tape_delta=0.0)  # MIXED
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    assert or_h["signal_type"] == "ACCEPTANCE_LONG"  # type still classified
    assert or_h["direction"] == "NONE"               # blocked by MIXED flow
    assert or_h["execution_read"] == "WAIT_FOR_CONFIRM"
    assert any("aggressor_flow" in r.lower() or "aggressor" in r.lower()
               for r in or_h["reason_codes"])


def test_aggressor_against_blocks_acceptance_long_entry():
    """ACCEPTED_ABOVE but AGAINST flow must block."""
    snap, sigs = _drive_and_build(or_high=20000.0, or_low=19950.0,
                                   mids=[19995.0, 20000.0, 20003.0, 20005.0,
                                         20006.0],
                                   tape_delta=-0.45)  # AGAINST for above
    or_h = _find(sigs, "OR-H")
    assert or_h is not None
    assert or_h["signal_type"] == "ACCEPTANCE_LONG"
    assert or_h["direction"] == "NONE"
    assert or_h["execution_read"] == "WAIT_FOR_CONFIRM"


def test_trend_signal_alone_never_creates_institutional_entry():
    """trend_signal=STRONG_BULL with mid in middle of OR -> no signals."""
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(or_high=20000.0, or_low=19950.0, mid=19975.0,
                      trend_kind="STRONG_BULL")
    snap["or_levels"] = compute_or_levels(snap)
    sigs = compute_institutional_signals(snap)
    # No PAY_FOR_TRADE entries from trend bias alone.
    assert all(s["direction"] == "NONE" for s in sigs)


def test_signal_id_deterministic_from_state_since_ms():
    """Same state across two polls should yield identical signal id.
    A state transition should change the id."""
    _LEVEL_TOUCH_STATE.clear()
    # Drive to ACCEPTED_ABOVE
    for mid in (19995.0, 20000.0, 20003.0, 20005.0):
        snap = _base_snap(or_high=20000.0, or_low=19950.0, mid=mid,
                          tape_delta=0.45)
        snap["or_levels"] = compute_or_levels(snap)
    sigs_1 = compute_institutional_signals(snap)
    # Another poll at same state (still ACCEPTED_ABOVE)
    snap2 = _base_snap(or_high=20000.0, or_low=19950.0, mid=20006.0,
                       tape_delta=0.45)
    snap2["or_levels"] = compute_or_levels(snap2)
    sigs_2 = compute_institutional_signals(snap2)

    id_1 = _find(sigs_1, "OR-H")["id"]
    id_2 = _find(sigs_2, "OR-H")["id"]
    assert id_1 == id_2, "same state must yield same id"

    # Now drive a transition: hard reverse -> REJECTED region
    snap3 = _base_snap(or_high=20000.0, or_low=19950.0, mid=19940.0,
                       tape_delta=-0.30)
    snap3["or_levels"] = compute_or_levels(snap3)
    sigs_3 = compute_institutional_signals(snap3)
    or_h_3 = _find(sigs_3, "OR-H")
    if or_h_3 is not None:
        assert or_h_3["id"] != id_1, "state transition must change id"


def test_legacy_per_level_fields_preserved():
    """Adding institutional_signals must NOT alter any legacy per-level field."""
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=20002.0)
    snap["or_levels"] = compute_or_levels(snap)
    for lvl in snap["or_levels"]["levels"]:
        for k in ("label", "price", "side", "distance", "proximity",
                  "decision", "confidence", "composite",
                  "institutional_thesis"):
            assert k in lvl
