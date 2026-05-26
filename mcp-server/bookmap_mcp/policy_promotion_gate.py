"""Deterministic promotion gate for active-policy changes.

Given a list of changed files and a list of candidate replay-report paths,
the gate answers one question: *is there enough structural evidence to
allow this active-policy change?*

This module is a mechanical guard, not a judgment of merit:

  - It does NOT score or rerun the replay -- it only checks that a
    valid replay report exists and that the candidate row claiming the
    change has been promoted past ``research_only`` by a named actor.
  - It does NOT mutate any active-policy surface, any report, or any DB.
  - It does NOT touch the network. The optional CLI shells out to
    ``git diff --name-only`` for convenience but the pure function path
    needs no git at all.

The gate is intentionally additive to the existing research / replay /
calibration pipeline. Other modules continue to produce candidates and
reports; this module just refuses to wave them past when the discipline
hasn't been followed.

Active-policy surfaces (Phase 3):

  - ``mcp-server/bookmap_mcp/pax_weights.json``
  - ``pax-ai/pax_ai/prompts.py``
  - any path under ``pax-ai/skills/``
  - any path under ``skills/``

Promotion ladder (matches ``pax_policy_replay``):

  ``research_only -> replay_passed -> paper_candidate -> paper_passed ->
   human_approved -> active``

The replay module auto-assigns at most ``replay_passed`` or
``paper_candidate``. Every status beyond ``research_only`` must carry a
``promoted_by`` actor field in the report; ``active`` additionally
requires ``human_approved_by`` evidence AND the explicit
``allow_active=True`` flag.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------- surface set

ACTIVE_POLICY_EXACT: Tuple[str, ...] = (
    "mcp-server/bookmap_mcp/pax_weights.json",
    "pax-ai/pax_ai/prompts.py",
)

ACTIVE_POLICY_PREFIXES: Tuple[str, ...] = (
    "pax-ai/skills/",
    "skills/",
)


# ----------------------------------------------------------------- statuses

ALLOWED_STATUSES: Tuple[str, ...] = (
    "research_only",
    "replay_passed",
    "paper_candidate",
    "paper_passed",
    "human_approved",
    "active",
)

# Statuses that require a non-empty ``promoted_by`` actor on the candidate.
STATUSES_REQUIRING_PROMOTED_BY: Tuple[str, ...] = (
    "replay_passed",
    "paper_candidate",
    "paper_passed",
    "human_approved",
    "active",
)


# ------------------------------------------------------------- required keys

REQUIRED_REPORT_KEYS: Tuple[str, ...] = (
    "generated_ms",
    "n_forecasts",
    "n_paired",
    "split_sizes",
    "candidates",
)
REQUIRED_SPLIT_NAMES: Tuple[str, ...] = ("train", "validation", "test")
REQUIRED_CANDIDATE_KEYS: Tuple[str, ...] = (
    "promotion_status",
    "reason",
    "splits",
)
REQUIRED_SPLIT_SIDES: Tuple[str, ...] = ("current", "candidate")


# ---------------------------------------------------------------- predicates

def _normalize(path: str) -> str:
    return str(path).replace("\\", "/").lstrip("./")


def _is_active_policy(path: str) -> bool:
    p = _normalize(path)
    if p in ACTIVE_POLICY_EXACT:
        return True
    for prefix in ACTIVE_POLICY_PREFIXES:
        if p.startswith(prefix):
            return True
    return False


# ---------------------------------------------------------- report validation

def _validate_replay_report(path: Path,
                             *,
                             min_samples: int
                             ) -> Tuple[bool, str, Dict[str, Any]]:
    """Return (ok, reason, parsed_doc). Pure: no mutation of ``path``."""
    try:
        body = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return (False, f"read_error:{type(exc).__name__}", {})
    try:
        doc = json.loads(body)
    except json.JSONDecodeError as exc:
        return (False, f"invalid_json:{exc.msg}", {})
    if not isinstance(doc, dict):
        return (False, "report_must_be_object", {})

    for key in REQUIRED_REPORT_KEYS:
        if key not in doc:
            return (False, f"missing_required_key:{key}", doc)

    split_sizes = doc.get("split_sizes")
    if not isinstance(split_sizes, dict):
        return (False, "split_sizes_must_be_object", doc)
    for sk in REQUIRED_SPLIT_NAMES:
        if sk not in split_sizes:
            return (False, f"split_sizes_missing:{sk}", doc)

    candidates = doc.get("candidates")
    if not isinstance(candidates, list):
        return (False, "candidates_must_be_list", doc)

    for i, c in enumerate(candidates):
        if not isinstance(c, dict):
            return (False, f"candidate_{i}_not_object", doc)
        for key in REQUIRED_CANDIDATE_KEYS:
            if key not in c:
                return (False, f"candidate_{i}_missing_{key}", doc)
        status = c.get("promotion_status")
        if status not in ALLOWED_STATUSES:
            return (False, f"candidate_{i}_invalid_status:{status}", doc)
        if status in STATUSES_REQUIRING_PROMOTED_BY:
            promoted_by = c.get("promoted_by")
            if not (isinstance(promoted_by, str) and promoted_by.strip()):
                return (False,
                        f"candidate_{i}_status_{status}_missing_promoted_by",
                        doc)

        c_splits = c.get("splits")
        if not isinstance(c_splits, dict):
            return (False, f"candidate_{i}_splits_must_be_object", doc)
        for sk in REQUIRED_SPLIT_NAMES:
            if sk not in c_splits:
                return (False, f"candidate_{i}_splits_missing_{sk}", doc)
            split = c_splits[sk]
            if not isinstance(split, dict):
                return (False, f"candidate_{i}_split_{sk}_not_object", doc)
            for side in REQUIRED_SPLIT_SIDES:
                if side not in split:
                    return (False,
                            f"candidate_{i}_split_{sk}_missing_{side}", doc)
                metrics = split[side]
                if not isinstance(metrics, dict):
                    return (False,
                            f"candidate_{i}_split_{sk}_{side}_not_object", doc)
                if "n_samples" not in metrics:
                    return (False,
                            f"candidate_{i}_split_{sk}_{side}_missing_n_samples",
                            doc)

        n_test = int(c_splits["test"]["candidate"].get("n_samples") or 0)
        if n_test < int(min_samples):
            return (False,
                    f"candidate_{i}_test_n_samples_below_min:"
                    f"{n_test}<{min_samples}",
                    doc)

    return (True, "ok", doc)


# ------------------------------------------------------------------ the gate

def check_promotion_gate(*,
                          changed_files: Iterable[str],
                          report_paths: Iterable[Path],
                          allow_active: bool = False,
                          min_samples: int = 30) -> Dict[str, Any]:
    """Run the gate against the supplied file list and report list.

    Returns a dict that callers can serialize directly:

        {
            "passed": bool,
            "reason": str,
            "active_policy_changes": List[str],
            "accepted_reports":      List[str],
            "rejected_reports":      List[{"path": str, "reason": str}],
            "candidate_statuses":    List[str],
        }
    """
    changes = sorted({_normalize(p) for p in changed_files
                      if _is_active_policy(p)})

    if not changes:
        return _result(passed=True, reason="no_active_policy_change",
                       changes=changes)

    report_list = list(report_paths)
    if not report_list:
        return _result(
            passed=False,
            reason="active_policy_changed_without_replay_report",
            changes=changes,
        )

    accepted: List[str] = []
    rejected: List[Dict[str, str]] = []
    all_statuses: List[str] = []
    active_observed: List[Dict[str, Any]] = []

    for rp in report_list:
        rp_p = Path(rp)
        ok, reason, doc = _validate_replay_report(rp_p, min_samples=min_samples)
        if not ok:
            rejected.append({"path": str(rp_p), "reason": reason})
            continue
        accepted.append(str(rp_p))
        for c in (doc.get("candidates") or []):
            status = c.get("promotion_status")
            all_statuses.append(status)
            if status == "active":
                active_observed.append(c)

    if not accepted:
        return _result(
            passed=False,
            reason="no_valid_replay_report",
            changes=changes,
            accepted=accepted,
            rejected=rejected,
            statuses=all_statuses,
        )

    non_research = [s for s in all_statuses if s != "research_only"]
    if not non_research:
        return _result(
            passed=False,
            reason=("research_only_alone_is_insufficient_"
                    "for_active_policy_change"),
            changes=changes,
            accepted=accepted,
            rejected=rejected,
            statuses=all_statuses,
        )

    if active_observed:
        if not allow_active:
            return _result(
                passed=False,
                reason="active_status_requires_allow_active_flag",
                changes=changes,
                accepted=accepted,
                rejected=rejected,
                statuses=all_statuses,
            )
        for c in active_observed:
            evidence = c.get("human_approved_by")
            if not isinstance(evidence, str) or not evidence.strip():
                return _result(
                    passed=False,
                    reason="active_status_missing_human_approved_evidence",
                    changes=changes,
                    accepted=accepted,
                    rejected=rejected,
                    statuses=all_statuses,
                )

    return _result(
        passed=True,
        reason="ok",
        changes=changes,
        accepted=accepted,
        rejected=rejected,
        statuses=all_statuses,
    )


def _result(*,
             passed: bool,
             reason: str,
             changes: List[str],
             accepted: Optional[List[str]] = None,
             rejected: Optional[List[Dict[str, str]]] = None,
             statuses: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "passed":                passed,
        "reason":                reason,
        "active_policy_changes": list(changes),
        "accepted_reports":      list(accepted or []),
        "rejected_reports":      list(rejected or []),
        "candidate_statuses":    list(statuses or []),
    }


# ------------------------------------------------------------------------ CLI

def _git_diff_name_only(base_ref: str,
                         *,
                         cwd: Optional[Path] = None
                         ) -> Tuple[List[str], Optional[str]]:
    """Resolve changed files via ``git diff --name-only <base> HEAD``.

    Returns ``(files, error_reason)``. On success ``error_reason`` is None.
    On any failure (git missing, non-zero exit, OS error) the function
    returns ``([], "git_diff_failed:<detail>")`` so the CLI can fail closed
    rather than silently treating the diff as empty -- masking real
    active-policy changes as a passing no-op.
    """
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            cwd=str(cwd) if cwd is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as exc:
        return ([], f"git_diff_failed:{type(exc).__name__}:{exc}")
    if proc.returncode != 0:
        first_err = ""
        for line in (proc.stderr or "").splitlines():
            if line.strip():
                first_err = line.strip()
                break
        detail = first_err or f"exit_code={proc.returncode}"
        return ([], f"git_diff_failed:{detail}")
    files = [ln.strip() for ln in (proc.stdout or "").splitlines()
             if ln.strip()]
    return (files, None)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bookmap_mcp.policy_promotion_gate",
        description=("Block active-policy changes unless a structurally "
                      "valid replay report exists with non-research_only "
                      "promotion evidence."),
    )
    ap.add_argument("--changed-file", action="append", default=[],
                    help="Path of a changed file (repeatable)")
    ap.add_argument("--git-base", default=None,
                    help="Resolve changed files via "
                         "`git diff --name-only <base> HEAD`")
    ap.add_argument("--report", action="append", default=[], type=Path,
                    help="Path to a replay report JSON (repeatable)")
    ap.add_argument("--allow-active", action="store_true",
                    help="Permit candidates with promotion_status=active "
                         "(still requires human_approved_by)")
    ap.add_argument("--min-samples", type=int, default=30,
                    help="Minimum n_samples on the test-split candidate "
                         "policy for a report to count")
    args = ap.parse_args(argv)

    changed: List[str] = list(args.changed_file)
    if args.git_base:
        files, err = _git_diff_name_only(args.git_base)
        if err is not None:
            # Fail closed: do NOT fall back to "empty diff = no active
            # policy change". A promotion gate that cannot see the diff
            # MUST refuse, not wave it through.
            fail_result = _result(
                passed=False,
                reason=err,
                changes=[],
                accepted=[],
                rejected=[],
                statuses=[],
            )
            sys.stdout.write(json.dumps(fail_result, indent=2,
                                          sort_keys=True) + "\n")
            return 1
        changed.extend(files)

    result = check_promotion_gate(
        changed_files=changed,
        report_paths=args.report,
        allow_active=args.allow_active,
        min_samples=args.min_samples,
    )
    sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["passed"] else 1


__all__ = [
    "ACTIVE_POLICY_EXACT",
    "ACTIVE_POLICY_PREFIXES",
    "ALLOWED_STATUSES",
    "STATUSES_REQUIRING_PROMOTED_BY",
    "check_promotion_gate",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
