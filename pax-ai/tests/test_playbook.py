"""Tests for pax_ai.playbook - scenario tree derivation."""

from __future__ import annotations

from pax_ai import playbook as pb


def _base_snap():
    return {
        "alias": "NQM6.CME@RITHMIC",
        "or_levels": {
            "middleLock": False, "inProximity": True,
            "orHigh": 21340.0, "orLow": 21320.0, "orWidthPts": 20.0,
            "levels": [
                {"label": "+1", "price": 21391.0, "side": "above", "distance": 12.0,
                 "proximity": True, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.62},
            ],
        },
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7},
        "vwap_bias": {"components": {"regime": "INSIDE_BAND"}},
        "gates": {
            "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
            "news": {"blocked": False, "label": None},
        },
        "position": {"size": 0},
        "pax": {"decision": "WAIT"},
    }


def test_state_when_flat_and_in_proximity_is_at_level_entry():
    snap = _base_snap()
    p = pb.build_playbook(snap)
    assert p["current_state"] == "AT_LEVEL_ENTRY"
    assert p["active_level"] == "+1"


def test_middle_lock_forces_wait_state():
    snap = _base_snap()
    snap["or_levels"]["middleLock"] = True
    p = pb.build_playbook(snap)
    assert p["current_state"] == "WAIT_FOR_LEVEL"


def test_branches_include_follow_long_entry_with_price():
    snap = _base_snap()
    p = pb.build_playbook(snap)
    names = [b["name"] for b in p["branches"]]
    assert any("FOLLOW long break of +1" in n for n in names)
    follow = next(b for b in p["branches"] if "FOLLOW long" in b["name"])
    assert "21391.00" in follow["if"]


def test_blowoff_revert_adds_stand_down_branch():
    snap = _base_snap()
    snap["vwap_bias"]["components"]["regime"] = "BLOWOFF_REVERT"
    p = pb.build_playbook(snap)
    names = [b["name"] for b in p["branches"]]
    assert any("STAND DOWN (VWAP blowoff)" in n for n in names)


def test_news_blocked_adds_stand_down_branch():
    snap = _base_snap()
    snap["gates"]["news"] = {"blocked": True, "label": "CPI 08:30"}
    p = pb.build_playbook(snap)
    names = [b["name"] for b in p["branches"]]
    assert any("STAND DOWN (news blackout)" in n for n in names)
    assert p["gates"]["news"] == "BLOCKED"


def test_in_position_state():
    snap = _base_snap()
    snap["position"]["size"] = 3
    p = pb.build_playbook(snap)
    assert p["current_state"] in ("IN_TRADE", "RUNNER", "EXIT_WATCH")


def test_active_level_picks_proximity_over_nearest():
    snap = _base_snap()
    snap["or_levels"]["levels"] = [
        {"label": "OR-H", "price": 21340.0, "side": "above", "distance": -2.0,
         "proximity": False, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.40},
        {"label": "+1", "price": 21405.0, "side": "above", "distance": 65.0,
         "proximity": True, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.62},
    ]
    p = pb.build_playbook(snap)
    assert p["active_level"] == "+1"  # proximity wins despite larger distance
