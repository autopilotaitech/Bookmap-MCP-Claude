"""Tests for policy_promotion_gate.

The gate is a deterministic, filesystem-only guard. Given a list of changed
files and a list of candidate replay-report paths, it answers one question:
*should this active-policy change be allowed in?* The replay report is the
evidence; the gate is the mechanical checker.

Contract under test:
  - no active-policy file changed -> pass (no report required)
  - any active-policy file changed -> require >= 1 structurally valid replay
    report AND >= 1 candidate with promotion_status > research_only AND
    each non-research candidate carries promoted_by
  - 'active' is rejected by default; only allowed when allow_active=True AND
    the candidate carries human_approved_by evidence
  - the gate never mutates pax_weights.json / pax_ai_config.json / prompts /
    reports / DBs
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from bookmap_mcp import policy_promotion_gate as gate


# ----------------------------------------------------------------- fixtures

_BASE_SPLIT_METRICS = {
    "n_samples":            30,
    "mean_realized_r":      0.5,
    "median_realized_r":    0.5,
    "hit_payline_pct":      0.55,
    "hit_invalidation_pct": 0.10,
}


def _split_block(*, current_n=30, candidate_n=30):
    cur  = dict(_BASE_SPLIT_METRICS); cur["n_samples"]  = current_n
    cand = dict(_BASE_SPLIT_METRICS); cand["n_samples"] = candidate_n
    return {"current": cur, "candidate": cand}


def _candidate(*,
                lesson_id="pax_lesson|t",
                status="replay_passed",
                reason="validation_and_test_r_improved",
                promoted_by="ci-bot",
                human_approved_by=None,
                test_n=30,
                splits=None):
    c: Dict[str, Any] = {
        "lesson_id":        lesson_id,
        "setup":            "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300",
        "change":           {"kind": "filter", "target": "OR-H|..."},
        "promotion_status": status,
        "reason":           reason,
        "splits": splits or {
            "train":      _split_block(),
            "validation": _split_block(),
            "test":       _split_block(candidate_n=test_n),
        },
    }
    if promoted_by is not None:
        c["promoted_by"] = promoted_by
    if human_approved_by is not None:
        c["human_approved_by"] = human_approved_by
    return c


def _valid_report(*,
                   generated_ms=1_700_000_000_000,
                   n_forecasts=120,
                   n_paired=120,
                   candidates=None,
                   **extras):
    doc: Dict[str, Any] = {
        "generated_ms": generated_ms,
        "n_forecasts":  n_forecasts,
        "n_paired":     n_paired,
        "split_sizes":  {"train": 72, "validation": 24, "test": 24},
        "candidates":   candidates or [_candidate()],
    }
    doc.update(extras)
    return doc


def _write_report(tmp_path: Path, name: str, doc: Dict[str, Any]) -> Path:
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ---------------------------------------------------------------- no-change

def test_no_active_policy_changes_passes_without_report():
    """If only non-active-policy files changed, the gate passes."""
    result = gate.check_promotion_gate(
        changed_files=[
            "pax-ai/pax_ai/server.py",
            "mcp-server/bookmap_mcp/pax_calibration.py",
            "README.md",
        ],
        report_paths=[],
    )
    assert result["passed"] is True
    assert result["reason"] == "no_active_policy_change"
    assert result["active_policy_changes"] == []


# ---------------------------------------------------------------- missing report

def test_pax_weights_changed_without_report_fails():
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[],
    )
    assert result["passed"] is False
    assert "without_replay_report" in result["reason"]
    assert "mcp-server/bookmap_mcp/pax_weights.json" in result["active_policy_changes"]


def test_prompts_py_changed_without_report_fails():
    result = gate.check_promotion_gate(
        changed_files=["pax-ai/pax_ai/prompts.py"],
        report_paths=[],
    )
    assert result["passed"] is False
    assert "without_replay_report" in result["reason"]
    assert "pax-ai/pax_ai/prompts.py" in result["active_policy_changes"]


def test_skills_path_changed_without_report_fails():
    """Both repo-root skills/ and pax-ai/skills/ count as active policy."""
    result1 = gate.check_promotion_gate(
        changed_files=["skills/momentum-scan/SKILL.md"],
        report_paths=[],
    )
    assert result1["passed"] is False
    assert "skills/momentum-scan/SKILL.md" in result1["active_policy_changes"]

    result2 = gate.check_promotion_gate(
        changed_files=["pax-ai/skills/pax-or/SKILL.md"],
        report_paths=[],
    )
    assert result2["passed"] is False
    assert "pax-ai/skills/pax-or/SKILL.md" in result2["active_policy_changes"]


def test_backslash_paths_are_normalized():
    """Windows git output may use backslashes; gate must normalize."""
    result = gate.check_promotion_gate(
        changed_files=[r"mcp-server\bookmap_mcp\pax_weights.json"],
        report_paths=[],
    )
    assert result["passed"] is False
    assert "mcp-server/bookmap_mcp/pax_weights.json" in result["active_policy_changes"]


# ---------------------------------------------------------------- malformed report

def test_malformed_report_fails_with_clear_reason(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[bad],
    )
    assert result["passed"] is False
    assert any("invalid_json" in r["reason"]
               for r in result["rejected_reports"])


def test_report_missing_top_level_key_fails(tmp_path):
    doc = _valid_report()
    del doc["n_paired"]
    rp = _write_report(tmp_path, "missing-n-paired", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert any("missing_required_key:n_paired" in r["reason"]
               for r in result["rejected_reports"])


def test_report_missing_split_sizes_key_fails(tmp_path):
    doc = _valid_report()
    doc["split_sizes"] = {"train": 72, "validation": 24}  # missing test
    rp = _write_report(tmp_path, "missing-test-split", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert any("split_sizes_missing:test" in r["reason"]
               for r in result["rejected_reports"])


def test_report_missing_candidate_splits_fails(tmp_path):
    """A candidate without train/validation/test under .splits is malformed."""
    bad_cand = _candidate()
    bad_cand["splits"] = {"train": _split_block(),
                           "validation": _split_block()}  # missing test
    doc = _valid_report(candidates=[bad_cand])
    rp = _write_report(tmp_path, "missing-cand-test", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert any("splits_missing_test" in r["reason"]
               for r in result["rejected_reports"])


def test_test_split_below_min_samples_fails(tmp_path):
    cand = _candidate(test_n=5)
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "thin-test-split", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
        min_samples=30,
    )
    assert result["passed"] is False
    assert any("test_n_samples_below_min" in r["reason"]
               for r in result["rejected_reports"])


# ---------------------------------------------------------------- valid replay

def test_replay_passed_report_passes(tmp_path):
    cand = _candidate(status="replay_passed", promoted_by="ci-bot")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "replay-good", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is True
    assert result["accepted_reports"] == [str(rp)]
    assert "replay_passed" in result["candidate_statuses"]


def test_paper_candidate_report_passes(tmp_path):
    cand = _candidate(status="paper_candidate", promoted_by="ci-bot")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "paper-candidate-good", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is True
    assert "paper_candidate" in result["candidate_statuses"]


def test_non_research_status_missing_promoted_by_fails(tmp_path):
    """A replay_passed/paper_candidate/.../active row without promoted_by
    is malformed -- the field is the actor-attribution anchor."""
    cand = _candidate(status="replay_passed", promoted_by=None)
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "missing-promoted-by", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert any("missing_promoted_by" in r["reason"]
               for r in result["rejected_reports"])


# ---------------------------------------------------------------- research-only

def test_research_only_alone_is_insufficient_for_active_policy_change(tmp_path):
    """research_only candidates count as evidence-of-zero. The change is not
    approvable until at least one candidate has been promoted past it."""
    cand = _candidate(status="research_only", promoted_by=None,
                      reason="did_not_improve_r")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "all-research", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert "research_only" in result["reason"]


# ---------------------------------------------------------------- active rules

def test_active_status_fails_by_default(tmp_path):
    cand = _candidate(status="active", promoted_by="op",
                      human_approved_by="op")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "active-default", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
        allow_active=False,
    )
    assert result["passed"] is False
    assert "active_status_requires_allow_active_flag" in result["reason"]


def test_active_status_passes_with_allow_active_and_human_approved(tmp_path):
    cand = _candidate(status="active", promoted_by="op",
                      human_approved_by="op@firm")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "active-approved", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
        allow_active=True,
    )
    assert result["passed"] is True
    assert "active" in result["candidate_statuses"]


def test_active_status_without_human_approved_fails_even_with_allow_active(
        tmp_path):
    """allow_active=True is necessary but not sufficient. The candidate
    must carry human_approved_by evidence."""
    cand = _candidate(status="active", promoted_by="op",
                      human_approved_by=None)
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "active-no-human", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
        allow_active=True,
    )
    assert result["passed"] is False
    assert "missing_human_approved" in result["reason"]


def test_unknown_promotion_status_fails(tmp_path):
    cand = _candidate(status="wishful_thinking", promoted_by="op")
    doc = _valid_report(candidates=[cand])
    rp = _write_report(tmp_path, "bad-status", doc)
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert result["passed"] is False
    assert any("invalid_status" in r["reason"]
               for r in result["rejected_reports"])


# ---------------------------------------------------------------- read-only

def test_gate_is_read_only_against_sentinel_files(tmp_path):
    """Running the gate must not touch any of the active-policy files."""
    sentinels = {
        tmp_path / "pax_ai_config.json": "ORIG_CONFIG",
        tmp_path / "pax_weights.json":   "ORIG_WEIGHTS",
        tmp_path / "prompts.py":         "ORIG_PROMPTS",
        tmp_path / "SKILL.md":           "ORIG_SKILL",
    }
    for p, body in sentinels.items():
        p.write_text(body, encoding="utf-8")

    rp = _write_report(tmp_path, "ok-report",
                       _valid_report(candidates=[
                           _candidate(status="replay_passed",
                                       promoted_by="ci-bot"),
                       ]))
    # Run the gate twice with various inputs.
    gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    gate.check_promotion_gate(
        changed_files=["pax-ai/pax_ai/server.py"],
        report_paths=[],
    )
    for p, body in sentinels.items():
        assert p.read_text(encoding="utf-8") == body


def test_gate_does_not_rewrite_replay_report(tmp_path):
    """The gate reads reports; it must never touch them."""
    rp = _write_report(tmp_path, "ok",
                       _valid_report(candidates=[
                           _candidate(status="replay_passed",
                                       promoted_by="ci-bot"),
                       ]))
    before = rp.read_bytes()
    mtime_before = rp.stat().st_mtime_ns
    gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[rp],
    )
    assert rp.read_bytes() == before
    assert rp.stat().st_mtime_ns == mtime_before


# ---------------------------------------------------------------- multi-report

def test_one_valid_report_among_many_is_sufficient(tmp_path):
    """If multiple reports are supplied, one structurally valid + promoted
    report unblocks the change. Other malformed reports are reported as
    rejected but don't fail the gate."""
    ok = _write_report(tmp_path, "ok-report",
                       _valid_report(candidates=[
                           _candidate(status="replay_passed",
                                       promoted_by="ci-bot"),
                       ]))
    bad = tmp_path / "bad.json"
    bad.write_text("garbage", encoding="utf-8")
    result = gate.check_promotion_gate(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        report_paths=[bad, ok],
    )
    assert result["passed"] is True
    assert str(ok) in result["accepted_reports"]
    assert any(r["path"] == str(bad) for r in result["rejected_reports"])


# ---------------------------------------------------------------- CLI smoke

def test_cli_runs_against_explicit_changed_files_and_reports(tmp_path, capsys):
    rp = _write_report(tmp_path, "ok",
                       _valid_report(candidates=[
                           _candidate(status="replay_passed",
                                       promoted_by="ci-bot"),
                       ]))
    rc = gate.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
        "--report", str(rp),
        "--min-samples", "30",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["passed"] is True


def test_cli_returns_nonzero_when_gate_fails(tmp_path):
    """No reports + active-policy change must produce a non-zero exit."""
    rc = gate.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
    ])
    assert rc != 0


# ---------------------------------------------------------------- git-base fail-closed

def test_git_diff_helper_returns_files_and_none_on_success(monkeypatch):
    """Successful git invocation must return (files, None)."""
    import subprocess as _sp

    class _Completed:
        returncode = 0
        stdout = "mcp-server/bookmap_mcp/pax_weights.json\npax-ai/pax_ai/prompts.py\n"
        stderr = ""

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Completed())
    files, err = gate._git_diff_name_only("origin/main")
    assert err is None
    assert "mcp-server/bookmap_mcp/pax_weights.json" in files
    assert "pax-ai/pax_ai/prompts.py" in files


def test_git_diff_helper_returns_error_on_nonzero_exit(monkeypatch):
    """When git returns a non-zero exit code (e.g. unknown ref) the helper
    MUST surface the failure rather than masking it as 'no diff'."""
    import subprocess as _sp

    class _Completed:
        returncode = 128
        stdout = ""
        stderr = "fatal: bad revision 'refs/does-not-exist'\n"

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Completed())
    files, err = gate._git_diff_name_only("refs/does-not-exist")
    assert files == []
    assert err is not None
    assert err.startswith("git_diff_failed")


def test_git_diff_helper_returns_error_when_git_not_installed(monkeypatch):
    """If the `git` binary is missing entirely, fail closed."""
    import subprocess as _sp

    def _raise(*a, **k):
        raise FileNotFoundError("[WinError 2] git not on PATH")

    monkeypatch.setattr(_sp, "run", _raise)
    files, err = gate._git_diff_name_only("origin/main")
    assert files == []
    assert err is not None
    assert err.startswith("git_diff_failed")


def test_cli_fails_closed_when_git_base_resolution_fails(monkeypatch, capsys):
    """CLI must NOT silently report 'no_active_policy_change' when git diff
    discovery fails. It must:
       - exit non-zero
       - emit JSON with passed=false and reason starting 'git_diff_failed'
       - leave all list fields empty (no spurious accepted/rejected reports)
    """
    monkeypatch.setattr(gate, "_git_diff_name_only",
                         lambda *_a, **_kw: ([], "git_diff_failed:fake-error"))
    rc = gate.main(["--git-base", "refs/does-not-exist"])
    assert rc != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["reason"].startswith("git_diff_failed")
    assert payload["active_policy_changes"] == []
    assert payload["accepted_reports"] == []
    assert payload["rejected_reports"] == []
    assert payload["candidate_statuses"] == []


def test_cli_fails_closed_with_real_invalid_git_base(tmp_path, capsys):
    """End-to-end: a genuinely invalid ref must produce a fail-closed exit.

    Skipped when `git` is not on PATH (some CI images strip it). The
    monkeypatched test above is the deterministic guard; this one is the
    integration sanity-check the audit asked for.
    """
    import shutil
    if shutil.which("git") is None:
        pytest.skip("git binary not available")
    rc = gate.main(["--git-base", "refs/__definitely_does_not_exist__"])
    assert rc != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert payload["reason"].startswith("git_diff_failed")


def test_cli_explicit_changed_file_unaffected_by_git_fix(tmp_path, capsys):
    """The explicit --changed-file path (no --git-base) must keep working
    exactly as before the fix."""
    rp = _write_report(tmp_path, "ok",
                       _valid_report(candidates=[
                           _candidate(status="replay_passed",
                                       promoted_by="ci-bot"),
                       ]))
    rc = gate.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
        "--report", str(rp),
    ])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert "mcp-server/bookmap_mcp/pax_weights.json" in payload["active_policy_changes"]


def test_pure_check_promotion_gate_result_keys_unchanged():
    """The pure function's result shape MUST NOT regress after the CLI fix."""
    result = gate.check_promotion_gate(
        changed_files=["README.md"],
        report_paths=[],
    )
    assert set(result.keys()) == {
        "passed", "reason", "active_policy_changes",
        "accepted_reports", "rejected_reports", "candidate_statuses",
    }
    assert result["passed"] is True
    assert result["reason"] == "no_active_policy_change"
