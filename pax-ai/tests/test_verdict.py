"""Contract tests for compute_verdict.

These tests pin the deterministic six-field verdict shape. They build
snapshots from literals and never touch the live bridge or the poller.
"""

from __future__ import annotations

import copy
import re

import pytest

from pax_ai.verdict import (
    compute_verdict, SETUPS, BIASES, CONFIDENCES,
    NQ_TICK,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A fixed wall-clock so as_of_ms / now_ms comparisons are deterministic.
_NOW_MS = 1_700_000_000_000


def _live_snap():
    """Minimal LIVE / healthy snapshot. Default state is chop at OR-H."""
    return {
        "alias":  "NQM6.CME@RITHMIC",
        "health": "ok",
        "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
        "news":    {"blocked": False, "label": None},
        "gates":   {"vwap_or": "ALLOW_LONG"},
        "book":    {"mid": 20100.00, "bestBid": 20099.75, "bestAsk": 20100.25,
                    "spread": 0.50},
        "or_levels": {
            "orHigh": 20112.00, "orLow":  20088.00,
            "middleLock": True, "inProximity": False,
            "levels": [
                {"label": "OR-H", "price": 20112.00, "side": "high",
                 "distance": 12.00, "proximity": False,
                 "composite_dir": None,
                 "confidence": 0.20,
                 "composite_score": 0.05,
                 "composite": {"direction": "WAIT", "confidence": 0.20,
                               "coverage": 0.60},
                 "institutional_thesis": {"execution_read": "WAIT_FOR_CONFIRM",
                                          "state": "APPROACHING",
                                          "thesis": "NONE",
                                          "liquidity": "NORMAL"}},
            ],
        },
        "vwap_bias":  {"sigma_z": 0.30, "regime": "INSIDE_BAND",
                       "label": "NEUTRAL"},
        "conviction": {"score": 0.05, "trend": "CHOP", "trajectory": "FLAT",
                       "anchorMode": "LIVE"},
        "micro_events": {"events": []},
    }


def _v(snap, **kw):
    """Helper to invoke compute_verdict with fixed timing defaults."""
    kw.setdefault("as_of_ms", _NOW_MS)
    kw.setdefault("age_ms", 0)
    kw.setdefault("now_ms", _NOW_MS)
    return compute_verdict(snap, **kw)


# ---------------------------------------------------------------------------
# Scenario builders
# ---------------------------------------------------------------------------

def _level_defend_snap():
    snap = _live_snap()
    snap["book"]["mid"] = 20111.75
    snap["or_levels"]["levels"][0]["institutional_thesis"]["execution_read"] = "STAND_DOWN"
    return snap


def _or_break_follow_long_snap():
    snap = _live_snap()
    snap["book"]["mid"] = 20115.25  # > OR-H 20112
    L = snap["or_levels"]["levels"][0]
    L["composite_dir"] = "FOLLOW_LONG"
    L["composite"] = {"direction": "FOLLOW_LONG", "confidence": 0.65,
                       "coverage": 0.80}
    L["confidence"] = 0.65
    snap["vwap_bias"] = {"sigma_z": 0.5, "regime": "MEAN_REVERT",
                          "label": "BEARISH"}
    snap["conviction"]["trajectory"] = "RISING"
    return snap


def _or_break_follow_short_snap():
    snap = _live_snap()
    snap["book"]["mid"] = 20085.00  # < OR-L 20088
    snap["or_levels"]["middleLock"] = False
    snap["or_levels"]["levels"] = [
        {"label": "OR-L", "price": 20088.00, "side": "low",
         "distance": -3.00, "proximity": True,
         "composite_dir": "FOLLOW_SHORT",
         "confidence": 0.55,
         "composite": {"direction": "FOLLOW_SHORT", "confidence": 0.55,
                        "coverage": 0.70},
         "institutional_thesis": {"execution_read": "WAIT_FOR_CONFIRM"}},
    ]
    snap["conviction"]["trajectory"] = "FALLING"
    return snap


def _or_break_fade_short_snap():
    """Price broke above OR-H, now back inside. Fade SHORT into the range."""
    snap = _live_snap()
    snap["book"]["mid"] = 20109.75  # back inside (< 20112)
    L = snap["or_levels"]["levels"][0]
    L["composite_dir"] = "FADE_SHORT"
    L["composite"] = {"direction": "FADE_SHORT", "confidence": 0.55,
                       "coverage": 0.70}
    L["confidence"] = 0.55
    snap["or_levels"]["middleLock"] = False
    return snap


def _vwap_revert_short_snap():
    snap = _live_snap()
    snap["vwap_bias"] = {"sigma_z": 2.41, "regime": "BLOWOFF_REVERT",
                          "label": "STRETCHED"}
    snap["conviction"]["trajectory"] = "FLAT"
    L = snap["or_levels"]["levels"][0]
    L["composite"]["coverage"] = 0.80
    L["composite"]["confidence"] = 0.50
    L["confidence"] = 0.50
    return snap


def _or_revert_short_snap():
    snap = _live_snap()
    # middle_lock True, weak FADE at OR-H, conf >= 0.35
    L = snap["or_levels"]["levels"][0]
    L["composite_dir"] = "FADE_SHORT"
    L["composite"] = {"direction": "FADE_SHORT", "confidence": 0.40,
                       "coverage": 0.50}
    L["confidence"] = 0.40
    return snap


def _sweep_rev_long_snap():
    snap = _live_snap()
    snap["or_levels"]["middleLock"] = False
    snap["book"]["mid"] = 20087.50  # below OR-L
    snap["or_levels"]["levels"] = [
        {"label": "OR-L", "price": 20088.00, "side": "low",
         "distance": -0.50, "proximity": True,
         "composite_dir": "FADE_LONG",
         "confidence": 0.50,
         "composite": {"direction": "FADE_LONG", "confidence": 0.50,
                        "coverage": 0.70},
         "institutional_thesis": {"execution_read": "WAIT_FOR_CONFIRM"}},
    ]
    snap["micro_events"] = {
        "events": [
            {"kind": "SWEEP", "price": 20087.75, "side": "sell"},
        ],
    }
    return snap


def _missing_or_levels_snap():
    snap = _live_snap()
    snap["or_levels"] = {}
    return snap


# ---------------------------------------------------------------------------
# Hard-gate tests
# ---------------------------------------------------------------------------

def test_no_snapshot_returns_neutral_no_trade():
    v = compute_verdict(None)
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "NO_TRADE_GATE"
    assert v["NO_TRADE"] == "no_snapshot"


def test_bridge_offline_blocks_trade():
    snap = _live_snap()
    snap["health"] = "offline"
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "NO_TRADE_GATE"
    assert v["NO_TRADE"].startswith("bridge_offline")


@pytest.mark.parametrize("mode", ["LAST_KNOWN_STALE", "FALLBACK"])
def test_stale_anchor_forces_neutral(mode):
    snap = _live_snap()
    snap["session"]["anchorMode"] = mode
    snap["conviction"]["anchorMode"] = mode
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "NO_TRADE_GATE"
    assert v["NO_TRADE"].startswith("stale_anchor")
    assert any("session.anchorMode" in e for e in v["EVIDENCE"])


def test_missing_anchor_mode_treated_as_non_live():
    snap = _live_snap()
    snap["session"].pop("anchorMode", None)
    snap["conviction"].pop("anchorMode", None)
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["NO_TRADE"].startswith("stale_anchor")


def test_stale_snapshot_forces_no_trade():
    snap = _live_snap()
    v = _v(snap, age_ms=6_000, stale_threshold_ms=5_000)
    assert v["BIAS"] == "NEUTRAL"
    assert v["NO_TRADE"].startswith("stale_snapshot")


def test_news_blackout_forces_no_trade():
    snap = _live_snap()
    snap["news"] = {"blocked": True, "label": "FOMC_T_MINUS_30"}
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["NO_TRADE"].startswith("news_blackout")
    assert v["NO_TRADE"].endswith("FOMC_T_MINUS_30")


@pytest.mark.parametrize("code", ["PRE_MARKET", "POST_MARKET",
                                    "OR_FORMING", "CLOSE_RISK"])
def test_blocking_session_code(code):
    snap = _live_snap()
    snap["session"]["code"] = code
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "NO_TRADE_GATE"
    assert v["NO_TRADE"].startswith("session:")


# ---------------------------------------------------------------------------
# Setup vocabulary (closed set)
# ---------------------------------------------------------------------------

_ALL_SCENARIOS = [
    ("default_chop",        _live_snap),
    ("missing_or_levels",   _missing_or_levels_snap),
    ("level_defend",        _level_defend_snap),
    ("or_break_follow_long", _or_break_follow_long_snap),
    ("or_break_follow_short", _or_break_follow_short_snap),
    ("or_break_fade_short", _or_break_fade_short_snap),
    ("vwap_revert_short",   _vwap_revert_short_snap),
    ("or_revert_short",     _or_revert_short_snap),
    ("sweep_rev_long",      _sweep_rev_long_snap),
]


@pytest.mark.parametrize("name,builder", _ALL_SCENARIOS,
                           ids=[n for n, _ in _ALL_SCENARIOS])
def test_setup_always_in_fixed_vocab(name, builder):
    v = _v(builder())
    assert v["SETUP"] in SETUPS, f"{name} produced {v['SETUP']}"
    assert v["BIAS"] in BIASES
    assert v["CONFIDENCE"] in CONFIDENCES


@pytest.mark.parametrize("name,builder", _ALL_SCENARIOS,
                           ids=[n for n, _ in _ALL_SCENARIOS])
def test_neutral_setup_pairs_with_no_trade(name, builder):
    v = _v(builder())
    if v["BIAS"] == "NEUTRAL":
        # NO_TRADE may be empty only when SETUP is also empty; for any
        # NEUTRAL verdict, NO_TRADE must explain why.
        assert v["SETUP"] in ("NO_TRADE_GATE", "NO_TRADE_CHOP", "LEVEL_DEFEND")
        assert v["NO_TRADE"], f"{name} NEUTRAL with empty NO_TRADE"


# ---------------------------------------------------------------------------
# Positive setup classifier tests
# ---------------------------------------------------------------------------

def test_level_defend_when_execution_read_stand_down():
    v = _v(_level_defend_snap())
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "LEVEL_DEFEND"
    assert v["NO_TRADE"] == "level_defended"
    assert any("execution_read=STAND_DOWN" in e for e in v["EVIDENCE"])


def test_or_break_follow_long():
    v = _v(_or_break_follow_long_snap())
    assert v["BIAS"] == "LONG"
    assert v["SETUP"] == "OR_BREAK_FOLLOW"
    assert v["INVALIDATION"]
    assert v["ENTRY"]


def test_or_break_follow_short():
    v = _v(_or_break_follow_short_snap())
    assert v["BIAS"] == "SHORT"
    assert v["SETUP"] == "OR_BREAK_FOLLOW"


def test_or_break_fade_short():
    v = _v(_or_break_fade_short_snap())
    assert v["BIAS"] == "SHORT"
    assert v["SETUP"] == "OR_BREAK_FADE"


def test_vwap_revert_short():
    v = _v(_vwap_revert_short_snap())
    assert v["BIAS"] == "SHORT"
    assert v["SETUP"] == "VWAP_REVERT"
    assert any("vwap_bias.sigma_z" in e for e in v["EVIDENCE"])


def test_sweep_reversal_with_recent_sweep():
    v = _v(_sweep_rev_long_snap())
    assert v["BIAS"] == "LONG"
    assert v["SETUP"] == "LEVEL_SWEEP_REV"
    assert any("kind=SWEEP" in e for e in v["EVIDENCE"])


def test_no_or_levels_returns_chop_not_gate():
    v = _v(_missing_or_levels_snap())
    assert v["SETUP"] == "NO_TRADE_CHOP"
    assert v["BIAS"] == "NEUTRAL"
    assert v["NO_TRADE"] == "no_or_levels"


# ---------------------------------------------------------------------------
# Missing-optional-fields must NOT escalate to NO_TRADE_GATE
# ---------------------------------------------------------------------------

def test_missing_vwap_bias_does_not_gate():
    snap = _or_break_follow_long_snap()
    snap.pop("vwap_bias", None)
    v = _v(snap)
    # Setup still classifies; confidence cannot be HIGH.
    assert v["SETUP"] != "NO_TRADE_GATE"
    assert v["CONFIDENCE"] != "HIGH"


def test_missing_micro_events_skips_sweep_setup():
    snap = _sweep_rev_long_snap()
    snap.pop("micro_events", None)
    v = _v(snap)
    assert v["SETUP"] != "LEVEL_SWEEP_REV"
    assert v["SETUP"] != "NO_TRADE_GATE"


def test_missing_institutional_thesis_skips_level_defend():
    snap = _level_defend_snap()
    for L in snap["or_levels"]["levels"]:
        L.pop("institutional_thesis", None)
    v = _v(snap)
    assert v["SETUP"] != "LEVEL_DEFEND"
    assert v["SETUP"] != "NO_TRADE_GATE"


def test_missing_conviction_does_not_gate():
    snap = _or_break_follow_long_snap()
    # Keep anchorMode reachable from session; drop the rest.
    snap["conviction"] = {"anchorMode": "LIVE"}
    v = _v(snap)
    assert v["SETUP"] != "NO_TRADE_GATE"


# ---------------------------------------------------------------------------
# Confidence rules
# ---------------------------------------------------------------------------

def test_high_confidence_requires_warm_sigma():
    snap = _or_break_follow_long_snap()
    snap["vwap_bias"]["regime"] = "NO_SIGMA"
    v = _v(snap)
    assert v["CONFIDENCE"] != "HIGH"


def test_high_confidence_requires_not_warmup():
    snap = _or_break_follow_long_snap()
    snap["conviction"]["trajectory"] = "WARMUP"
    v = _v(snap)
    assert v["CONFIDENCE"] != "HIGH"


def test_high_confidence_requires_coverage():
    snap = _or_break_follow_long_snap()
    snap["or_levels"]["levels"][0]["composite"]["coverage"] = 0.40
    v = _v(snap)
    assert v["CONFIDENCE"] != "HIGH"


def test_high_confidence_requires_composite_confidence():
    snap = _or_break_follow_long_snap()
    snap["or_levels"]["levels"][0]["composite"]["confidence"] = 0.30
    snap["or_levels"]["levels"][0]["confidence"] = 0.30
    v = _v(snap)
    assert v["CONFIDENCE"] != "HIGH"


def test_high_confidence_impossible_when_neutral():
    snap = _level_defend_snap()
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["CONFIDENCE"] != "HIGH"


def test_high_confidence_reachable_when_all_conditions_hold():
    snap = _or_break_follow_long_snap()
    L = snap["or_levels"]["levels"][0]
    L["composite"] = {"direction": "FOLLOW_LONG",
                       "confidence": 0.80, "coverage": 0.85}
    L["confidence"] = 0.80
    v = _v(snap)
    assert v["CONFIDENCE"] == "HIGH"


# ---------------------------------------------------------------------------
# EVIDENCE shape and language rules
# ---------------------------------------------------------------------------

_FIELD_PATH_ALLOWED_PREFIXES = (
    "book.", "or_levels.", "vwap_bias.", "vwap_or.", "conviction.",
    "session.", "news.", "gates.", "micro_events.", "health=", "ageMs=",
    "alias=",
)


def _looks_like_field_path(line: str) -> bool:
    if "=" not in line:
        return False
    if line.startswith("health=") or line.startswith("ageMs="):
        return True
    return any(line.startswith(p) for p in _FIELD_PATH_ALLOWED_PREFIXES)


@pytest.mark.parametrize("name,builder", _ALL_SCENARIOS,
                           ids=[n for n, _ in _ALL_SCENARIOS])
def test_evidence_cites_snapshot_fields(name, builder):
    v = _v(builder())
    for line in v["EVIDENCE"]:
        assert _looks_like_field_path(line), \
            f"{name}: evidence '{line}' is not a snapshot field path"


# Phrases banned in human text fields ONLY. The word `institutional` is
# fine on its own because legitimate snapshot fields use
# `institutional_thesis`. We blacklist phrases, not lone tokens.
_VAGUE_PHRASES = (
    "smart money", "big player", "big players", "algo slam", "the algos",
    "mysterious", "hidden buyers", "hidden sellers", "institutions are",
    "institutional flow", "institutional buying", "institutional selling",
    "whale",
)


@pytest.mark.parametrize("name,builder", _ALL_SCENARIOS,
                           ids=[n for n, _ in _ALL_SCENARIOS])
def test_human_text_fields_have_no_vague_phrases(name, builder):
    v = _v(builder())
    for field in ("ENTRY", "INVALIDATION", "NO_TRADE"):
        text = (v[field] or "").lower()
        for phrase in _VAGUE_PHRASES:
            assert phrase not in text, \
                f"{name}: vague phrase '{phrase}' in {field}: {v[field]!r}"


def test_evidence_may_reference_institutional_thesis_field_path():
    # The standalone word 'institutional' is allowed inside a field path.
    v = _v(_level_defend_snap())
    joined = "\n".join(v["EVIDENCE"])
    assert "institutional_thesis.execution_read=STAND_DOWN" in joined


# ---------------------------------------------------------------------------
# Conservatism: ambiguous input does not invent a trade
# ---------------------------------------------------------------------------

def test_chop_when_inside_or_with_weak_signal():
    snap = _live_snap()  # default: middle_lock True, direction None, conf 0.20
    v = _v(snap)
    assert v["BIAS"] == "NEUTRAL"
    assert v["SETUP"] == "NO_TRADE_CHOP"
    assert v["NO_TRADE"].startswith("no_clear_setup")


def test_invalidation_present_for_directional_setups():
    for builder in (_or_break_follow_long_snap, _or_break_follow_short_snap,
                    _or_break_fade_short_snap, _vwap_revert_short_snap,
                    _or_revert_short_snap, _sweep_rev_long_snap):
        v = _v(builder())
        assert v["BIAS"] in ("LONG", "SHORT")
        assert v["INVALIDATION"], \
            f"{builder.__name__}: missing INVALIDATION for {v['BIAS']}"
        assert v["ENTRY"], \
            f"{builder.__name__}: missing ENTRY for {v['BIAS']}"
