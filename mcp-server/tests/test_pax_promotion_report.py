"""Promotion report (STAGE 3): honest per-setup status, no invented edge."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_promotion_report as P   # noqa: E402


def test_empty_scorecard_is_insufficient():
    rep = P.build_promotion_report({"setups": []}, now_ms=1)
    assert rep["has_outcome_data"] is False
    assert rep["setups"] == []
    assert rep["candidate_count"] == 0
    assert rep["live_blocked"] is True
    assert any("no scorecard" in s for s in rep["limitations"])


def test_none_scorecard_is_safe():
    rep = P.build_promotion_report(None, now_ms=1)
    assert rep["has_outcome_data"] is False
    assert rep["live_blocked"] is True


def test_small_positive_sample_not_validated():
    sc = {"min_samples": 30,
          "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 10,
                      "mean_realized_r": 0.4, "hit_rate": 0.6}]}
    rep = P.build_promotion_report(sc, now_ms=1)
    row = rep["setups"][0]
    assert row["promotion_status"] == "insufficient_sample"
    assert rep["candidate_count"] == 0


def test_sufficient_positive_sample_is_candidate_not_validated():
    # n>=30, avgR>=0.05, netR=0.3*40=12 >= 1.0 -> candidate (ceiling).
    sc = {"min_samples": 30,
          "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
                      "mean_realized_r": 0.3, "hit_rate": 0.62}]}
    rep = P.build_promotion_report(sc, now_ms=1)
    row = rep["setups"][0]
    assert row["promotion_status"] == "candidate"
    assert row["net_r"] == 12.0
    assert row["win_rate"] == 0.62
    # validated must never be auto-assigned.
    assert all(r["promotion_status"] != "validated" for r in rep["setups"])
    assert any("validated NOT auto" in r for r in row["reasons"])


def test_positive_below_gates_is_exploratory():
    # n>=30 but netR = 0.01*30 = 0.3 < 1.0 and avgR 0.01 < 0.05 -> exploratory.
    sc = {"min_samples": 30,
          "setups": [{"setup": "B|SHORT|OR-L|ETH", "n": 30,
                      "mean_realized_r": 0.01, "hit_rate": 0.5}]}
    rep = P.build_promotion_report(sc, now_ms=1)
    assert rep["setups"][0]["promotion_status"] == "exploratory"


def test_negative_expectancy_is_blocked():
    sc = {"min_samples": 30,
          "setups": [{"setup": "C|LONG|OR-H|RTH", "n": 50,
                      "mean_realized_r": -0.4, "hit_rate": 0.3}]}
    rep = P.build_promotion_report(sc, now_ms=1)
    assert rep["setups"][0]["promotion_status"] == "blocked"


def test_max_drawdown_and_calibration_unavailable_reported_null():
    sc = {"min_samples": 30,
          "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
                      "mean_realized_r": 0.3, "hit_rate": 0.6}]}
    row = P.build_promotion_report(sc, now_ms=1)["setups"][0]
    assert row["max_drawdown"] is None
    assert row["calibration_bucket"] is None


def test_json_shape_stable_except_generated_ms():
    sc = {"min_samples": 30,
          "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
                      "mean_realized_r": 0.3, "hit_rate": 0.6}]}
    a = P.build_promotion_report(sc, now_ms=10)
    b = P.build_promotion_report(sc, now_ms=20)
    a.pop("generated_ms"); b.pop("generated_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_load_scorecard_missing_returns_none(tmp_path):
    assert P.load_scorecard(tmp_path / "nope.json") is None
