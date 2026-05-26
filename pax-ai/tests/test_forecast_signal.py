from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from pax_ai import forecast_signal


# -------------------------------------------------------------- helpers

def _block_json(**overrides: Any) -> str:
    base: Dict[str, Any] = {
        "alias":          "NQM6.CME@RITHMIC",
        "level":          "OR-H",
        "thesis":         "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction":      "LONG",
        "horizon_sec":    300,
        "prob_success":   0.62,
        "expected_r":     0.75,
        "invalidation":   "back below OR-H with ask absorption",
        "features_used": ["or_levels", "pull_stack", "tape_buckets"],
    }
    base.update(overrides)
    return json.dumps(base)


def _wrap(block_json: str, prefix: str = "Some Pax prose.\n",
          suffix: str = "") -> str:
    return f"{prefix}<<PAX_FORECAST>>\n{block_json}\n<<END_FORECAST>>{suffix}"


def _snap(alias: str = "NQM6.CME@RITHMIC",
          level_label: str = "OR-H") -> Dict[str, Any]:
    return {
        "health": "ok",
        "alias":  alias,
        "or_levels": {
            "levels": [
                {"label": level_label, "price": 20100.0, "distance": 1.0},
                {"label": "OR-L",      "price": 20080.0, "distance": -19.0},
            ],
        },
    }


# -------------------------------------------------------------- extract

def test_no_block_returns_none():
    assert forecast_signal.extract_block("just prose, no block") is None


def test_empty_input_returns_none():
    assert forecast_signal.extract_block("") is None
    assert forecast_signal.extract_block(None) is None


def test_malformed_json_returns_none():
    bad = "<<PAX_FORECAST>>\n{not valid json}\n<<END_FORECAST>>"
    assert forecast_signal.extract_block(bad) is None


def test_extracts_well_formed_block():
    out = forecast_signal.extract_block(_wrap(_block_json()))
    assert out is not None
    assert out["alias"] == "NQM6.CME@RITHMIC"
    assert out["level"] == "OR-H"
    assert out["thesis"] == "ACCEPTANCE_LONG"
    assert out["execution_read"] == "PAY_FOR_TRADE"
    assert out["direction"] == "LONG"
    assert out["horizon_sec"] == 300
    assert out["prob_success"] == pytest.approx(0.62)
    assert out["expected_r"] == pytest.approx(0.75)
    assert out["invalidation"] == "back below OR-H with ask absorption"
    assert out["features_used"] == ["or_levels", "pull_stack", "tape_buckets"]


def test_missing_required_field_returns_none():
    bad = json.dumps({"alias": "NQM6"})
    assert forecast_signal.extract_block(_wrap(bad)) is None


def test_bad_level_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(level="GARBAGE"))) is None


def test_bad_thesis_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(thesis="FOO_BAR"))) is None


def test_thesis_prefix_match_accepted():
    out = forecast_signal.extract_block(_wrap(_block_json(thesis="ICEBERG_DEFENSE")))
    assert out is not None
    assert out["thesis"] == "ICEBERG_DEFENSE"


def test_pay_for_trade_with_none_direction_rejected():
    bad = _block_json(execution_read="PAY_FOR_TRADE", direction="NONE")
    assert forecast_signal.extract_block(_wrap(bad)) is None


def test_wait_for_confirm_with_long_rejected():
    bad = _block_json(execution_read="WAIT_FOR_CONFIRM", direction="LONG")
    assert forecast_signal.extract_block(_wrap(bad)) is None


def test_stand_down_with_none_accepted():
    out = forecast_signal.extract_block(_wrap(
        _block_json(execution_read="STAND_DOWN", direction="NONE")))
    assert out is not None


def test_prob_out_of_range_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(prob_success=1.5))) is None
    assert forecast_signal.extract_block(_wrap(_block_json(prob_success=-0.1))) is None


def test_horizon_out_of_range_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(horizon_sec=0))) is None
    assert forecast_signal.extract_block(_wrap(_block_json(horizon_sec=999_999))) is None


def test_expected_r_out_of_range_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(expected_r=99.0))) is None
    assert forecast_signal.extract_block(_wrap(_block_json(expected_r=-99.0))) is None


def test_features_used_empty_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(features_used=[]))) is None


def test_features_used_non_string_rejected():
    assert forecast_signal.extract_block(_wrap(_block_json(features_used=[1, 2]))) is None


def test_features_used_deduplicates_preserving_order():
    out = forecast_signal.extract_block(_wrap(
        _block_json(features_used=["or_levels", "or_levels", "tape_buckets"])))
    assert out is not None
    assert out["features_used"] == ["or_levels", "tape_buckets"]


def test_multiple_blocks_last_well_formed_wins():
    text = (
        _wrap(_block_json(prob_success=0.55, expected_r=0.5),
              prefix="first\n", suffix="\nmid\n")
        + _wrap(_block_json(prob_success=0.80, expected_r=1.5))
    )
    out = forecast_signal.extract_block(text)
    assert out is not None
    assert out["prob_success"] == pytest.approx(0.80)


def test_chart_signal_and_forecast_coexist():
    text = (
        "Pax prose.\n"
        '<<PAX_AI_CHART_SIGNAL>>\n'
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20100.0,"confidence":0.72,"reason":"acceptance"}\n'
        '<<END>>\n'
        + _wrap(_block_json())
    )
    out = forecast_signal.extract_block(text)
    assert out is not None
    assert out["alias"] == "NQM6.CME@RITHMIC"


# -------------------------------------------------------------- validate

def test_validate_returns_none_on_non_dict_block():
    assert forecast_signal.validate_against_snapshot("nope", _snap()) is None
    assert forecast_signal.validate_against_snapshot(None, _snap()) is None


def test_validate_rejects_offline_snapshot():
    blk = forecast_signal.extract_block(_wrap(_block_json()))
    snap = _snap()
    snap["health"] = "offline"
    assert forecast_signal.validate_against_snapshot(blk, snap) is None


def test_validate_rejects_alias_mismatch():
    blk = forecast_signal.extract_block(_wrap(_block_json()))
    snap = _snap(alias="ESM6.CME@RITHMIC")
    assert forecast_signal.validate_against_snapshot(blk, snap) is None


def test_validate_rejects_unknown_level():
    blk = forecast_signal.extract_block(_wrap(_block_json(level="+3")))
    snap = _snap(level_label="OR-H")  # no "+3" in snapshot
    assert forecast_signal.validate_against_snapshot(blk, snap) is None


def test_validate_accepts_when_grounded_and_returns_normalized_record():
    blk = forecast_signal.extract_block(_wrap(_block_json()))
    out = forecast_signal.validate_against_snapshot(
        blk, _snap(), ts_ms=1_765_000_000_000)
    assert out is not None
    assert out["alias"] == "NQM6.CME@RITHMIC"
    assert out["level"] == "OR-H"
    assert out["forecast_id"].startswith("pax_fcst|")
    assert out["ts_ms"] == 1_765_000_000_000


def test_validate_defense_in_depth_rejects_bad_combo():
    """Even if a hand-edited block sneaks past extract_block (e.g. tests
    that pass a dict directly), validate_against_snapshot must reject it."""
    blk = {
        "alias": "NQM6.CME@RITHMIC",
        "level": "OR-H",
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": "WAIT_FOR_CONFIRM",
        "direction": "LONG",
        "horizon_sec": 300,
        "prob_success": 0.6,
        "expected_r": 0.5,
        "invalidation": "x",
        "features_used": ["or_levels"],
    }
    assert forecast_signal.validate_against_snapshot(blk, _snap()) is None


def test_validate_never_raises_on_bad_input():
    for arg in (None, "string", 123, [], {}, {"alias": object()}):
        try:
            forecast_signal.validate_against_snapshot(arg, _snap())
        except Exception as exc:
            pytest.fail(f"validate raised on {arg!r}: {exc}")
