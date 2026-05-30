"""Session report writer.

Builds a structured end-of-session report from the agent-loop decision feed,
the SIM equity curve, and journal error events, then writes it to
``D:\\BookmapLogs\\pax-agent\\session-report.json``.

Read-only: it consumes existing logs and SIM/journal DBs and writes one JSON
artifact. It never touches the decision or order path.

CLI:
    python -m bookmap_mcp.pax_session_report
    python -m bookmap_mcp.pax_session_report --out D:\\BookmapLogs\\pax-agent\\session-report.json
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import pax_freshness


def build_session_report(*,
                         feed: List[Dict[str, Any]],
                         equity: Dict[str, Any],
                         errors: List[Dict[str, Any]],
                         eval_state: Optional[Dict[str, Any]] = None,
                         now_ms: Optional[int] = None) -> Dict[str, Any]:
    """Assemble the report from already-loaded records. Pure function."""
    feed = feed or []
    actions: Counter = Counter()
    setups: Counter = Counter()
    model_calls = 0
    vetoes: List[Dict[str, Any]] = []
    executions: List[Dict[str, Any]] = []
    blocked: List[Dict[str, Any]] = []
    risk_halts: List[Dict[str, Any]] = []
    halt_codes: Counter = Counter()
    malformed = 0

    for rec in feed:
        if not isinstance(rec, dict):
            malformed += 1
            continue
        act = str(rec.get("action") or "?")
        actions[act] += 1
        if rec.get("setup_type"):
            setups[str(rec.get("setup_type"))] += 1
        if rec.get("llm") or rec.get("model"):
            model_calls += 1
        gov = str(rec.get("governor") or "")
        if gov.startswith("VETO"):
            vetoes.append({"ts_ms": rec.get("ts_ms"), "action": act,
                           "reason": gov})
            blocked.append({"ts_ms": rec.get("ts_ms"), "action": act,
                            "reason": gov})
        halt_code = rec.get("risk_halt_code") or rec.get("risk_halt")
        if halt_code:
            halt_codes[str(halt_code)] += 1
            risk_halts.append({"ts_ms": rec.get("ts_ms"), "action": act,
                               "code": str(halt_code),
                               "message": rec.get("risk_halt_message")})
        if rec.get("executed") and rec.get("order"):
            executions.append({"ts_ms": rec.get("ts_ms"), "action": act,
                               "setup_type": rec.get("setup_type"),
                               "order": rec.get("order")})

    # Categorize enforced operational halts (pax_risk_gate vocabulary).
    _STALE_CODES = ("stale_heartbeat", "stale_market_data")
    _SESSION_CODES = ("max_trades_reached", "max_consecutive_losses_reached",
                      "max_loss_reached", "max_drawdown_reached")
    stale_data_blocks = sum(halt_codes.get(c, 0) for c in _STALE_CODES)
    session_limit_blocks = sum(halt_codes.get(c, 0) for c in _SESSION_CODES)

    wins = int(equity.get("wins") or 0)
    losses = int(equity.get("losses") or 0)
    decided = sum(actions.values())
    enters = sum(v for k, v in actions.items() if str(k).startswith(("ENTER", "PLACE")))

    dict_feed = [r for r in feed if isinstance(r, dict)]
    first_ts = dict_feed[0].get("ts_ms") if dict_feed else None
    last_ts = dict_feed[-1].get("ts_ms") if dict_feed else None

    return {
        "generated_ms": pax_freshness.now_ms() if now_ms is None else now_ms,
        "epoch": {"first_ts_ms": first_ts, "last_ts_ms": last_ts,
                  "heartbeats": len(feed)},
        "decisions": {
            "total": decided,
            "by_action": dict(actions),
            "entry_intents": enters,
        },
        "executions": {
            "count": len(executions),
            "list": executions[-50:],
        },
        "blocked": {
            "count": len(blocked),
            "list": blocked[-50:],
        },
        "risk_events": {
            "veto_count": len(vetoes),
            "list": vetoes[-50:],
        },
        "risk_halts": {
            "count": len(risk_halts),
            "by_code": dict(halt_codes),
            "kill_switch": halt_codes.get("kill_switch_active", 0),
            "stale_data": stale_data_blocks,
            "sim_broker": halt_codes.get("sim_broker_unavailable", 0),
            "session_limits": session_limit_blocks,
            "recent": risk_halts[-50:],
        },
        "pnl": {
            "realized_usd": equity.get("realized", 0.0),
            "wins": wins,
            "losses": losses,
            "win_rate": (round(100.0 * wins / (wins + losses), 1)
                         if (wins + losses) else None),
        },
        "setup_stats": dict(setups),
        "model_calls": model_calls,
        "errors": {
            "count": len(errors or []),
            "list": (errors or [])[:30],
        },
        "stale_data_events": {
            "malformed_records": malformed,
            "stale_data_blocks": stale_data_blocks,
        },
        "evaluation_state": eval_state,
    }


def archive_path(out_path: Path, now_ms: int) -> Path:
    """Timestamped archive path for a session report:
    ``<out_dir>/sessions/session-report-YYYYMMDD-HHMMSS.json`` (UTC).
    Pure/deterministic given ``now_ms``."""
    import datetime
    out_path = Path(out_path)
    stamp = datetime.datetime.fromtimestamp(
        int(now_ms) / 1000.0, datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return out_path.parent / "sessions" / f"session-report-{stamp}.json"


def gather_and_write(*,
                     journal: Path,
                     sim_db: Path,
                     learn_dir: Optional[Path] = None,
                     out_path: Optional[Path] = None,
                     archive: bool = False) -> Path:
    """Read live data via OverviewQueries, build the report, write JSON.

    When ``archive`` is set, ALSO writes a timestamped copy under
    ``<out_dir>/sessions/`` so a session history accumulates. Archive-write
    failure never prevents the canonical write."""
    from .overview_ui import OverviewQueries  # local import: avoid cycle at import time

    q = OverviewQueries(journal, sim_db, learn_dir=learn_dir)
    learn = q.learn_dir
    feed = q.agent_feed(limit=2000)
    equity = q.equity_curve()
    errors = q.errors()
    try:
        from . import pax_eval_state
        scorecard = q.learning_scorecard()
        policy = q.runtime_policy()
        summary = q.agent_summary()
        eq = q.equity_curve()
        eval_state = pax_eval_state.compute_eval_state(
            scorecard=scorecard, runtime_policy=policy,
            agent_stats={"armed": summary.get("armed"),
                         "cycles": summary.get("cycles"),
                         "executed": summary.get("executed"),
                         "wins": eq.get("wins"), "losses": eq.get("losses")},
            ops={"heartbeat_stale": (summary.get("last_heartbeat_age_sec") or 0)
                 > pax_freshness.stale_threshold_sec("heartbeat")},
            kill_switch_active=(learn / "KILL_SWITCH").exists(),
        )
    except Exception:
        eval_state = None

    report = build_session_report(feed=feed, equity=equity, errors=errors,
                                  eval_state=eval_state)
    out = Path(out_path) if out_path else (learn / "session-report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=2, default=str)
    out.write_text(body, encoding="utf-8")
    if archive:
        try:
            arch = archive_path(out, report.get("generated_ms")
                                or pax_freshness.now_ms())
            arch.parent.mkdir(parents=True, exist_ok=True)
            arch.write_text(body, encoding="utf-8")
        except OSError:
            pass   # archive is best-effort; canonical write already succeeded
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_session_report",
        description="Write a SIM session report JSON from the agent logs.")
    p.add_argument("--journal", type=Path,
                   default=Path(r"D:\BookmapLogs\pax-journal.db"))
    p.add_argument("--sim-db", type=Path,
                   default=Path(r"D:\BookmapLogs\pax-daemon-trades.db"))
    p.add_argument("--learn-dir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--archive", action="store_true",
                   help="Also write a timestamped copy under <out_dir>/sessions/.")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    out = gather_and_write(journal=args.journal, sim_db=args.sim_db,
                           learn_dir=args.learn_dir, out_path=args.out,
                           archive=args.archive)
    print(f"session report written: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
