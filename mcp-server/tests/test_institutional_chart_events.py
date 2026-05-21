"""Pin compute_institutional_chart_events(snap) — the evidence-trail
payload that drives Bookmap chart markers.

Contract: snap["institutional_chart_events"] surfaces ALL human-readable
institutional evidence (sweeps, absorption, iceberg defense, spoof risk,
pull/stack, acceptance, rejection, watch/touch, scratch) — NOT just final
entries. Only ACCEPTANCE / REJECTION with execution_read == PAY_FOR_TRADE
may carry direction LONG/SHORT. Everything else is direction NONE.

Spec: docs/superpowers/specs/institutional-chart-markers.md
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from bookmap_mcp.dashboard import (
    compute_institutional_chart_events,
    compute_or_levels,
    _CHART_EVENT_TYPES,
    _CHART_SEVERITY_RANKS,
    _LEVEL_TOUCH_STATE,
)


# ─── Test helpers ──────────────────────────────────────────────────────────

_REQUIRED_FIELDS = (
    "id", "alias", "label", "price", "side", "event_type", "direction",
    "execution_read", "marker_text", "marker_color_hint", "severity",
    "timestamp_ms", "source", "confidence", "reason_codes",
    "invalidation_price", "payline_price",
)


def _base_snap(or_high=20000.0, or_low=19950.0, mid=20002.0,
               alias="NQM6.CME@RITHMIC", micro_events=None,
               tape_delta=0.0, pull_stack=None) -> Dict[str, Any]:
    return {
        "alias": alias,
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": or_high, "orLow": or_low},
        "micro_events": micro_events or {"events": []},
        "tape_flow": {"deltaScore": tape_delta, "label": "MIXED"},
        "pull_stack": pull_stack,
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
        "trend_signal": {"kind": "NONE", "eligible": False},
    }


def _drive(*mids, tape_delta=0.0, micro_events=None, pull_stack=None,
           or_high=20000.0, or_low=19950.0):
    """Walk a sequence of mids through compute_or_levels so the thesis
    state machine advances. Returns the FINAL snap with or_levels populated."""
    _LEVEL_TOUCH_STATE.clear()
    snap = None
    for m in mids:
        snap = _base_snap(or_high=or_high, or_low=or_low, mid=m,
                          tape_delta=tape_delta, micro_events=micro_events,
                          pull_stack=pull_stack)
        snap["or_levels"] = compute_or_levels(snap)
    return snap


def _events(snap) -> List[Dict[str, Any]]:
    return compute_institutional_chart_events(snap)


def _find(events, event_type, label=None):
    for e in events:
        if e.get("event_type") != event_type:
            continue
        if label is not None and e.get("label") != label:
            continue
        return e
    return None


def _assert_full_shape(e):
    for f in _REQUIRED_FIELDS:
        assert f in e, f"missing field {f}"
    assert e["event_type"] in _CHART_EVENT_TYPES
    assert e["direction"] in ("LONG", "SHORT", "NONE")
    assert e["severity"] in ("ENTRY", "EXIT", "WARNING", "WATCH", "INFO")


# ─── 1. WATCH_LEVEL & TOUCHED_LEVEL ────────────────────────────────────────

def test_proximate_approaching_emits_watch_level():
    snap = _drive(19992.0)   # 8p below OR-H → in proximity, no touch
    events = _events(snap)
    w = _find(events, "WATCH_LEVEL", "OR-H")
    assert w is not None
    _assert_full_shape(w)
    assert w["marker_text"] == "WATCH"
    assert w["direction"] == "NONE"
    assert w["severity"] == "WATCH"


def test_touched_emits_touched_level():
    snap = _drive(19998.0, 20000.0)   # APPROACHING then TOUCHED
    events = _events(snap)
    t = _find(events, "TOUCHED_LEVEL", "OR-H")
    assert t is not None
    _assert_full_shape(t)
    assert t["marker_text"] == "TCH"
    assert t["direction"] == "NONE"


def test_middle_of_or_emits_no_watch_or_touched():
    snap = _drive(19975.0)   # dead center of OR (25p from each edge)
    events = _events(snap)
    assert _find(events, "WATCH_LEVEL") is None
    assert _find(events, "TOUCHED_LEVEL") is None


# ─── 2. LIQUIDITY_SWEEP ─────────────────────────────────────────────────────

def test_stop_sweep_above_or_h_emits_sweep_up():
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    snap = _drive(20000.0, micro_events=me)
    events = _events(snap)
    sw = _find(events, "LIQUIDITY_SWEEP", "OR-H")
    assert sw is not None
    _assert_full_shape(sw)
    assert sw["marker_text"] == "SWP↑"
    assert sw["direction"] == "NONE"
    assert sw["severity"] == "WARNING"
    assert sw["source"] == "micro_events"


def test_stop_sweep_below_or_l_emits_sweep_down():
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": True,
                      "price": 19950.0, "size": 400, "timeMs": 1}]}
    snap = _drive(19950.0, micro_events=me)
    events = _events(snap)
    sw = _find(events, "LIQUIDITY_SWEEP", "OR-L")
    assert sw is not None
    assert sw["marker_text"] == "SWP↓"


def test_sweep_emits_even_when_no_proximity_at_mid():
    """A sweep at OR-H still emits even if current mid is in the middle of
    the OR — the sweep is anchored at the level, not at current mid."""
    me = {"events": [{"kind": "STOP_SWEEP", "isBid": False,
                      "price": 20000.0, "size": 400, "timeMs": 1}]}
    snap = _drive(19975.0, micro_events=me)
    events = _events(snap)
    assert _find(events, "LIQUIDITY_SWEEP", "OR-H") is not None


# ─── 3. ICEBERG_DEFENSE ─────────────────────────────────────────────────────

def test_iceberg_ask_at_or_h_emits_ice_a():
    me = {"events": [{"kind": "ICEBERG", "isBid": False,
                      "price": 20000.0, "size": 8000, "timeMs": 1}]}
    snap = _drive(20000.0, micro_events=me)
    events = _events(snap)
    ice = _find(events, "ICEBERG_DEFENSE", "OR-H")
    assert ice is not None
    _assert_full_shape(ice)
    assert ice["marker_text"] == "ICE-A"
    assert ice["direction"] == "NONE"
    assert ice["severity"] == "WARNING"


def test_iceberg_bid_at_or_l_emits_ice_b():
    me = {"events": [{"kind": "ICEBERG", "isBid": True,
                      "price": 19950.0, "size": 8000, "timeMs": 1}]}
    snap = _drive(19950.0, micro_events=me)
    events = _events(snap)
    ice = _find(events, "ICEBERG_DEFENSE", "OR-L")
    assert ice is not None
    assert ice["marker_text"] == "ICE-B"


# ─── 4. SPOOF_RISK ──────────────────────────────────────────────────────────

def test_spoof_at_level_emits_spoof_risk():
    me = {"events": [{"kind": "SPOOF", "isBid": True,
                      "price": 20000.0, "size": 200, "timeMs": 1}]}
    snap = _drive(20000.0, micro_events=me)
    events = _events(snap)
    sp = _find(events, "SPOOF_RISK", "OR-H")
    assert sp is not None
    _assert_full_shape(sp)
    assert sp["marker_text"] == "SPD"
    assert sp["direction"] == "NONE"
    assert sp["severity"] == "WARNING"


# ─── 5. ABSORPTION ──────────────────────────────────────────────────────────

def test_strong_counter_flow_at_or_h_emits_absorption():
    """Touched OR-H with aggressor heavily AGAINST (down) — under OFI
    (Cont/Kukanov/Stoikov) this means hidden passive is absorbing."""
    snap = _drive(20000.0, tape_delta=-0.85)
    events = _events(snap)
    ab = _find(events, "ABSORPTION", "OR-H")
    assert ab is not None
    _assert_full_shape(ab)
    assert ab["marker_text"] == "ABS-B"   # bid absorbing at upper level
    assert ab["direction"] == "NONE"
    assert ab["source"] == "tape_flow"


def test_strong_counter_flow_at_or_l_emits_absorption_ask():
    snap = _drive(19950.0, tape_delta=+0.85)
    events = _events(snap)
    ab = _find(events, "ABSORPTION", "OR-L")
    assert ab is not None
    assert ab["marker_text"] == "ABS-A"   # ask absorbing at lower level


# ─── 6. PULLING / STACKING ──────────────────────────────────────────────────

def test_pull_stack_rotation_up_at_proximate_or_h_emits_stacking():
    snap = _drive(19992.0, pull_stack={"rotation": "ROTATION_UP", "aggregateZ": 1.5})
    events = _events(snap)
    st = _find(events, "STACKING", "OR-H")
    assert st is not None
    assert st["marker_text"] == "STACK"
    assert st["severity"] == "INFO"


def test_pull_stack_rotation_dn_at_proximate_or_h_emits_pulling():
    snap = _drive(19992.0, pull_stack={"rotation": "ROTATION_DN", "aggregateZ": 1.5})
    events = _events(snap)
    pl = _find(events, "PULLING", "OR-H")
    assert pl is not None
    assert pl["marker_text"] == "PULL"


def test_pull_stack_emits_only_at_proximate_levels():
    """Rotation context is per-instrument; we attach it only to proximate
    levels so the chart isn't covered in PULL/STACK markers at every rung."""
    snap = _drive(19975.0, pull_stack={"rotation": "ROTATION_UP", "aggregateZ": 1.5})
    events = _events(snap)
    assert _find(events, "STACKING") is None
    assert _find(events, "PULLING") is None


# ─── 7. ACCEPTANCE / REJECTION (entry events) ──────────────────────────────

def test_acceptance_pay_for_trade_emits_acc_l_with_long():
    # 4 polls past OR-H with WITH flow → ACCEPTED_ABOVE + PAY_FOR_TRADE.
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0, 20006.0,
                  tape_delta=+0.45)
    events = _events(snap)
    acc = _find(events, "ACCEPTANCE", "OR-H")
    assert acc is not None
    _assert_full_shape(acc)
    assert acc["marker_text"] == "ACC-L"
    assert acc["direction"] == "LONG"
    assert acc["execution_read"] == "PAY_FOR_TRADE"
    assert acc["severity"] == "ENTRY"


def test_acceptance_below_emits_acc_s_with_short():
    snap = _drive(19955.0, 19950.0, 19947.0, 19945.0, 19944.0,
                  tape_delta=-0.45)
    events = _events(snap)
    acc = _find(events, "ACCEPTANCE", "OR-L")
    assert acc is not None
    assert acc["marker_text"] == "ACC-S"
    assert acc["direction"] == "SHORT"


def test_rejection_at_or_h_emits_rej_s():
    snap = _drive(19995.0, 20000.0, 19960.0, tape_delta=-0.30)
    events = _events(snap)
    rej = _find(events, "REJECTION", "OR-H")
    assert rej is not None
    assert rej["marker_text"] == "REJ-S"
    assert rej["direction"] == "SHORT"
    assert rej["execution_read"] == "PAY_FOR_TRADE"


def test_acceptance_without_aligned_flow_does_not_emit_entry():
    """ACCEPTED_ABOVE state but MIXED flow → no ACCEPTANCE event, since
    execution_read won't be PAY_FOR_TRADE."""
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0, 20006.0,
                  tape_delta=0.0)   # MIXED
    events = _events(snap)
    assert _find(events, "ACCEPTANCE") is None


# ─── 8. Negative requirements ──────────────────────────────────────────────

def test_trend_signal_alone_does_not_emit_chart_events():
    """A bullish trend_signal in mid-of-OR with no level interaction must
    produce no chart events (negative test for rule 'never plot from
    trend bias')."""
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=19975.0)
    snap["or_levels"] = compute_or_levels(snap)
    snap["trend_signal"] = {"kind": "STRONG_BULL", "eligible": True}
    events = _events(snap)
    # No events whose source is trend_signal.
    assert all(e.get("source") != "trend_signal" for e in events)
    # And no ACCEPTANCE/REJECTION emitted from trend bias alone.
    assert _find(events, "ACCEPTANCE") is None
    assert _find(events, "REJECTION") is None


def test_pax_decision_alone_does_not_emit_chart_events():
    _LEVEL_TOUCH_STATE.clear()
    snap = _base_snap(mid=19975.0)
    snap["or_levels"] = compute_or_levels(snap)
    snap["pax"] = {"decision": "ENTER_LONG_FOLLOW", "size_tier": "FULL",
                   "level_label": "OR-H", "entry": 20000.0}
    events = _events(snap)
    assert all(e.get("source") != "pax_decision" for e in events)


def test_only_entry_events_have_direction_long_or_short():
    snap = _drive(19995.0, 20000.0, 20003.0, 20005.0, 20006.0,
                  tape_delta=+0.45,
                  micro_events={"events": [
                      {"kind": "ICEBERG", "isBid": True, "price": 19950.0,
                       "size": 8000, "timeMs": 1},
                      {"kind": "SPOOF", "isBid": False, "price": 19950.0,
                       "size": 200, "timeMs": 1},
                  ]},
                  pull_stack={"rotation": "ROTATION_UP", "aggregateZ": 1.5})
    events = _events(snap)
    for e in events:
        if e["event_type"] in ("ACCEPTANCE", "REJECTION"):
            assert e["direction"] in ("LONG", "SHORT"), f"{e['event_type']} must direct"
        else:
            assert e["direction"] == "NONE", \
                f"{e['event_type']} must be direction NONE, got {e['direction']}"


def test_dedup_id_is_stable_across_repeated_polls():
    """Same state across two polls -> same id."""
    snap1 = _drive(19992.0)
    events1 = _events(snap1)
    w1 = _find(events1, "WATCH_LEVEL", "OR-H")
    # Another poll, same state.
    snap2 = _base_snap(mid=19992.0)
    snap2["or_levels"] = compute_or_levels(snap2)
    events2 = _events(snap2)
    w2 = _find(events2, "WATCH_LEVEL", "OR-H")
    assert w1["id"] == w2["id"], f"same state must yield same id; got {w1['id']} != {w2['id']}"


# ─── 9. Snapshot wiring ────────────────────────────────────────────────────

def test_compute_returns_empty_when_or_levels_missing():
    snap = _base_snap()
    snap["or_levels"] = None
    assert compute_institutional_chart_events(snap) == []


def test_severity_ranking_consistent():
    assert _CHART_SEVERITY_RANKS["ENTRY"] == 0
    assert _CHART_SEVERITY_RANKS["EXIT"] == 1
    assert _CHART_SEVERITY_RANKS["WARNING"] == 2
    assert _CHART_SEVERITY_RANKS["WATCH"] == 3
    assert _CHART_SEVERITY_RANKS["INFO"] == 4
