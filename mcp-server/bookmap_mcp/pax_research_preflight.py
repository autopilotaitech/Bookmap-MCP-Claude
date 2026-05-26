"""Phase 9: single-command research preflight.

Wraps two existing safety checks into one operator-/CI-grade entry
point:

  - Phase 3 ``policy_promotion_gate.check_promotion_gate`` -- refuses
    to wave an active-policy change past without a structurally valid
    replay report and named promotion evidence.
  - Phase 5/8 ``pax_turn_audit.render_turn_audit_summary`` -- renders
    the operator-readable daily summary from an already-built
    turn-audit JSON.

The preflight is a thin orchestrator with one extra guarantee on top
of those two pieces:

  - **Stricter than the gate alone** on replay paths: every supplied
    ``--replay-report`` PATH must exist and parse as a structurally valid
    replay report. The gate alone would silently accept "no reports +
    non-active diff"; the preflight refuses missing or malformed reports,
    so an operator who typoed a path in CI does not get a false green.
  - Read-only against every input: the turn-audit JSON is opened with
    ``Path.read_text`` only; the gate's read path is unchanged.
  - Never mutates any DB, report, prompt, weights, or config file.

This module does NOT define new promotion semantics. ``allow_active``
and ``min_samples`` are forwarded to the gate verbatim.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import policy_promotion_gate as _gate
from .pax_turn_audit import render_turn_audit_summary


SCHEMA_VERSION = 1


# ----------------------------------------------------------- pure entry

def run_research_preflight(*,
                            changed_files: Iterable[str],
                            replay_report_paths: Iterable[Path],
                            turn_audit_report_path: Optional[Path] = None,
                            allow_active: bool = False,
                            min_samples: int = 30) -> Dict[str, Any]:
    """Run the composite preflight and return a structured result.

    Result shape::

        {
            "passed":              bool,
            "reason":              str,
            "promotion_gate":      <full check_promotion_gate result>,
            "turn_audit_summary":  str | None,
            "errors":              list[str],
        }

    Pass rules (all must hold):
      1. Every supplied ``replay_report_paths`` entry exists on disk and
         validates as a replay report.
      2. ``check_promotion_gate(...)`` returns ``passed=True``.
      3. If ``turn_audit_report_path`` is supplied, the file exists,
         parses as JSON, and the root is a dict; the rendered summary
         is included in the result.

    Any failure mode populates ``errors`` and sets
    ``passed=False`` with the first error as ``reason``.
    Gate failures set ``reason="promotion_gate:<gate.reason>"``.
    """
    replay_list = [Path(p) for p in replay_report_paths]
    errors: List[str] = []

    for rp in replay_list:
        if not rp.exists():
            errors.append(f"replay_report_missing:{rp}")
            continue
        ok, reason, _doc = _gate._validate_replay_report(
            rp, min_samples=min_samples)
        if not ok:
            errors.append(f"replay_report_invalid:{rp}:{reason}")

    gate_result = _gate.check_promotion_gate(
        changed_files=list(changed_files),
        report_paths=replay_list,
        allow_active=allow_active,
        min_samples=min_samples,
    )

    turn_audit_summary: Optional[str] = None
    if turn_audit_report_path is not None:
        tap = Path(turn_audit_report_path)
        if not tap.exists():
            errors.append(f"turn_audit_report_missing:{tap}")
        else:
            try:
                body = tap.read_text(encoding="utf-8")
            except OSError as exc:
                errors.append(f"turn_audit_read_failed:{type(exc).__name__}")
            else:
                try:
                    report = json.loads(body)
                except json.JSONDecodeError as exc:
                    errors.append(f"turn_audit_invalid_json:{exc.msg}")
                else:
                    if not isinstance(report, dict):
                        errors.append("turn_audit_root_not_object")
                    else:
                        turn_audit_summary = render_turn_audit_summary(report)

    if errors:
        passed = False
        reason = errors[0]
    elif not gate_result["passed"]:
        passed = False
        reason = f"promotion_gate:{gate_result['reason']}"
    else:
        passed = True
        reason = "ok"

    return {
        "schema_version":     SCHEMA_VERSION,
        "passed":             passed,
        "reason":             reason,
        "promotion_gate":     gate_result,
        "turn_audit_summary": turn_audit_summary,
        "errors":             errors,
    }


# ------------------------------------------------------------------- CLI

def _emit_json(json_report_path: Optional[Path],
                payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, indent=2, sort_keys=True, default=str)
    if json_report_path is not None:
        json_report_path.parent.mkdir(parents=True, exist_ok=True)
        json_report_path.write_text(body, encoding="utf-8")
    else:
        sys.stdout.write(body + "\n")


def _fail_result(reason: str) -> Dict[str, Any]:
    return {
        "schema_version":     SCHEMA_VERSION,
        "passed":             False,
        "reason":             reason,
        "promotion_gate":     None,
        "turn_audit_summary": None,
        "errors":             [reason],
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_research_preflight",
        description=("Single-command preflight for research / promotion "
                      "work. Runs the Phase 3 promotion gate against the "
                      "supplied changed files + replay reports and, when "
                      "provided, renders the Phase 5/8 turn-audit summary."),
    )
    ap.add_argument("--changed-file", action="append", default=[],
                    help="Path of a changed file (repeatable). Use either "
                         "this OR --git-base (or both).")
    ap.add_argument("--git-base", default=None,
                    help="Resolve additional changed files via "
                         "`git diff --name-only <ref> HEAD`. Fails closed "
                         "on any discovery error.")
    ap.add_argument("--replay-report", action="append", default=[], type=Path,
                    help="Replay report JSON path (repeatable). Every path "
                         "MUST exist on disk; missing => fail-closed.")
    ap.add_argument("--turn-audit-report", type=Path, default=None,
                    help="Optional turn-audit JSON to summarize. Malformed "
                         "or missing => fail-closed.")
    ap.add_argument("--allow-active", action="store_true",
                    help="Forwarded to the gate; permits candidates with "
                         "promotion_status=active (still requires "
                         "human_approved_by).")
    ap.add_argument("--min-samples", type=int, default=30,
                    help="Forwarded to the gate (test-split candidate "
                         "n_samples floor).")
    ap.add_argument("--json-report", type=Path, default=None,
                    help="Optional path to write the result JSON. If "
                         "omitted the result is written to stdout.")
    ap.add_argument("--summary-report", type=Path, default=None,
                    help="Optional path to write the turn-audit text "
                         "summary. Only written when --turn-audit-report "
                         "is also supplied and parseable.")
    args = ap.parse_args(argv)

    if not args.changed_file and not args.git_base:
        result = _fail_result("no_changed_input_supplied")
        _emit_json(args.json_report, result)
        return 1

    changed: List[str] = list(args.changed_file)
    if args.git_base:
        files, err = _gate._git_diff_name_only(args.git_base)
        if err is not None:
            result = _fail_result(err)
            _emit_json(args.json_report, result)
            return 1
        changed.extend(files)

    result = run_research_preflight(
        changed_files=changed,
        replay_report_paths=args.replay_report,
        turn_audit_report_path=args.turn_audit_report,
        allow_active=args.allow_active,
        min_samples=args.min_samples,
    )
    _emit_json(args.json_report, result)

    # Write the turn-audit summary out only when both inputs are present
    # AND the audit JSON actually rendered (skip on parse failures).
    if (args.turn_audit_report is not None
            and args.summary_report is not None
            and result.get("turn_audit_summary") is not None):
        args.summary_report.parent.mkdir(parents=True, exist_ok=True)
        args.summary_report.write_text(result["turn_audit_summary"],
                                        encoding="utf-8")

    return 0 if result["passed"] else 1


__all__ = [
    "SCHEMA_VERSION",
    "main",
    "run_research_preflight",
]


if __name__ == "__main__":
    raise SystemExit(main())
