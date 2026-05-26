from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookmap_mcp import pax_calibration as calib
from bookmap_mcp import pax_forecast_schema as schema
from bookmap_mcp import pax_forecast_store as store


# ---------------------------------------------------------------- fixtures

def _raw(level="OR-H", prob=0.62, horizon=300, **overrides):
    base = {
        "alias": "NQM6.CME@RITHMIC",
        "level": level,
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction": "LONG",
        "horizon_sec": horizon,
        "prob_success": prob,
        "expected_r": 0.75,
        "invalidation": "back below level",
        "features_used": ["or_levels"],
    }
    base.update(overrides)
    return base


def _outcome(r):
    return {"realized_r": r, "horizon_used_sec": 300, "source": "test"}


# ---------------------------------------------------------------- helpers

def test_utc_day_window_returns_half_open_ms_pair():
    start, end = calib.utc_day_window("2026-05-25")
    assert isinstance(start, int)
    assert isinstance(end, int)
    assert end - start == 86_400_000


def test_utc_day_window_rejects_bad_format():
    with pytest.raises(ValueError):
        calib.utc_day_window("not-a-date")


# ---------------------------------------------------------------- compute

def test_compute_calibration_empty_input():
    report = calib.compute_calibration([])
    assert report["global"]["n_forecasts"] == 0
    assert report["global"]["n_paired"] == 0
    assert report["probability_buckets"] == []
    assert report["setup_buckets"] == []
    assert isinstance(report["notes"], list)


def test_compute_calibration_global_metrics():
    pairs = []
    for i in range(10):
        f = schema.validate_forecast(_raw(prob=0.60), ts_ms=1000 + i, source_turn_id=i)
        # 6 wins, 4 losses to match stated probability 0.60
        out = _outcome(1.0 if i < 6 else -0.5)
        pairs.append((f, out))

    report = calib.compute_calibration(pairs, min_samples=5)

    g = report["global"]
    assert g["n_forecasts"] == 10
    assert g["n_paired"] == 10
    assert g["n_unpaired"] == 0
    assert g["mean_stated_prob"] == pytest.approx(0.60)
    assert g["actual_hit_rate"] == pytest.approx(0.6)
    assert g["calibration_error"] == pytest.approx(0.0, abs=1e-9)
    expected_mean_r = (6 * 1.0 + 4 * -0.5) / 10
    assert g["mean_realized_r"] == pytest.approx(expected_mean_r)


def test_compute_calibration_probability_buckets():
    pairs = []
    # Bucket 0.60-0.65: 10 forecasts at p=0.62, 7 wins
    for i in range(10):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=2000 + i, source_turn_id=100 + i)
        pairs.append((f, _outcome(0.5 if i < 7 else -0.3)))
    # Bucket 0.80-0.85: 8 forecasts at p=0.82, 4 wins
    for i in range(8):
        f = schema.validate_forecast(_raw(prob=0.82, level="OR-L",
                                          thesis="REJECTION_SHORT",
                                          direction="SHORT"),
                                     ts_ms=3000 + i, source_turn_id=200 + i)
        pairs.append((f, _outcome(0.5 if i < 4 else -0.3)))

    report = calib.compute_calibration(pairs, min_samples=5)

    buckets = {b["bucket"]: b for b in report["probability_buckets"]}
    assert "0.60-0.65" in buckets
    assert "0.80-0.85" in buckets

    low = buckets["0.60-0.65"]
    assert low["n_samples"] == 10
    assert low["actual_hit_rate"] == pytest.approx(0.7)
    assert low["mean_stated_prob"] == pytest.approx(0.62)
    assert low["calibration_error"] == pytest.approx(abs(0.62 - 0.7))
    assert low["warning"] is None

    hi = buckets["0.80-0.85"]
    assert hi["n_samples"] == 8
    assert hi["actual_hit_rate"] == pytest.approx(0.5)
    assert hi["mean_stated_prob"] == pytest.approx(0.82)
    assert hi["calibration_error"] == pytest.approx(abs(0.82 - 0.5))


def test_compute_calibration_setup_buckets_group_by_full_signature():
    pairs = []
    for i in range(6):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=4000 + i, source_turn_id=300 + i)
        pairs.append((f, _outcome(0.5 if i < 4 else -0.5)))
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.65, level="OR-L",
                                          thesis="REJECTION_SHORT",
                                          direction="SHORT"),
                                     ts_ms=5000 + i, source_turn_id=400 + i)
        pairs.append((f, _outcome(-0.5)))

    report = calib.compute_calibration(pairs, min_samples=5)

    setups = {b["setup"]: b for b in report["setup_buckets"]}
    long_setup = "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    short_setup = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    assert long_setup in setups
    assert short_setup in setups
    assert setups[long_setup]["n_samples"] == 6
    assert setups[short_setup]["n_samples"] == 5
    assert setups[short_setup]["actual_hit_rate"] == pytest.approx(0.0)
    assert setups[short_setup]["mean_realized_r"] == pytest.approx(-0.5)


def test_compute_calibration_warns_on_low_sample_count():
    pairs = []
    for i in range(3):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=6000 + i, source_turn_id=500 + i)
        pairs.append((f, _outcome(0.5)))

    report = calib.compute_calibration(pairs, min_samples=5)

    pb = report["probability_buckets"][0]
    assert pb["n_samples"] == 3
    assert pb["warning"] == "insufficient_sample_count"

    sb = report["setup_buckets"][0]
    assert sb["warning"] == "insufficient_sample_count"

    assert any("insufficient" in n for n in report["notes"])


def test_compute_calibration_horizon_breakdown():
    pairs = []
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.62, horizon=300),
                                     ts_ms=7000 + i, source_turn_id=600 + i)
        pairs.append((f, _outcome(0.5 if i < 3 else -0.5)))
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.62, horizon=900),
                                     ts_ms=8000 + i, source_turn_id=700 + i)
        pairs.append((f, _outcome(1.0 if i < 4 else -1.0)))

    report = calib.compute_calibration(pairs, min_samples=5)

    horizons = report["horizon_breakdown"]
    assert 300 in horizons
    assert 900 in horizons
    assert horizons[300]["n_samples"] == 5
    assert horizons[900]["n_samples"] == 5
    assert horizons[900]["actual_hit_rate"] == pytest.approx(0.8)


def test_compute_calibration_skips_non_pay_for_trade():
    pairs = []
    for i in range(3):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=9000 + i, source_turn_id=800 + i)
        pairs.append((f, _outcome(0.5)))
    # WAIT_FOR_CONFIRM forecasts should not contribute to probability_buckets
    for i in range(3):
        f = schema.validate_forecast(_raw(prob=0.40, execution_read="WAIT_FOR_CONFIRM",
                                          direction="NONE"),
                                     ts_ms=10000 + i, source_turn_id=900 + i)
        pairs.append((f, _outcome(0.0)))

    report = calib.compute_calibration(pairs, min_samples=5)

    # Only the 3 PAY rows belong to probability_buckets
    total = sum(b["n_samples"] for b in report["probability_buckets"])
    assert total == 3
    # But the global counts include both
    assert report["global"]["n_forecasts"] == 6
    assert report["non_pay_for_trade"]["n_forecasts"] == 3


def test_compute_calibration_handles_missing_outcomes():
    pairs = []
    for i in range(4):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=11000 + i, source_turn_id=1000 + i)
        out = _outcome(0.5) if i < 2 else None
        pairs.append((f, out))

    report = calib.compute_calibration(pairs, min_samples=5)
    assert report["global"]["n_forecasts"] == 4
    assert report["global"]["n_paired"] == 2
    assert report["global"]["n_unpaired"] == 2


# ---------------------------------------------------------------- CLI / day

def test_calibration_for_day_with_in_memory_lookup(tmp_path):
    db_path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db_path)
    base_ms = calib.utc_day_window("2026-05-25")[0]
    for i in range(6):
        s.record(_raw(prob=0.62), ts_ms=base_ms + i * 1000, source_turn_id=i + 1)
    s.close()

    fake_outcomes = {i + 1: _outcome(1.0 if i < 4 else -1.0) for i in range(6)}

    def lookup(forecast):
        return fake_outcomes.get(forecast["source_turn_id"])

    report = calib.calibration_for_day(
        forecasts_path=db_path,
        outcome_lookup=lookup,
        date_utc="2026-05-25",
        min_samples=5,
    )

    assert report["date_utc"] == "2026-05-25"
    assert report["global"]["n_paired"] == 6
    assert report["global"]["actual_hit_rate"] == pytest.approx(4 / 6)


def test_main_writes_report_file_with_expected_keys(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db_path)
    base_ms = calib.utc_day_window("2026-05-25")[0]
    for i in range(6):
        s.record(_raw(prob=0.62), ts_ms=base_ms + i * 1000, source_turn_id=i + 1)
    s.close()

    report_path = tmp_path / "reports" / "calibration-2026-05-25.json"

    rc = calib.main([
        "--date", "2026-05-25",
        "--forecasts", str(db_path),
        "--min-samples", "5",
        "--report", str(report_path),
    ])
    assert rc == 0
    assert report_path.exists()

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["date_utc"] == "2026-05-25"
    for key in ("global", "probability_buckets", "setup_buckets",
                "horizon_breakdown", "non_pay_for_trade", "notes",
                "window", "generated_ms", "min_samples", "width"):
        assert key in payload


def test_calibration_is_read_only(tmp_path):
    """Calibration must never touch active config / weights / prompts."""
    db_path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db_path)
    s.record(_raw(prob=0.62), ts_ms=1, source_turn_id=1)
    s.close()

    # Sentinel files in the temp dir simulating the active config surfaces.
    forbidden = {
        tmp_path / "pax_ai_config.json": "ORIGINAL_CONFIG",
        tmp_path / "pax_weights.json": "ORIGINAL_WEIGHTS",
        tmp_path / "prompts.py": "ORIGINAL_PROMPTS",
    }
    for p, content in forbidden.items():
        p.write_text(content, encoding="utf-8")

    calib.calibration_for_day(
        forecasts_path=db_path,
        outcome_lookup=lambda f: None,
        date_utc="2026-05-25",
        min_samples=5,
    )

    for p, content in forbidden.items():
        assert p.read_text(encoding="utf-8") == content


def test_bus_outcome_lookup_missing_db_does_not_create_file(tmp_path):
    missing = tmp_path / "missing-bus.db"
    lookup = calib.bus_outcome_lookup(missing)

    assert lookup({"source_turn_id": 1, "horizon_sec": 300}) is None
    assert not missing.exists()


# --------------------------------------------------- Phase 2 calibration honesty

def _invalidated_outcome(r=1.0, reason="HORIZON_DATA_MISSING"):
    """An outcome dict shaped the way bus_outcome_lookup(include_invalidated=True)
    produces them: realized_r is present but invalidated=1 must keep the row
    out of calibration math."""
    return {"realized_r": r, "horizon_used_sec": 300, "source": "test",
            "invalidated": 1, "invalidation_reason": reason}


def test_global_counts_include_phase2_breakdown():
    """Global block carries explicit n_pay_for_trade / n_non_pay_for_trade
    / n_paired_valid / n_invalidated alongside the existing n_paired."""
    pairs = []
    # 5 PAY rows with valid outcomes
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=1000 + i,
                                      source_turn_id=i + 1)
        pairs.append((f, _outcome(0.5 if i < 3 else -0.5)))
    # 2 PAY rows that surface as invalidated (audit-mode lookup)
    for i in range(2):
        f = schema.validate_forecast(_raw(prob=0.62), ts_ms=1100 + i,
                                      source_turn_id=10 + i)
        pairs.append((f, _invalidated_outcome(1.0)))
    # 3 non-PAY rows
    for i in range(3):
        f = schema.validate_forecast(_raw(prob=0.4,
                                          execution_read="STAND_DOWN",
                                          direction="NONE"),
                                      ts_ms=1200 + i, source_turn_id=20 + i)
        pairs.append((f, None))
    # 1 PAY row with no outcome (truly unpaired)
    f = schema.validate_forecast(_raw(prob=0.62), ts_ms=1300,
                                  source_turn_id=30)
    pairs.append((f, None))

    report = calib.compute_calibration(pairs, min_samples=5)
    g = report["global"]
    assert g["n_forecasts"]          == 11
    assert g["n_pay_for_trade"]      == 8
    assert g["n_non_pay_for_trade"]  == 3
    assert g["n_paired_valid"]       == 5
    assert g["n_invalidated"]        == 2
    # n_paired stays backwards-compat: anything returned by lookup with a
    # realized_r counts, regardless of validity.
    assert g["n_paired"]             == 7
    # n_unpaired counts pairs across the whole input where lookup returned
    # nothing (the truly missing rows). non-PAY rows + unpaired-PAY row.
    assert g["n_unpaired"]           == 4


def test_invalidated_rows_excluded_from_calibration_math():
    """Five valid wins at p=0.6 + invalidated noise must not move hit_rate."""
    pairs = []
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.60),
                                      ts_ms=2000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(1.0)))  # all valid wins
    for i in range(3):
        f = schema.validate_forecast(_raw(prob=0.60),
                                      ts_ms=2100 + i, source_turn_id=100 + i)
        pairs.append((f, _invalidated_outcome(-5.0)))  # invalidated loss

    report = calib.compute_calibration(pairs, min_samples=5)
    g = report["global"]
    # Math sees only the 5 valid wins.
    assert g["actual_hit_rate"] == pytest.approx(1.0)
    assert g["mean_realized_r"] == pytest.approx(1.0)
    assert g["n_paired_valid"] == 5
    assert g["n_invalidated"] == 3


def test_brier_score_global_correct_for_known_outcomes():
    """Brier = mean((p - hit)^2). 10 forecasts at p=0.6, 6 wins, 4 losses:
       wins:   (0.6 - 1)^2 = 0.16  (x6 = 0.96)
       losses: (0.6 - 0)^2 = 0.36  (x4 = 1.44)
       brier  = 2.40 / 10           = 0.24
    """
    pairs = []
    for i in range(10):
        f = schema.validate_forecast(_raw(prob=0.60),
                                      ts_ms=3000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(1.0 if i < 6 else -0.5)))

    report = calib.compute_calibration(pairs, min_samples=5)
    assert report["global"]["brier_score"] == pytest.approx(0.24)


def test_brier_score_per_probability_bucket():
    """Per-bucket Brier exposed alongside calibration_error.

       Bucket 0.60-0.65 (p=0.62, n=10, 7 wins):
         brier = (7*(0.62-1)^2 + 3*(0.62-0)^2) / 10
               = (7*0.1444   + 3*0.3844)        / 10
               = (1.0108     + 1.1532)          / 10
               = 0.2164
    """
    pairs = []
    for i in range(10):
        f = schema.validate_forecast(_raw(prob=0.62),
                                      ts_ms=4000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(0.5 if i < 7 else -0.3)))

    report = calib.compute_calibration(pairs, min_samples=5)
    buckets = {b["bucket"]: b for b in report["probability_buckets"]}
    bucket = buckets["0.60-0.65"]
    assert bucket["brier_score"] == pytest.approx(0.2164, abs=1e-6)


def test_brier_score_zero_when_all_certainty_correct():
    """All forecasts at p=0 with all losses -> hit=0 each -> brier=0."""
    pairs = []
    for i in range(8):
        f = schema.validate_forecast(_raw(prob=0.0),
                                      ts_ms=5000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(-0.5)))

    report = calib.compute_calibration(pairs, min_samples=5)
    assert report["global"]["brier_score"] == pytest.approx(0.0)


def test_width_sensitivity_section_present_with_both_widths():
    """The report exposes a width_sensitivity block keyed '0.05' and '0.10'."""
    pairs = []
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.62),
                                      ts_ms=6000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(0.5)))
    for i in range(5):
        f = schema.validate_forecast(_raw(prob=0.68),
                                      ts_ms=6100 + i, source_turn_id=100 + i)
        pairs.append((f, _outcome(0.5)))

    report = calib.compute_calibration(pairs, min_samples=5)
    sens = report["width_sensitivity"]
    assert "0.05" in sens
    assert "0.10" in sens
    assert sens["0.05"]["width"] == 0.05
    assert sens["0.10"]["width"] == 0.10
    # Two distinct p-values 0.62/0.68 fall in the same 0.10 bucket but in
    # two different 0.05 buckets.
    assert sens["0.05"]["bucket_count"] == 2
    assert sens["0.10"]["bucket_count"] == 1


def test_width_sensitivity_is_deterministic():
    """Same input must yield byte-identical width_sensitivity output across
    runs (the only time-varying part is generated_ms which lives above)."""
    import json as _json
    pairs = []
    for i in range(7):
        f = schema.validate_forecast(_raw(prob=0.62),
                                      ts_ms=7000 + i, source_turn_id=i + 1)
        pairs.append((f, _outcome(0.5 if i < 4 else -0.5)))

    r1 = calib.compute_calibration(pairs, min_samples=5)["width_sensitivity"]
    r2 = calib.compute_calibration(pairs, min_samples=5)["width_sensitivity"]
    assert _json.dumps(r1, sort_keys=True) == _json.dumps(r2, sort_keys=True)


def test_width_sensitivity_does_not_recommend_policy_changes():
    """The sensitivity block is informational. It must not name an
    optimal width, recommend re-tuning, or include 'recommended' keys."""
    pairs = [
        (schema.validate_forecast(_raw(prob=0.62),
                                   ts_ms=8000 + i, source_turn_id=i + 1),
         _outcome(0.5))
        for i in range(5)
    ]
    report = calib.compute_calibration(pairs, min_samples=5)
    sens = report["width_sensitivity"]
    for body in sens.values():
        for forbidden in ("recommended", "recommended_width",
                           "best_width", "policy"):
            assert forbidden not in body
