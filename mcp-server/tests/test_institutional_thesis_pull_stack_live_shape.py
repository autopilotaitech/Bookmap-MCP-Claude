"""Regression: pull_stack.rotation in the live bridge is a STRING, not a dict.

PullStackHandler.java emits `.prop("rotation", snap.rotation)` where
snap.rotation is one of "NONE" / "ROTATION_UP" / "ROTATION_DN". Our thesis
helpers must accept that shape without crashing, while remaining tolerant
of the older nested-dict / rot_dir shapes used in some test fixtures.
"""
from __future__ import annotations

import pytest

from bookmap_mcp.dashboard import (
    _ps_rotation_value,
    _thesis_liquidity_quality,
    _thesis_book_state,
    compute_or_levels,
    _LEVEL_TOUCH_STATE,
)


# -- _ps_rotation_value normalization --------------------------------------

def test_ps_rotation_value_reads_live_string_up():
    assert _ps_rotation_value({"rotation": "ROTATION_UP"}) == "ROTATION_UP"


def test_ps_rotation_value_reads_live_string_dn():
    assert _ps_rotation_value({"rotation": "ROTATION_DN"}) == "ROTATION_DN"


def test_ps_rotation_value_reads_live_string_none():
    assert _ps_rotation_value({"rotation": "NONE"}) == "NONE"


def test_ps_rotation_value_reads_nested_dict_shape():
    assert _ps_rotation_value({"rotation": {"direction": "ROTATION_UP"}}) == "ROTATION_UP"


def test_ps_rotation_value_reads_legacy_rot_dir():
    assert _ps_rotation_value({"rot_dir": "ROTATION_DN"}) == "ROTATION_DN"


def test_ps_rotation_value_missing_returns_none():
    assert _ps_rotation_value({}) == "NONE"


def test_ps_rotation_value_none_input_returns_none():
    assert _ps_rotation_value(None) == "NONE"


def test_ps_rotation_value_bad_shape_returns_none():
    # rotation is a number — clearly invalid.
    assert _ps_rotation_value({"rotation": 42}) == "NONE"
    # rotation is a list — also invalid.
    assert _ps_rotation_value({"rotation": ["ROTATION_UP"]}) == "NONE"


# -- _thesis_liquidity_quality with live string shape ----------------------

def test_liquidity_quality_string_rotation_up_above_yields_stacking():
    ps = {"rotation": "ROTATION_UP", "aggregateZ": 1.5}
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=ps, lt_obj=None, tape_obj=None,
    )
    assert lq == "STACKING"


def test_liquidity_quality_string_rotation_dn_above_yields_pulling():
    ps = {"rotation": "ROTATION_DN", "aggregateZ": 1.5}
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=ps, lt_obj=None, tape_obj=None,
    )
    assert lq == "PULLING"


def test_liquidity_quality_string_rotation_none_falls_back_to_real():
    ps = {"rotation": "NONE", "aggregateZ": 0.0}
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=ps, lt_obj=None, tape_obj=None,
    )
    assert lq == "REAL"


def test_liquidity_quality_bad_rotation_shape_does_not_crash():
    """A truthy-but-unparseable rotation value must not throw."""
    ps = {"rotation": 42, "aggregateZ": 1.0}
    lq, _ = _thesis_liquidity_quality(
        side="above", price=20000.0, me_obj=None,
        ps_obj=ps, lt_obj=None, tape_obj=None,
    )
    assert lq == "REAL"


# -- _thesis_book_state with live string shape -----------------------------

def test_book_state_string_rotation_up_above_yields_stacking():
    ps = {"rotation": "ROTATION_UP", "aggregateZ": 1.5}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "STACKING"


def test_book_state_string_rotation_dn_above_yields_pulling():
    ps = {"rotation": "ROTATION_DN", "aggregateZ": 1.5}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "PULLING"


def test_book_state_string_rotation_none_falls_back_to_stable():
    ps = {"rotation": "NONE", "aggregateZ": 0.0}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "STABLE"


def test_book_state_bad_rotation_shape_does_not_crash():
    ps = {"rotation": [1, 2, 3], "aggregateZ": 0.0}
    bs, _ = _thesis_book_state(side="above", price=20000.0,
                                ps_obj=ps, me_obj=None)
    assert bs == "STABLE"


# -- End-to-end smoke: compute_or_levels with live pull_stack shape --------

def test_compute_or_levels_smoke_with_live_pull_stack_shape():
    """The exact snap from the audit finding — must NOT crash."""
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels({
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "book": {"mid": 20000.0},
        "or_row": {"orHigh": 20000.0, "orLow": 19950.0},
        "micro_events": {"events": []},
        "tape_flow": {"deltaScore": 0.0},
        "pull_stack": {"aggregateZ": 0.0, "rotation": "NONE",
                        "windows": [{"zScore": 0.0}]},
        "lt_liquidity": None,
        "volume_profile": None,
        "vwap_obj": None,
    })
    assert ol is not None
    assert "levels" in ol
    or_h = next(l for l in ol["levels"] if l["label"] == "OR-H")
    ith = or_h["institutional_thesis"]
    assert ith["book_state"] in ("STABLE", "STACKING", "PULLING", "FADING", "UNTRUSTED")
    assert ith["liquidity_quality"] in (
        "REAL", "THIN", "SPOOF_RISK", "ICEBERG_DEFENDED",
        "ABSORPTION", "PULLING", "STACKING", "MIXED",
    )
