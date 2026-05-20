"""Phase 4B retention pruner CLI.

Default mode is DRY-RUN. Must pass --yes to actually delete. Refuses to
run with --days < 1. Deletes rows older than cutoff from 5 high-volume
tables, leaving trade_outcomes untouched for orphan-tolerant forensics.
Deletes blob date-partition dirs older than cutoff (best-effort).

Usage:
  python -m bookmap_mcp.pax_bus_prune --days 30          (dry-run)
  python -m bookmap_mcp.pax_bus_prune --days 30 --yes   (live)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple


_TABLES_TO_PRUNE = (
    "snapshot_features",
    "level_events",
    "microstructure_events",
    "trigger_events",
    "ai_turns",
)


def _resolve_paths(args) -> Tuple[Path, Path, Path, Path, int]:
    """Returns (db, snap_dir, dig_dir, report, retention_days_from_config).
    Precedence: CLI flag -> PAX_AI_* env var -> pax_ai.config default."""
    db   = args.db   or os.environ.get("PAX_AI_BUS_DB")
    snap = args.snap or os.environ.get("PAX_AI_SNAP_DIR")
    dig  = args.dig  or os.environ.get("PAX_AI_DIGEST_DIR")
    rpt  = args.report or os.environ.get("PAX_AI_REPORT")
    retention = 30
    try:
        from pax_ai import config as pax_cfg
        db   = db   or str(pax_cfg.get("feature_bus.db_path") or "")
        snap = snap or str(pax_cfg.get("feature_bus.snapshot_blob_dir") or "")
        dig  = dig  or str(pax_cfg.get("feature_bus.digest_blob_dir")   or "")
        try:
            retention = int(pax_cfg.get("feature_bus.retention_days", 30) or 30)
        except (TypeError, ValueError):
            retention = 30
    except ImportError:
        pass
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    rpt = rpt or f"reports/bus-prune-{today}.md"
    return Path(db or ""), Path(snap or ""), Path(dig or ""), Path(rpt), retention


def _count_old_rows(conn: sqlite3.Connection, table: str,
                     cutoff_ms: int) -> int:
    try:
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE ts_ms < ?",
            (cutoff_ms,)).fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def _delete_old_rows(conn: sqlite3.Connection, table: str,
                      cutoff_ms: int) -> int:
    try:
        cur = conn.execute(
            f"DELETE FROM {table} WHERE ts_ms < ?", (cutoff_ms,))
        return int(cur.rowcount)
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[prune] delete {table} failed: {exc}\n")
        return 0


def _enumerate_old_blob_dirs(root: Path, cutoff_ms: int) -> List[Path]:
    """Date-named subdirs (YYYY-MM-DD) older than cutoff."""
    if not root.exists() or not root.is_dir():
        return []
    cutoff_date = _dt.datetime.fromtimestamp(
        cutoff_ms / 1000.0, _dt.timezone.utc).date()
    out: List[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            d = _dt.datetime.strptime(child.name, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d < cutoff_date:
            out.append(child)
    return out


def _delete_dir(p: Path) -> bool:
    try:
        shutil.rmtree(p)
        return True
    except OSError as exc:
        sys.stderr.write(f"[prune] failed to delete {p}: {exc}\n")
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pax_bus_prune")
    ap.add_argument("--days", type=int, default=None,
                     help="Retention window in days (default: feature_bus.retention_days)")
    ap.add_argument("--yes", action="store_true",
                     help="Actually delete (default: dry-run)")
    ap.add_argument("--db",   default=None)
    ap.add_argument("--snap", default=None)
    ap.add_argument("--dig",  default=None)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    db_path, snap_dir, dig_dir, report_path, retention_cfg = _resolve_paths(args)
    days = args.days if args.days is not None else retention_cfg
    if days is None or days < 1:
        sys.stderr.write(
            f"[prune] refusing to run with days={days}; must be >= 1\n")
        return 2

    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - days * 86_400_000
    cutoff_iso = _dt.datetime.fromtimestamp(
        cutoff_ms / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    mode = "live" if args.yes else "dry-run"
    sys.stderr.write(f"[prune] mode={mode} days={days} cutoff={cutoff_iso}\n")

    per_table: dict = {}
    blob_dirs_to_delete: List[Path] = []
    blob_dirs_deleted:   List[Path] = []
    vacuum_freed_bytes:  Optional[int] = None

    if not db_path.exists():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            f"# pax_bus_prune {cutoff_iso}\n\n"
            f"mode: {mode}\n\nNo DB at `{db_path}`; nothing to prune.\n",
            encoding="utf-8")
        return 0

    try:
        conn = sqlite3.connect(str(db_path), timeout=5.0)
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[prune] DB open failed: {exc}\n")
        return 2

    try:
        # Count first (used by both modes for the report).
        for t in _TABLES_TO_PRUNE:
            per_table[t] = _count_old_rows(conn, t, cutoff_ms)

        if args.yes:
            with conn:
                for t in _TABLES_TO_PRUNE:
                    per_table[t] = _delete_old_rows(conn, t, cutoff_ms)
            # blob deletion (best-effort)
            for d in _enumerate_old_blob_dirs(snap_dir, cutoff_ms):
                if _delete_dir(d):
                    blob_dirs_deleted.append(d)
            for d in _enumerate_old_blob_dirs(dig_dir, cutoff_ms):
                if _delete_dir(d):
                    blob_dirs_deleted.append(d)
            # VACUUM (live only)
            try:
                before_bytes = db_path.stat().st_size
                conn.execute("PRAGMA optimize")
                conn.execute("VACUUM")
                after_bytes = db_path.stat().st_size
                vacuum_freed_bytes = before_bytes - after_bytes
            except sqlite3.OperationalError as exc:
                sys.stderr.write(f"[prune] VACUUM failed: {exc}\n")
        else:
            # Dry-run: enumerate would-delete blob dirs.
            blob_dirs_to_delete = (_enumerate_old_blob_dirs(snap_dir, cutoff_ms)
                                    + _enumerate_old_blob_dirs(dig_dir, cutoff_ms))
    finally:
        conn.close()

    # ---- write report --------------------------------------------------
    lines: List[str] = [
        f"# pax_bus_prune {cutoff_iso}",
        "",
        f"- mode:               **{mode}**",
        f"- days:               {days}",
        f"- cutoff_ms:          {cutoff_ms}",
        f"- cutoff_iso_utc:     {cutoff_iso}",
        "",
    ]
    label = "deleted" if args.yes else "would delete"
    lines.append("## Rows " + label)
    for t in _TABLES_TO_PRUNE:
        lines.append(f"  - {t:25s} {per_table[t]}")
    lines.append("  - trade_outcomes           (preserved; not pruned)")
    lines.append("")
    lines.append("## Blob date partitions " + label)
    if args.yes:
        if blob_dirs_deleted:
            for d in blob_dirs_deleted:
                lines.append(f"  - {d}")
        else:
            lines.append("  (none)")
    else:
        if blob_dirs_to_delete:
            for d in blob_dirs_to_delete:
                lines.append(f"  - {d}")
        else:
            lines.append("  (none)")
    lines.append("")
    lines.append("## VACUUM")
    if args.yes:
        if vacuum_freed_bytes is not None:
            lines.append(f"  freed {vacuum_freed_bytes} bytes")
        else:
            lines.append("  attempted, but failed (see stderr)")
    else:
        lines.append("  (skipped - dry-run)")
    lines.append("")

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
