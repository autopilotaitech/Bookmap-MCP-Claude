"""Phase 9 research-preflight tests.

The preflight orchestrates two existing checks (Phase 3 promotion gate +
Phase 5/8 turn audit) into a single deterministic ``pass / fail`` answer
operators can run locally or in CI before touching active-policy
surfaces.

Contract under test:
  - Pure function path: explicit changed-file list, explicit replay
    reports, optional turn-audit report; returns a result dict with
    ``passed``, ``reason``, ``promotion_gate``, ``turn_audit_summary``,
    ``errors``.
  - Fail-closed on missing replay report files (stricter than the gate
    alone, which would silently pass a non-active diff with no reports).
  - Fail-closed on malformed turn-audit JSON.
  - Read-only against the turn-audit JSON (bytes + mtime stable).
  - CLI: fails closed when neither --changed-file nor --git-base is
    supplied; fails closed on git-base discovery failure; writes JSON
    result + optional summary report; exit code mirrors ``passed``.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from bookmap_mcp import pax_research_preflight as preflight
from bookmap_mcp import pax_turn_audit as audit


# ---------------------------------------------------------- replay fixtures

def _valid_replay_candidate(*, status="replay_passed",
                              promoted_by="ci-bot",
                              human_approved_by=None,
                              test_n=30):
    splits = {
        "train":      {"current": {"n_samples": 30},
                        "candidate": {"n_samples": 30}},
        "validation": {"current": {"n_samples": 30},
                        "candidate": {"n_samples": 30}},
        "test":       {"current": {"n_samples": 30},
                        "candidate": {"n_samples": test_n}},
    }
    c: Dict[str, Any] = {
        "promotion_status": status,
        "reason":           "ok",
        "splits":           splits,
    }
    if promoted_by is not None:
        c["promoted_by"] = promoted_by
    if human_approved_by is not None:
        c["human_approved_by"] = human_approved_by
    return c


def _valid_replay_report(**extras):
    doc: Dict[str, Any] = {
        "generated_ms": 1_700_000_000_000,
        "n_forecasts":  100,
        "n_paired":     100,
        "split_sizes":  {"train": 60, "validation": 20, "test": 20},
        "candidates":   [_valid_replay_candidate()],
    }
    doc.update(extras)
    return doc


def _write_replay(tmp_path: Path, name: str,
                    doc: Dict[str, Any]) -> Path:
    p = tmp_path / f"{name}.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


# ---------------------------------------------------- turn-audit fixture

def _ai_turn_row(*, id_=1, ts_ms=1_000, chat_run_id="A",
                  digest_sha256="d" * 64, prompt_sha256="p" * 64):
    return {
        "id": id_, "schema_version": 1, "ts_ms": ts_ms,
        "chat_run_id": chat_run_id, "deep": 0, "model": "claude-haiku-4-5",
        "router_primary": "pax-or",
        "user_text_raw": "ping", "user_text_normalized": "ping",
        "pax_text": "ok",
        "snapshot_alias": "NQM6.CME@RITHMIC",
        "snapshot_ts_ms": ts_ms, "snapshot_age_ms": 0,
        "snapshot_sha256": "s" * 64, "digest_sha256": digest_sha256,
        "exit_code": 0, "elapsed_ms": 5, "aborted": 0,
        "prompt_sha256": prompt_sha256,
        "prompt_version": "1.0.0",
        "model_release_id": "claude-haiku-4-5",
        "skill_bundle_sha256": "k" * 64,
        "prompt_archive_path": "",
    }


def _make_bus_db(path: Path, ai_turn_rows, trade_outcomes_rows) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
        CREATE TABLE ai_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          schema_version INTEGER NOT NULL, ts_ms INTEGER NOT NULL,
          chat_run_id TEXT NOT NULL, deep INTEGER NOT NULL,
          model TEXT NOT NULL, router_primary TEXT,
          user_text_raw TEXT NOT NULL, user_text_normalized TEXT NOT NULL,
          pax_text TEXT,
          snapshot_alias TEXT, snapshot_ts_ms INTEGER, snapshot_age_ms INTEGER,
          snapshot_sha256 TEXT NOT NULL, digest_sha256 TEXT NOT NULL,
          exit_code INTEGER, elapsed_ms INTEGER, aborted INTEGER NOT NULL,
          prompt_sha256 TEXT, prompt_version TEXT, model_release_id TEXT,
          skill_bundle_sha256 TEXT, prompt_archive_path TEXT
        );
        CREATE TABLE trade_outcomes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ai_turn_id INTEGER NOT NULL, verdict TEXT,
          realized_r_at_t60s REAL, realized_r_at_t180s REAL,
          realized_r_at_t300s REAL, realized_r_at_t900s REAL,
          invalidated INTEGER, invalidation_reason TEXT, label_method TEXT
        );
        """)
        for r in ai_turn_rows:
            cols = list(r.keys())
            placeholders = ",".join("?" * len(cols))
            conn.execute(
                f"INSERT INTO ai_turns ({','.join(cols)}) VALUES ({placeholders})",
                tuple(r[c] for c in cols))
        for r in trade_outcomes_rows:
            cols = list(r.keys())
            placeholders = ",".join("?" * len(cols))
            conn.execute(
                f"INSERT INTO trade_outcomes ({','.join(cols)}) VALUES ({placeholders})",
                tuple(r[c] for c in cols))
        conn.commit()
    finally:
        conn.close()


def _write_real_turn_audit(tmp_path: Path) -> Path:
    """Build a minimal turn-audit JSON by running the real audit code
    against a tmp bus DB. Returns the JSON path."""
    bus = tmp_path / "bus-for-preflight.db"
    _make_bus_db(bus,
                 ai_turn_rows=[_ai_turn_row(id_=1, ts_ms=500)],
                 trade_outcomes_rows=[])
    audit_path = tmp_path / "turn-audit.json"
    audit.main([
        "--bus-db", str(bus),
        "--start-ms", "0", "--end-ms", "1000",
        "--report", str(audit_path),
    ])
    return audit_path


# ----------------------------------------------------------- pure function

def test_pure_no_changed_files_passes():
    """Empty changed-file list is a valid input (nothing to gate). The
    pure function passes and the gate reports no_active_policy_change."""
    r = preflight.run_research_preflight(
        changed_files=[], replay_report_paths=[])
    assert r["passed"] is True
    assert r["promotion_gate"]["passed"] is True
    assert r["promotion_gate"]["reason"] == "no_active_policy_change"
    assert r["turn_audit_summary"] is None
    assert r["errors"] == []


def test_non_active_changed_file_passes_with_no_replay_report():
    r = preflight.run_research_preflight(
        changed_files=["mcp-server/bookmap_mcp/pax_calibration.py"],
        replay_report_paths=[])
    assert r["passed"] is True
    assert r["promotion_gate"]["reason"] == "no_active_policy_change"


def test_active_policy_change_without_replay_report_fails():
    r = preflight.run_research_preflight(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        replay_report_paths=[])
    assert r["passed"] is False
    assert "promotion_gate" in r["reason"]
    assert r["promotion_gate"]["passed"] is False


def test_active_policy_change_passes_with_valid_replay(tmp_path):
    rp = _write_replay(tmp_path, "ok", _valid_replay_report())
    r = preflight.run_research_preflight(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        replay_report_paths=[rp])
    assert r["passed"] is True, r
    assert r["promotion_gate"]["passed"] is True


def test_missing_replay_report_path_fails_closed_even_for_non_active_diff(
        tmp_path):
    """The preflight is stricter than the gate alone: a supplied replay
    path that does not exist on disk MUST fail-close, even when the
    diff carries no active-policy change. Otherwise CI could silently
    accept a 'replay attached' workflow where the path was a typo."""
    r = preflight.run_research_preflight(
        changed_files=["README.md"],   # not active policy
        replay_report_paths=[tmp_path / "ghost.json"])
    assert r["passed"] is False
    assert r["reason"].startswith("replay_report_missing")
    assert any("replay_report_missing" in e for e in r["errors"])


def test_malformed_replay_report_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    r = preflight.run_research_preflight(
        changed_files=["mcp-server/bookmap_mcp/pax_weights.json"],
        replay_report_paths=[bad])
    # The gate marks it as rejected; preflight fails via the gate.
    assert r["passed"] is False
    assert r["promotion_gate"]["passed"] is False
    assert any("invalid_json" in rep["reason"]
               for rep in r["promotion_gate"]["rejected_reports"])


def test_malformed_replay_report_fails_closed_even_for_non_active_diff(
        tmp_path):
    """The gate returns early on no-active-policy changes, so the
    preflight itself must validate any supplied replay report before a
    non-active diff can pass."""
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    r = preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[bad])
    assert r["passed"] is False
    assert r["reason"].startswith("replay_report_invalid")
    assert "invalid_json" in r["reason"]


# --------------------------------------------------------- turn-audit

def test_turn_audit_summary_included_when_supplied(tmp_path):
    audit_json = _write_real_turn_audit(tmp_path)
    r = preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[],
        turn_audit_report_path=audit_json)
    assert r["passed"] is True
    assert r["turn_audit_summary"] is not None
    assert "n_ai_turns" in r["turn_audit_summary"]


def test_malformed_turn_audit_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json", encoding="utf-8")
    r = preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[],
        turn_audit_report_path=bad)
    assert r["passed"] is False
    assert "turn_audit_invalid_json" in r["reason"]


def test_missing_turn_audit_path_fails_closed(tmp_path):
    r = preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[],
        turn_audit_report_path=tmp_path / "ghost.json")
    assert r["passed"] is False
    assert r["reason"].startswith("turn_audit_report_missing")


def test_turn_audit_non_object_root_fails_closed(tmp_path):
    p = tmp_path / "list.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    r = preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[],
        turn_audit_report_path=p)
    assert r["passed"] is False
    assert r["reason"] == "turn_audit_root_not_object"


def test_turn_audit_read_preserves_bytes_and_mtime(tmp_path):
    audit_json = _write_real_turn_audit(tmp_path)
    before_body  = audit_json.read_bytes()
    before_mtime = audit_json.stat().st_mtime_ns
    preflight.run_research_preflight(
        changed_files=["README.md"],
        replay_report_paths=[],
        turn_audit_report_path=audit_json)
    assert audit_json.read_bytes()       == before_body
    assert audit_json.stat().st_mtime_ns == before_mtime


# ------------------------------------------------------------------- CLI

def test_cli_happy_path_changed_file_and_replay(tmp_path, capsys):
    rp = _write_replay(tmp_path, "ok", _valid_replay_report())
    rc = preflight.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
        "--replay-report", str(rp),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["passed"] is True


def test_cli_neither_changed_file_nor_git_base_fails_closed(capsys):
    rc = preflight.main([])
    assert rc != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["passed"] is False
    assert "no_changed" in payload["reason"]


def test_cli_git_base_failure_fails_closed(monkeypatch, capsys):
    monkeypatch.setattr(
        preflight._gate, "_git_diff_name_only",
        lambda *_a, **_kw: ([], "git_diff_failed:fake-error"))
    rc = preflight.main(["--git-base", "refs/__nothing__"])
    assert rc != 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["passed"] is False
    assert payload["reason"].startswith("git_diff_failed")


def test_cli_git_base_with_no_changes_passes(monkeypatch, capsys):
    """git-base discovery succeeded but found 0 changed files -> the
    preflight passes (nothing to gate)."""
    monkeypatch.setattr(
        preflight._gate, "_git_diff_name_only",
        lambda *_a, **_kw: ([], None))
    rc = preflight.main(["--git-base", "origin/main"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True


def test_cli_writes_json_report(tmp_path):
    out_path = tmp_path / "out.json"
    rc = preflight.main([
        "--changed-file", "README.md",
        "--json-report", str(out_path),
    ])
    assert rc == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["passed"] is True


def test_cli_writes_summary_report_when_audit_supplied(tmp_path):
    audit_json = _write_real_turn_audit(tmp_path)
    summary_path = tmp_path / "summary.txt"
    rc = preflight.main([
        "--changed-file", "README.md",
        "--turn-audit-report", str(audit_json),
        "--summary-report", str(summary_path),
    ])
    assert rc == 0
    assert summary_path.exists()
    body = summary_path.read_text(encoding="utf-8")
    assert "n_ai_turns" in body


def test_cli_summary_report_not_written_without_audit(tmp_path):
    """--summary-report without --turn-audit-report is a no-op (the
    audit summary is the only thing the summary path would carry)."""
    summary_path = tmp_path / "summary.txt"
    rc = preflight.main([
        "--changed-file", "README.md",
        "--summary-report", str(summary_path),
    ])
    assert rc == 0
    assert not summary_path.exists()


def test_cli_active_status_in_replay_default_blocks(tmp_path, capsys):
    """An 'active' candidate in the replay report must be blocked by
    default, mirroring the Phase 3 gate's allow_active=False contract."""
    cand = _valid_replay_candidate(status="active", promoted_by="op",
                                     human_approved_by="op@firm")
    rp = _write_replay(tmp_path, "active", _valid_replay_report(
        candidates=[cand]))
    rc = preflight.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
        "--replay-report", str(rp),
    ])
    assert rc != 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is False
    assert "active_status_requires_allow_active_flag" in payload["promotion_gate"]["reason"]


def test_cli_allow_active_unlocks_active_with_human_approved(tmp_path):
    cand = _valid_replay_candidate(status="active", promoted_by="op",
                                     human_approved_by="op@firm")
    rp = _write_replay(tmp_path, "active-approved",
                       _valid_replay_report(candidates=[cand]))
    rc = preflight.main([
        "--changed-file", "mcp-server/bookmap_mcp/pax_weights.json",
        "--replay-report", str(rp),
        "--allow-active",
    ])
    assert rc == 0
