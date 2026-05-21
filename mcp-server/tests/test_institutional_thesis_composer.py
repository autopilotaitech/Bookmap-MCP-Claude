"""Pin the institutional_thesis payload attached to each per-level dict.

Composer-level integration tests. The state machine + helpers themselves are
pinned in test_institutional_thesis_state_machine.py and
test_institutional_thesis_microstructure.py.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    compute_or_levels,
    compute_institutional_thesis,
    _LEVEL_TOUCH_STATE,
    _THESIS_STATE_CODES,
    _THESIS_THESIS_CODES,
    _THESIS_LIQ_CODES,
    _THESIS_AGG_CODES,
    _THESIS_BOOK_CODES,
    _THESIS_EXEC_CODES,
)


def _base_snap(or_high=20000.0, or_low=19500.0, mid=20002.0,
               alias="NQM6.CME@RITHMIC", micro_events=None,
               tape_delta=0.0):
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
    }


def test_each_level_carries_institutional_thesis_payload():
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=20002.0)
    ol = compute_or_levels(snap)
    assert ol is not None
    for lvl in ol["levels"]:
        ith = lvl.get("institutional_thesis")
        assert ith is not None, f"missing thesis on {lvl['label']}"
        assert ith["state"] in ("",) + _THESIS_STATE_CODES
        assert ith["thesis"] in _THESIS_THESIS_CODES
        assert ith["liquidity_quality"] in _THESIS_LIQ_CODES
        assert ith["aggressor_flow"] in _THESIS_AGG_CODES
        assert ith["book_state"] in _THESIS_BOOK_CODES
        assert ith["execution_read"] in _THESIS_EXEC_CODES
        assert 0.0 <= float(ith["confidence"]) <= 1.0
        assert isinstance(ith["reasons"], list)
        assert isinstance(ith["invalidations"], list)


def test_thesis_wall_clock_fields_present():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_base_snap(mid=20002.0))
    for lvl in ol["levels"]:
        ith = lvl["institutional_thesis"]
        for field in ("touched_at_ms", "last_state_change_ms",
                      "polls_since_touch", "confirm_ms_since_touch"):
            assert field in ith, f"{lvl['label']} missing wall-clock field {field}"
        assert isinstance(ith["last_state_change_ms"], int)
        assert ith["last_state_change_ms"] > 0
        assert isinstance(ith["polls_since_touch"], int)


def test_touched_at_ms_set_on_first_touch_and_persisted():
    _LEVEL_TOUCH_STATE.clear()
    # Drive: APPROACHING -> TOUCHED -> past
    compute_or_levels(_base_snap(mid=19995.0))   # APPROACHING
    ol_touch = compute_or_levels(_base_snap(mid=20000.0))   # TOUCHED
    or_h_touch = next(l for l in ol_touch["levels"] if l["label"] == "OR-H")
    touched_at = or_h_touch["institutional_thesis"]["touched_at_ms"]
    assert touched_at is not None and touched_at > 0

    # Next poll: still tracking the same touch
    ol_after = compute_or_levels(_base_snap(mid=20003.0))
    or_h_after = next(l for l in ol_after["levels"] if l["label"] == "OR-H")
    assert or_h_after["institutional_thesis"]["touched_at_ms"] == touched_at
    assert or_h_after["institutional_thesis"]["confirm_ms_since_touch"] is not None


def test_state_transitions_across_consecutive_polls_for_or_h():
    _LEVEL_TOUCH_STATE.clear()
    # Poll 1: mid 5 below OR-H
    compute_or_levels(_base_snap(mid=19995.0))
    # Poll 2: mid exactly at OR-H (TOUCHED)
    ol2 = compute_or_levels(_base_snap(mid=20000.0))
    or_h_2 = next(l for l in ol2["levels"] if l["label"] == "OR-H")
    assert or_h_2["institutional_thesis"]["state"] == "TOUCHED"

    # Poll 3: 3 pts past (one poll past)
    ol3 = compute_or_levels(_base_snap(mid=20003.0))
    or_h_3 = next(l for l in ol3["levels"] if l["label"] == "OR-H")
    # not yet accepted (need 2 polls past)
    assert or_h_3["institutional_thesis"]["state"] != "ACCEPTED_ABOVE"

    # Poll 4: still past
    ol4 = compute_or_levels(_base_snap(mid=20005.0))
    or_h_4 = next(l for l in ol4["levels"] if l["label"] == "OR-H")
    assert or_h_4["institutional_thesis"]["state"] == "ACCEPTED_ABOVE"


def test_iceberg_at_or_h_blocks_acceptance_long_until_broken():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "ICEBERG", "isBid": False,
                      "price": 20000.0, "size": 8000, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "ICEBERG_DEFENSE"
    assert ith["execution_read"] == "STAND_DOWN"
    assert ith["liquidity_quality"] == "ICEBERG_DEFENDED"


def test_iceberg_at_or_l_blocks_acceptance_short_until_broken():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "ICEBERG", "isBid": True,
                      "price": 19500.0, "size": 8000, "timeMs": 1}]}
    snap = _base_snap(mid=19500.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_l = next(l for l in ol["levels"] if l["label"] == "OR-L")
    ith = or_l["institutional_thesis"]
    assert ith["thesis"] == "ICEBERG_DEFENSE"
    assert ith["execution_read"] == "STAND_DOWN"


def test_spoof_at_or_h_does_not_create_false_continuation():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "SPOOF", "isBid": True,
                      "price": 20000.0, "size": 200, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "NONE"
    assert ith["execution_read"] == "STAND_DOWN"


def test_stop_sweep_requires_confirmation():
    _LEVEL_TOUCH_STATE.clear()
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    snap = _base_snap(mid=20000.0, micro_events=me)
    ol = compute_or_levels(snap)
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["thesis"] == "STOP_SWEEP_CONTINUATION"
    assert ith["execution_read"] == "WAIT_FOR_CONFIRM"


def test_accepted_above_with_aligned_flow_maps_to_pay_for_trade():
    _LEVEL_TOUCH_STATE.clear()
    # Drive through 4 polls until acceptance; aligned tape delta.
    for mid in (19995.0, 20000.0, 20003.0, 20005.0):
        compute_or_levels(_base_snap(mid=mid, tape_delta=0.45))
    ol = compute_or_levels(_base_snap(mid=20006.0, tape_delta=0.45))
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["state"] == "ACCEPTED_ABOVE"
    assert ith["thesis"] == "ACCEPTANCE_LONG"
    assert ith["aggressor_flow"] == "WITH"
    assert ith["execution_read"] == "PAY_FOR_TRADE"


def test_rejected_at_or_h_maps_to_rejection_short():
    _LEVEL_TOUCH_STATE.clear()
    compute_or_levels(_base_snap(mid=19995.0))    # APPROACHING
    compute_or_levels(_base_snap(mid=20000.0))    # TOUCHED
    ol = compute_or_levels(_base_snap(mid=19950.0))  # reversed > REJECT backoff
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["state"] == "REJECTED"
    assert ith["thesis"] == "REJECTION_SHORT"


def test_standalone_composer_returns_per_label_pairs():
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=20002.0)
    # compute_or_levels populates snap['or_levels'] indirectly via the bridge
    # pipeline; for compute_institutional_thesis we set it manually.
    snap["or_levels"] = compute_or_levels(snap)
    out = compute_institutional_thesis(snap)
    assert out is not None
    labels = {entry["label"] for entry in out}
    assert "OR-H" in labels and "OR-L" in labels
    for entry in out:
        assert "thesis" in entry
        assert "state" in entry["thesis"]


def test_standalone_composer_returns_none_when_mid_missing():
    snap = {
        "alias": "NQM6.CME@RITHMIC",
        "book": {"mid": None},
        "or_levels": {"levels": [{"label": "OR-H", "price": 20000.0, "side": "above"}]},
    }
    assert compute_institutional_thesis(snap) is None
