from __future__ import annotations

import pytest

from bookmap_mcp import pax_forecast_schema as schema


def _forecast():
    return {
        "alias": "NQM6.CME@RITHMIC",
        "level": "OR-H",
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction": "LONG",
        "horizon_sec": 300,
        "prob_success": 0.62,
        "expected_r": 0.74,
        "invalidation": "back below OR-H with ask absorption",
        "features_used": ["or_levels", "pull_stack", "tape_buckets"],
    }


def test_validate_forecast_normalizes_required_shape():
    out = schema.validate_forecast(_forecast(), ts_ms=1_765_000_000_000,
                                   source_turn_id=42)

    assert out["schema_version"] == 1
    assert out["forecast_id"].startswith("pax_fcst|")
    assert out["source_turn_id"] == 42
    assert out["ts_ms"] == 1_765_000_000_000
    assert out["alias"] == "NQM6.CME@RITHMIC"
    assert out["level"] == "OR-H"
    assert out["prob_success"] == 0.62
    assert out["expected_r"] == 0.74
    assert out["features_used"] == ["or_levels", "pull_stack", "tape_buckets"]


def test_validate_forecast_rejects_missing_required_field():
    raw = _forecast()
    raw.pop("prob_success")

    with pytest.raises(ValueError, match="missing required fields: prob_success"):
        schema.validate_forecast(raw)


def test_validate_forecast_rejects_bad_probability():
    raw = _forecast()
    raw["prob_success"] = 1.25

    with pytest.raises(ValueError, match="prob_success"):
        schema.validate_forecast(raw)


def test_validate_forecast_rejects_directional_wait():
    raw = _forecast()
    raw["execution_read"] = "WAIT_FOR_CONFIRM"
    raw["direction"] = "LONG"

    with pytest.raises(ValueError, match="WAIT_FOR_CONFIRM requires NONE"):
        schema.validate_forecast(raw)


def test_validate_forecast_accepts_wait_with_none_direction():
    raw = _forecast()
    raw["execution_read"] = "WAIT_FOR_CONFIRM"
    raw["direction"] = "NONE"

    out = schema.validate_forecast(raw)
    assert out["execution_read"] == "WAIT_FOR_CONFIRM"
    assert out["direction"] == "NONE"


def test_coerce_forecast_returns_none_on_invalid_input():
    assert schema.coerce_forecast({"alias": "NQM6"}) is None


def test_forecast_setup_bucket_is_stable():
    out = schema.validate_forecast(_forecast())

    assert schema.forecast_setup_bucket(out) == (
        "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    )


def test_probability_bucket_uses_reliability_ranges():
    assert schema.probability_bucket(0.62) == "0.60-0.65"
    assert schema.probability_bucket(1.0) == "0.95-1.00"
