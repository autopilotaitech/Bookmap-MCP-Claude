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
        if rec.get("executed") and rec.get("order"):
            executions.append({"ts_ms": rec.get("ts_ms"), "action": act,
                               "setup_type": rec.get("setup_type"),
                               "order": rec.get("order")})

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
        },
        "evaluation_state": eval_state,
    }


def gather_and_write(*,
                     journal: Path,
                     sim_db: Path,
                     learn_dir: Optional[Path] = None,
                     out_path: Optional[Path] = None) -> Path:
    """Read live data via OverviewQueries, build the report, write JSON."""
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
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
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
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    out = gather_and_write(journal=args.journal, sim_db=args.sim_db,
                           learn_dir=args.learn_dir, out_path=args.out)
    print(f"session report written: {out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    sys.exit(main())
