"""Backcompat contract: adding snap["institutional_signals"] must NOT remove
or alter any pre-existing snapshot or per-level field. trend_signal is still
emitted as CONTEXT (its kind/eligible fields preserved), but it is not the
authoritative source of buy/sell markers anymore.
"""
from __future__ import annotations

from bookmap_mcp.dashboard import (
    compute_or_levels,
    compute_institutional_signals,
    compute_trend_signal,
    _LEVEL_TOUCH_STATE,
)


_LEGACY_LEVEL_KEYS = {
    "label", "price", "side", "distance", "proximity",
    "decision", "decisionLabel", "score", "confidence",
    "reasons", "components", "composite", "institutional_thesis",
}


def _snap(mid=20002.0, tape_delta=0.0):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": 20000.0, "orLow": 19950.0},
        "micro_events": {"events": []},
        "tape_flow": {"deltaScore": tape_delta, "label": "MIXED"},
        "pull_stack": None,
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
        "trend_signal": {"kind": "STRONG_BULL", "eligible": False},
    }


def test_compute_institutional_signals_returns_list():
    _LEVEL_TOUCH_STATE.clear()
    snap = _snap()
    snap["or_levels"] = compute_or_levels(snap)
    sigs = compute_institutional_signals(snap)
    assert isinstance(sigs, list)


def test_compute_institutional_signals_empty_when_or_missing():
    snap = _snap()
    snap["or_levels"] = None
    assert compute_institutional_signals(snap) == []


def test_per_level_legacy_fields_preserved():
    _LEVEL_TOUCH_STATE.clear()
    snap = _snap(mid=20002.0)
    snap["or_levels"] = compute_or_levels(snap)
    for lvl in snap["or_levels"]["levels"]:
        for k in _LEGACY_LEVEL_KEYS:
            assert k in lvl, f"{lvl.get('label')} missing legacy key {k}"


def test_decision_field_remains_in_legacy_set():
    _LEVEL_TOUCH_STATE.clear()
    snap = _snap()
    snap["or_levels"] = compute_or_levels(snap)
    legacy_decisions = {"ENTER_LONG_FOLLOW", "ENTER_LONG_FADE",
                        "ENTER_SHORT_FOLLOW", "ENTER_SHORT_FADE", "WAIT"}
    for lvl in snap["or_levels"]["levels"]:
        assert lvl["decision"] in legacy_decisions


def test_trend_signal_still_callable_as_context():
    """compute_trend_signal must still be a working function. The Java side
    may still consult it for non-entry context."""
    _LEVEL_TOUCH_STATE.clear()
    snap = _snap()
    snap["conviction"] = None
    snap["alias"] = "NQM6.CME@RITHMIC"
    snap["book"] = {"mid": 20002.0}
    ts = compute_trend_signal(snap)
    assert "kind" in ts
    assert "eligible" in ts


def test_signal_engine_exports_composer():
    from bookmap_mcp import signal_engine as se
    for name in (
        "compute_institutional_signals",
        "_signal_type_from_thesis",
        "_signal_aggressor_align_required",
        "_signal_size_tier_from_confidence",
        "_SIGNAL_TYPE_CODES",
        "_SIGNAL_DIRECTION_CODES",
    ):
        assert hasattr(se, name), f"signal_engine missing {name}"
        assert name in se.__all__, f"signal_engine __all__ missing {name}"
