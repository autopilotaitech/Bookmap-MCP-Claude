"""Post-session data-quality report for PAX SIM (read-only).

After a SIM session, answer one question honestly: *is the data usable for
tuning, or only for review, or not at all?* Thin layer over existing modules --
nothing is re-derived and no linkage is invented.

Reuses:
- ``pax_session_report.compute_replay_readiness`` (replay coverage),
- ``pax_promotion_report.build_promotion_report`` (setup/candidate counts),
- ``pax_evidence_report.REPLAY_COVERAGE_FLOOR`` (replayable threshold),
- ``pax_trade_learning.link_agent_log`` (fills linked to a setup),
- ``OverviewQueries.recent_fills`` / ``sim_broker_preflight`` (broker readability).

Verdict ladder (worst -> best):
- ``no_data``                     -- no heartbeats AND no scorecard.
- ``unusable``                    -- heartbeats exist but neither replay-grade
  logs nor a scorecard (nothing to learn from).
- ``usable_for_review``           -- replay-grade logs OR a scorecard, but not
  enough to start tuning a candidate.
- ``usable_for_tuning_candidate`` -- replay-grade logs AND scorecard AND
  executions/linked fills AND >= 1 candidate setup AND a readable broker.

Honest caps:
- A scorecard with non-replayable logs cannot exceed ``usable_for_review``.
- No executions/fills cannot reach ``usable_for_tuning_candidate``.
SIM-only; live stays hard-blocked; no market-edge claim.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import pax_promotion_report
from .pax_session_report import compute_replay_readiness
from .pax_evidence_report import REPLAY_COVERAGE_FLOOR

_DEF_LEARN = Path(r"D:\BookmapLogs\pax-agent")
_DEF_JOURNAL = Path(r"D:\BookmapLogs\pax-journal.db")
_DEF_SIM_DB = Path(r"D:\BookmapLogs\pax-daemon-trades.db")

_REQUIRED_ARTIFACTS = ("agent-loop.jsonl", "scorecard.json", "session-report.json")
_STALE_CODES = ("stale_heartbeat", "stale_market_data")


def _load_feed(agent_log: Path, max_lines: int = 5000) -> List[Dict[str, Any]]:
    from .pax_sim_tools import tail_lines
    out: List[Dict[str, Any]] = []
    if not Path(agent_log).exists():
        return out
    for ln in tail_lines(Path(agent_log), max_lines=max_lines):
        try:
            obj = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None


def build_data_quality(*,
                       feed: List[Dict[str, Any]],
                       scorecard: Optional[Dict[str, Any]],
                       fills_linked: int,
                       entry_fills_observed: int,
                       broker_ok: bool,
                       missing_artifacts: List[str],
                       now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Pure: assemble the data-quality verdict from already-loaded inputs."""
    feed = feed or []
    rr = compute_replay_readiness(feed)
    promo = pax_promotion_report.build_promotion_report(scorecard, now_ms=now_ms)

    actions: Counter = Counter()
    halt_codes: Counter = Counter()
    executions = 0
    entry_intents = 0
    for rec in feed:
        if not isinstance(rec, dict):
            continue
        act = str(rec.get("action") or "?")
        actions[act] += 1
        if act.startswith(("PLACE_", "ENTER_")):
            entry_intents += 1
        if rec.get("executed") and rec.get("order"):
            executions += 1
        code = rec.get("risk_halt_code") or rec.get("risk_halt")
        if code:
            halt_codes[str(code)] += 1

    total_heartbeats = sum(1 for r in feed
                           if isinstance(r, dict) and r.get("heartbeat") is True)
    replay_pct = rr.get("replay_input_pct") or 0.0
    replayable = total_heartbeats > 0 and replay_pct >= REPLAY_COVERAGE_FLOOR
    setup_count = len(promo.get("setups") or [])
    candidate_count = int(promo.get("candidate_count") or 0)
    has_scorecard = bool(promo.get("has_outcome_data"))
    has_exec = executions > 0 or fills_linked > 0
    stale_blocks = sum(halt_codes.get(c, 0) for c in _STALE_CODES)
    unlinked_fills = max(0, entry_fills_observed - fills_linked)

    required_actions: List[str] = []
    if missing_artifacts:
        required_actions.append("produce missing artifacts: "
                                + ", ".join(missing_artifacts))
    if not replayable:
        required_actions.append("make logs replay-grade (relaunch on the current "
                                "build so heartbeats embed replay_input)")
    if not has_scorecard:
        required_actions.append("run armed SIM so pax_trade_learning writes "
                                "scorecard.json")
    if not has_exec:
        required_actions.append("execute SIM trades (armed) so fills link to "
                                "setups")
    if not broker_ok:
        required_actions.append("repair/point to a readable SIM broker DB")

    # --- verdict (honest ladder) ---
    if total_heartbeats == 0 and not has_scorecard:
        verdict = "no_data"
    elif (replayable and has_scorecard and has_exec and candidate_count >= 1
            and broker_ok):
        verdict = "usable_for_tuning_candidate"
    elif replayable or has_scorecard:
        verdict = "usable_for_review"
    else:
        verdict = "unusable"

    return {
        "generated_ms": int(now_ms) if now_ms is not None else int(time.time() * 1000),
        "verdict": verdict,
        "total_heartbeats": total_heartbeats,
        "replay_input_coverage_pct": replay_pct,
        "replayable": replayable,
        "decision_counts": dict(sorted(actions.items())),
        "entry_intent_count": entry_intents,
        "execution_count": executions,
        "risk_halt_counts": dict(sorted(halt_codes.items())),
        "stale_data_block_count": stale_blocks,
        "fills_linked_to_setup": fills_linked,
        "unlinked_fills": unlinked_fills,
        "entry_fills_observed": entry_fills_observed,
        "scorecard_setup_count": setup_count,
        "candidate_setup_count": candidate_count,
        "broker_readable": broker_ok,
        "missing_required_artifacts": missing_artifacts,
        "required_actions": required_actions,
        "live_blocked": True,
        "limitations": [
            "SIM-only; live trading hard-blocked; no live-market edge claim.",
            "linkage is read from pax_trade_learning; missing linkage is reported, "
            "never invented.",
            "entry_fills_observed is bounded by recent_fills (may undercount a "
            "very long session); unlinked_fills is derived from it.",
            "a scorecard without replay-grade logs cannot exceed usable_for_review.",
        ],
    }


def gather_data_quality(*,
                        journal: Path,
                        sim_db: Path,
                        learn_dir: Optional[Path] = None,
                        out_path: Optional[Path] = None,
                        now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Load inputs read-only, build the report, optionally write it. Never
    raises for missing files; never mutates the SIM/journal DBs."""
    from .overview_ui import OverviewQueries

    q = OverviewQueries(journal, sim_db_path=sim_db, learn_dir=learn_dir)
    learn = q.learn_dir
    agent_log = learn / "agent-loop.jsonl"
    feed = _load_feed(agent_log)
    scorecard = _load_json(learn / "scorecard.json")

    missing = [name for name in _REQUIRED_ARTIFACTS
               if not (learn / name).exists()]

    # broker readability (read-only preflight) + observed entry fills.
    sim_pf = _safe(q.sim_broker_preflight,
                   {"openable": False, "readable": False})
    broker_ok = bool(sim_pf.get("openable") and sim_pf.get("readable"))
    fills = _safe(lambda: q.recent_fills(limit=1000), [])
    entry_fills_observed = sum(1 for f in fills
                               if isinstance(f, dict)
                               and str(f.get("role")) == "ENTRY")

    # fills linked to a setup (honest; link_agent_log returns [] if either the
    # agent log or the SIM DB is missing -- it never creates files).
    fills_linked = 0
    try:
        from . import pax_trade_learning
        linked = pax_trade_learning.link_agent_log(agent_log=agent_log,
                                                   db_path=Path(sim_db))
        fills_linked = sum(1 for o in linked
                           if getattr(o, "setup_type", "UNKNOWN") != "UNKNOWN")
    except Exception:
        fills_linked = 0

    report = build_data_quality(
        feed=feed, scorecard=scorecard, fills_linked=fills_linked,
        entry_fills_observed=entry_fills_observed, broker_ok=broker_ok,
        missing_artifacts=missing, now_ms=now_ms)
    report["input_paths"] = {"journal": str(journal), "sim_db": str(sim_db),
                             "learn_dir": str(learn), "agent_log": str(agent_log)}

    if out_path is not None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True,
                                       default=str), encoding="utf-8")
    return report


def _safe(fn, default):
    try:
        return fn()
    except Exception:
        return default


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_data_quality",
        description="Post-session SIM data-quality report (read-only). Is the "
                    "data usable for tuning? SIM-only; live hard-blocked.")
    p.add_argument("--journal", type=Path, default=_DEF_JOURNAL)
    p.add_argument("--sim-db", type=Path, default=_DEF_SIM_DB)
    p.add_argument("--learn-dir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = gather_data_quality(journal=args.journal, sim_db=args.sim_db,
                                 learn_dir=args.learn_dir, out_path=args.out)
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        print(f"data-quality report written: {args.out} (verdict="
              f"{report['verdict']})", file=sys.stderr)
    else:
        print(body)
    return 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
