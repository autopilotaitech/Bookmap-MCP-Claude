"""Pin pax_decision's gating on per-level execution_read.

STAND_DOWN forces WAIT. WAIT_FOR_CONFIRM / SCRATCH_READY force size=0.
PAY_FOR_TRADE passes through to the legacy pipeline. Missing or empty
institutional_thesis must not break the legacy flow.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import pax_decision, _LEVEL_TOUCH_STATE


def _level_with_thesis(execution_read, *, label="OR-H", price=20000.0,
                       side="above", decision="ENTER_LONG_FOLLOW",
                       confidence=0.65, thesis="ACCEPTANCE_LONG",
                       state="ACCEPTED_ABOVE",
                       liquidity="REAL", aggressor="WITH"):
    return {
        "label": label, "price": price, "side": side,
        "distance": 2.0, "proximity": True,
        "decision": decision, "confidence": confidence,
        "components": {"ps_rot": "NONE"},
        "reasons": [],
        "composite": {"score": 0.5, "direction": "FOLLOW_LONG",
                      "confidence": 0.6, "drivers": [], "warnings": []},
        "institutional_thesis": {
            "state": state, "thesis": thesis,
            "liquidity_quality": liquidity, "aggressor_flow": aggressor,
            "book_state": "STABLE",
            "execution_read": execution_read,
            "confidence": 0.7,
            "reasons": [], "invalidations": [],
            "touched_at_ms": 1000, "last_state_change_ms": 2000,
            "polls_since_touch": 2, "confirm_ms_since_touch": 2000,
        },
    }


def _live_snap(level):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "gates": {
            "session": {"code": "ACTIVE", "anchorMode": "LIVE",
                        "anchorReason": "live"},
            "news": {"blocked": False, "label": "clear"},
        },
        "book": {"mid": 20002.0},
        "or_levels": {
            "orHigh": 20010.0, "orLow": 19990.0,
            "orWidthPts": 20.0, "middleLock": False,
            "inProximity": True,
            "levels": [level],
        },
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
                 "biasScore": 0.4, "biasTrajectory": "RISING",
                 "vwapSlope": {"label": "NEUTRAL"}},
        "vwap_bias": {"label": "BULLISH"},
        "vp_bias":   {"label": "BULLISH"},
    }


def test_stand_down_forces_wait():
    _LEVEL_TOUCH_STATE.clear()
    snap = _live_snap(_level_with_thesis("STAND_DOWN",
                                          thesis="ICEBERG_DEFENSE",
                                          liquidity="ICEBERG_DEFENDED"))
    out = pax_decision(snap)
    assert out["decision"] == "WAIT"
    assert out["size"] == 0
    assert any("STAND_DOWN" in r or "thesis" in r.lower() for r in out["reasons"])


def test_wait_for_confirm_forces_size_zero_even_at_high_confidence():
    _LEVEL_TOUCH_STATE.clear()
    snap = _live_snap(_level_with_thesis(
        "WAIT_FOR_CONFIRM", confidence=0.65,
        state="TOUCHED", thesis="STOP_SWEEP_CONTINUATION"))
    out = pax_decision(snap)
    assert out["size"] == 0


def test_scratch_ready_forces_size_zero():
    _LEVEL_TOUCH_STATE.clear()
    snap = _live_snap(_level_with_thesis(
        "SCRATCH_READY", confidence=0.65,
        state="RETEST_FAIL", thesis="NONE"))
    out = pax_decision(snap)
    assert out["size"] == 0


def test_pay_for_trade_does_not_block_legacy_pipeline():
    _LEVEL_TOUCH_STATE.clear()
    snap = _live_snap(_level_with_thesis(
        "PAY_FOR_TRADE", confidence=0.65,
        state="ACCEPTED_ABOVE", thesis="ACCEPTANCE_LONG"))
    out = pax_decision(snap)
    # Legacy pipeline may still WAIT for its own reasons (regime, bias),
    # but it must NOT inject a STAND_DOWN purely from the thesis layer.
    assert out["decision"] in ("ENTER_LONG_FOLLOW", "WAIT")


def test_legacy_return_shape_preserved():
    _LEVEL_TOUCH_STATE.clear()
    snap = _live_snap(_level_with_thesis("PAY_FOR_TRADE"))
    out = pax_decision(snap)
    for k in ("decision", "reasons", "components"):
        assert k in out
    # The new thesis subblock in components must exist.
    assert "thesis" in out["components"]
    assert out["components"]["thesis"]["execution_read"] == "PAY_FOR_TRADE"


def test_missing_institutional_thesis_falls_back_safely():
    """If the level has no institutional_thesis (older test fixtures), the
    gate must not crash — exec_read defaults to None / pass-through."""
    _LEVEL_TOUCH_STATE.clear()
    level = _level_with_thesis("PAY_FOR_TRADE")
    del level["institutional_thesis"]
    snap = _live_snap(level)
    out = pax_decision(snap)
    assert out["decision"] in ("ENTER_LONG_FOLLOW", "WAIT", "STAND_DOWN")
