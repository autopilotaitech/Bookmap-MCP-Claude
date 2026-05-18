"""Phase 5: forward-return outcomes backfill against the journal DB.

For every signal row in `signals`, find the snapshot mid at +5m, +10m,
+30m, +1h, compute the directional return, and write into the `outcomes`
table. This is the input to setup-stats aggregation and (eventually) the
weight-recommendation pipeline. Designed as a daily batch job:

    python -m bookmap_mcp.journal_outcomes \\
        --journal D:\\BookmapLogs\\pax-journal.db \\
        --since 2026-05-01 --until 2026-05-31

Critical: NEVER writes to pax_weights.json. Human-in-the-loop only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple


# Default forward-return horizons (seconds).
DEFAULT_HORIZONS_SEC = (5 * 60, 10 * 60, 30 * 60, 60 * 60)


def _direction_sign(decision: str) -> int:
    """+1 for LONG decisions, -1 for SHORT, 0 for WAIT/STAND_DOWN/etc."""
    if not decision: return 0
    d = decision.upper()
    if "ENTER_LONG" in d:  return +1
    if "ENTER_SHORT" in d: return -1
    return 0


def _fetch_mid_at_or_after(c: sqlite3.Connection, alias: str,
                            ts_ms: int) -> Optional[float]:
    """Return the mid of the first snapshot at or after ts_ms for the alias.
    None when there's no snapshot in the window (replay too short, etc.)."""
    row = c.execute(
        "SELECT mid FROM snapshots WHERE alias=? AND ts_ms>=? "
        "ORDER BY ts_ms LIMIT 1",
        (alias, ts_ms)).fetchone()
    if row is None or row[0] is None:
        return None
    return float(row[0])


def backfill(journal_path: Path,
              horizons_sec: Tuple[int, ...] = DEFAULT_HORIZONS_SEC,
              since_ms: Optional[int] = None,
              until_ms: Optional[int] = None) -> dict:
    """Compute forward returns for every signal in the date range and
    INSERT OR REPLACE into outcomes. Returns a summary dict."""
    p = Path(journal_path)
    c = sqlite3.connect(str(p), timeout=5.0)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        # signals → exit prices → outcomes
        where = ""
        args: List = []
        if since_ms is not None:
            where += " AND s.ts_ms >= ?"
            args.append(since_ms)
        if until_ms is not None:
            where += " AND s.ts_ms <= ?"
            args.append(until_ms)
        signals = c.execute(
            "SELECT s.run_id, s.ts_ms, s.alias, s.decision, s.level_label, "
            "       snap.mid AS entry_mid "
            "FROM signals s "
            "LEFT JOIN snapshots snap "
            "  ON snap.run_id = s.run_id AND snap.ts_ms = s.ts_ms "
            "       AND snap.alias = s.alias "
            "WHERE s.decision LIKE 'ENTER_%'" + where +
            " ORDER BY s.ts_ms",
            args).fetchall()
        n_signals = 0
        n_outcomes = 0
        for sig_run, sig_ts, alias, decision, level_label, entry_mid in signals:
            n_signals += 1
            if entry_mid is None:
                # No mid available at the signal timestamp — skip.
                continue
            sign = _direction_sign(decision)
            for h in horizons_sec:
                exit_ts = sig_ts + int(h) * 1000
                exit_mid = _fetch_mid_at_or_after(c, alias, exit_ts)
                return_pts = None
                win = None
                if exit_mid is not None:
                    raw_pts = exit_mid - entry_mid
                    return_pts = raw_pts * sign  # +ve = decision direction worked
                    # win iff strictly positive directional return.
                    win = 1 if return_pts > 0 else 0
                c.execute(
                    "INSERT OR REPLACE INTO outcomes(run_id, signal_ts_ms, "
                    "alias, horizon_sec, entry_mid, exit_mid, return_pts, "
                    "win, decision, level_label) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (sig_run, sig_ts, alias, int(h), entry_mid, exit_mid,
                     return_pts, win, decision, level_label))
                n_outcomes += 1
        c.commit()
        return {"signals_scanned": n_signals,
                 "outcomes_written": n_outcomes,
                 "horizons_sec": list(horizons_sec)}
    finally:
        c.close()


def setup_stats(journal_path: Path) -> List[dict]:
    """Aggregate outcomes by (decision, level_label, horizon_sec):
    win-rate, n, avg-return. Read-only — no DB writes."""
    p = Path(journal_path)
    c = sqlite3.connect(str(p), timeout=5.0)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "SELECT decision, level_label, horizon_sec, "
            "  COUNT(*) AS n, "
            "  SUM(CASE WHEN win=1 THEN 1 ELSE 0 END) AS wins, "
            "  AVG(return_pts) AS avg_return_pts "
            "FROM outcomes "
            "WHERE return_pts IS NOT NULL "
            "GROUP BY decision, level_label, horizon_sec "
            "ORDER BY decision, level_label, horizon_sec"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ─── CLI ────────────────────────────────────────────────────────────────

def _parse_date_to_ms(s: Optional[str]) -> Optional[int]:
    if not s: return None
    try:
        d = dt.datetime.fromisoformat(s)
    except ValueError:
        raise SystemExit(f"bad date: {s} (expected YYYY-MM-DD)")
    return int(d.timestamp() * 1000)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="journal_outcomes",
        description="Backfill forward-return outcomes from journal signals.")
    p.add_argument("--journal", type=Path, required=True)
    p.add_argument("--since", default=None,
                    help="YYYY-MM-DD lower bound on signal ts (optional).")
    p.add_argument("--until", default=None,
                    help="YYYY-MM-DD upper bound on signal ts (optional).")
    p.add_argument("--horizons", default="300,600,1800,3600",
                    help="Comma-separated horizons in seconds.")
    p.add_argument("--print-stats", action="store_true",
                    help="After backfill, print setup-stats summary.")
    return p


def main(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    horizons = tuple(int(x.strip()) for x in args.horizons.split(",") if x.strip())
    result = backfill(args.journal, horizons_sec=horizons,
                       since_ms=_parse_date_to_ms(args.since),
                       until_ms=_parse_date_to_ms(args.until))
    sys.stderr.write(
        f"signals scanned: {result['signals_scanned']}, "
        f"outcomes written: {result['outcomes_written']}\n")
    if args.print_stats:
        for r in setup_stats(args.journal):
            wr = (r["wins"] / r["n"]) if r["n"] else 0.0
            sys.stdout.write(
                f"{r['decision']:>22s} @ {r['level_label']:>6s} "
                f"h={r['horizon_sec']}s n={r['n']:>4d} "
                f"win={wr:.0%} avg={r['avg_return_pts']:+.2f}pts\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
