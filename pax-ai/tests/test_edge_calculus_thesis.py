"""Pin edge_calculus.level_edge thesis-aware size downgrade.

Mirrors pax_decision exactly: STAND_DOWN, WAIT_FOR_CONFIRM, and
SCRATCH_READY all collapse thesis_gated_size_tier to NONE. PAY_FOR_TRADE
(or missing institutional_thesis) preserves the legacy size_tier. Legacy
keys (size_tier, expected_R, ...) are NOT removed.
"""
from __future__ import annotations

from pax_ai.edge_calculus import level_edge


def _level(execution_read, *, confidence=0.65, decision="ENTER_LONG_FOLLOW",
           state="TOUCHED", thesis="STOP_SWEEP_CONTINUATION"):
    return {
        "label": "OR-H", "decision": decision, "confidence": confidence,
        "price": 20000.0, "side": "above",
        "institutional_thesis": {
            "state": state, "thesis": thesis,
            "liquidity_quality": "REAL",
            "aggressor_flow": "WITH",
            "book_state": "STABLE",
            "execution_read": execution_read,
            "confidence": 0.6,
            "reasons": [], "invalidations": [],
            "touched_at_ms": 1000, "last_state_change_ms": 1500,
            "polls_since_touch": 1, "confirm_ms_since_touch": 1000,
        },
    }


def _snap():
    return {
        "alias": "NQM6.CME@RITHMIC",
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7},
        "or_levels": {"orHigh": 20000.0, "orLow": 19500.0, "orWidthPts": 500.0},
    }


def test_size_tier_legacy_field_still_present():
    out = level_edge(_level("PAY_FOR_TRADE"), _snap())
    assert "size_tier" in out


def test_thesis_gated_size_tier_added():
    out = level_edge(_level("PAY_FOR_TRADE"), _snap())
    assert "thesis_gated_size_tier" in out


def test_institutional_thesis_summary_added():
    out = level_edge(_level("STAND_DOWN", thesis="ICEBERG_DEFENSE"), _snap())
    assert "institutional_thesis_summary" in out
    s = out["institutional_thesis_summary"]
    assert s["execution_read"] == "STAND_DOWN"
    assert s["thesis"] == "ICEBERG_DEFENSE"


def test_stand_down_forces_thesis_gated_none():
    out = level_edge(_level("STAND_DOWN"), _snap())
    assert out["thesis_gated_size_tier"] == "NONE"


def test_wait_for_confirm_collapses_to_none():
    """WAIT_FOR_CONFIRM must collapse to NONE (mirrors pax_decision)."""
    out = level_edge(_level("WAIT_FOR_CONFIRM", confidence=0.65), _snap())
    assert out["thesis_gated_size_tier"] == "NONE"


def test_scratch_ready_collapses_to_none():
    """SCRATCH_READY must collapse to NONE (mirrors pax_decision)."""
    out = level_edge(_level("SCRATCH_READY", confidence=0.65,
                             state="RETEST_FAIL", thesis="NONE"), _snap())
    assert out["thesis_gated_size_tier"] == "NONE"


def test_pay_for_trade_does_not_downgrade():
    """PAY_FOR_TRADE preserves the legacy size_tier."""
    out = level_edge(_level("PAY_FOR_TRADE", confidence=0.65), _snap())
    assert out["thesis_gated_size_tier"] == out["size_tier"]


def test_missing_institutional_thesis_falls_back_to_legacy_size_tier():
    """Missing thesis preserves legacy size_tier (no gate applied)."""
    level = _level("PAY_FOR_TRADE")
    del level["institutional_thesis"]
    out = level_edge(level, _snap())
    assert out["thesis_gated_size_tier"] == out["size_tier"]


def test_full_size_tier_collapses_to_none_under_wait_for_confirm():
    """Even a FULL-tier confidence collapses to NONE under WAIT_FOR_CONFIRM."""
    out = level_edge(_level("WAIT_FOR_CONFIRM", confidence=0.85), _snap())
    assert out["size_tier"] == "FULL"
    assert out["thesis_gated_size_tier"] == "NONE"


def test_legacy_keys_preserved():
    out = level_edge(_level("PAY_FOR_TRADE"), _snap())
    for k in ("expected_R", "prob_pay_for_trade", "prob_reach_next_rung",
              "max_heat_pts", "invalidation_price", "scratch_price",
              "payline_price", "rung1_price", "size_tier",
              "composite_dir", "level_kind"):
        assert k in out, f"legacy key {k} missing"
