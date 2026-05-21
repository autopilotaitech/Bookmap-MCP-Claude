"""Phase 5A tuning report CLI (READ-ONLY ADVISORY).

Reads ai_turns + trade_outcomes + snapshot_features + trigger_events for
a UTC day (or trailing --days N window), classifies ai_turns by digest
blob content, computes per-kind trigger counts and per-source Spearman
rank correlations vs realized_r_at_t300s, and writes an advisory JSON.

This module:
  - NEVER mutates pax_weights.json or any settings file
  - NEVER writes to the bus DB
  - NEVER calls feature_bus writer-path functions
  - NEVER imports prompts.py / claude_stream.py / chat / triggers /
    journal / edge_calculus / voice / outcomes

Usage:
  python -m bookmap_mcp.pax_bus_tune
  python -m bookmap_mcp.pax_bus_tune --date 2026-05-19
  python -m bookmap_mcp.pax_bus_tune --days 7 --min-samples 5
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


_BUS_MARKERS = (
    "[STATE]", "[ANCHOR]", "[GATES]", "[LEVELS]",
    "[MICROSTRUCTURE]", "[RECENT_EVENTS]", "[POSITION]",
    "[SESSION_MEMORY]", "[USER]",
)
_LEGACY_MARKER = "SNAPSHOT DIGEST"

_NUMERIC_SOURCES = (
    "conviction_score", "flow_bias_score", "vwap_sigma_z",
    "momentum_i10", "momentum_i50", "momentum_i200",
    "pax_confidence",
)

_DEFAULT_MIN_SAMPLES = 5
_WEIGHT_DELTA_CAP    = 0.10
_ADVISORY_PREAMBLE   = (
    "advisory only - operator must edit pax_weights.json manually; "
    "this tool never writes settings"
)


# ---------------------------------------------------------------------------
# Math primitives (exposed for unit tests)
# ---------------------------------------------------------------------------

def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    """Spearman rank correlation. Returns 0.0 if n<2 or either side has
    zero variance after ranking. Handles ties via average ranks."""
    if len(xs) != len(ys) or len(xs) < 2:
        return 0.0
    rx = _rank(xs)
    ry = _rank(ys)
    n = len(rx)
    mean_x = sum(rx) / n
    mean_y = sum(ry) / n
    num = 0.0
    sx = 0.0
    sy = 0.0
    for a, b in zip(rx, ry):
        da = a - mean_x
        db = b - mean_y
        num += da * db
        sx  += da * da
        sy  += db * db
    if sx == 0.0 or sy == 0.0:
        return 0.0
    return num / (sx * sy) ** 0.5


def _rank(values: Sequence[float]) -> List[float]:
    """Average-rank for ties."""
    indexed = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0   # 1-based
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg
        i = j + 1
    return ranks


def _clamp_weight_delta(raw: float) -> float:
    """Bound an advisory weight delta to +/- _WEIGHT_DELTA_CAP."""
    if raw >  _WEIGHT_DELTA_CAP: return  _WEIGHT_DELTA_CAP
    if raw < -_WEIGHT_DELTA_CAP: return -_WEIGHT_DELTA_CAP
    return raw


# ---------------------------------------------------------------------------
# Time + path helpers
# ---------------------------------------------------------------------------

def _utc_day_bounds_ms(date_str: str) -> Tuple[int, int]:
    """Half-open [start_ms, end_ms) for one UTC day. Matches pax_bus_eod."""
    d = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    return (int(d.timestamp() * 1000),
            int((d + _dt.timedelta(days=1)).timestamp() * 1000))


def _window_bounds_ms(date_str: str, days: int) -> Tuple[int, int]:
    """For --days N anchored on date_str, returns [start_of_(date - N + 1), end_of_date)."""
    end_d = _dt.datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
    start_d = end_d - _dt.timedelta(days=days - 1)
    return (int(start_d.timestamp() * 1000),
            int((end_d + _dt.timedelta(days=1)).timestamp() * 1000))


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
        rpt = f"reports/tune-recommendations-{today}.json"
    return Path(db or ""), Path(snap or ""), Path(dig or ""), Path(rpt)


def _avoid_clobber(p: Path) -> Path:
    if not p.exists():
        return p
    n = 1
    while True:
        cand = p.parent / f"{p.stem}_{n}{p.suffix}"
        if not cand.exists():
            return cand
        n += 1


def _classify_digest(dig_dir: Path, ts_ms: int, digest_sha: str) -> str:
    """Bus/legacy/missing_blob/unknown classifier. Mirrors pax_bus_eod.
    Date partition is derived from ts_ms (UTC)."""
    date = _dt.datetime.fromtimestamp(ts_ms / 1000.0,
                                       _dt.timezone.utc).strftime("%Y-%m-%d")
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


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pax_bus_tune")
    ap.add_argument("--date",        default=None,
                     help="Anchor date (UTC, YYYY-MM-DD); default = today UTC")
    ap.add_argument("--days",        type=int, default=1,
                     help="Trailing window length in days (default 1)")
    ap.add_argument("--alias",       default=None)
    ap.add_argument("--min-samples", type=int, default=_DEFAULT_MIN_SAMPLES,
                     help=f"Suppress classes below this N (default {_DEFAULT_MIN_SAMPLES})")
    ap.add_argument("--db",          default=None)
    ap.add_argument("--snap",        default=None)
    ap.add_argument("--dig",         default=None)
    ap.add_argument("--report",      default=None)
    args = ap.parse_args(argv)

    if args.days < 1:
        sys.stderr.write(f"[tune] refusing --days={args.days}; must be >= 1\n")
        return 2
    if args.min_samples < 1:
        sys.stderr.write(f"[tune] refusing --min-samples={args.min_samples}; must be >= 1\n")
        return 2

    args.date = args.date or _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    start_ms, end_ms = _window_bounds_ms(args.date, args.days)
    db_path, snap_dir, dig_dir, report_path = _resolve_paths(args)
    report_path = _avoid_clobber(report_path)

    notes: List[str] = [_ADVISORY_PREAMBLE]
    payload: Dict[str, Any] = {
        "generated_ms":         int(time.time() * 1000),
        "anchor_date":          args.date,
        "days":                 args.days,
        "min_samples":          args.min_samples,
        "window":               {"start_ms": start_ms, "end_ms": end_ms},
        "format_breakdown":     {"bus": 0, "legacy": 0,
                                  "missing_blob": 0, "unknown": 0},
        "trigger_stats":        [],
        "source_correlations":  [],
        "advisory_weight_deltas": [],
        "notes":                notes,
    }

    if not db_path.exists():
        notes.append(f"no bus DB at {db_path}; nothing to report")
        _write_report(report_path, payload)
        return 0

    conn = sqlite3.connect(str(db_path), timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        # ----- ai_turns + format classification ----------------------------
        sql_at = (
            "SELECT id, ts_ms, snapshot_sha256, digest_sha256, snapshot_alias "
            "FROM ai_turns WHERE ts_ms>=? AND ts_ms<?"
        )
        params: List[Any] = [start_ms, end_ms]
        if args.alias:
            sql_at += " AND snapshot_alias=?"
            params.append(args.alias)
        sql_at += " ORDER BY ts_ms ASC"
        ai_rows = conn.execute(sql_at, params).fetchall()

        bus_ids: List[int] = []
        bus_snap_shas: List[str] = []
        for r in ai_rows:
            fmt = _classify_digest(dig_dir, r["ts_ms"], r["digest_sha256"])
            payload["format_breakdown"][fmt] += 1
            if fmt == "bus":
                bus_ids.append(int(r["id"]))
                bus_snap_shas.append(r["snapshot_sha256"])

        # ----- trigger stats ------------------------------------------------
        sql_tr = (
            "SELECT kind, COUNT(*) AS n "
            "FROM trigger_events WHERE ts_ms>=? AND ts_ms<? "
        )
        tparams: List[Any] = [start_ms, end_ms]
        if args.alias:
            sql_tr += "AND alias=? "
            tparams.append(args.alias)
        sql_tr += "GROUP BY kind ORDER BY n DESC"
        try:
            trig_rows = conn.execute(sql_tr, tparams).fetchall()
        except sqlite3.OperationalError:
            trig_rows = []
        for tr in trig_rows:
            if int(tr["n"]) < args.min_samples:
                continue
            payload["trigger_stats"].append({
                "kind":  tr["kind"],
                "count": int(tr["n"]),
            })
        if not trig_rows:
            notes.append("no trigger_events in window")

        # ----- source correlations -----------------------------------------
        if not bus_ids:
            notes.append("no bus-format ai_turns in window")
        else:
            pairs_by_source: Dict[str, List[Tuple[float, float]]] = {
                s: [] for s in _NUMERIC_SOURCES
            }
            placeholders = ",".join("?" * len(bus_ids))
            sql_outs = (
                "SELECT to_.ai_turn_id, to_.realized_r_at_t300s, "
                "       at_.snapshot_sha256 "
                "FROM trade_outcomes to_ "
                "JOIN ai_turns at_ ON to_.ai_turn_id=at_.id "
                f"WHERE to_.ai_turn_id IN ({placeholders}) "
                "  AND to_.realized_r_at_t300s IS NOT NULL"
            )
            outcome_rows = conn.execute(sql_outs, bus_ids).fetchall()
            if not outcome_rows:
                notes.append("no bus rows with non-null realized_r_at_t300s "
                              "outcomes")
            else:
                outcomes_by_snap_sha: Dict[str, float] = {
                    r["snapshot_sha256"]: float(r["realized_r_at_t300s"])
                    for r in outcome_rows
                }
                if outcomes_by_snap_sha:
                    snap_placeholders = ",".join(
                        "?" * len(outcomes_by_snap_sha))
                    snap_sql = (
                        "SELECT raw_json_sha256, "
                        + ", ".join(_NUMERIC_SOURCES) +
                        " FROM snapshot_features "
                        f"WHERE raw_json_sha256 IN ({snap_placeholders})"
                    )
                    snap_rows = conn.execute(
                        snap_sql, list(outcomes_by_snap_sha.keys())
                    ).fetchall()
                    for sr in snap_rows:
                        r_val = outcomes_by_snap_sha.get(sr["raw_json_sha256"])
                        if r_val is None:
                            continue
                        for s in _NUMERIC_SOURCES:
                            v = sr[s]
                            if v is None:
                                continue
                            pairs_by_source[s].append((float(v), r_val))

                for s, pairs in pairs_by_source.items():
                    n = len(pairs)
                    if n < args.min_samples:
                        continue
                    xs = [p[0] for p in pairs]
                    ys = [p[1] for p in pairs]
                    rho = _spearman(xs, ys)
                    wins   = [y for y in ys if y > 0]
                    losses = [y for y in ys if y <= 0]
                    mean_x_win  = (sum(p[0] for p in pairs if p[1] > 0)
                                    / len(wins)) if wins else None
                    mean_x_loss = (sum(p[0] for p in pairs if p[1] <= 0)
                                    / len(losses)) if losses else None
                    payload["source_correlations"].append({
                        "source":       s,
                        "n_samples":    n,
                        "spearman":     round(rho, 6),
                        "mean_when_win":  (round(mean_x_win, 6)
                                            if mean_x_win is not None else None),
                        "mean_when_loss": (round(mean_x_loss, 6)
                                            if mean_x_loss is not None else None),
                    })
                    if abs(rho) >= 0.3:
                        delta = _clamp_weight_delta(rho * _WEIGHT_DELTA_CAP)
                        payload["advisory_weight_deltas"].append({
                            "source":              s,
                            "suggested_delta_pct": round(delta, 6),
                            "n_samples":           n,
                            "spearman":            round(rho, 6),
                            "rationale": (
                                f"spearman={rho:+.3f} vs realized_r_at_t300s "
                                f"over n={n}; delta clamped to "
                                f"+/-{_WEIGHT_DELTA_CAP*100:.0f}% (advisory)"
                            ),
                        })

    finally:
        conn.close()

    _write_report(report_path, payload)
    return 0


def _write_report(report_path: Path, payload: Dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
