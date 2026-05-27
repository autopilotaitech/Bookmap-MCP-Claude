"""Contract tests for compute_level_edge_payload.

These tests pin the aggregate /api/pax/levels/edge endpoint shape. They
build snapshots from literals and never touch the live bridge or poller.
"""

from __future__ import annotations

import copy

import pytest

from pax_ai.level_edge import compute_level_edge_payload


_NOW_MS = 1_700_000_000_000


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _live_snap_long():
    """LIVE healthy snap with one OR-H level that is actionable LONG."""
    return {
        "alias":  "NQM6.CME@RITHMIC",
        "health": "ok",
        "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
        "news":    {"blocked": False, "label": None},
        "book":    {"mid": 20114.25, "bestBid": 20114.00,
                    "bestAsk": 20114.50, "spread": 0.50},
        "flow":    {"regime": "TRENDING_UP", "regimeConfidence": 0.70},
        "or_levels": {
            "orHigh": 20112.00, "orLow": 20088.00, "orWidthPts": 24.0,
            "middleLock": False, "inProximity": True,
            "levels": [
                {"label": "OR-H", "price": 20112.00, "side": "high",
                 "distance": 2.25, "proximity": True,
                 "decision": "ENTER_LONG_FOLLOW",
                 "confidence": 0.55,
                 "composite_score": 0.55,
                 "composite_dir": "FOLLOW_LONG",
                 "composite": {"direction": "FOLLOW_LONG",
                                "confidence": 0.55, "coverage": 0.70},
                 "institutional_thesis": {
                     "execution_read":    "PAY_FOR_TRADE",
                     "state":             "ACCEPTED",
                     "thesis":            "BREAK_LONG",
                     "liquidity_quality": "DEEP",
                     "aggressor_flow":    "BUY",
                 }},
            ],
        },
        "vwap_bias":  {"sigma_z": 0.5, "regime": "INSIDE_BAND"},
        "conviction": {"score": 0.40, "trend": "BULL_LEAN",
                       "trajectory": "RISING", "anchorMode": "LIVE"},
        "micro_events": {"events": []},
    }


def _live_snap_short():
    snap = _live_snap_long()
    snap["book"]["mid"] = 20086.00
    snap["flow"]["regime"] = "TRENDING_DOWN"
    snap["or_levels"]["levels"] = [
        {"label": "OR-L", "price": 20088.00, "side": "low",
         "distance": -2.00, "proximity": True,
         "decision": "ENTER_SHORT_FOLLOW",
         "confidence": 0.60,
         "composite_score": -0.60,
         "composite_dir": "FOLLOW_SHORT",
         "composite": {"direction": "FOLLOW_SHORT",
                        "confidence": 0.60, "coverage": 0.70},
         "institutional_thesis": {"execution_read": "PAY_FOR_TRADE"}},
    ]
    snap["conviction"]["trend"] = "BEAR_LEAN"
    snap["conviction"]["trajectory"] = "FALLING"
    return snap


def _payload(snap, **kw):
    kw.setdefault("as_of_ms", _NOW_MS)
    kw.setdefault("age_ms", 0)
    kw.setdefault("now_ms", _NOW_MS)
    return compute_level_edge_payload(snap, **kw)


def _one(snap, **kw):
    body = _payload(snap, **kw)
    assert body["levels"], "expected at least one level"
    return body, body["levels"][0]


# ---------------------------------------------------------------------------
# Empty / missing inputs
# ---------------------------------------------------------------------------

def test_none_snapshot_returns_empty_levels_and_all_blocks():
    body = compute_level_edge_payload(None, as_of_ms=None, age_ms=None)
    assert body["levels"] == []
    assert body["mid"] is None
    assert body["blocked"] == {
        "health_ok": False, "news": False, "session": False,
        "stale": True, "anchor": True,
    }
    assert body["stale"] is True


def test_missing_or_levels_does_not_crash():
    snap = _live_snap_long()
    snap.pop("or_levels", None)
    body = _payload(snap)
    assert body["levels"] == []
    assert body["alias"] == "NQM6.CME@RITHMIC"


def test_empty_levels_list_returns_empty():
    snap = _live_snap_long()
    snap["or_levels"]["levels"] = []
    body = _payload(snap)
    assert body["levels"] == []


def test_missing_institutional_thesis_does_not_crash():
    snap = _live_snap_long()
    for L in snap["or_levels"]["levels"]:
        L.pop("institutional_thesis", None)
    body, lvl = _one(snap)
    # No thesis -> thesis_gated_size_tier collapses to raw size_tier,
    # which for confidence=0.55 is FULL -> still actionable.
    assert lvl["actionable"] is True
    assert lvl["direction"] == "LONG"


# ---------------------------------------------------------------------------
# Honesty contract: score_R only, fake probability fields absent
# ---------------------------------------------------------------------------

_HONEST_LEVEL_KEYS = {
    "label", "price", "side", "distance", "distance_abs", "proximity",
    "level_relevant",
    "direction", "raw_direction", "composite_dir", "confidence", "score_R",
    "stop_price", "size_tier", "actionable", "color_hint", "reasons",
    "setup", "top_drivers", "blocked_reason", "snapshot_ts_ms",
    "mid_at_signal",
}


def test_score_R_present_expected_R_absent():
    _, lvl = _one(_live_snap_long())
    assert "score_R" in lvl
    assert "expected_R" not in lvl
    assert lvl["score_R"] is not None


def test_prob_pay_for_trade_absent():
    _, lvl = _one(_live_snap_long())
    assert "prob_pay_for_trade" not in lvl


def test_prob_reach_next_rung_absent():
    _, lvl = _one(_live_snap_long())
    assert "prob_reach_next_rung" not in lvl


def test_level_keys_are_exactly_the_honest_set():
    _, lvl = _one(_live_snap_long())
    assert set(lvl.keys()) == _HONEST_LEVEL_KEYS


# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

_VALID_DIRECTIONS = frozenset(["LONG", "SHORT", "WAIT"])
_VALID_COLORS = frozenset(["positive", "negative", "neutral"])
_VALID_TIERS = frozenset(["FULL", "HALF", "NONE"])


@pytest.mark.parametrize("name,builder", [
    ("long",        _live_snap_long),
    ("short",       _live_snap_short),
])
def test_direction_closed_vocab(name, builder):
    body = _payload(builder())
    for lvl in body["levels"]:
        assert lvl["direction"] in _VALID_DIRECTIONS, name


@pytest.mark.parametrize("name,builder", [
    ("long",        _live_snap_long),
    ("short",       _live_snap_short),
])
def test_color_hint_closed_vocab(name, builder):
    body = _payload(builder())
    for lvl in body["levels"]:
        assert lvl["color_hint"] in _VALID_COLORS, name


def test_size_tier_closed_vocab():
    body = _payload(_live_snap_long())
    for lvl in body["levels"]:
        assert lvl["size_tier"] in _VALID_TIERS


# ---------------------------------------------------------------------------
# Direction / color mapping
# ---------------------------------------------------------------------------

def test_actionable_long_is_positive():
    _, lvl = _one(_live_snap_long())
    assert lvl["actionable"] is True
    assert lvl["direction"] == "LONG"
    assert lvl["color_hint"] == "positive"


def test_actionable_short_is_negative():
    _, lvl = _one(_live_snap_short())
    assert lvl["actionable"] is True
    assert lvl["direction"] == "SHORT"
    assert lvl["color_hint"] == "negative"


def test_low_confidence_is_neutral_wait():
    snap = _live_snap_long()
    snap["or_levels"]["levels"][0]["confidence"] = 0.20
    snap["or_levels"]["levels"][0]["composite"]["confidence"] = 0.20
    _, lvl = _one(snap)
    assert lvl["actionable"] is False
    assert lvl["direction"] == "WAIT"
    assert lvl["color_hint"] == "neutral"


def test_wait_decision_is_neutral():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "WAIT"
    L["composite_dir"] = None
    L["composite"]["direction"] = "WAIT"
    _, lvl = _one(snap)
    assert lvl["actionable"] is False
    assert lvl["direction"] == "WAIT"
    assert lvl["color_hint"] == "neutral"


def test_fade_long_maps_to_long():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "ENTER_LONG_FADE"
    L["composite_dir"] = "FADE_LONG"
    L["composite"]["direction"] = "FADE_LONG"
    _, lvl = _one(snap)
    assert lvl["direction"] == "LONG"
    assert lvl["color_hint"] == "positive"


def test_fade_short_maps_to_short():
    snap = _live_snap_short()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "ENTER_SHORT_FADE"
    L["composite_dir"] = "FADE_SHORT"
    L["composite"]["direction"] = "FADE_SHORT"
    _, lvl = _one(snap)
    assert lvl["direction"] == "SHORT"
    assert lvl["color_hint"] == "negative"


# ---------------------------------------------------------------------------
# Global gates: every level becomes WAIT / neutral when a gate is hit
# ---------------------------------------------------------------------------

def _assert_all_levels_non_actionable(body, reason: str):
    assert body["levels"], "fixture lost its level row"
    for lvl in body["levels"]:
        assert lvl["actionable"] is False, reason
        assert lvl["direction"] == "WAIT", reason
        assert lvl["color_hint"] == "neutral", reason


def test_stale_snapshot_blocks_all_levels():
    body = _payload(_live_snap_long(), age_ms=6_000, stale_threshold_ms=5_000)
    assert body["stale"] is True
    assert body["blocked"]["stale"] is True
    _assert_all_levels_non_actionable(body, "stale")


@pytest.mark.parametrize("mode", ["LAST_KNOWN_STALE", "FALLBACK"])
def test_anchor_not_live_blocks_all_levels(mode):
    snap = _live_snap_long()
    snap["session"]["anchorMode"] = mode
    snap["conviction"]["anchorMode"] = mode
    body = _payload(snap)
    assert body["blocked"]["anchor"] is True
    _assert_all_levels_non_actionable(body, f"anchor={mode}")


def test_missing_anchor_mode_blocks_all_levels():
    snap = _live_snap_long()
    snap["session"].pop("anchorMode", None)
    snap["conviction"].pop("anchorMode", None)
    body = _payload(snap)
    assert body["blocked"]["anchor"] is True
    _assert_all_levels_non_actionable(body, "anchor=missing")


def test_news_blackout_blocks_all_levels():
    snap = _live_snap_long()
    snap["news"] = {"blocked": True, "label": "FOMC_T_MINUS_30"}
    body = _payload(snap)
    assert body["blocked"]["news"] is True
    _assert_all_levels_non_actionable(body, "news_blackout")


@pytest.mark.parametrize("code", ["PRE_MARKET", "POST_MARKET",
                                    "OR_FORMING", "CLOSE_RISK"])
def test_blocking_session_code_blocks_all_levels(code):
    snap = _live_snap_long()
    snap["session"]["code"] = code
    body = _payload(snap)
    assert body["blocked"]["session"] is True
    _assert_all_levels_non_actionable(body, f"session={code}")


def test_bridge_offline_blocks_all_levels():
    snap = _live_snap_long()
    snap["health"] = "offline"
    body = _payload(snap)
    assert body["blocked"]["health_ok"] is False
    _assert_all_levels_non_actionable(body, "health_offline")


# ---------------------------------------------------------------------------
# Per-level gating: thesis NONE collapses size_tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exec_read", ["STAND_DOWN", "WAIT_FOR_CONFIRM",
                                         "SCRATCH_READY"])
def test_thesis_collapse_makes_level_non_actionable(exec_read):
    snap = _live_snap_long()
    snap["or_levels"]["levels"][0]["institutional_thesis"][
        "execution_read"] = exec_read
    body, lvl = _one(snap)
    assert lvl["size_tier"] == "NONE", \
        f"{exec_read} should collapse thesis_gated_size_tier to NONE"
    assert lvl["actionable"] is False
    assert lvl["direction"] == "WAIT"
    assert lvl["color_hint"] == "neutral"


# ---------------------------------------------------------------------------
# score_R is the edge_calculus expected_R number (forwarded, not invented)
# ---------------------------------------------------------------------------

def test_score_R_forwards_edge_calculus_expected_R():
    from pax_ai import edge_calculus
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    ec = edge_calculus.level_edge(L, snap)
    body, lvl = _one(snap)
    assert lvl["score_R"] == ec["expected_R"]
    assert lvl["stop_price"] == ec["invalidation_price"]


# ---------------------------------------------------------------------------
# Reasons are short field-grounded strings
# ---------------------------------------------------------------------------

def test_reasons_are_short_string_list():
    _, lvl = _one(_live_snap_long())
    assert isinstance(lvl["reasons"], list)
    assert len(lvl["reasons"]) <= 4
    for r in lvl["reasons"]:
        assert isinstance(r, str)
        assert len(r) <= 120


# ---------------------------------------------------------------------------
# Top-level payload shape
# ---------------------------------------------------------------------------

_TOP_KEYS = {"alias", "asOfMs", "ageMs", "stale", "mid",
              "anchorMode", "blocked", "levels"}
_BLOCKED_KEYS = {"health_ok", "news", "session", "stale", "anchor"}


def test_top_level_payload_keys_are_fixed():
    body = _payload(_live_snap_long())
    assert set(body.keys()) == _TOP_KEYS
    assert set(body["blocked"].keys()) == _BLOCKED_KEYS


def test_mid_is_rounded_to_two_decimals():
    snap = _live_snap_long()
    snap["book"]["mid"] = 20114.2567
    body = _payload(snap)
    assert body["mid"] == 20114.26


# ---------------------------------------------------------------------------
# Slice 1: direction-source priority, raw_direction, setup, top_drivers,
# blocked_reason, snapshot_ts_ms, mid_at_signal
# ---------------------------------------------------------------------------


def test_decision_wait_with_composite_follow_long_produces_long():
    """When decision='WAIT' but composite.direction='FOLLOW_LONG', the level
    should still go LONG/actionable provided the other gates pass."""
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "WAIT"
    L["composite_dir"] = None  # legacy field intentionally cleared
    # composite.direction kept at FOLLOW_LONG from the fixture
    _, lvl = _one(snap)
    assert lvl["raw_direction"] == "LONG"
    assert lvl["direction"] == "LONG"
    assert lvl["composite_dir"] == "FOLLOW_LONG"
    assert lvl["actionable"] is True
    assert lvl["blocked_reason"] is None


def test_decision_wait_and_composite_wait_blocks_no_direction():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "WAIT"
    L["composite_dir"] = None
    L["composite"]["direction"] = "WAIT"
    _, lvl = _one(snap)
    assert lvl["raw_direction"] == "WAIT"
    assert lvl["direction"] == "WAIT"
    assert lvl["composite_dir"] is None
    assert lvl["actionable"] is False
    assert lvl["blocked_reason"] == "no_direction"


def test_decision_overrides_composite_when_both_directional():
    """If decision is ENTER_LONG_FOLLOW and composite.direction is FADE_SHORT,
    decision wins (it is first in the priority chain)."""
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "ENTER_LONG_FOLLOW"
    L["composite"]["direction"] = "FADE_SHORT"
    _, lvl = _one(snap)
    assert lvl["composite_dir"] == "FOLLOW_LONG"
    assert lvl["raw_direction"] == "LONG"
    assert lvl["direction"] == "LONG"


def test_low_confidence_blocked_reason():
    snap = _live_snap_long()
    snap["or_levels"]["levels"][0]["confidence"] = 0.20
    _, lvl = _one(snap)
    assert lvl["actionable"] is False
    assert lvl["blocked_reason"] == "low_confidence"
    # Composite direction was still inferable -> raw_direction preserved.
    assert lvl["raw_direction"] == "LONG"
    assert lvl["direction"] == "WAIT"


def test_thesis_gate_blocked_reason_when_direction_valid():
    snap = _live_snap_long()
    snap["or_levels"]["levels"][0]["institutional_thesis"]["execution_read"] = "WAIT_FOR_CONFIRM"
    _, lvl = _one(snap)
    assert lvl["actionable"] is False
    assert lvl["blocked_reason"] == "thesis_gate"
    assert lvl["raw_direction"] == "LONG"
    assert lvl["direction"] == "WAIT"


def test_missing_stop_blocks_actionability():
    """If invalidation_price cannot be computed (or_low None), the level must
    not be actionable and blocked_reason must be 'missing_stop'."""
    snap = _live_snap_long()
    snap["or_levels"]["orLow"] = None  # invalidation needs or_low for LONG
    body, lvl = _one(snap)
    assert lvl["stop_price"] is None
    assert lvl["actionable"] is False
    assert lvl["blocked_reason"] == "missing_stop"
    # Composite direction still computed -> raw_direction preserved.
    assert lvl["raw_direction"] == "LONG"
    assert lvl["direction"] == "WAIT"


@pytest.mark.parametrize("gate,setter,expected", [
    ("stale",   lambda s: s.update({}),         "stale"),
    ("anchor",  lambda s: s["session"].update({"anchorMode": "FALLBACK"}),
                                                "anchor"),
    ("news",    lambda s: s.update({"news": {"blocked": True}}), "news"),
    ("session", lambda s: s["session"].update({"code": "OR_FORMING"}), "session"),
    ("health",  lambda s: s.update({"health": "offline"}), "health"),
])
def test_global_blocked_reason_priority(gate, setter, expected):
    snap = _live_snap_long()
    setter(snap)
    if gate == "stale":
        body = _payload(snap, age_ms=10_000, stale_threshold_ms=5_000)
    else:
        body = _payload(snap)
    for lvl in body["levels"]:
        assert lvl["blocked_reason"] == expected
        assert lvl["actionable"] is False
        assert lvl["direction"] == "WAIT"


def test_actionable_level_has_null_blocked_reason():
    _, lvl = _one(_live_snap_long())
    assert lvl["actionable"] is True
    assert lvl["blocked_reason"] is None


def test_setup_or_break_follow_long():
    _, lvl = _one(_live_snap_long())
    assert lvl["label"] == "OR-H"
    assert lvl["setup"] == "OR_BREAK_FOLLOW"


def test_setup_or_break_follow_short():
    _, lvl = _one(_live_snap_short())
    assert lvl["label"] == "OR-L"
    assert lvl["setup"] == "OR_BREAK_FOLLOW"


def test_setup_fade_long_at_or_l():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["label"] = "OR-L"
    L["side"] = "low"
    L["decision"] = "ENTER_LONG_FADE"
    L["composite"]["direction"] = "FADE_LONG"
    _, lvl = _one(snap)
    assert lvl["setup"] == "LEVEL_FADE_LONG"


def test_setup_fade_short_at_or_h():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "ENTER_SHORT_FADE"
    L["composite"]["direction"] = "FADE_SHORT"
    _, lvl = _one(snap)
    assert lvl["setup"] == "LEVEL_FADE_SHORT"


def test_setup_extension_follow_long():
    snap = _live_snap_long()
    snap["or_levels"]["levels"] = [{
        "label": "+1", "price": 20177.00, "side": "above",
        "distance": 62.75, "proximity": True,
        "decision": "ENTER_LONG_FOLLOW",
        "confidence": 0.55,
        "composite": {"direction": "FOLLOW_LONG", "confidence": 0.55,
                       "drivers": [{"name": "pull_stack",
                                     "score": 0.4, "weight": 0.22}]},
        "institutional_thesis": {"execution_read": "PAY_FOR_TRADE"},
    }]
    _, lvl = _one(snap)
    assert lvl["setup"] == "OR_BREAK_FOLLOW"


def test_setup_unknown_when_label_missing():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["label"] = None
    _, lvl = _one(snap)
    assert lvl["setup"] == "UNKNOWN_SETUP"


def test_setup_unknown_when_direction_label_mismatch():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["label"] = "OR-L"
    # decision says FOLLOW_LONG but level is on the LOW side -> mismatch
    _, lvl = _one(snap)
    assert lvl["setup"] == "UNKNOWN_SETUP"


def test_top_drivers_capped_at_three_and_whitelisted():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["composite"]["drivers"] = [
        {"name": "pull_stack",         "score":  0.50, "weight": 0.22},
        {"name": "tape",               "score":  0.30, "weight": 0.18},
        {"name": "vwap",               "score": -0.20, "weight": 0.08},
        {"name": "micro",              "score":  0.10, "weight": 0.16},
        {"name": "orderbook",          "score":  0.05, "weight": 0.12},
        {"name": "lt_liquidity",       "score":  0.0,  "weight": 0.10},
        {"name": "totally_made_up",    "score":  0.99, "weight": 0.99},
    ]
    _, lvl = _one(snap)
    assert len(lvl["top_drivers"]) == 3
    for n in lvl["top_drivers"]:
        assert n in {"pull_stack", "tape", "vwap", "micro", "orderbook",
                       "volume_profile", "session_conviction", "lt_liquidity"}
    # Highest |score * weight|: pull_stack 0.110, tape 0.054, micro 0.016
    assert lvl["top_drivers"][0] == "pull_stack"


def test_top_drivers_empty_when_composite_missing():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L.pop("composite", None)
    _, lvl = _one(snap)
    assert lvl["top_drivers"] == []


def test_top_drivers_filters_zero_contribution():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["composite"]["drivers"] = [
        {"name": "pull_stack", "score": 0.0,  "weight": 0.22},
        {"name": "tape",       "score": 0.0,  "weight": 0.0},
    ]
    _, lvl = _one(snap)
    assert lvl["top_drivers"] == []


def test_snapshot_ts_ms_and_mid_at_signal_populated():
    _, lvl = _one(_live_snap_long())
    assert lvl["snapshot_ts_ms"] == _NOW_MS
    assert lvl["mid_at_signal"] == 20114.25


def test_non_actionable_preserves_raw_direction():
    """Chart-facing direction collapses to WAIT but raw_direction must keep
    the upstream LONG/SHORT so the outcome log can audit suppressed
    signals."""
    snap = _live_snap_long()
    snap["or_levels"]["levels"][0]["institutional_thesis"]["execution_read"] = "STAND_DOWN"
    _, lvl = _one(snap)
    assert lvl["actionable"] is False
    assert lvl["direction"] == "WAIT"
    assert lvl["raw_direction"] == "LONG"


def test_no_ev_or_probability_fields_leak():
    body = _payload(_live_snap_long())
    forbidden = {"expected_R", "prob_pay_for_trade", "prob_reach_next_rung"}
    for lvl in body["levels"]:
        assert not (forbidden & set(lvl.keys()))


# ---------------------------------------------------------------------------
# Slice 1B: level_relevant proximity gate
# ---------------------------------------------------------------------------


def test_level_relevant_true_when_proximity_true():
    _, lvl = _one(_live_snap_long())
    assert lvl["proximity"] is True
    assert lvl["level_relevant"] is True
    assert lvl["actionable"] is True
    assert lvl["blocked_reason"] is None


def test_proximity_false_blocks_with_not_near_level():
    """Otherwise-actionable row with proximity=False must be blocked by the
    not_near_level gate. raw_direction / setup / top_drivers must still be
    emitted so the outcome log sees the suppressed signal."""
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["proximity"] = False
    _, lvl = _one(snap)
    assert lvl["level_relevant"] is False
    assert lvl["actionable"] is False
    assert lvl["direction"] == "WAIT"
    assert lvl["blocked_reason"] == "not_near_level"
    # Far-away raw signal still preserved.
    assert lvl["raw_direction"] == "LONG"
    assert lvl["composite_dir"] == "FOLLOW_LONG"
    assert lvl["setup"] == "OR_BREAK_FOLLOW"
    assert isinstance(lvl["top_drivers"], list)


def test_proximity_true_can_become_actionable():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["proximity"] = True
    _, lvl = _one(snap)
    assert lvl["level_relevant"] is True
    assert lvl["actionable"] is True
    assert lvl["direction"] == "LONG"
    assert lvl["blocked_reason"] is None


def test_missing_proximity_defaults_false_not_actionable():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L.pop("proximity", None)
    _, lvl = _one(snap)
    assert lvl["level_relevant"] is False
    assert lvl["actionable"] is False
    assert lvl["blocked_reason"] == "not_near_level"


def test_no_direction_beats_not_near_level():
    """blocked_reason priority: no_direction must win over not_near_level."""
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["decision"] = "WAIT"
    L["composite_dir"] = None
    L["composite"]["direction"] = "WAIT"
    L["proximity"] = False
    _, lvl = _one(snap)
    assert lvl["blocked_reason"] == "no_direction"


def test_not_near_level_beats_low_confidence():
    """blocked_reason priority: not_near_level must win over low_confidence."""
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["confidence"] = 0.10  # would otherwise be low_confidence
    L["proximity"] = False
    _, lvl = _one(snap)
    assert lvl["blocked_reason"] == "not_near_level"


def test_distance_abs_populated_from_signed_distance():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L["distance"] = -7.5
    _, lvl = _one(snap)
    assert lvl["distance_abs"] == 7.5


def test_distance_abs_none_when_distance_missing():
    snap = _live_snap_long()
    L = snap["or_levels"]["levels"][0]
    L.pop("distance", None)
    _, lvl = _one(snap)
    assert lvl["distance_abs"] is None


def test_no_ev_or_probability_fields_leak_with_level_relevant():
    """Re-pin the honesty contract after Slice 1B field additions."""
    body = _payload(_live_snap_long())
    forbidden = {"expected_R", "prob_pay_for_trade", "prob_reach_next_rung"}
    for lvl in body["levels"]:
        assert not (forbidden & set(lvl.keys()))
        assert "level_relevant" in lvl
        assert "distance_abs" in lvl


def test_multiple_levels_each_get_a_card():
    snap = _live_snap_long()
    snap["or_levels"]["levels"].append({
        "label": "+1", "price": 20177.00, "side": "above",
        "distance": 62.75, "proximity": False,
        "decision": "ENTER_LONG_FOLLOW",
        "confidence": 0.40,
        "composite_dir": "FOLLOW_LONG",
        "composite": {"direction": "FOLLOW_LONG", "confidence": 0.40,
                       "coverage": 0.60},
        "institutional_thesis": {"execution_read": "PAY_FOR_TRADE"},
    })
    body = _payload(snap)
    assert len(body["levels"]) == 2
    labels = {lvl["label"] for lvl in body["levels"]}
    assert labels == {"OR-H", "+1"}
