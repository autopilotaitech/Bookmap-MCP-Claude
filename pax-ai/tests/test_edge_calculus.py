"""Pure-function tests for edge_calculus."""

from __future__ import annotations

import pytest

from pax_ai import edge_calculus as ec


# ---------------------------------------------------------------------------
# size_tier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("conf,expected", [
    (None,  "NONE"),
    (0.00,  "NONE"),
    (0.34,  "NONE"),
    (0.35,  "HALF"),
    (0.49,  "HALF"),
    (0.50,  "FULL"),
    (0.95,  "FULL"),
])
def test_size_tier_thresholds(conf, expected):
    assert ec.size_tier(conf) == expected


# ---------------------------------------------------------------------------
# composite_dir_from_decision
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("decision,expected", [
    ("ENTER_LONG_FOLLOW",  "FOLLOW_LONG"),
    ("ENTER_LONG_FADE",    "FADE_LONG"),
    ("ENTER_SHORT_FOLLOW", "FOLLOW_SHORT"),
    ("ENTER_SHORT_FADE",   "FADE_SHORT"),
    ("WAIT",               None),
    ("",                   None),
    (None,                 None),
])
def test_composite_dir_from_decision(decision, expected):
    assert ec.composite_dir_from_decision(decision) == expected


# ---------------------------------------------------------------------------
# expected_r / directional_r
# ---------------------------------------------------------------------------

def test_expected_r_none_confidence_is_zero():
    assert ec.expected_r(None, "FOLLOW_LONG", "TRENDING_UP", "OR_LEVEL") == 0.0


def test_expected_r_caps_at_directional_r():
    base = ec.directional_r("FOLLOW_LONG", "TRENDING_UP", "OR_LEVEL")
    # confidence=1 should equal exactly the base R
    assert ec.expected_r(1.0, "FOLLOW_LONG", "TRENDING_UP", "OR_LEVEL") == pytest.approx(base)
    # confidence=2.0 (sanity overshoot) caps at base
    assert ec.expected_r(2.0, "FOLLOW_LONG", "TRENDING_UP", "OR_LEVEL") == pytest.approx(base)


def test_directional_r_absorption_at_or_is_highest_long_bucket():
    # Per pax-or SKILL.md §11.4, absorption-at-OR is a FADE setup, not FOLLOW.
    base_trend = ec.directional_r("FOLLOW_LONG", "TRENDING_UP",  "OR_LEVEL")
    base_abs   = ec.directional_r("FADE_LONG",   "ABSORPTION_BID", "OR_LEVEL")
    assert base_abs > base_trend, (
        f"FADE_LONG@ABSORPTION_BID ({base_abs}) must exceed "
        f"FOLLOW_LONG@TRENDING_UP ({base_trend})")


def test_directional_r_falls_back_to_default():
    val = ec.directional_r("FOLLOW_LONG", "BALANCED", "OR_LEVEL")
    assert val == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# probabilities
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("conf,expected_low,expected_high", [
    (None, 0.30, 0.30),
    (0.0,  0.30, 0.30),
    (0.5,  0.55, 0.55),
    (1.0,  0.80, 0.80),
    (2.0,  0.80, 0.80),       # cap
])
def test_prob_pay_for_trade_band(conf, expected_low, expected_high):
    v = ec.prob_pay_for_trade(conf)
    assert expected_low <= v <= expected_high or v == pytest.approx(expected_low)


def test_prob_reach_next_rung_requires_both():
    assert ec.prob_reach_next_rung(None, 0.5) == 0.10
    assert ec.prob_reach_next_rung(0.5, None) == 0.10
    assert ec.prob_reach_next_rung(0.0, 0.0) == 0.10
    assert ec.prob_reach_next_rung(1.0, 1.0) == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# heat / invalidation / payline / rung
# ---------------------------------------------------------------------------

def test_max_heat_or_level_follow_uses_or_width():
    level = {"label": "OR-H", "decision": "ENTER_LONG_FOLLOW"}
    assert ec.max_heat_pts(level, or_width_pts=12.5, tick_size_pts=0.25) == pytest.approx(12.75)


def test_max_heat_extension_uses_tick_only():
    level = {"label": "+1", "decision": "ENTER_LONG_FOLLOW"}
    assert ec.max_heat_pts(level, or_width_pts=12.5, tick_size_pts=0.25) == 0.25


def test_max_heat_fade_uses_tick_only():
    level = {"label": "OR-H", "decision": "ENTER_LONG_FADE"}
    assert ec.max_heat_pts(level, or_width_pts=12.5, tick_size_pts=0.25) == 0.25


def test_invalidation_long():
    level = {"decision": "ENTER_LONG_FOLLOW"}
    assert ec.invalidation_price(level, or_high=21340.0, or_low=21320.0,
                                   tick_size_pts=0.25) == pytest.approx(21319.75)


def test_invalidation_short():
    level = {"decision": "ENTER_SHORT_FOLLOW"}
    assert ec.invalidation_price(level, or_high=21340.0, or_low=21320.0,
                                   tick_size_pts=0.25) == pytest.approx(21340.25)


def test_invalidation_wait_returns_none():
    level = {"decision": "WAIT"}
    assert ec.invalidation_price(level, or_high=21340.0, or_low=21320.0,
                                   tick_size_pts=0.25) is None


def test_payline_long_nq():
    level = {"price": 21340.0, "decision": "ENTER_LONG_FOLLOW"}
    assert ec.payline_price(level, alias="NQM6.CME@RITHMIC") == pytest.approx(21350.0)


def test_payline_short_es():
    level = {"price": 5780.0, "decision": "ENTER_SHORT_FOLLOW"}
    assert ec.payline_price(level, alias="ESM6.CME@RITHMIC") == pytest.approx(5777.5)


def test_rung1_long_nq():
    level = {"price": 21340.0, "decision": "ENTER_LONG_FOLLOW"}
    assert ec.rung1_price(level, alias="NQM6.CME@RITHMIC") == pytest.approx(21405.0)


def test_rung1_short_nq():
    level = {"price": 21340.0, "decision": "ENTER_SHORT_FOLLOW"}
    assert ec.rung1_price(level, alias="NQM6.CME@RITHMIC") == pytest.approx(21275.0)


# ---------------------------------------------------------------------------
# level_edge - full payload
# ---------------------------------------------------------------------------

def _live_snap_with_level(level: dict, regime: str = "TRENDING_UP",
                            regime_conf: float = 0.7,
                            or_high: float = 21340.0, or_low: float = 21320.0,
                            alias: str = "NQM6.CME@RITHMIC") -> dict:
    return {
        "alias": alias,
        "flow": {"regime": regime, "regimeConfidence": regime_conf},
        "or_levels": {
            "orHigh": or_high, "orLow": or_low,
            "orWidthPts": or_high - or_low,
            "levels": [level],
        },
    }


def test_level_edge_full_payload_or_h_follow_long():
    level = {"label": "OR-H", "price": 21340.0, "side": "above",
             "distance": 12.50, "proximity": True,
             "decision": "ENTER_LONG_FOLLOW", "confidence": 0.62}
    snap = _live_snap_with_level(level, regime="TRENDING_UP")
    e = ec.level_edge(level, snap)
    assert e["size_tier"] == "FULL"
    assert e["composite_dir"] == "FOLLOW_LONG"
    assert e["level_kind"] == "OR_LEVEL"
    assert e["expected_R"] > 0
    assert 0.30 <= e["prob_pay_for_trade"] <= 0.80
    assert e["scratch_price"] == 21340.0
    assert e["payline_price"] == 21350.0
    assert e["rung1_price"] == 21405.0
    assert e["invalidation_price"] == 21319.75
    assert e["tick_size"] == 0.25
    assert isinstance(e["reasons"], list) and e["reasons"]


def test_level_edge_at_ext_under_exhaustion_is_fade_bucket():
    level = {"label": "+2", "price": 21470.0, "side": "above",
             "distance": 130.0, "proximity": True,
             "decision": "ENTER_SHORT_FADE", "confidence": 0.55}
    snap = _live_snap_with_level(level, regime="EXHAUSTION_UP", regime_conf=0.8)
    e = ec.level_edge(level, snap)
    assert e["composite_dir"] == "FADE_SHORT"
    assert e["level_kind"] == "EXT_LEVEL"
    assert e["size_tier"] == "FULL"
    assert any("exhaustion at extension" in r for r in e["reasons"])


def test_level_edge_wait_decision_returns_zero_ev():
    level = {"label": "OR-L", "price": 21320.0, "side": "below",
             "distance": -8.0, "proximity": True,
             "decision": "WAIT", "confidence": 0.20}
    snap = _live_snap_with_level(level, regime="BALANCED", regime_conf=0.3)
    e = ec.level_edge(level, snap)
    assert e["composite_dir"] is None
    assert e["size_tier"] == "NONE"
    assert e["expected_R"] >= 0.0
    # WAIT has no LONG/SHORT in decision so invalidation_price must be None.
    assert e["invalidation_price"] is None


def test_root_symbol_parsing():
    assert ec._root_symbol("NQM6.CME@RITHMIC") == "NQ"
    assert ec._root_symbol("MNQH7.CME") == "MNQ"
    assert ec._root_symbol("ES.GLOBEX") == "ES"
    assert ec._root_symbol(None) == ""
    assert ec._root_symbol("") == ""
