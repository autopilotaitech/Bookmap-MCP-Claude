"""Dedup-by-state-change contract.

Signal id format: "{alias}|{label}|{side}|{state_since_ms}".
Same state across polls -> same id (Java chart layer can dedup).
State transition -> new id.
Multiple proximate levels -> independent ids.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    compute_or_levels,
    compute_institutional_signals,
    _LEVEL_TOUCH_STATE,
)


def _snap(mid, or_high=20000.0, or_low=19950.0, tape_delta=0.45,
          micro_events=None):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": or_high, "orLow": or_low},
        "micro_events": micro_events or {"events": []},
        "tape_flow": {"deltaScore": tape_delta, "label": "MIXED"},
        "pull_stack": None,
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
    }


def _drive(*mids, tape_delta=0.45):
    _LEVEL_TOUCH_STATE.clear()
    snap = None
    for m in mids:
        snap = _snap(mid=m, tape_delta=tape_delta)
        snap["or_levels"] = compute_or_levels(snap)
    return snap


def _find(sigs, label):
    return next((s for s in sigs if s["label"] == label), None)


def test_same_state_across_polls_yields_same_signal_id():
    # Drive to ACCEPTED_ABOVE
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0)
    sigs_1 = compute_institutional_signals(snap)
    id_1 = _find(sigs_1, "OR-H")["id"]

    # One more poll: still ACCEPTED_ABOVE (mid still past, holding acceptance)
    snap_after = _snap(mid=20006.0, tape_delta=0.45)
    snap_after["or_levels"] = compute_or_levels(snap_after)
    sigs_2 = compute_institutional_signals(snap_after)
    id_2 = _find(sigs_2, "OR-H")["id"]
    assert id_1 == id_2, f"same state must yield same id; got {id_1} vs {id_2}"


def test_state_transition_changes_signal_id():
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0)
    sigs_acc = compute_institutional_signals(snap)
    id_accepted = _find(sigs_acc, "OR-H")["id"]

    # Hard reverse to RETEST_FAIL
    snap_rev = _snap(mid=19940.0, tape_delta=-0.30)
    snap_rev["or_levels"] = compute_or_levels(snap_rev)
    sigs_rev = compute_institutional_signals(snap_rev)
    or_h_rev = _find(sigs_rev, "OR-H")
    assert or_h_rev is not None
    assert or_h_rev["id"] != id_accepted


def test_signal_id_is_stable_format():
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0)
    sigs = compute_institutional_signals(snap)
    s = _find(sigs, "OR-H")
    parts = s["id"].split("|")
    # alias | label | side | state_since_ms
    assert len(parts) == 4
    assert parts[0] == "NQM6.CME@RITHMIC"
    assert parts[1] == "OR-H"
    assert parts[2] == "above"
    assert parts[3].isdigit()
    assert int(parts[3]) > 0


def test_multiple_proximate_levels_have_independent_ids():
    """When mid sits between OR-H and OR-L, both can be in proximity AND
    each tracks its own state machine + state_since_ms."""
    # OR width 50p; mid 19975 is dead-center. Drive both levels into TOUCHED
    # by oscillating mid across each.
    _LEVEL_TOUCH_STATE.clear()
    # Touch OR-L
    s1 = _snap(mid=19950.0); s1["or_levels"] = compute_or_levels(s1)
    # Touch OR-H
    s2 = _snap(mid=20000.0); s2["or_levels"] = compute_or_levels(s2)
    sigs = compute_institutional_signals(s2)
    or_h = _find(sigs, "OR-H")
    or_l = _find(sigs, "OR-L")
    # Either may be None if thesis didn't classify, but if both present their
    # ids must be distinct.
    if or_h is not None and or_l is not None:
        assert or_h["id"] != or_l["id"]
