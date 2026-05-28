"""Phase 3: background paper-trading daemon.

Runs unattended for months. Reads from a pluggable DataAdapter, drives the
pure signal engine, advances a local SimEngine, and journals everything to
a SQLite database. No live broker calls EVER.

Usage:
    python -m bookmap_mcp.pax_daemon --source csv --path replay.csv
    python -m bookmap_mcp.pax_daemon --source csv --path replay.csv --once
    python -m bookmap_mcp.pax_daemon --source csv --path replay.csv \
        --journal D:\\BookmapLogs\\pax-journal.db --dry-run

Safety:
- Refuses to start if BOOKMAP_ALLOW_TRADING=1 is set in the environment.
- Does not import the live-order MCP tools (test_daemon_lifecycle pins this
  by grepping the module source for the tool names).
- SimEngine is local sqlite only (no HTTP).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import signal as signal_lib
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

from . import signal_engine as se
from .adapters import BookmapLiveAdapter, CsvReplayAdapter, FileTailAdapter
from .adapters.base import AdapterHealth, DataAdapter
from .journal import Journal
from .pax_trader import decide_and_act
from .sim_engine import SimEngine
from .snapshot import is_valid


# ─── helpers ────────────────────────────────────────────────────────────

def _refuse_if_live_trading_allowed() -> None:
    if os.environ.get("BOOKMAP_ALLOW_TRADING") == "1":
        sys.stderr.write(
            "ERROR: BOOKMAP_ALLOW_TRADING=1 is set in the environment. "
            "The paper-trading daemon refuses to start while live trading "
            "is enabled. Unset the env var and retry.\n")
        sys.exit(2)


def _weights_hash(path: Optional[Path]) -> str:
    if path is None or not path.exists():
        return ""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


def _enrich(snap: Dict[str, Any]) -> Dict[str, Any]:
    """Run the signal engine over a normalized snapshot. Adds computed
    keys in place and returns the snap for convenience."""
    snap["tape_flow"]   = _safe_call(se.compute_tape_flow,           snap)
    snap["or_levels"]   = _safe_call(se.compute_or_levels,           snap)
    snap["vwap_bias"]   = _safe_call(se.compute_vwap_bias,           snap)
    snap["vp_bias"]     = _safe_call(se.compute_vp_bias,             snap)
    snap["conviction"]  = _safe_call(se.compute_session_conviction,  snap)
    snap["pax"]         = _safe_call(se.pax_decision,                snap)
    return snap


def _safe_call(fn, snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        return fn(snap)
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {exc}"}


def _build_adapter(source: str, path: Optional[Path],
                    alias: str) -> DataAdapter:
    if source == "csv":
        if path is None:
            raise SystemExit("--path is required for --source csv")
        return CsvReplayAdapter(path, alias=alias)
    if source == "tail":
        if path is None:
            raise SystemExit("--path is required for --source tail")
        return FileTailAdapter(path, alias=alias)
    if source == "bookmap":
        # Live Bookmap snapshots → sim execution. Read-only on the bridge
        # side; no /place_limit_order or /cancel_order. SimEngine writes
        # paper fills locally to its own SQLite DB.
        return BookmapLiveAdapter(alias=alias)
    raise SystemExit(f"unsupported source: {source}")


# ─── main loop ──────────────────────────────────────────────────────────

def run_daemon(args: argparse.Namespace) -> int:
    """Synchronous main loop. Returns process exit code."""
    _refuse_if_live_trading_allowed()

    adapter = _build_adapter(args.source, args.path, args.alias)
    sim = SimEngine(alias=args.alias, db_path=args.sim_db)

    journal: Optional[Journal] = None
    if not args.dry_run:
        journal = Journal(args.journal)
        journal.open()
        weights_path = Path(args.weights) if args.weights else None
        journal.begin_run(
            adapter_name=adapter.name,
            adapter_config={"path": str(args.path), "alias": args.alias},
            signal_version=se.CONVICTION_METHOD_VERSION,
            weights_hash=_weights_hash(weights_path),
            notes=args.notes)

    shutdown = {"requested": False}
    def _on_signal(*_a):
        shutdown["requested"] = True

    signal_lib.signal(signal_lib.SIGINT, _on_signal)
    try:
        signal_lib.signal(signal_lib.SIGTERM, _on_signal)
    except Exception:    # pragma: no cover — SIGTERM not on Windows
        pass

    adapter.start()
    last_heartbeat = 0.0
    n_snapshots = 0
    n_signals = 0
    exit_code = 0
    try:
        while not shutdown["requested"]:
            snap = adapter.next_snapshot()
            if snap is None:
                # EOF (replay finished) or no data → exit cleanly.
                if journal is not None:
                    journal.write_event(
                        "ADAPTER_EOF", "daemon",
                        f"adapter {adapter.name} returned None")
                break

            if not is_valid(snap):
                if journal is not None:
                    journal.write_event(
                        "SCHEMA_INVALID", "daemon",
                        "snapshot failed validate_snapshot",
                        {"alias": snap.get("alias")})
                continue

            _enrich(snap)
            n_snapshots += 1
            sim_result = sim.tick(snap)

            # On ENTER_* decisions, actually place the paper bracket. Without
            # this the daemon would just log decisions without ever taking
            # paper trades — exactly the previous bug. decide_and_act handles:
            #   - bail-out on WAIT/STAND_DOWN/already-positioned/no-level
            #   - side selection from LONG/SHORT
            #   - bracket compute (FOLLOW=STOP-LIMIT, FADE=LIMIT) + TPs
            #   - eng.place_bracket() call
            # use_claude=False so the daemon never spawns Claude subprocesses.
            # --no-auto-decide skips this entirely so an external operator
            # (pax_manual CLI) can drive trades against the same sim-db.
            if args.no_auto_decide:
                decision_action = {"action": "skipped_no_auto_decide"}
            else:
                decision_action = decide_and_act(snap, sim, use_claude=False)
            n_placed = 0
            if decision_action.get("action") == "placed_bracket":
                n_placed = 1

            if journal is not None:
                journal.write_snapshot(snap)
                pax = snap.get("pax") or {}
                if pax and pax.get("decision"):
                    journal.write_signal(snap, pax)
                    n_signals += 1
                if decision_action.get("action") == "placed_bracket":
                    journal.write_event(
                        "BRACKET_PLACED", "daemon",
                        f"{decision_action.get('decision')} @ "
                        f"{decision_action.get('level')} qty="
                        f"{decision_action.get('qty')}",
                        {"ids": decision_action.get("ids"),
                         "entry": decision_action.get("entry_px"),
                         "stop":  decision_action.get("stop_loss"),
                         "tps":   decision_action.get("take_profits")})
                # Tick actions (fills, TIF) also worth journaling at INFO level.
                for act in (sim_result.get("actions") or []):
                    if act.get("kind") in ("FILL", "STOP_TRIGGER",
                                              "TIF_EXPIRE",
                                              "EOD_AUTO_FLATTEN"):
                        journal.write_event(
                            act["kind"], "sim", f"order {act.get('id')}",
                            act)
                # Heartbeat every 10s of wall clock.
                now = time.time()
                if now - last_heartbeat > 10.0:
                    journal.write_adapter_health(adapter.health())
                    journal.commit()
                    last_heartbeat = now

            if args.once:
                break
            if args.poll_ms > 0:
                time.sleep(args.poll_ms / 1000.0)
    except Exception as exc:        # pragma: no cover — defensive
        exit_code = 1
        sys.stderr.write(f"daemon loop crashed: {type(exc).__name__}: {exc}\n")
        traceback.print_exc()
        if journal is not None:
            journal.write_event(
                "LOOP_CRASH", "daemon",
                f"{type(exc).__name__}: {exc}",
                {"trace": traceback.format_exc()[:4000]})
    finally:
        try:
            adapter.stop()
        except Exception:
            pass
        if journal is not None:
            try:
                journal.write_adapter_health(adapter.health())
                journal.write_event(
                    "DAEMON_EXIT", "daemon",
                    f"snapshots={n_snapshots} signals={n_signals}")
                journal.end_run(notes=f"clean: {n_snapshots} snapshots")
            finally:
                journal.close()
    return exit_code


# ─── CLI ────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pax_daemon",
        description="Background paper-trading research daemon.")
    p.add_argument("--source", choices=["csv", "tail", "bookmap"],
                    default="csv",
                    help="Data adapter. csv = replay; tail = live-tailed "
                         "CSV; bookmap = live Bookmap bridge (read-only, "
                         "paper-sim only).")
    p.add_argument("--path", type=Path, default=None,
                    help="Source-specific path (CSV file for csv/tail; "
                         "ignored for bookmap).")
    p.add_argument("--alias", default="NQ", help="Instrument alias.")
    p.add_argument("--journal", type=Path,
                    default=Path(r"D:\BookmapLogs\pax-journal.db"),
                    help="SQLite journal path.")
    p.add_argument("--sim-db", type=Path,
                    default=Path(r"D:\BookmapLogs\pax-daemon-trades.db"),
                    help="Separate sim DB so the daemon doesn't share state "
                         "with the interactive pax_trader.")
    p.add_argument("--weights", type=Path, default=None,
                    help="Path to pax_weights.json (default: built-in).")
    p.add_argument("--poll-ms", type=int, default=0,
                    help="Sleep between snapshots (0 = as fast as possible, "
                         "appropriate for replay).")
    p.add_argument("--notes", default=None,
                    help="Free text recorded in runs.notes.")
    p.add_argument("--dry-run", action="store_true",
                    help="Compute decisions but do not write to the journal.")
    p.add_argument("--once", action="store_true",
                    help="Process a single snapshot and exit.")
    p.add_argument("--no-auto-decide", action="store_true",
                    help="Skip decide_and_act(); keep sim.tick(), fills, and "
                         "journaling. Use when an external operator drives "
                         "trades via the pax_manual CLI against the same "
                         "sim-db. SimEngine still ticks every snapshot so "
                         "fills + brackets work normally.")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    return run_daemon(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
