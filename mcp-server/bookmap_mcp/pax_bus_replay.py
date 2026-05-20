"""Phase 4A bus replay CLI.

Streams ai_turns for one UTC day, loads the snapshot blob by
snapshot_sha256, rebuilds the bus digest via pax_ai.bus_digest.render_user_message,
and asserts SHA equality against ai_turns.digest_sha256.

Outputs a markdown report. Exit code is non-zero on any mismatch or missing blob.

Usage:
  python -m bookmap_mcp.pax_bus_replay --date 2026-01-15
  python -m bookmap_mcp.pax_bus_replay --date 2026-01-15 --alias NQM6
  python -m bookmap_mcp.pax_bus_replay --date 2026-01-15 --report custom.md

For test isolation: PAX_AI_BUS_DB / PAX_AI_SNAP_DIR / PAX_AI_DIGEST_DIR /
PAX_AI_REPORT env vars override the corresponding pax_ai config values.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def _utc_day_bounds_ms(date_str: str) -> Tuple[int, int]:
    d = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _resolve_paths(args) -> Tuple[Path, Path, Path, Path]:
    """Returns (db_path, snap_dir, dig_dir, report_path).

    Precedence: CLI flags > PAX_AI_* env vars > pax_ai.config defaults.
    """
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
    rpt = rpt or f"bus-replay-{args.date}.md"
    return Path(db or ""), Path(snap or ""), Path(dig or ""), Path(rpt)


def _load_snapshot_blob(snap_dir: Path, date_str: str,
                         snap_sha256: str) -> Optional[dict]:
    fp = snap_dir / date_str / f"{snap_sha256}.json"
    if not fp.exists():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _rebuild_digest(snap: dict, user_text: str, alias: str,
                     ts_ms: int, router_primary: Optional[str]) -> str:
    """Rebuild the bus digest from a saved snapshot blob.

    Uses session_memory_before_ts_ms=ts_ms so that the SESSION_MEMORY block
    contains exactly the prior turns the capture-time chat.py saw (rows
    with ts_ms strictly less than the current ai_turn's ts_ms). Capture-time
    chat.py did not pass this kwarg because the current turn wasn't in the
    DB yet; both paths therefore see the same set of session-memory rows."""
    from pax_ai import bus_digest
    router_hint = f"ROUTER: {router_primary}" if router_primary else ""
    return bus_digest.render_user_message(
        snap=snap, user_text=user_text or "",
        router_hint=router_hint, alias=alias, ts_ms=ts_ms,
        session_memory_before_ts_ms=ts_ms,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pax_bus_replay")
    ap.add_argument("--date", required=True, help="UTC day YYYY-MM-DD")
    ap.add_argument("--alias", default=None, help="Optional alias filter")
    ap.add_argument("--db",   default=None, help="Override db_path")
    ap.add_argument("--snap", default=None, help="Override snapshot_blob_dir")
    ap.add_argument("--dig",  default=None, help="Override digest_blob_dir")
    ap.add_argument("--report", default=None, help="Markdown report output path")
    args = ap.parse_args(argv)

    start_ms, end_ms = _utc_day_bounds_ms(args.date)
    db_path, snap_dir, dig_dir, report_path = _resolve_paths(args)

    matched = 0
    mismatched: List[dict] = []
    missing_blob: List[dict] = []

    if not db_path.exists():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            f"# pax_bus_replay {args.date}\n\n"
            f"No bus DB at `{db_path}`. 0 rows replayed.\n",
            encoding="utf-8")
        sys.stderr.write(f"[pax_bus_replay] no DB at {db_path}\n")
        return 0

    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[pax_bus_replay] DB open failed: {exc}\n")
        return 2

    try:
        sql = ("SELECT id, ts_ms, snapshot_alias, snapshot_sha256, "
               "digest_sha256, user_text_raw, router_primary "
               "FROM ai_turns WHERE ts_ms>=? AND ts_ms<?")
        params: list = [start_ms, end_ms]
        if args.alias:
            sql += " AND snapshot_alias=?"
            params.append(args.alias)
        sql += " ORDER BY ts_ms ASC"
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    for r in rows:
        snap = _load_snapshot_blob(snap_dir, args.date, r["snapshot_sha256"])
        if snap is None:
            missing_blob.append({"id": r["id"], "ts_ms": r["ts_ms"],
                                  "snapshot_sha256": r["snapshot_sha256"]})
            continue
        try:
            rebuilt = _rebuild_digest(
                snap=snap, user_text=r["user_text_raw"] or "",
                alias=r["snapshot_alias"] or "",
                ts_ms=r["ts_ms"], router_primary=r["router_primary"])
        except Exception as exc:
            mismatched.append({"id": r["id"], "ts_ms": r["ts_ms"],
                                "reason": f"rebuild failed: {exc}"})
            continue
        rebuilt_sha = hashlib.sha256(rebuilt.encode("utf-8")).hexdigest()
        if rebuilt_sha == r["digest_sha256"]:
            matched += 1
        else:
            mismatched.append({"id": r["id"], "ts_ms": r["ts_ms"],
                                "stored_sha": r["digest_sha256"][:12],
                                "rebuilt_sha": rebuilt_sha[:12]})

    total = matched + len(mismatched) + len(missing_blob)
    lines: List[str] = [
        f"# pax_bus_replay {args.date}",
        "",
        f"- Total ai_turns rows: {total}",
        f"- Matched:             {matched}",
        f"- Mismatched:          {len(mismatched)}",
        f"- Missing blob:        {len(missing_blob)}",
        "",
    ]
    if mismatched:
        lines.append("## Mismatched")
        for m in mismatched:
            lines.append(f"  - id={m['id']} ts_ms={m['ts_ms']} {m}")
        lines.append("")
    if missing_blob:
        lines.append("## Missing snapshot blob")
        for m in missing_blob:
            lines.append(f"  - id={m['id']} ts_ms={m['ts_ms']} "
                          f"snapshot_sha256={m['snapshot_sha256'][:12]}")
        lines.append("")
    if total == 0:
        lines.append("(no rows for this date)")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    if mismatched or missing_blob:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
