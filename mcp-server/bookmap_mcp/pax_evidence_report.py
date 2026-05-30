"""Evidence-quality reporting for PAX SIM logs (STAGE 1-2).

Answers one operator question honestly: *how much usable evidence exists, and
is it good enough to start tuning?* It does NOT invent profitability and never
unlocks live trading.

Thin layer over existing modules -- nothing is re-derived:
- ``pax_session_report.compute_replay_readiness`` -> replay coverage.
- ``pax_promotion_report.build_promotion_report`` -> per-setup status / candidate
  count (which itself reuses ``pax_eval_state.setup_eligibility``).

Evidence grades (ladder, worst -> best):
- ``no_data``             -- no agent records AND no scorecard.
- ``logging_only``        -- records exist but replay_input coverage too low and
  no outcomes.
- ``replayable``          -- replay_input coverage sufficient, but no
  outcomes/scorecard.
- ``outcome_linked``      -- a scorecard/outcomes exist (decisions linked to R).
- ``promotion_candidate`` -- outcome_linked AND >= 1 candidate setup from the
  existing promotion report.

``candidate`` is NOT ``validated``: promotion past candidate still requires the
human + replay + paper-pass gate. live trading stays hard-blocked.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import pax_promotion_report
from .pax_session_report import compute_replay_readiness

# Replay coverage (% of records carrying replay_input) needed to call the logs
# "replayable". Below this, decisions cannot be reliably re-derived.
REPLAY_COVERAGE_FLOOR = 50.0

EVIDENCE_GRADES = ("no_data", "logging_only", "replayable", "outcome_linked",
                   "promotion_candidate")


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# STAGE 2 — setup evidence table                                              #
# --------------------------------------------------------------------------- #

def build_setup_evidence_table(promotion: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Per-setup evidence rows for operator tuning, derived from the promotion
    report. Honest about missing data (avg_r/win_rate) -- never auto-validates."""
    promotion = promotion or {}
    min_samples = int(promotion.get("min_samples") or 30)
    rows: List[Dict[str, Any]] = []
    for r in promotion.get("setups") or []:
        if not isinstance(r, dict):
            continue
        n = int(_num(r.get("n")) or 0)
        avg_r = _num(r.get("avg_r"))
        win_rate = _num(r.get("win_rate"))
        net_r = _num(r.get("net_r"))
        pstatus = r.get("promotion_status")
        reasons = list(r.get("reasons") or [])

        missing: List[str] = []
        if avg_r is None:
            missing.append("avg_r")
        if win_rate is None:
            missing.append("win_rate")

        if n < min_samples:
            estatus, action = "insufficient", "collect_more_data"
        elif avg_r is None:
            estatus, action = "insufficient", "review_manually"
            reasons.append("avg_r unavailable; cannot judge expectancy honestly")
        elif avg_r <= 0:
            estatus, action = "blocked", "block_or_throttle"
        elif pstatus == "candidate":
            estatus, action = "candidate", "candidate_for_paper_focus"
        else:
            estatus, action = "exploratory", "keep_observing"

        rows.append({
            "setup": r.get("setup"),
            "n": n,
            "avg_r": avg_r,
            "net_r": net_r,
            "win_rate": win_rate,
            "promotion_status": pstatus,
            "evidence_status": estatus,
            "missing_fields": missing,
            "reasons": reasons,
            "recommended_action": action,
            "live_blocked": True,
        })
    rows.sort(key=lambda x: str(x.get("setup")))
    return rows


# --------------------------------------------------------------------------- #
# STAGE 1 — evidence grading                                                  #
# --------------------------------------------------------------------------- #

def grade_evidence(replay_readiness: Dict[str, Any],
                   promotion: Dict[str, Any]
                   ) -> Tuple[str, List[str], List[str], List[str]]:
    """Pure grade from replay coverage + promotion summary.
    Returns (grade, blockers, warnings, next_required_data)."""
    rr = replay_readiness or {}
    promo = promotion or {}
    records = int(rr.get("total_records") or 0)
    pct = _num(rr.get("replay_input_pct")) or 0.0
    has_scorecard = bool(promo.get("has_outcome_data"))
    candidate = int(promo.get("candidate_count") or 0)
    replay_ok = records > 0 and pct >= REPLAY_COVERAGE_FLOOR

    blockers: List[str] = []
    warnings: List[str] = []
    next_data: List[str] = []

    if records == 0:
        blockers.append("no_agent_records")
        next_data.append("run PAX (paxi.bat start) to generate replay-grade "
                         "agent-loop heartbeats")
    elif not replay_ok:
        warnings.append(f"low_replay_input_coverage ({pct}% < "
                        f"{REPLAY_COVERAGE_FLOOR}%)")
        next_data.append("accumulate replay_input heartbeats (relaunch on the "
                         "current build; pre-replay_input lines are summarized "
                         "only)")
    if not has_scorecard:
        blockers.append("no_scorecard_or_outcomes")
        next_data.append("generate SIM outcomes: run armed SIM so "
                         "pax_trade_learning links fills into scorecard.json")
    elif candidate == 0:
        warnings.append("no_candidate_setups_yet")
        next_data.append("accumulate samples to reach candidate gates "
                         "(n>=min_samples, avgR>=candidate_avg_r, "
                         "netR>=candidate_net_r)")

    # STRICT LADDER. outcome_linked / promotion_candidate require BOTH a
    # scorecard AND replay-grade logs -- a scorecard alone cannot lift the grade
    # past logging_only, because without replayable logs the decisions behind
    # those outcomes cannot be audited. This is the honest semantics.
    if records == 0 and not has_scorecard:
        grade = "no_data"
    elif replay_ok and has_scorecard:
        grade = "promotion_candidate" if candidate >= 1 else "outcome_linked"
    elif replay_ok:
        grade = "replayable"
    else:
        grade = "logging_only"
        if has_scorecard:
            # outcomes exist but the logs are not replay-grade -> cannot reach
            # outcome_linked. Surface this explicitly so it is not silent.
            blockers.append("scorecard_present_but_logs_not_replayable")
            next_data.append("make logs replay-grade (relaunch on the current "
                             "build so heartbeats embed replay_input) before the "
                             "scorecard can lift the grade to outcome_linked")

    return grade, blockers, warnings, next_data


# --------------------------------------------------------------------------- #
# Compact summary (cheap; for /api/health + session report)                   #
# --------------------------------------------------------------------------- #

def compute_evidence_summary(feed: List[Dict[str, Any]],
                             scorecard: Optional[Dict[str, Any]],
                             *,
                             thresholds: Optional[Dict[str, Any]] = None,
                             now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Cheap evidence summary (no replay run) for embedding in health/session."""
    rr = compute_replay_readiness(feed)
    promo = pax_promotion_report.build_promotion_report(
        scorecard, thresholds=thresholds, now_ms=now_ms)
    grade, blockers, _warnings, next_data = grade_evidence(rr, promo)
    return {
        "evidence_grade": grade,
        "replay_input_pct": rr.get("replay_input_pct"),
        "candidate_setup_count": promo.get("candidate_count", 0),
        "evidence_blockers": len(blockers),
        "next_required_data": next_data,
        "live_blocked": True,
    }


# --------------------------------------------------------------------------- #
# STAGE 1 — full evidence report (pure given loaded inputs)                    #
# --------------------------------------------------------------------------- #

def build_evidence_report(*,
                          feed: List[Dict[str, Any]],
                          scorecard: Optional[Dict[str, Any]],
                          session_summary: Optional[Dict[str, Any]] = None,
                          replay_summary: Optional[Dict[str, Any]] = None,
                          input_paths: Optional[Dict[str, Any]] = None,
                          thresholds: Optional[Dict[str, Any]] = None,
                          now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Assemble the evidence report from already-loaded inputs. Pure;
    deterministic except ``generated_ms`` (pin with ``now_ms``)."""
    rr = compute_replay_readiness(feed)
    promo = pax_promotion_report.build_promotion_report(
        scorecard, thresholds=thresholds, now_ms=now_ms)
    table = build_setup_evidence_table(promo)
    grade, blockers, warnings, next_data = grade_evidence(rr, promo)

    scorecard_summary = {
        "has_outcome_data": promo.get("has_outcome_data", False),
        "setup_count": len(promo.get("setups") or []),
        "candidate_count": promo.get("candidate_count", 0),
        "counts": promo.get("counts", {}),
        "min_samples": promo.get("min_samples"),
    }

    return {
        "generated_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
        "input_paths": input_paths or {},
        "replay_readiness": rr,
        "replay_summary": replay_summary,
        "session_summary": session_summary,
        "scorecard_summary": scorecard_summary,
        "setup_evidence": table,
        "evidence_grade": grade,
        "blockers": blockers,
        "warnings": warnings,
        "next_required_data": next_data,
        "live_blocked": True,
        "limitations": [
            "SIM-only; no live-market edge claim. live trading hard-blocked.",
            "'candidate' is NOT 'validated': promotion past candidate needs a "
            "human + replay + paper-pass gate.",
            "grades describe evidence QUANTITY/quality, not profitability.",
        ],
    }


# --------------------------------------------------------------------------- #
# I/O wrapper + CLI                                                           #
# --------------------------------------------------------------------------- #

def _load_feed(agent_log: Path, max_lines: int = 5000) -> List[Dict[str, Any]]:
    """Read the tail of an agent-loop JSONL into a list of records (bounded).
    Reuses pax_sim_tools.tail_lines so a huge log is not slurped whole."""
    from .pax_sim_tools import tail_lines
    out: List[Dict[str, Any]] = []
    for ln in tail_lines(Path(agent_log), max_lines=max_lines):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_json(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def _session_summary_from(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(report, dict):
        return None
    return {
        "generated_ms": report.get("generated_ms"),
        "decisions_total": (report.get("decisions") or {}).get("total"),
        "executions": (report.get("executions") or {}).get("count"),
        "risk_halts": (report.get("risk_halts") or {}).get("count"),
        "pnl": report.get("pnl"),
        "replay_readiness": report.get("replay_readiness"),
    }


def gather_evidence_report(*,
                           agent_log: Path,
                           scorecard_path: Optional[Path] = None,
                           session_report_path: Optional[Path] = None,
                           replay: bool = False,
                           out_path: Optional[Path] = None,
                           now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Load inputs from disk, build the evidence report, optionally write it.
    Missing files degrade to safe no_data -- never raises for a missing file.
    A full replay pass runs ONLY when ``replay=True``."""
    feed = _load_feed(agent_log) if Path(agent_log).exists() else []
    scorecard = _load_json(scorecard_path)
    session_report = _load_json(session_report_path)
    session_summary = _session_summary_from(session_report)

    replay_summary = None
    if replay and Path(agent_log).exists():
        try:
            from . import pax_agent_replay
            full = pax_agent_replay.replay_file(Path(agent_log))
            replay_summary = {k: full.get(k) for k in (
                "event_count", "usable_snapshot_count", "decisions_generated",
                "replay_input_count", "replay_input_version_counts",
                "op_gate_replayed_count", "op_gate_missing_fields_count",
                "divergence_count", "risk_halt_divergence_count")}
        except Exception as exc:
            replay_summary = {"error": f"{type(exc).__name__}: {exc}"[:200]}

    input_paths = {
        "agent_log": str(agent_log),
        "scorecard": str(scorecard_path) if scorecard_path else None,
        "session_report": str(session_report_path) if session_report_path else None,
        "replay_ran": bool(replay),
    }
    report = build_evidence_report(
        feed=feed, scorecard=scorecard, session_summary=session_summary,
        replay_summary=replay_summary, input_paths=input_paths, now_ms=now_ms)

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True,
                                       default=str), encoding="utf-8")
    return report


def build_parser() -> argparse.ArgumentParser:
    learn = Path(r"D:\BookmapLogs\pax-agent")
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_evidence_report",
        description="Evidence-quality report for PAX SIM logs (how much usable "
                    "evidence exists; SIM-only, no live-edge claim).")
    p.add_argument("--agent-log", type=Path, default=learn / "agent-loop.jsonl")
    p.add_argument("--scorecard", type=Path, default=learn / "scorecard.json")
    p.add_argument("--session-report", type=Path,
                   default=learn / "session-report.json")
    p.add_argument("--replay", action="store_true",
                   help="Also run pax_agent_replay (slower) and embed a summary.")
    p.add_argument("--out", type=Path, default=learn / "evidence-report.json")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = gather_evidence_report(
        agent_log=args.agent_log,
        scorecard_path=args.scorecard if Path(args.scorecard).exists() else None,
        session_report_path=(args.session_report
                             if Path(args.session_report).exists() else None),
        replay=args.replay, out_path=args.out)
    print(f"evidence report written: {args.out} (grade="
          f"{report['evidence_grade']})")
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
