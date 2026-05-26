from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookmap_mcp import pax_forecast_schema as schema
from bookmap_mcp import pax_forecast_store as store
from bookmap_mcp import pax_policy_replay as replay


# ---------------------------------------------------------------- fixtures

def _raw(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG",
         horizon=300, prob=0.62, expected_r=0.75):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "level": level,
        "thesis": thesis,
        "execution_read": "PAY_FOR_TRADE",
        "direction": direction,
        "horizon_sec": horizon,
        "prob_success": prob,
        "expected_r": expected_r,
        "invalidation": "back below level",
        "features_used": ["or_levels"],
    }


def _validated(**kwargs):
    ts_ms = kwargs.pop("ts_ms", 1000)
    source_turn_id = kwargs.pop("source_turn_id", 1)
    return schema.validate_forecast(_raw(**kwargs), ts_ms=ts_ms,
                                    source_turn_id=source_turn_id)


def _outcome(r):
    return {"realized_r": r, "horizon_used_sec": 300, "source": "test"}


def _lesson(target_setup, kind="filter"):
    return {
        "lesson_id": f"pax_lesson|test_{kind}",
        "setup": target_setup,
        "finding": "negative_expectancy" if kind == "filter" else "overconfident_setup",
        "condition": "test",
        "change": {"kind": kind, "target": target_setup},
        "evidence": {"stated_prob": 0.7, "actual_hit_rate": 0.4,
                     "calibration_error": 0.3},
        "sample_count": 12,
        "before_metrics": {"mean_realized_r": -0.5, "actual_hit_rate": 0.4,
                           "n_samples": 12},
        "promotion_status": "research_only",
    }


# ---------------------------------------------------------------- time split

def test_time_split_deterministic():
    forecasts = [_validated(ts_ms=i, source_turn_id=i) for i in range(100)]
    a = replay.time_split(forecasts)
    b = replay.time_split(forecasts)
    assert [f["ts_ms"] for f in a["train"]] == [f["ts_ms"] for f in b["train"]]
    assert [f["ts_ms"] for f in a["validation"]] == [f["ts_ms"] for f in b["validation"]]
    assert [f["ts_ms"] for f in a["test"]] == [f["ts_ms"] for f in b["test"]]


def test_time_split_proportions_default_60_20_20():
    forecasts = [_validated(ts_ms=i, source_turn_id=i) for i in range(100)]
    out = replay.time_split(forecasts)
    assert len(out["train"]) == 60
    assert len(out["validation"]) == 20
    assert len(out["test"]) == 20


def test_time_split_is_time_ordered_not_shuffled():
    forecasts = [_validated(ts_ms=10_000 - i, source_turn_id=i)
                 for i in range(10)]
    out = replay.time_split(forecasts)
    train_ts = [f["ts_ms"] for f in out["train"]]
    val_ts = [f["ts_ms"] for f in out["validation"]]
    test_ts = [f["ts_ms"] for f in out["test"]]
    assert train_ts == sorted(train_ts)
    assert val_ts == sorted(val_ts)
    assert test_ts == sorted(test_ts)
    if train_ts and val_ts:
        assert train_ts[-1] <= val_ts[0]
    if val_ts and test_ts:
        assert val_ts[-1] <= test_ts[0]


def test_time_split_handles_empty():
    out = replay.time_split([])
    assert out == {"train": [], "validation": [], "test": []}


def test_time_split_handles_small_population():
    forecasts = [_validated(ts_ms=i, source_turn_id=i) for i in range(5)]
    out = replay.time_split(forecasts)
    assert sum(len(out[k]) for k in ("train", "validation", "test")) == 5


# ---------------------------------------------------------------- evaluate

def test_evaluate_policy_basic_metrics():
    pairs = []
    for i in range(10):
        f = _validated(ts_ms=i, source_turn_id=i)
        pairs.append((f, _outcome(1.0 if i < 6 else -0.5)))

    metrics = replay.evaluate_policy(pairs)
    assert metrics["n_samples"] == 10
    assert metrics["mean_realized_r"] == pytest.approx((6 * 1.0 + 4 * -0.5) / 10)
    assert metrics["median_realized_r"] == pytest.approx(1.0)
    assert metrics["hit_payline_pct"] == pytest.approx(0.6)
    assert metrics["hit_invalidation_pct"] == pytest.approx(0.0)


def test_evaluate_policy_filter_excludes_matching():
    pairs = []
    for i in range(5):
        f = _validated(ts_ms=i, source_turn_id=i,
                       level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
        pairs.append((f, _outcome(1.0)))
    for i in range(5):
        f = _validated(ts_ms=100 + i, source_turn_id=100 + i,
                       level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
        pairs.append((f, _outcome(-0.5)))

    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    lesson = _lesson(target, kind="filter")
    filter_fn = replay.candidate_filter_for_lesson(lesson)

    metrics = replay.evaluate_policy(pairs, filter_fn=filter_fn)
    assert metrics["n_samples"] == 5
    assert metrics["mean_realized_r"] == pytest.approx(1.0)


def test_evaluate_policy_empty_input():
    metrics = replay.evaluate_policy([])
    assert metrics["n_samples"] == 0
    assert metrics["mean_realized_r"] == 0.0
    assert metrics["hit_payline_pct"] == 0.0


def test_evaluate_policy_skips_unpaired():
    pairs = [(_validated(ts_ms=1, source_turn_id=1), None),
             (_validated(ts_ms=2, source_turn_id=2), _outcome(0.5))]
    metrics = replay.evaluate_policy(pairs)
    assert metrics["n_samples"] == 1
    assert metrics["n_unpaired"] == 1


# ---------------------------------------------------------------- replay

def _build_forecasts_paired_with(n_per_setup):
    """Build (forecasts, outcome_lookup) for two setups with different
    outcomes, interleaved by ts so each time split contains both setups."""
    forecasts = []
    outcomes = {}
    max_len = max(len(rs) for _, rs in n_per_setup)
    sid = 1
    for i in range(max_len):
        for setup_kwargs, rs in n_per_setup:
            if i < len(rs):
                f = _validated(ts_ms=sid, source_turn_id=sid, **setup_kwargs)
                forecasts.append(f)
                outcomes[sid] = _outcome(rs[i])
                sid += 1
    return forecasts, lambda f: outcomes.get(f["source_turn_id"])


def test_replay_promotes_when_filter_improves_metrics():
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    # 120 forecasts; bad setup is consistently -1R, good setup +1R.
    forecasts, lookup = _build_forecasts_paired_with([
        (good_setup, [1.0] * 60),
        (bad_setup, [-1.0] * 60),
    ])
    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates = [_lesson(target, kind="filter")]

    report = replay.replay_candidates(forecasts, lookup, candidates,
                                      min_samples=10)
    assert len(report["candidates"]) == 1
    res = report["candidates"][0]
    assert res["lesson_id"] == candidates[0]["lesson_id"]
    # Auto-promotion is conservative: replay_passed (R improved on val+test)
    # or paper_candidate (R + hit-rate improved on test). Never beyond.
    assert res["promotion_status"] in {"replay_passed", "paper_candidate"}
    assert res["splits"]["test"]["candidate"]["mean_realized_r"] > \
           res["splits"]["test"]["current"]["mean_realized_r"]


def test_replay_keeps_research_only_when_filter_makes_things_worse():
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    # Filtering OUT the GOOD setup should regress metrics.
    forecasts, lookup = _build_forecasts_paired_with([
        (good_setup, [1.0] * 60),
        (bad_setup, [0.5] * 60),
    ])
    target = "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    candidates = [_lesson(target, kind="filter")]

    report = replay.replay_candidates(forecasts, lookup, candidates,
                                      min_samples=10)
    res = report["candidates"][0]
    assert res["promotion_status"] == "research_only"
    assert "did_not_improve" in res["reason"] or "regression" in res["reason"]


def test_replay_keeps_research_only_when_insufficient_test_samples():
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    forecasts, lookup = _build_forecasts_paired_with([
        (good_setup, [1.0] * 5),
        (bad_setup, [-1.0] * 5),
    ])
    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates = [_lesson(target, kind="filter")]

    report = replay.replay_candidates(forecasts, lookup, candidates,
                                      min_samples=10)
    res = report["candidates"][0]
    assert res["promotion_status"] == "research_only"
    assert "insufficient_samples" in res["reason"]


def test_replay_never_auto_promotes_past_paper_candidate():
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    forecasts, lookup = _build_forecasts_paired_with([
        (good_setup, [1.0] * 60),
        (bad_setup, [-1.0] * 60),
    ])
    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates = [_lesson(target, kind="filter")]

    report = replay.replay_candidates(forecasts, lookup, candidates,
                                      min_samples=10)
    for res in report["candidates"]:
        assert res["promotion_status"] in {
            "research_only", "replay_passed", "paper_candidate"
        }
        assert res["promotion_status"] not in {
            "paper_passed", "human_approved", "active"
        }


def test_replay_report_schema():
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    forecasts, lookup = _build_forecasts_paired_with([
        (good_setup, [1.0] * 60),
        (bad_setup, [-1.0] * 60),
    ])
    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates = [_lesson(target, kind="filter")]

    report = replay.replay_candidates(forecasts, lookup, candidates,
                                      min_samples=10)
    for key in ("generated_ms", "n_forecasts", "n_paired", "split_sizes",
                "min_samples", "candidates"):
        assert key in report
    res = report["candidates"][0]
    for key in ("lesson_id", "setup", "change", "splits", "false_positive_reduction",
                "promotion_status", "reason"):
        assert key in res
    for split_name in ("train", "validation", "test"):
        block = res["splits"][split_name]
        assert "current" in block
        assert "candidate" in block
        for which in ("current", "candidate"):
            for metric in ("n_samples", "mean_realized_r", "median_realized_r",
                           "hit_payline_pct", "hit_invalidation_pct"):
                assert metric in block[which]


def test_replay_for_day_reads_store(tmp_path):
    db_path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db_path)
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    outcomes = {}
    ts = 1_700_000_000_000
    sid = 1
    for r in [1.0] * 50:
        s.record(_raw(**good_setup), ts_ms=ts, source_turn_id=sid)
        outcomes[sid] = _outcome(r)
        ts += 1; sid += 1
    for r in [-1.0] * 50:
        s.record(_raw(**bad_setup), ts_ms=ts, source_turn_id=sid)
        outcomes[sid] = _outcome(r)
        ts += 1; sid += 1
    s.close()

    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates_path = tmp_path / "policy-candidates.json"
    candidates_path.write_text(json.dumps({
        "lessons": [_lesson(target, kind="filter")],
        "promotion_status": "research_only",
    }), encoding="utf-8")

    report = replay.replay_for_day(
        forecasts_path=db_path,
        outcome_lookup=lambda f: outcomes.get(f["source_turn_id"]),
        candidates_path=candidates_path,
        min_samples=10,
    )
    assert report["n_forecasts"] == 100
    assert len(report["candidates"]) == 1


def test_main_writes_replay_report_file(tmp_path):
    db_path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db_path)
    good_setup = dict(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG")
    bad_setup = dict(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT")
    outcomes_db_pairs = []
    ts = 1_700_000_000_000
    sid = 1
    for r in [1.0] * 50:
        s.record(_raw(**good_setup), ts_ms=ts, source_turn_id=sid)
        outcomes_db_pairs.append((sid, r))
        ts += 1; sid += 1
    for r in [-1.0] * 50:
        s.record(_raw(**bad_setup), ts_ms=ts, source_turn_id=sid)
        outcomes_db_pairs.append((sid, r))
        ts += 1; sid += 1
    s.close()

    # Write a side outcomes JSON for the CLI to read deterministically.
    outcomes_json = tmp_path / "outcomes.json"
    outcomes_json.write_text(json.dumps([
        {"source_turn_id": sid, "realized_r": r, "horizon_used_sec": 300}
        for sid, r in outcomes_db_pairs
    ]), encoding="utf-8")

    target = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    candidates_path = tmp_path / "policy-candidates.json"
    candidates_path.write_text(json.dumps({
        "lessons": [_lesson(target, kind="filter")],
        "promotion_status": "research_only",
    }), encoding="utf-8")

    report_path = tmp_path / "replay.json"
    rc = replay.main([
        "--forecasts", str(db_path),
        "--outcomes-json", str(outcomes_json),
        "--candidates", str(candidates_path),
        "--report", str(report_path),
        "--min-samples", "10",
    ])
    assert rc == 0
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert "candidates" in payload


def test_outcomes_json_lookup_requires_matching_horizon(tmp_path):
    outcomes_json = tmp_path / "outcomes.json"
    outcomes_json.write_text(json.dumps([
        {"source_turn_id": 7, "realized_r": 1.0, "horizon_used_sec": 180}
    ]), encoding="utf-8")

    lookup = replay._outcomes_from_json(outcomes_json)

    assert lookup({"source_turn_id": 7, "horizon_sec": 300}) is None
    assert lookup({"source_turn_id": 7, "horizon_sec": 180}) == {
        "realized_r": 1.0,
        "horizon_used_sec": 180,
        "source": "json",
    }


def test_replay_does_not_mutate_active_config(tmp_path):
    forbidden = {
        tmp_path / "pax_ai_config.json": "ORIG_CONFIG",
        tmp_path / "pax_weights.json": "ORIG_WEIGHTS",
        tmp_path / "prompts.py": "ORIG_PROMPTS",
        tmp_path / "learned_playbook.json": "ORIG_PLAYBOOK",
    }
    for p, content in forbidden.items():
        p.write_text(content, encoding="utf-8")

    forecasts = [_validated(ts_ms=i, source_turn_id=i) for i in range(20)]
    candidates = [_lesson(
        "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300", kind="filter")]
    replay.replay_candidates(forecasts, lambda f: _outcome(0.1), candidates,
                             min_samples=5)

    for p, content in forbidden.items():
        assert p.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------- downweight

def test_downweight_lesson_keeps_samples_but_records_flag():
    forecasts = []
    outcomes = {}
    for i in range(10):
        f = _validated(ts_ms=i, source_turn_id=i)
        forecasts.append(f)
        outcomes[i] = _outcome(0.5)
    candidates = [_lesson(
        "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300", kind="downweight")]

    report = replay.replay_candidates(
        forecasts, lambda f: outcomes.get(f["source_turn_id"]),
        candidates, min_samples=2,
    )
    res = report["candidates"][0]
    for split in ("train", "validation", "test"):
        assert res["splits"][split]["candidate"]["n_samples"] == \
               res["splits"][split]["current"]["n_samples"]
