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


def _safe2(fn: Callable[[], Any]):
    """Call fn -> (value, error_str_or_None). Errors are surfaced, never hidden."""
    try:
        return fn(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"[:200]


def _artifact_status(path: Path) -> Dict[str, Any]:
    """existence / mtime_ms / size_bytes for one artifact (no read)."""
    p = Path(path)
    try:
        st = p.stat()
        return {"path": str(p), "exists": True,
                "mtime_ms": int(st.st_mtime * 1000), "size_bytes": int(st.st_size)}
    except OSError:
        return {"path": str(p), "exists": False, "mtime_ms": None,
                "size_bytes": None}


def _repo_dirty() -> Optional[bool]:
    """True if the repo working tree is dirty. Best-effort, no window on win32."""
    try:
        import subprocess
        repo = Path(__file__).resolve().parents[2]
        kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                           capture_output=True, text=True, timeout=3.0, **kwargs)
        if r.returncode != 0:
            return None
        return bool((r.stdout or "").strip())
    except Exception:
        return None


def run_acceptance(*,
                   journal: Path,
                   sim_db: Path,
                   learn_dir: Optional[Path] = None,
                   replay: bool = False,
                   now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Build the acceptance report. Read-only; never raises for missing data.

    FAIL-CLOSED: if the health surface cannot be gathered (exception or empty),
    or omits ``live_blocked`` / its ``sources``, the dependent checks FAIL rather
    than silently pass. SIM-only; live stays hard-blocked in the output."""
    from .overview_ui import OverviewQueries, _git_commit  # local: avoid cycle

    q = OverviewQueries(journal, sim_db_path=sim_db, learn_dir=learn_dir)
    learn = q.learn_dir
    now = int(now_ms) if now_ms is not None else int(time.time() * 1000)

    health, health_err = _safe2(q.health)
    arming, arming_err = _safe2(q.arming_check)
    evidence, evidence_err = _safe2(q.evidence_report)
    promotion, promotion_err = _safe2(q.promotion_report)
    sim_pf = _safe(q.sim_broker_preflight, {"openable": False, "readable": False,
                                            "error": "preflight failed"})
    health = health if isinstance(health, dict) else {}
    arming = arming if isinstance(arming, dict) else {}
    evidence = evidence if isinstance(evidence, dict) else {}
    promotion = promotion if isinstance(promotion, dict) else {}

    source_errors = {k: v for k, v in (
        ("health_error", health_err), ("arming_error", arming_err),
        ("evidence_error", evidence_err), ("promotion_error", promotion_err))
        if v}

    checks: List[Dict[str, Any]] = []
    required: List[str] = []

    def add(code: str, status: str, message: str,
            action: Optional[str] = None) -> None:
        checks.append({"code": code, "status": status, "message": message})
        if action and status in ("fail", "warn"):
            required.append(action)

    # --- health must be gatherable (fail-closed) -----------------------------
    health_ok = bool(health) and health_err is None
    add("health_available", "pass" if health_ok else "fail",
        "health surface gathered" if health_ok
        else f"health unavailable: {health_err or 'empty'}",
        "investigate the overview health surface")

    # --- live_blocked: pass ONLY when health explicitly reports True ---------
    lb = health.get("live_blocked") if health_ok else None
    if lb is True:
        add("live_blocked", "pass", "live trading hard-blocked (SIM only)")
    elif lb is False:
        add("live_blocked", "fail", "live_blocked is NOT true -- refuse to proceed",
            "restore the live hard-block")
    else:
        add("live_blocked", "fail",
            "live_blocked_unknown -- health did not report live_blocked",
            "restore the live hard-block / repair the health surface")

    # --- kill switch from the authoritative file check (not via health) ------
    ks = _safe(q.kill_switch_active, None)
    if ks is True:
        add("kill_switch_absent", "fail", "operator kill switch engaged",
            "remove the KILL_SWITCH file")
    elif ks is False:
        add("kill_switch_absent", "pass", "no kill switch file")
    else:
        add("kill_switch_absent", "fail",
            "kill switch state unknown (learn dir unreadable)",
            "make the learn dir readable")

    # --- heartbeat / market: a missing sources dict or source FAILS ----------
    sources = health.get("sources") if health_ok else None
    have_sources = isinstance(sources, dict) and bool(sources)

    def _source_fresh(name: str):
        if not have_sources:
            return False, "health has no sources dict -- cannot prove freshness"
        src = sources.get(name)
        if not isinstance(src, dict) or "is_stale" not in src:
            return False, f"{name} source missing/unknown"
        if src.get("is_stale"):
            return False, f"{name} stale/absent"
        return True, f"{name} fresh"

    hb_ok, hb_msg = _source_fresh("heartbeat")
    add("heartbeat_fresh", "pass" if hb_ok else "fail", hb_msg,
        "start PAX observe (paxi.bat start) and confirm a fresh heartbeat")

    mk_ok, mk_msg = _source_fresh("market")
    add("market_data_fresh", "pass" if mk_ok else "fail", mk_msg,
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

    git_commit = health.get("git_commit") or _safe(_git_commit, None)
    agent_log = learn / "agent-loop.jsonl"
    scorecard = learn / "scorecard.json"
    out: Dict[str, Any] = {
        "overall": overall,
        "sim_only": True,
        "live_blocked": True,
        "checked_ms": now,
        "git_commit": git_commit,
        "current_git_commit": git_commit,
        "repo_dirty": _repo_dirty(),
        "evidence_grade": grade,
        "candidate_setup_count": (evidence.get("scorecard_summary") or {}).get(
            "candidate_count", 0),
        "session_report": {"present": has_session, "archived": archived,
                           "path": str(session_report)},
        "sim_broker": sim_pf,
        "risk_halt": {"active": rha, "code": health.get("risk_halt_code")},
        "input_paths": {
            "journal": str(journal), "sim_db": str(sim_db),
            "learn_dir": str(learn), "agent_log": str(agent_log),
            "scorecard": str(scorecard), "session_report": str(session_report),
        },
        "source_errors": source_errors,
        "artifact_status": {
            "agent_loop": _artifact_status(agent_log),
            "scorecard": _artifact_status(scorecard),
            "session_report": _artifact_status(session_report),
            "evidence_report": _artifact_status(learn / "evidence-report.json"),
            "runtime_policy": _artifact_status(learn / "runtime-policy.json"),
            "calibration": _artifact_status(learn / "calibration.json"),
        },
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
        rsum = _replay_summary(agent_log)
        out["replay_summary"] = rsum
        if isinstance(rsum, dict) and rsum.get("error"):
            out["source_errors"]["replay_error"] = rsum["error"]
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


def build_bundle(report: Dict[str, Any], learn_dir: Path) -> Dict[str, Any]:
    """One JSON bundle: the acceptance report + COMPACT evidence/session/replay
    summaries (no raw logs). Read-only; missing files degrade to None."""
    learn = Path(learn_dir)
    evidence = None
    ev = learn / "evidence-report.json"
    if ev.exists():
        try:
            d = json.loads(ev.read_text(encoding="utf-8"))
            evidence = {k: d.get(k) for k in (
                "generated_ms", "evidence_grade", "scorecard_summary",
                "replay_readiness", "blockers", "warnings",
                "next_required_data")}
        except (OSError, json.JSONDecodeError) as exc:
            evidence = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    session = None
    sr = learn / "session-report.json"
    if sr.exists():
        try:
            d = json.loads(sr.read_text(encoding="utf-8"))
            session = {k: d.get(k) for k in (
                "generated_ms", "decisions", "executions", "risk_halts",
                "pnl", "replay_readiness", "evidence_summary")}
        except (OSError, json.JSONDecodeError) as exc:
            session = {"error": f"{type(exc).__name__}: {exc}"[:200]}
    return {
        "bundle_version": 1,
        "acceptance": report,
        "evidence_summary": evidence,
        "session_summary": session,
        "replay_summary": report.get("replay_summary"),
        "sim_only": True,
        "live_blocked": True,
    }


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
    p.add_argument("--bundle-out", type=Path, default=None,
                   help="Also write a single JSON bundle (acceptance + compact "
                        "evidence/session/replay summaries) here.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # A bundle needs the replay summary; turn replay on when bundling.
    replay = bool(args.replay or args.bundle_out)
    report = run_acceptance(journal=args.journal, sim_db=args.sim_db,
                            learn_dir=args.learn_dir, replay=replay)
    body = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(body, encoding="utf-8")
        print(f"acceptance report written: {args.out} (overall="
              f"{report['overall']})", file=sys.stderr)
    if args.bundle_out:
        from .overview_ui import OverviewQueries
        learn = OverviewQueries(args.journal, sim_db_path=args.sim_db,
                                learn_dir=args.learn_dir).learn_dir
        bundle = build_bundle(report, learn)
        args.bundle_out.parent.mkdir(parents=True, exist_ok=True)
        args.bundle_out.write_text(
            json.dumps(bundle, indent=2, sort_keys=True, default=str),
            encoding="utf-8")
        print(f"acceptance bundle written: {args.bundle_out}", file=sys.stderr)
    if not args.out and not args.bundle_out:
        print(body)
    # Exit code mirrors the verdict so it can gate a script: pass/warn = 0,
    # fail = 1 (operationally not ready).
    return 1 if report["overall"] == "fail" else 0


if __name__ == "__main__":   # pragma: no cover
    raise SystemExit(main())
