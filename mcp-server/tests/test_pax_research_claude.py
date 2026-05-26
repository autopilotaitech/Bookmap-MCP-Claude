from __future__ import annotations

import json
from pathlib import Path

import pytest

from bookmap_mcp import pax_research_claude as research


# ---------------------------------------------------------------- helpers

def _calibration_with(setup_buckets, prob_buckets=None, horizon=None,
                      non_pay_n=0):
    return {
        "generated_ms": 1_700_000_000_000,
        "date_utc": "2026-05-25",
        "window": {"start_ms": 1, "end_ms": 86_400_001},
        "min_samples": 5,
        "width": 0.05,
        "global": {
            "n_forecasts": sum(b["n_samples"] for b in setup_buckets),
            "n_paired": sum(b["n_samples"] for b in setup_buckets),
            "n_unpaired": 0,
            "mean_stated_prob": 0.62,
            "actual_hit_rate": 0.55,
            "calibration_error": 0.07,
            "mean_realized_r": 0.1,
        },
        "probability_buckets": prob_buckets or [],
        "setup_buckets": setup_buckets,
        "horizon_breakdown": horizon or {},
        "non_pay_for_trade": {"n_forecasts": non_pay_n},
        "notes": [],
    }


def _setup_bucket(setup, n=10, stated=0.65, hit=0.4, mean_r=-0.2, warning=None):
    return {
        "setup": setup,
        "n_samples": n,
        "mean_stated_prob": stated,
        "actual_hit_rate": hit,
        "calibration_error": abs(stated - hit),
        "mean_realized_r": mean_r,
        "warning": warning,
    }


# ---------------------------------------------------------------- derive

def test_derive_empty_calibration_returns_no_lessons():
    cal = _calibration_with(setup_buckets=[])
    assert research.derive_candidate_lessons(cal) == []


def test_derive_skips_buckets_below_min_samples():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=3, stated=0.8, hit=0.2, mean_r=-1.0,
                      warning="insufficient_sample_count"),
    ])
    assert research.derive_candidate_lessons(cal, min_samples=5) == []


def test_derive_flags_overconfident_setup():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    lessons = research.derive_candidate_lessons(
        cal, overconfidence_threshold=0.10, min_samples=5)
    assert len(lessons) >= 1
    overconf = [L for L in lessons if L["finding"] == "overconfident_setup"]
    assert overconf
    lesson = overconf[0]
    assert lesson["setup"] == "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    assert lesson["sample_count"] == 15
    assert lesson["promotion_status"] == "research_only"
    assert lesson["change"]["kind"] in {"downweight", "filter"}
    assert lesson["evidence"]["stated_prob"] == pytest.approx(0.75)
    assert lesson["evidence"]["actual_hit_rate"] == pytest.approx(0.40)
    assert lesson["before_metrics"]["mean_realized_r"] == pytest.approx(-0.10)


def test_derive_flags_negative_expectancy_setup():
    cal = _calibration_with([
        _setup_bucket("OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300",
                      n=12, stated=0.55, hit=0.50, mean_r=-0.30),
    ])
    lessons = research.derive_candidate_lessons(cal, min_samples=5)
    neg = [L for L in lessons if L["finding"] == "negative_expectancy"]
    assert neg
    assert neg[0]["change"]["kind"] == "filter"
    assert neg[0]["sample_count"] == 12


def test_lesson_ids_are_deterministic_for_identical_input():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    a = research.derive_candidate_lessons(cal)
    b = research.derive_candidate_lessons(cal)
    assert [L["lesson_id"] for L in a] == [L["lesson_id"] for L in b]


def test_lesson_schema_has_required_fields():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    lessons = research.derive_candidate_lessons(cal)
    for lesson in lessons:
        for key in ("lesson_id", "setup", "finding", "condition", "change",
                    "evidence", "sample_count", "before_metrics",
                    "promotion_status"):
            assert key in lesson, f"missing key: {key}"
        assert lesson["promotion_status"] == "research_only"


# ---------------------------------------------------------------- prompt

def test_build_research_prompt_mentions_lessons_and_calibration():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    lessons = research.derive_candidate_lessons(cal)
    prompt = research.build_research_prompt(calibration=cal, lessons=lessons)
    assert "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300" in prompt
    assert "calibration_error" in prompt
    assert "research_only" in prompt
    # And the date should be present.
    assert "2026-05-25" in prompt


def test_build_research_prompt_is_byte_stable():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    lessons = research.derive_candidate_lessons(cal)
    a = research.build_research_prompt(calibration=cal, lessons=lessons)
    b = research.build_research_prompt(calibration=cal, lessons=lessons)
    assert a == b


def test_build_research_prompt_handles_tune_report():
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    lessons = research.derive_candidate_lessons(cal)
    tune = {"advisory_weight_deltas": [
        {"source": "conviction_score", "suggested_delta_pct": -5.0,
         "n_samples": 50, "spearman": -0.31, "rationale": "negative correlation"}]}
    prompt = research.build_research_prompt(
        calibration=cal, lessons=lessons, tune_report=tune)
    assert "advisory_weight_deltas" in prompt or "conviction_score" in prompt


# ---------------------------------------------------------------- artifacts

def test_write_candidate_artifacts_creates_both_files(tmp_path):
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    paths = research.write_candidate_artifacts(
        date_utc="2026-05-25",
        calibration=cal,
        reports_dir=tmp_path,
        dry_run=True,
    )
    json_path = paths["policy_candidates"]
    md_path = paths["prompt_lessons"]

    assert json_path.exists()
    assert md_path.exists()
    assert json_path.name == "policy-candidates-2026-05-25.json"
    assert md_path.name == "prompt-lessons-2026-05-25.md"

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["date_utc"] == "2026-05-25"
    assert payload["promotion_status"] == "research_only"
    assert len(payload["lessons"]) >= 1
    md = md_path.read_text(encoding="utf-8")
    assert "research_only" in md


def test_write_candidate_artifacts_empty_lessons_still_writes(tmp_path):
    cal = _calibration_with(setup_buckets=[])
    paths = research.write_candidate_artifacts(
        date_utc="2026-05-25",
        calibration=cal,
        reports_dir=tmp_path,
        dry_run=True,
    )
    payload = json.loads(paths["policy_candidates"].read_text(encoding="utf-8"))
    assert payload["lessons"] == []
    assert "no_candidate_lessons" in payload.get("notes", [])


# ---------------------------------------------------------------- safety

def test_dry_run_never_invokes_subprocess(monkeypatch, tmp_path):
    """In dry-run mode we never spawn Claude (or any process)."""
    import subprocess

    def boom(*a, **kw):
        raise AssertionError("subprocess invoked in dry-run mode")

    monkeypatch.setattr(subprocess, "Popen", boom)
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(subprocess, "check_output", boom)
    monkeypatch.setattr(subprocess, "check_call", boom)

    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    research.write_candidate_artifacts(
        date_utc="2026-05-25",
        calibration=cal,
        reports_dir=tmp_path,
        dry_run=True,
    )


def test_no_mutation_of_active_config(tmp_path):
    forbidden = {
        tmp_path / "pax_ai_config.json": "ORIGINAL_CONFIG",
        tmp_path / "pax_weights.json": "ORIGINAL_WEIGHTS",
        tmp_path / "prompts.py": "ORIGINAL_PROMPTS",
        tmp_path / "learned_playbook.json": "ORIGINAL_PLAYBOOK",
    }
    for p, content in forbidden.items():
        p.write_text(content, encoding="utf-8")

    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    research.write_candidate_artifacts(
        date_utc="2026-05-25",
        calibration=cal,
        reports_dir=tmp_path / "out",
        dry_run=True,
    )

    for p, content in forbidden.items():
        assert p.read_text(encoding="utf-8") == content


def test_main_dry_run_writes_artifacts(tmp_path):
    cal = _calibration_with([
        _setup_bucket("OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
                      n=15, stated=0.75, hit=0.40, mean_r=-0.10),
    ])
    cal_path = tmp_path / "calibration-2026-05-25.json"
    cal_path.write_text(json.dumps(cal), encoding="utf-8")

    out_dir = tmp_path / "out"
    rc = research.main([
        "--date", "2026-05-25",
        "--calibration", str(cal_path),
        "--out-dir", str(out_dir),
        "--dry-run",
    ])
    assert rc == 0
    assert (out_dir / "policy-candidates-2026-05-25.json").exists()
    assert (out_dir / "prompt-lessons-2026-05-25.md").exists()
