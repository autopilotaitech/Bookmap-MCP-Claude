"""PAX SIM acceptance / doctor CLI (read-only).

One JSON verdict on whether the stack is operationally ready for SIM
observe/armed -- gathered from existing read-only surfaces only. It starts no
service, places no order, and calls no LLM. SIM-only; live stays hard-blocked.

Sources (all via ``OverviewQueries`` + pure helpers):
- health (freshness, kill switch, risk halt, evidence block, git commit)
- arming_check
- evidence report (grade + setup table)
- promotion report (candidate count)
- replay readiness (+ optional full replay summary with --replay)
- SIM broker preflight
- session-report existence / archive status

Verdict rules:
- FAIL  -> kill switch active, stale heartbeat, stale market data, SIM broker
          unreadable, or live_blocked is not true.
- WARN  -> evidence grade below ``replayable``, replay_input missing, scorecard
          missing, session report missing, or R/drawdown counters unavailable.
- PASS  -> operational readiness clean AND evidence at least ``replayable`` AND
          no warns. (In the current build R-denominated counters are
          unavailable, so a clean stack typically reports WARN, not PASS --
          that is honest: real data + tuning are still needed.)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# Evidence grade ladder (worst -> best) for the "at least replayable" gate.
_GRADE_ORDER = {"no_data": 0, "logging_only": 1, "replayable": 2,
                "outcome_linked": 3, "promotion_candidate": 4}

_DEF_LEARN = Path(r"D:\BookmapLogs\pax-agent")
_DEF_JOURNAL = Path(r"D:\BookmapLogs\pax-journal.db")
_DEF_SIM_DB = Path(r"D:\BookmapLogs\pax-daemon-trades.db")


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except Exception:
        return default


def run_acceptance(*,
                   journal: Path,
                   sim_db: Path,
                   learn_dir: Optional[Path] = None,
                   replay: bool = False,
                   now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Build the acceptance report. Read-only; never raises for missing data."""
    from .overview_ui import OverviewQueries, _git_commit  # local: avoid cycle

    q = OverviewQueries(journal, sim_db_path=sim_db, learn_dir=learn_dir)
    learn = q.learn_dir
    now = int(now_ms) if now_ms is not None else int(time.time() * 1000)

    health = _safe(q.health, {})
    arming = _safe(q.arming_check, {})
    evidence = _safe(q.evidence_report, {})
    sim_pf = _safe(q.sim_broker_preflight, {"openable": False, "readable": False,
                                            "error": "preflight failed"})
    promotion = _safe(q.promotion_report, {})

    sources = health.get("sources") or {}
    checks: List[Dict[str, Any]] = []
    required: List[str] = []

    def add(code: str, status: str, message: str,
            action: Optional[str] = None) -> None:
        checks.append({"code": code, "status": status, "message": message})
        if action and status in ("fail", "warn"):
            required.append(action)

    # --- hard FAIL conditions ------------------------------------------------
    live_blocked = bool(health.get("live_blocked", True))
    add("live_blocked", "pass" if live_blocked else "fail",
        "live trading hard-blocked (SIM only)" if live_blocked
        else "live_blocked is NOT true -- refuse to proceed",
        "restore the live hard-block")

    ks = bool(health.get("kill_switch_active"))
    add("kill_switch_absent", "fail" if ks else "pass",
        "operator kill switch engaged" if ks else "no kill switch file",
        "remove the KILL_SWITCH file")

    hb_stale = bool((sources.get("heartbeat") or {}).get("is_stale"))
    add("heartbeat_fresh", "fail" if hb_stale else "pass",
        "agent heartbeat stale/absent" if hb_stale else "heartbeat fresh",
        "start PAX observe (paxi.bat start) and confirm a fresh heartbeat")

    mk_stale = bool((sources.get("market") or {}).get("is_stale"))
    add("market_data_fresh", "fail" if mk_stale else "pass",
        "market data stale/absent (bridge/Bookmap feed down)" if mk_stale
        else "market data fresh",
        "bring up Bookmap + bridge so market data is fresh")

    sim_ok = bool(sim_pf.get("openable") and sim_pf.get("readable"))
    add("sim_broker_readable", "pass" if sim_ok else "fail",
        "SIM broker openable + readable" if sim_ok
        else (sim_pf.get("error") or "SIM broker unreadable"),
        "point PAX at a valid, readable SIM broker DB")

    rha = bool(health.get("risk_halt_active"))
    add("risk_halt_clear", "fail" if rha else "pass",
        (health.get("risk_halt_message") or health.get("risk_halt_code")
         or "active risk halt") if rha else "no active operational risk halt")

    # --- WARN conditions -----------------------------------------------------
    grade = str(evidence.get("evidence_grade")
                or (health.get("evidence") or {}).get("evidence_grade")
                or "no_data")
    grade_ok = _GRADE_ORDER.get(grade, 0) >= _GRADE_ORDER["replayable"]
    add("evidence_at_least_replayable", "pass" if grade_ok else "warn",
        f"evidence grade = {grade}",
        "raise evidence grade to at least replayable (see next_required_data)")

    ri_pct = _num((health.get("replay_readiness") or {}).get(
        "replay_input_pct_recent"))
    ri_present = ri_pct is not None and ri_pct > 0
    add("replay_input_present", "pass" if ri_present else "warn",
        f"recent replay_input coverage = {ri_pct}%" if ri_pct is not None
        else "no replay_input in recent heartbeats",
        "relaunch on the current build so heartbeats embed replay_input")

    has_scorecard = bool((evidence.get("scorecard_summary") or {}).get(
        "has_outcome_data") or promotion.get("has_outcome_data"))
    add("scorecard_present", "pass" if has_scorecard else "warn",
        "scorecard/outcomes present" if has_scorecard
        else "no scorecard/outcomes yet",
        "run armed SIM so pax_trade_learning writes scorecard.json")

    session_report = learn / "session-report.json"
    sessions_dir = learn / "sessions"
    has_session = session_report.exists()
    archived = sessions_dir.exists() and any(sessions_dir.glob(
        "session-report-*.json"))
    add("session_report_present", "pass" if has_session else "warn",
        ("session report present" + (" (+archive)" if archived else ""))
        if has_session else "no session-report.json yet",
        "run paxi.bat stop (writes session report) at least once")

    # R-denominated counters are unavailable by design (no per-trade risk in the
    # SIM close stream). USD drawdown + consecutive-loss ARE available now.
    add("risk_counters_available", "warn",
        "USD drawdown + consecutive-loss counters available; R-denominated "
        "counters unavailable (no per-trade risk in the SIM close stream)")

    # --- overall verdict -----------------------------------------------------
    has_fail = any(c["status"] == "fail" for c in checks)
    has_warn = any(c["status"] == "warn" for c in checks)
    overall = "fail" if has_fail else ("warn" if has_warn else "pass")

    # required_actions: acceptance-specific + arming + evidence next_required.
    for a in (arming.get("required_actions") or []):
        if a not in required:
            required.append(a)
    for a in (evidence.get("next_required_data") or []):
        if a not in required:
            required.append(a)

    out: Dict[str, Any] = {
        "overall": overall,
        "sim_only": True,
        "live_blocked": True,
        "checked_ms": now,
        "git_commit": health.get("git_commit") or _safe(_git_commit, None),
        "evidence_grade": grade,
        "candidate_setup_count": (evidence.get("scorecard_summary") or {}).get(
            "candidate_count", 0),
        "session_report": {"present": has_session, "archived": archived,
                           "path": str(session_report)},
        "sim_broker": sim_pf,
        "risk_halt": {"active": rha, "code": health.get("risk_halt_code")},
        "checks": checks,
        "required_actions": required,
        "limitations": [
            "SIM-only; live trading hard-blocked; no live-market edge claim.",
            "R-denominated risk counters unavailable (no per-trade risk in the "
            "SIM close stream); USD drawdown + consecutive-loss are available.",
            "'candidate' is NOT 'validated'; promotion past candidate needs a "
            "human + replay + paper-pass gate.",
            "a clean stack typically reports WARN (not PASS) until R counters "
            "exist and evidence is collected -- that is honest, not a failure.",
        ],
    }
    if replay:
        out["replay_summary"] = _replay_summary(learn / "agent-loop.jsonl")
    return out


def _num(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _replay_summary(agent_log: Path) -> Dict[str, Any]:
    try:
        from . import pax_agent_replay
        full = pax_agent_replay.replay_file(Path(agent_log))
        return {k: full.get(k) for k in (
            "event_count", "usable_snapshot_count", "decisions_generated",
            "replay_input_count", "op_gate_replayed_count",
            "op_gate_missing_fields_count", "divergence_count",
            "risk_halt_divergence_count")}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_acceptance",
        description="Read-only SIM acceptance/doctor verdict (no service start, "
                    "no broker order, no LLM). SIM-only; live hard-blocked.")
    p.add_argument("--journal", type=Path, default=_DEF_JOURNAL)
    p.add_argument("--sim-db", type=Path, default=_DEF_SIM_DB)
    p.add_argument("--learn-dir", type=Path, default=None)
    p.add_argument("--replay", action="store_true",
                   help="Also run pax_agent_replay over the agent log (slower).")
    p.add_argument("--out", type=Path, default=None,
                   help="Write the JSON here (else stdout).")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_acceptance(journal=args.journal, sim_db=args.sim_db,
                            learn_dir=args.learn_dir, replay=args.replay)
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"acceptance report written: {args.out} (overall="
              f"{report['overall']})", file=sys.stderr)
    else:
        print(body)
    # Exit code mirrors the verdict so it can gate a script: pass/warn = 0,
    # fail = 1 (operationally not ready).
    return 1 if report["overall"] == "fail" else 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
