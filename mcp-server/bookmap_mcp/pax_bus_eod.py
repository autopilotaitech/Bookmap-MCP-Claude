"""Phase 4B-3 end-of-day rollup CLI.

Reads ai_turns + trade_outcomes for one UTC day, produces a markdown
report covering counts, model/router rollups, verdict distribution,
mid drift, content-based format breakdown, and optional replay
verification limited to format=bus rows.

Usage:
  python -m bookmap_mcp.pax_bus_eod [--date YYYY-MM-DD] [--verify-replay]

Format detection (content-based; see plan):
  - bus           : digest blob has ALL 9 bus markers
  - legacy        : digest blob has "SNAPSHOT DIGEST" but not bus markers
  - missing_blob  : no blob file at expected path
  - unknown       : blob exists but matches neither shape
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import os
import sqlite3
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_BUS_MARKERS = (
    "[STATE]", "[ANCHOR]", "[GATES]", "[LEVELS]",
    "[MICROSTRUCTURE]", "[RECENT_EVENTS]", "[POSITION]",
    "[SESSION_MEMORY]", "[USER]",
)
_LEGACY_MARKER = "SNAPSHOT DIGEST"
_TABLES_FOR_COUNTS = (
    "snapshot_features", "level_events", "microstructure_events",
    "trigger_events", "ai_turns", "trade_outcomes",
)


def _utc_day_bounds_ms(date_str: str) -> Tuple[int, int]:
    """Return (start_ms, end_ms) for the UTC day.

    end_ms is the start of the following day (midnight boundary), and queries
    use <= so that rows stamped at exactly midnight are captured in the earlier
    day's report.
    """
    d = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    return (
        int(d.timestamp() * 1000),
        int((d + _dt.timedelta(days=1)).timestamp() * 1000),
    )


def _resolve_paths(args) -> Tuple[Path, Path, Path, Path]:
    db   = args.db   or os.environ.get("PAX_AI_BUS_DB")
    snap = args.snap or os.environ.get("PAX_AI_SNAP_DIR")
    dig  = args.dig  or os.environ.get("PAX_AI_DIGEST_DIR")
    rpt  = args.report or os.environ.get("PAX_AI_REPORT")
    if not (db and snap and dig):
        try:
            from pax_ai import config as pax_cfg
            db   = db   or str(pax_cfg.get("feature_bus.db_path") or "")
            snap = snap or str(pax_cfg.get("feature_bus.snapshot_blob_dir") or "")
            dig  = dig  or str(pax_cfg.get("feature_bus.digest_blob_dir")   or "")
        except ImportError:
            pass
    if not rpt:
        today = args.date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
        rpt = f"reports/bus-eod-{today}.md"
    return Path(db or ""), Path(snap or ""), Path(dig or ""), Path(rpt)


def _avoid_clobber(p: Path) -> Path:
    if not p.exists():
        return p
    stem   = p.stem
    suffix = p.suffix
    parent = p.parent
    n = 1
    while True:
        cand = parent / f"{stem}_{n}{suffix}"
        if not cand.exists():
            return cand
        n += 1


def _classify_digest(dig_dir: Path, date: str, digest_sha: str) -> str:
    fp = dig_dir / date / f"{digest_sha}.txt"
    if not fp.exists():
        return "missing_blob"
    try:
        body = fp.read_text(encoding="utf-8")
    except OSError:
        return "missing_blob"
    if all(m in body for m in _BUS_MARKERS):
        return "bus"
    if _LEGACY_MARKER in body:
        return "legacy"
    return "unknown"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pax_bus_eod")
    ap.add_argument("--date",          default=None)
    ap.add_argument("--alias",         default=None)
    ap.add_argument("--verify-replay", action="store_true")
    ap.add_argument("--db",            default=None)
    ap.add_argument("--snap",          default=None)
    ap.add_argument("--dig",           default=None)
    ap.add_argument("--report",        default=None)
    args = ap.parse_args(argv)
    args.date = args.date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")

    start_ms, end_ms = _utc_day_bounds_ms(args.date)
    db_path, snap_dir, dig_dir, report_path = _resolve_paths(args)
    report_path = _avoid_clobber(report_path)

    lines: List[str] = [f"# pax_bus_eod {args.date} (UTC day)", ""]

    if not db_path.exists():
        lines += ["No bus DB at the configured path; nothing to report.", ""]
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return 0

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # ---- counts --------------------------------------------------------
        lines.append("## Counts")
        for t in _TABLES_FOR_COUNTS:
            try:
                if t == "trade_outcomes":
                    # trade_outcomes has no ts_ms; count via ai_turn join.
                    n = conn.execute(
                        "SELECT COUNT(*) FROM trade_outcomes to_ "
                        "JOIN ai_turns at_ ON to_.ai_turn_id=at_.id "
                        "WHERE at_.ts_ms>=? AND at_.ts_ms<=?",
                        (start_ms, end_ms),
                    ).fetchone()[0]
                else:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE ts_ms>=? AND ts_ms<=?",
                        (start_ms, end_ms),
                    ).fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
            lines.append(f"  - {t:25s} {n}")
        lines.append("")

        # ---- ai_turns query ------------------------------------------------
        sql = (
            "SELECT id, ts_ms, model, router_primary, elapsed_ms, "
            "total_cost_usd, snapshot_sha256, digest_sha256, snapshot_alias "
            "FROM ai_turns WHERE ts_ms>=? AND ts_ms<=?"
        )
        params: List[Any] = [start_ms, end_ms]
        if args.alias:
            sql += " AND snapshot_alias=?"
            params.append(args.alias)
        sql += " ORDER BY ts_ms ASC"
        ai_rows = conn.execute(sql, params).fetchall()

        # ---- turns ---------------------------------------------------------
        lines.append("## Turns")
        lines.append(f"  - total          : {len(ai_rows)}")
        by_model:  Dict[str, int] = {}
        by_router: Dict[str, int] = {}
        cost_sum = 0.0
        elapsed_vals: List[int] = []
        for r in ai_rows:
            mk = r["model"] or "(none)"
            rk = r["router_primary"] or "(none)"
            by_model[mk]  = by_model.get(mk, 0) + 1
            by_router[rk] = by_router.get(rk, 0) + 1
            if r["total_cost_usd"] is not None:
                cost_sum += float(r["total_cost_usd"])
            if r["elapsed_ms"] is not None:
                elapsed_vals.append(int(r["elapsed_ms"]))
        lines.append("  by model:")
        for k, v in sorted(by_model.items()):
            lines.append(f"    - {k:25s} {v}")
        lines.append("  by router_primary:")
        for k, v in sorted(by_router.items()):
            lines.append(f"    - {k:25s} {v}")
        lines.append(f"  - total_cost_usd : {cost_sum:.4f}")
        if elapsed_vals:
            elapsed_vals.sort()
            mean = sum(elapsed_vals) / len(elapsed_vals)
            med  = statistics.median(elapsed_vals)
            p95  = elapsed_vals[int(0.95 * (len(elapsed_vals) - 1))]
            lines.append(
                f"  - elapsed_ms     : mean={mean:.0f} median={med:.0f} p95={p95}"
            )
        else:
            lines.append("  - elapsed_ms     : n/a")
        lines.append("")

        # ---- verdicts ------------------------------------------------------
        lines.append("## Verdicts (Phase 4A heuristic placeholder)")
        try:
            v_rows = conn.execute(
                "SELECT to_.verdict, COUNT(*) AS n "
                "FROM trade_outcomes to_ "
                "JOIN ai_turns at_ ON to_.ai_turn_id=at_.id "
                "WHERE at_.ts_ms>=? AND at_.ts_ms<=? "
                "GROUP BY to_.verdict ORDER BY n DESC",
                (start_ms, end_ms),
            ).fetchall()
        except sqlite3.OperationalError:
            v_rows = []
        if v_rows:
            for r in v_rows:
                lines.append(f"  - {r['verdict']:18s} {r['n']}")
        else:
            lines.append("  (no outcomes for this day)")
        lines.append("")

        # ---- mid drift -----------------------------------------------------
        lines.append("## Mid drift (ENTER_LONG / ENTER_SHORT)")
        drift_cols = [
            ("t+60s",  "mid_at_t60s"),
            ("t+180s", "mid_at_t180s"),
            ("t+300s", "mid_at_t300s"),
            ("t+900s", "mid_at_t900s"),
        ]
        for verdict in ("ENTER_LONG", "ENTER_SHORT"):
            outcome_rows = conn.execute(
                "SELECT to_.mid_at_t0, to_.mid_at_t60s, to_.mid_at_t180s, "
                "to_.mid_at_t300s, to_.mid_at_t900s "
                "FROM trade_outcomes to_ "
                "JOIN ai_turns at_ ON to_.ai_turn_id=at_.id "
                "WHERE at_.ts_ms>=? AND at_.ts_ms<=? AND to_.verdict=?",
                (start_ms, end_ms, verdict),
            ).fetchall()
            lines.append(f"  {verdict}:")
            for label, col in drift_cols:
                deltas = [
                    r[col] - r["mid_at_t0"]
                    for r in outcome_rows
                    if r["mid_at_t0"] is not None and r[col] is not None
                ]
                if deltas:
                    avg = sum(deltas) / len(deltas)
                    lines.append(
                        f"    - {label}: mean delta = {avg:+.4f} (n={len(deltas)})"
                    )
                else:
                    lines.append(f"    - {label}: n/a")
        lines.append("")

        # ---- format breakdown (content-based) ------------------------------
        lines.append("## Format breakdown (content-based)")
        format_buckets: Dict[str, list] = {
            "bus": [], "legacy": [], "missing_blob": [], "unknown": [],
        }
        for r in ai_rows:
            fmt = _classify_digest(dig_dir, args.date, r["digest_sha256"])
            format_buckets[fmt].append(r)
        for k in ("bus", "legacy", "missing_blob", "unknown"):
            lines.append(f"  - format={k:14s} {len(format_buckets[k])}")
        lines.append("")

        # ---- replay verification (bus-only) --------------------------------
        if args.verify_replay:
            lines.append("## Replay verification (format=bus only)")
            bus_rows = format_buckets["bus"]
            if not bus_rows:
                lines.append("  (no bus-format rows on this day)")
            else:
                try:
                    from .pax_bus_replay import _rebuild_digest, _load_snapshot_blob
                except ImportError:
                    from bookmap_mcp.pax_bus_replay import (  # type: ignore[no-redef]
                        _rebuild_digest, _load_snapshot_blob,
                    )
                matched = 0
                mismatched: List[Dict[str, Any]] = []
                for r in bus_rows:
                    snap = _load_snapshot_blob(
                        snap_dir, args.date, r["snapshot_sha256"]
                    )
                    if snap is None:
                        # Bus-format but missing snapshot blob — skip, not a mismatch.
                        continue
                    ut = conn.execute(
                        "SELECT user_text_raw, user_text_normalized, snapshot_alias "
                        "FROM ai_turns WHERE id=?",
                        (r["id"],),
                    ).fetchone()
                    user_text_raw  = (ut["user_text_raw"]        if ut else "") or ""
                    user_text_norm = (ut["user_text_normalized"]  if ut else None)
                    alias          = (ut["snapshot_alias"]        if ut else "") or ""
                    try:
                        rebuilt = _rebuild_digest(
                            snap=snap,
                            user_text_raw=user_text_raw,
                            user_text_normalized=user_text_norm,
                            alias=alias,
                            ts_ms=r["ts_ms"],
                        )
                    except Exception as exc:
                        mismatched.append({
                            "id": r["id"], "ts_ms": r["ts_ms"],
                            "reason": f"rebuild failed: {exc}",
                        })
                        continue
                    rebuilt_sha = hashlib.sha256(rebuilt.encode("utf-8")).hexdigest()
                    if rebuilt_sha == r["digest_sha256"]:
                        matched += 1
                    else:
                        mismatched.append({
                            "id":          r["id"],
                            "ts_ms":       r["ts_ms"],
                            "stored_sha":  r["digest_sha256"][:12],
                            "rebuilt_sha": rebuilt_sha[:12],
                        })
                lines.append(f"  - total bus rows : {len(bus_rows)}")
                lines.append(f"  - matched        : {matched}")
                lines.append(f"  - mismatched     : {len(mismatched)}")
                if mismatched:
                    lines.append("")
                    lines.append("### Mismatched bus rows")
                    for m in mismatched:
                        lines.append(
                            f"  - id={m['id']} ts_ms={m['ts_ms']} {m}"
                        )
            lines.append("")
    finally:
        conn.close()

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
