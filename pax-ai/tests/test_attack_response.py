"""Stage 3 tests for pax_ai.attack_response.

Pinned contracts (plan reports/pax-ai-attack-response-plan-2026-05-27.md):
  * OR-L sweep + bid iceberg + bid stack -> OR_L_SWEEP_RECLAIM / BULL_WATCH.
  * OR-L sweep + sell tape + no bid evidence -> OR_L_BREAK_ACCEPT / BEAR_WATCH.
  * OR-H sweep + ask iceberg + reject -> OR_H_SWEEP_FAIL / BEAR_WATCH.
  * OR-H acceptance + buy tape -> OR_H_BREAK_ACCEPT / BULL_WATCH.
  * Conflicting evidence -> NO_EDGE / NEUTRAL.
  * Location alone (sub-OR-L, no events) -> empty states.
  * Missing events -> empty states.
  * health=offline -> blocked.health=true + empty states + health="offline".
  * anchorMode != LIVE -> blocked.anchor=true + empty states.
  * Output enums constrained to closed vocabulary.

All tests are pure-function tests over a synthetic snap dict. No HTTP,
no Claude, no disk I/O.
"""

from __future__ import annotations

import pytest

from pax_ai import attack_response as ar


def _base_snap(*, levels=None, events=None, tape_delta=None, tape_n30=None,
               health="ok", anchor_mode="LIVE", alias="NQM6.CME@RITHMIC"):
    snap = {
        "alias": alias,
        "health": health,
        "session": {"code": "ACTIVE", "anchorMode": anchor_mode},
        "or_levels": {
            "orHigh": 30192.0,
            "orLow": 30145.0,
            "levels": levels or [],
        },
        "institutional_chart_events": events or [],
        "book": {"mid": 30150.0},
    }
    if tape_delta is not None or tape_n30 is not None:
        snap["tape_flow"] = {}
        if tape_delta is not None:
            snap["tape_flow"]["deltaScore"] = tape_delta
        if tape_n30 is not None:
            snap["tape_flow"]["prints30s"] = tape_n30
    return snap


def _lvl(label, price, side, proximity=True):
    return {"label": label, "price": price, "side": side,
            "proximity": proximity, "distance": -1.0}


def _ev(event_type, label, side, **kw):
    base = {
        "event_type": event_type,
        "label": label,
        "side": side,
        "direction": kw.get("direction", "NONE"),
        "marker_text": kw.get("marker_text", ""),
        "alias": "NQM6.CME@RITHMIC",
        "price": 30150.0,
    }
    base.update({k: v for k, v in kw.items()
                 if k not in ("direction", "marker_text")})
    return base


# --- core mappings -------------------------------------------------------

def test_or_l_sweep_with_bid_defense_is_bull_watch():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
            _ev("STACKING", "OR-L", "below"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1_700_000_000_000)
    assert out["health"] == "ok"
    assert out["blocked"] == {"health": False, "stale": False, "anchor": False}
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_L_SWEEP_RECLAIM
    assert s["bias"] == ar.BIAS_BULL
    assert s["attack"] == ar.ATTACK_SWEEP_LOW
    assert s["response"] == ar.RESPONSE_RECLAIMED
    assert "sweep_low" in s["drivers"]
    assert any(d in s["drivers"] for d in (ar.PASSIVE_BID_ICEBERG,
                                            ar.PASSIVE_BID_ABSORB))
    assert ar.BOOK_BID_STACK in s["drivers"]
    assert s["proven_edge"] is False
    assert s["sample_n"] is None
    assert s["edge_R_60s"] is None


def test_or_l_sweep_with_sell_acceptance_is_bear_watch():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
        ],
        tape_delta=-0.8, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_L_BREAK_ACCEPT
    assert s["bias"] == ar.BIAS_BEAR
    assert s["response"] == ar.RESPONSE_ACCEPTED
    assert ar.TAPE_SELL in s["drivers"]


def test_or_h_sweep_with_ask_defense_is_bear_watch():
    snap = _base_snap(
        levels=[_lvl("OR-H", 30192.0, "above")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-H", "above"),
            _ev("ICEBERG_DEFENSE", "OR-H", "above", marker_text="ICE-A"),
        ],
        tape_delta=-0.6, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_H_SWEEP_FAIL
    assert s["bias"] == ar.BIAS_BEAR
    assert s["attack"] == ar.ATTACK_SWEEP_HIGH
    assert any(d == ar.PASSIVE_ASK_ICEBERG for d in s["drivers"])


def test_or_h_acceptance_with_buy_tape_is_bull_watch():
    snap = _base_snap(
        levels=[_lvl("OR-H", 30192.0, "above")],
        events=[
            _ev("ACCEPTANCE", "OR-H", "above", direction="LONG"),
        ],
        tape_delta=0.7, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_H_BREAK_ACCEPT
    assert s["bias"] == ar.BIAS_BULL
    assert s["response"] == ar.RESPONSE_ACCEPTED


def test_or_l_acceptance_with_sell_tape_is_bear_watch():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("ACCEPTANCE", "OR-L", "below", direction="SHORT"),
        ],
        tape_delta=-0.7, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_L_BREAK_ACCEPT
    assert s["bias"] == ar.BIAS_BEAR


# --- conflict / no-edge --------------------------------------------------

def test_conflicting_evidence_yields_no_edge():
    # Both bid defense (BULL signal) AND sell tape (BEAR signal) at the
    # same sweep -> NO_EDGE / NEUTRAL, drivers list both.
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
        ],
        tape_delta=-0.8, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_NO_EDGE
    assert s["bias"] == ar.BIAS_NEUTRAL


# --- location-only never bears bias --------------------------------------

def test_location_alone_below_or_l_emits_nothing():
    # Below OR-L with NO chart events must not become BEAR_WATCH.
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[],
        tape_delta=-0.8, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert out["states"] == []
    assert out["blocked"] == {"health": False, "stale": False, "anchor": False}


# --- gates ---------------------------------------------------------------

def test_health_offline_blocks_all_states():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[_ev("LIQUIDITY_SWEEP", "OR-L", "below")],
        health="offline",
        tape_delta=0.7, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert out["health"] == "offline"
    assert out["states"] == []
    assert out["blocked"]["health"] is True


def test_anchor_not_live_blocks_all_states():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[_ev("LIQUIDITY_SWEEP", "OR-L", "below")],
        anchor_mode="LAST_KNOWN_STALE",
        tape_delta=0.7, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert out["states"] == []
    assert out["blocked"]["anchor"] is True
    # health stays "ok" because the bridge itself is fine
    assert out["health"] == "ok"


def test_stale_snapshot_blocks_all_states():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[_ev("LIQUIDITY_SWEEP", "OR-L", "below")],
        tape_delta=0.7, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1, age_ms=10_000)
    assert out["health"] == "stale"
    assert out["states"] == []
    assert out["blocked"]["stale"] is True


def test_none_snapshot_returns_blocked_offline():
    out = ar.compute_attack_response(None, now_ms=1)
    assert out["health"] == "offline"
    assert out["blocked"] == {"health": True, "stale": True, "anchor": True}
    assert out["states"] == []


# --- vocabulary guards ---------------------------------------------------

def test_all_emitted_states_use_closed_vocabulary():
    snap = _base_snap(
        levels=[
            _lvl("OR-L", 30145.0, "below"),
            _lvl("OR-H", 30192.0, "above"),
            _lvl("-1",   30135.0, "below"),
            _lvl("+1",   30202.0, "above"),
        ],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
            _ev("LIQUIDITY_SWEEP", "OR-H", "above"),
            _ev("ICEBERG_DEFENSE", "OR-H", "above", marker_text="ICE-A"),
            _ev("LIQUIDITY_SWEEP", "-1", "below"),
            _ev("ABSORPTION",      "-1", "below", marker_text="ABS-B"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) >= 2
    for s in out["states"]:
        assert s["state"] in ar.ALLOWED_STATES
        assert s["bias"] in ar.ALLOWED_BIASES


def test_extension_sweep_low_with_bid_defense_is_ext_low_exhaust():
    snap = _base_snap(
        levels=[_lvl("-1", 30135.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "-1", "below"),
            _ev("ICEBERG_DEFENSE", "-1", "below", marker_text="ICE-B"),
            _ev("STACKING",        "-1", "below"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_EXT_LOW_EXHAUST
    assert s["bias"] == ar.BIAS_BULL


# --- absorption-only TOUCH path ------------------------------------------

def test_touched_or_l_with_bid_absorb_is_absorb_hold():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("TOUCHED_LEVEL", "OR-L", "below"),
            _ev("ABSORPTION",    "OR-L", "below", marker_text="ABS-B"),
        ],
        tape_delta=0.0, tape_n30=20,
    )
    out = ar.compute_attack_response(snap, now_ms=1)
    assert len(out["states"]) == 1
    s = out["states"][0]
    assert s["state"] == ar.STATE_OR_L_ABSORB_HOLD
    assert s["bias"] == ar.BIAS_BULL


# --- dedup, id stability -------------------------------------------------

def test_state_id_is_stable_within_one_second_bucket():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    a = ar.compute_attack_response(snap, now_ms=1_700_000_000_000)
    b = ar.compute_attack_response(snap, now_ms=1_700_000_000_500)
    assert a["states"][0]["id"] == b["states"][0]["id"]
    c = ar.compute_attack_response(snap, now_ms=1_700_000_001_500)
    assert a["states"][0]["id"] != c["states"][0]["id"]


# --- helper unit checks --------------------------------------------------

@pytest.mark.parametrize("text,side,expected", [
    ("ICE-A", "below", True),
    ("ICE-B", "above", False),
    ("ABS-A", "below", True),
    ("ABS-B", "above", False),
    ("STACK", "above", True),
    ("STACK", "below", False),
    ("",      "above", True),
    ("",      "below", False),
])
def test_is_above_marker_inference(text, side, expected):
    assert ar._is_above_marker(text, side) is expected


@pytest.mark.parametrize("label,is_below", [
    ("OR-L", True), ("OR-H", False),
    ("-1", True),   ("-3", True),
    ("+1", False),  ("+4", False),
    ("",   False),  ("foo", False),
])
def test_label_below_side(label, is_below):
    assert ar._label_is_below_side(label) is is_below


def test_classify_tape_flow_thresholds():
    assert ar._classify_tape_flow({"deltaScore": 0.8, "prints30s": 30}) == ar.TAPE_BUY
    assert ar._classify_tape_flow({"deltaScore": -0.8, "prints30s": 30}) == ar.TAPE_SELL
    assert ar._classify_tape_flow({"deltaScore": 0.05, "prints30s": 30}) == ar.TAPE_MIXED
    assert ar._classify_tape_flow({"deltaScore": 0.6, "prints30s": 2}) == ar.TAPE_THIN
    assert ar._classify_tape_flow(None) == ar.TAPE_THIN
    assert ar._classify_tape_flow({"_error": "down"}) == ar.TAPE_THIN


# --- regime veto (operator request 2026-05-28: chart labels should match
#     what institutional_flow is seeing) ----------------------------------

def _bearish_or_h_snap(institutional_flow=None):
    """Build a snap that would normally produce BEAR_WATCH on OR-H."""
    snap = _base_snap(
        levels=[_lvl("OR-H", 30192.0, "above")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-H", "above"),
            _ev("ICEBERG_DEFENSE", "OR-H", "above", marker_text="ICE-A"),
        ],
        tape_delta=-0.4, tape_n30=15,
    )
    if institutional_flow is not None:
        snap["institutional_flow"] = institutional_flow
    return snap


def test_regime_accumulation_high_conviction_vetoes_bear_watch():
    snap = _bearish_or_h_snap(institutional_flow={
        "regime": "ACCUMULATION",
        "conviction": 0.65,
    })
    out = ar.compute_attack_response(snap)
    # The label that WOULD have been BEAR_WATCH at OR-H is now suppressed.
    biases = [r["bias"] for r in out["states"]]
    assert ar.BIAS_BEAR not in biases
    # Veto reason recorded on the suppressed row
    for r in out["states"]:
        if r.get("veto_reason"):
            assert "ACCUMULATION" in r["veto_reason"]
            assert r["state"] == ar.STATE_NO_EDGE
            assert r["bias"] == ar.BIAS_NEUTRAL


def test_regime_distribution_high_conviction_vetoes_bull_watch():
    snap = _base_snap(
        levels=[_lvl("OR-L", 30145.0, "below")],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
            _ev("STACKING", "OR-L", "below"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    snap["institutional_flow"] = {"regime": "DISTRIBUTION", "conviction": 0.55}
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    # BULL_WATCH that would normally fire is now suppressed
    assert ar.BIAS_BULL not in biases


def test_regime_veto_blocked_below_min_conviction():
    """Conviction below 0.30 does NOT trigger the veto -- the regime is
    too weak to confidently override the classifier."""
    snap = _bearish_or_h_snap(institutional_flow={
        "regime": "ACCUMULATION",
        "conviction": 0.20,  # below 0.30 threshold
    })
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    # Veto did not fire -> BEAR_WATCH still present
    assert ar.BIAS_BEAR in biases


def test_regime_balanced_does_not_veto():
    """When institutional_flow is BALANCED, attack_response classifier is
    trusted as-is. No veto, no suppression."""
    snap = _bearish_or_h_snap(institutional_flow={
        "regime": "BALANCED",
        "conviction": 0.5,
    })
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    assert ar.BIAS_BEAR in biases


def test_regime_transition_does_not_veto():
    """TRANSITION regime also does not trigger veto -- direction is
    ambiguous so don't suppress."""
    snap = _bearish_or_h_snap(institutional_flow={
        "regime": "TRANSITION",
        "conviction": 0.7,
    })
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    assert ar.BIAS_BEAR in biases


def test_proximity_false_suppresses_label_even_with_events():
    """Operator bug 2026-05-28: chart showed BEAR_WATCH at +2 when mid
    was already 40pts past +2. The dashboard sets proximity=False when
    mid is too far from the level; classifier must respect this and
    not emit states for stale levels."""
    snap = _base_snap(
        levels=[{"label": "+2", "price": 30235.75, "side": "above",
                 "proximity": False, "distance": -40.0}],
        events=[
            _ev("ICEBERG_DEFENSE", "+2", "above", marker_text="ICE-A"),
        ],
        tape_delta=-0.4, tape_n30=15,
    )
    out = ar.compute_attack_response(snap)
    # No state should fire because the level is not in proximity
    assert out["states"] == []


def test_proximity_true_still_emits_normally():
    """Sanity: when proximity=True, classifier emits states as before."""
    snap = _base_snap(
        levels=[{"label": "OR-L", "price": 30145.0, "side": "below",
                 "proximity": True, "distance": -1.0}],
        events=[
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
            _ev("STACKING", "OR-L", "below"),
        ],
        tape_delta=0.6, tape_n30=20,
    )
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    assert ar.BIAS_BULL in biases


def test_no_institutional_flow_field_does_not_break():
    """When snap has no institutional_flow at all (old contract),
    classifier behaves the same as before."""
    snap = _bearish_or_h_snap(institutional_flow=None)
    out = ar.compute_attack_response(snap)
    biases = [r["bias"] for r in out["states"]]
    assert ar.BIAS_BEAR in biases
