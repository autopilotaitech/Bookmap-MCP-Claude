"""Pin that adding institutional_thesis does NOT remove or alter any legacy
per-level field. This is the contract that protects existing UI consumers
(OpenRange overlays, dashboards, edge_calculus, replay tools)."""
from __future__ import annotations

from bookmap_mcp.dashboard import compute_or_levels, _LEVEL_TOUCH_STATE


_LEGACY_LEVEL_KEYS = {
    "label", "price", "side", "distance", "proximity",
    "decision", "decisionLabel", "score", "confidence",
    "reasons", "components", "composite",
}


def _snap(mid=20002.0):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "book": {"mid": mid},
        "or_row": {"orHigh": 20000.0, "orLow": 19500.0},
        "micro_events": {"events": []},
        "tape_flow": {"deltaScore": 0.0, "label": "MIXED"},
    }


def test_every_legacy_per_level_key_still_present():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    assert ol is not None
    for lvl in ol["levels"]:
        for k in _LEGACY_LEVEL_KEYS:
            assert k in lvl, f"{lvl.get('label')} missing legacy key {k}"


def test_decision_value_remains_in_legacy_set():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    legacy = {"ENTER_LONG_FOLLOW", "ENTER_LONG_FADE",
              "ENTER_SHORT_FOLLOW", "ENTER_SHORT_FADE", "WAIT"}
    for lvl in ol["levels"]:
        assert lvl["decision"] in legacy


def test_composite_block_still_has_all_legacy_subkeys():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    for lvl in ol["levels"]:
        c = lvl["composite"]
        for k in ("score", "direction", "confidence", "drivers", "warnings"):
            assert k in c


def test_components_block_still_has_all_legacy_subkeys():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    for lvl in ol["levels"]:
        comp = lvl["components"]
        for k in ("ps_bbo", "ps_rot", "ps_rot_mag", "lt", "tape",
                  "micro", "vwap_stretch", "vp_ctx"):
            assert k in comp, f"{lvl['label']} components missing {k}"


def test_top_level_or_levels_block_legacy_keys_present():
    _LEVEL_TOUCH_STATE.clear()
    ol = compute_or_levels(_snap())
    for k in ("anchor", "orHigh", "orLow", "levels"):
        assert k in ol
