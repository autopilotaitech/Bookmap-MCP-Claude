"""Phase 5: turn-level audit report (read-only).

Consumes the Phase 1-4 capture surface and answers, for one operator-chosen
UTC window:

  *What did Pax AI see, what exact prompt + model did it use, what did it
   say, what structured forecast did it emit, and what happened next?*

Inputs:
  - feature_bus DB (ai_turns + trade_outcomes)
  - forecast store DB (forecasts; optional)
  - prompt archive root (optional; verified by re-hashing the on-disk bytes)

Output:
  - one row per ai_turn in the half-open ``[start_ms, end_ms)`` window
  - a summary block with seven count fields the operator can spot-check at
    a glance

Hard rules baked into this module:
  - Read-only relative to every input. The bus DB and forecast DB are opened
    with SQLite ``mode=ro``; archive files are read via ``Path.read_bytes``.
    Nothing here writes, ALTERs, or deletes.
  - Never imports broker, order, or live-trading paths.
  - Never touches ``pax_ai_config.json``, ``pax_weights.json``, prompts, or
    the policy promotion gate.

The CLI is a thin wrapper that resolves the window from ``--date`` or
``--start-ms/--end-ms`` and writes the JSON report to ``--report``.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1


# Columns the forecast index needs. If any are absent on the on-disk
# ``forecasts`` table (legacy / wrong DB), the index is treated as empty
# and the audit reports every turn as ``forecast_status=MISSING`` -- we
# never ALTER the forecast DB to add them.
_REQUIRED_FORECAST_COLUMNS = (
    "chat_run_id", "digest_sha256", "ts_ms", "alias",
    "forecast_id", "execution_read", "direction",
    "horizon_sec", "prob_success", "expected_r",
)


# --------------------------------------------------------- time / windows

def utc_day_window(date_utc: str) -> Tuple[int, int]:
    """Half-open ``[start_ms, end_ms)`` for the UTC day ``date_utc``."""
    try:
        d = _dt.datetime.strptime(date_utc, "%Y-%m-%d").replace(
            tzinfo=_dt.timezone.utc)
    except ValueError as exc:
        raise ValueError(f"date_utc must be YYYY-MM-DD: {date_utc}") from exc
    start = int(d.timestamp() * 1000)
    return start, start + 86_400_000


# ----------------------------------------------------------- DB readers

_AI_TURN_COLS = (
    "id", "ts_ms", "chat_run_id", "model",
    "user_text_normalized", "pax_text",
    "snapshot_alias", "snapshot_sha256", "digest_sha256",
    "prompt_sha256", "prompt_version", "model_release_id",
    "skill_bundle_sha256", "prompt_archive_path",
)


def _open_bus_readonly(bus_db_path: Path) -> Optional[sqlite3.Connection]:
    if not Path(bus_db_path).exists():
        return None
    uri = f"file:{Path(bus_db_path).resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0,
                                isolation_level=None,
                                check_same_thread=False)
    except sqlite3.Error:
        return None
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_ai_turns(conn: sqlite3.Connection, *,
                     start_ms: int, end_ms: int,
                     alias: Optional[str]) -> List[Dict[str, Any]]:
    cols_sql = ", ".join(_AI_TURN_COLS)
    sql = (f"SELECT {cols_sql} FROM ai_turns "
            "WHERE ts_ms >= ? AND ts_ms < ?")
    params: List[Any] = [int(start_ms), int(end_ms)]
    if alias:
        sql += " AND snapshot_alias = ?"
        params.append(alias)
    sql += " ORDER BY ts_ms ASC, id ASC"
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


_OUTCOME_COLS_BY_TURN = (
    "verdict", "invalidated", "invalidation_reason",
    "realized_r_at_t60s", "realized_r_at_t180s",
    "realized_r_at_t300s", "realized_r_at_t900s",
)


def _fetch_outcomes_by_turn(conn: sqlite3.Connection,
                              turn_ids: Iterable[int]
                              ) -> Dict[int, Dict[str, Any]]:
    """Map ai_turn_id -> most-recent trade_outcomes row (one per turn)."""
    turn_ids = list(turn_ids)
    if not turn_ids:
        return {}
    cols_sql = ", ".join(_OUTCOME_COLS_BY_TURN)
    out: Dict[int, Dict[str, Any]] = {}
    # SQLite parameter limit is high enough for any reasonable day, but be
    # safe with a chunked IN clause.
    CHUNK = 500
    for i in range(0, len(turn_ids), CHUNK):
        chunk = turn_ids[i:i + CHUNK]
        placeholders = ",".join("?" * len(chunk))
        sql = (f"SELECT ai_turn_id, {cols_sql} FROM trade_outcomes "
                f"WHERE ai_turn_id IN ({placeholders}) "
                "ORDER BY ai_turn_id ASC, id DESC")
        for row in conn.execute(sql, chunk).fetchall():
            tid = int(row["ai_turn_id"])
            if tid in out:
                continue  # take the FIRST (highest id) -- most recent
            out[tid] = dict(row)
    return out


# ----------------------------------------------------------- forecasts

def _index_forecasts(forecast_db_path: Path,
                      *,
                      start_ms: int,
                      end_ms: int,
                      alias: Optional[str]
                      ) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Read forecasts in the window and index by (chat_run_id, digest_sha256).

    Strict read-only: opens the SQLite file with ``mode=ro`` so SQLite
    refuses every write attempt. Does NOT use ``PaxForecastStore``, whose
    constructor runs CREATE TABLE / CREATE INDEX / ALTER TABLE migrations
    and would mutate a legacy DB. The forecast DB at ``forecast_db_path``
    is treated as a read-only artifact owned by another process.

    Any of the following => empty index, no exception, no mutation:
      - path does not exist
      - file is unreadable / not a valid SQLite DB
      - ``forecasts`` table is absent
      - one of ``_REQUIRED_FORECAST_COLUMNS`` is absent on that table
      - SQL or OS error during the read
    """
    p = Path(forecast_db_path)
    if not p.exists():
        return {}

    uri = f"file:{p.resolve().as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0,
                                isolation_level=None,
                                check_same_thread=False)
    except sqlite3.Error:
        return {}

    index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    try:
        conn.row_factory = sqlite3.Row
        # Table presence check.
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='forecasts'").fetchall()}
        except sqlite3.Error:
            return {}
        if "forecasts" not in tables:
            return {}

        # Required-columns check. A legacy schema missing the linkage
        # columns surfaces every turn as MISSING; we DO NOT ADD COLUMNS.
        try:
            cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(forecasts)").fetchall()}
        except sqlite3.Error:
            return {}
        if not set(_REQUIRED_FORECAST_COLUMNS).issubset(cols):
            return {}

        sql = ("SELECT chat_run_id, digest_sha256, forecast_id, "
                "execution_read, direction, horizon_sec, prob_success, "
                "expected_r, alias, ts_ms "
                "FROM forecasts WHERE ts_ms >= ? AND ts_ms < ?")
        params: List[Any] = [int(start_ms), int(end_ms)]
        if alias:
            sql += " AND alias = ?"
            params.append(alias)
        sql += " ORDER BY ts_ms ASC, forecast_id ASC"
        try:
            rows = conn.execute(sql, params).fetchall()
        except sqlite3.Error:
            return {}

        for row in rows:
            key_a = row["chat_run_id"]
            key_b = row["digest_sha256"]
            if not key_a or not key_b:
                continue
            index.setdefault((str(key_a), str(key_b)), []).append(dict(row))
        return index
    finally:
        conn.close()


def _match_forecast(ai: Dict[str, Any],
                     index: Dict[Tuple[str, str], List[Dict[str, Any]]]
                     ) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (forecast_dict_or_None, status)."""
    chat_run_id = ai.get("chat_run_id") or ""
    digest_sha256 = ai.get("digest_sha256") or ""
    if not chat_run_id or not digest_sha256:
        return (None, "MISSING")
    matches = index.get((str(chat_run_id), str(digest_sha256)), [])
    if not matches:
        return (None, "MISSING")
    if len(matches) > 1:
        return (None, "AMBIGUOUS")
    return (matches[0], "PRESENT")


# --------------------------------------------------------- prompt archive

def _verify_prompt_archive(prompt_archive_path: Optional[str],
                            prompt_sha256: Optional[str]) -> str:
    """One of PRESENT_VALID / MISSING / HASH_MISMATCH.

    Empty/null prompt_archive_path => MISSING.
    File missing on disk          => MISSING.
    File exists but sha mismatch  => HASH_MISMATCH.
    Otherwise                     => PRESENT_VALID.
    """
    if not prompt_archive_path:
        return "MISSING"
    p = Path(prompt_archive_path)
    if not p.exists():
        return "MISSING"
    try:
        body = p.read_bytes()
    except OSError:
        return "MISSING"
    actual = hashlib.sha256(body).hexdigest()
    if not prompt_sha256:
        # We have an archive file but no claim to verify against; safest
        # to call this MISSING (we cannot audit it).
        return "MISSING"
    if actual != prompt_sha256:
        return "HASH_MISMATCH"
    return "PRESENT_VALID"


# ------------------------------------------------------------- row build

def _build_row(ai: Dict[str, Any],
                forecast: Optional[Dict[str, Any]],
                forecast_status: str,
                outcome: Optional[Dict[str, Any]],
                prompt_archive_status: str) -> Dict[str, Any]:
    if outcome is None:
        outcome_status = "MISSING"
    elif int(outcome.get("invalidated") or 0) == 1:
        outcome_status = "INVALIDATED"
    else:
        outcome_status = "VALID"

    return {
        # identity + lineage
        "ai_turn_id":           int(ai["id"]),
        "ts_ms":                int(ai["ts_ms"]),
        "chat_run_id":          ai.get("chat_run_id"),
        "model":                ai.get("model"),
        "model_release_id":     ai.get("model_release_id"),
        "prompt_version":       ai.get("prompt_version"),
        "prompt_sha256":        ai.get("prompt_sha256"),
        "skill_bundle_sha256":  ai.get("skill_bundle_sha256"),
        "prompt_archive_path":  ai.get("prompt_archive_path") or "",
        "prompt_archive_status": prompt_archive_status,
        "snapshot_sha256":      ai.get("snapshot_sha256"),
        "digest_sha256":        ai.get("digest_sha256"),
        "snapshot_alias":       ai.get("snapshot_alias"),
        "user_text_normalized": ai.get("user_text_normalized"),
        "pax_text":             ai.get("pax_text"),
        # forecast
        "forecast_status":      forecast_status,
        "forecast_id":          (forecast or {}).get("forecast_id"),
        "execution_read":       (forecast or {}).get("execution_read"),
        "direction":            (forecast or {}).get("direction"),
        "horizon_sec":          (forecast or {}).get("horizon_sec"),
        "prob_success":         (forecast or {}).get("prob_success"),
        "expected_r":           (forecast or {}).get("expected_r"),
        # outcome
        "outcome_status":       outcome_status,
        "verdict":              (outcome or {}).get("verdict"),
        "invalidated":          (outcome or {}).get("invalidated"),
        "invalidation_reason":  (outcome or {}).get("invalidation_reason"),
        "realized_r_at_t60s":   (outcome or {}).get("realized_r_at_t60s"),
        "realized_r_at_t180s":  (outcome or {}).get("realized_r_at_t180s"),
        "realized_r_at_t300s":  (outcome or {}).get("realized_r_at_t300s"),
        "realized_r_at_t900s":  (outcome or {}).get("realized_r_at_t900s"),
    }


def _compute_summary(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    n_with_forecast = sum(1 for r in rows if r["forecast_status"] == "PRESENT")
    n_forecast_missing   = sum(1 for r in rows
                                if r["forecast_status"] == "MISSING")
    n_forecast_ambiguous = sum(1 for r in rows
                                if r["forecast_status"] == "AMBIGUOUS")
    n_with_outcome = sum(1 for r in rows
                          if r["outcome_status"] in ("VALID", "INVALIDATED"))
    n_outcome_invalidated = sum(1 for r in rows
                                 if r["outcome_status"] == "INVALIDATED")
    n_arch_present  = sum(1 for r in rows
                           if r["prompt_archive_status"] == "PRESENT_VALID")
    n_arch_missing  = sum(1 for r in rows
                           if r["prompt_archive_status"] == "MISSING")
    n_arch_mismatch = sum(1 for r in rows
                           if r["prompt_archive_status"] == "HASH_MISMATCH")
    return {
        "n_ai_turns":                    len(rows),
        "n_with_forecast":               n_with_forecast,
        "n_forecast_missing":            n_forecast_missing,
        "n_forecast_ambiguous":          n_forecast_ambiguous,
        "n_with_outcome":                n_with_outcome,
        "n_outcome_invalidated":         n_outcome_invalidated,
        "n_prompt_archive_present":      n_arch_present,
        "n_prompt_archive_missing":      n_arch_missing,
        "n_prompt_archive_hash_mismatch": n_arch_mismatch,
    }


# ------------------------------------------------------------ public API

def _empty_report(*, start_ms: int, end_ms: int,
                   alias: Optional[str]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_ms":   int(time.time() * 1000),
        "window":         {"start_ms": int(start_ms), "end_ms": int(end_ms)},
        "alias":          alias,
        "rows":           [],
        "summary":        _compute_summary([]),
    }


def build_turn_audit_report(*,
                              bus_db_path: Path,
                              forecast_db_path: Optional[Path] = None,
                              prompt_archive_root: Optional[Path] = None,
                              start_ms: int,
                              end_ms: int,
                              alias: Optional[str] = None) -> Dict[str, Any]:
    """Build the per-turn audit report for one half-open window.

    ``prompt_archive_root`` is accepted for symmetry with the CLI but the
    actual archive verification uses the absolute path stored on the
    ai_turns row (``prompt_archive_path``). The root is recorded in the
    report so the operator can re-resolve relocated archives.
    """
    if int(end_ms) <= int(start_ms):
        raise ValueError("end_ms must be greater than start_ms")

    conn = _open_bus_readonly(Path(bus_db_path))
    if conn is None:
        return _empty_report(start_ms=start_ms, end_ms=end_ms, alias=alias)
    try:
        ai_rows = _fetch_ai_turns(conn,
                                    start_ms=start_ms, end_ms=end_ms,
                                    alias=alias)
        outcome_by_turn = _fetch_outcomes_by_turn(
            conn, (int(r["id"]) for r in ai_rows))
    finally:
        conn.close()

    forecast_index: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    if forecast_db_path is not None:
        forecast_index = _index_forecasts(Path(forecast_db_path),
                                            start_ms=start_ms,
                                            end_ms=end_ms, alias=alias)

    rows: List[Dict[str, Any]] = []
    for ai in ai_rows:
        forecast, forecast_status = _match_forecast(ai, forecast_index)
        outcome = outcome_by_turn.get(int(ai["id"]))
        prompt_archive_status = _verify_prompt_archive(
            ai.get("prompt_archive_path"),
            ai.get("prompt_sha256"),
        )
        rows.append(_build_row(ai, forecast, forecast_status,
                                outcome, prompt_archive_status))

    return {
        "schema_version":      SCHEMA_VERSION,
        "generated_ms":        int(time.time() * 1000),
        "window":              {"start_ms": int(start_ms),
                                "end_ms":   int(end_ms)},
        "alias":               alias,
        "prompt_archive_root": (str(prompt_archive_root)
                                  if prompt_archive_root else None),
        "rows":                rows,
        "summary":             _compute_summary(rows),
    }


# ------------------------------------------------------------------- CLI

def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="bookmap_mcp.pax_turn_audit",
        description=("Turn-level audit report: join ai_turns, "
                      "trade_outcomes, and forecasts; verify prompt "
                      "archive integrity."),
    )
    ap.add_argument("--bus-db", type=Path, required=True,
                    help="Feature bus SQLite DB (ai_turns + trade_outcomes)")
    ap.add_argument("--forecast-db", type=Path, default=None,
                    help="Forecast store SQLite DB (optional)")
    ap.add_argument("--prompt-archive-root", type=Path, default=None,
                    help="Prompt archive root directory (informational)")
    ap.add_argument("--report", type=Path, required=True,
                    help="Output JSON report path")
    ap.add_argument("--date", default=None,
                    help="UTC day YYYY-MM-DD (mutually exclusive with "
                         "--start-ms/--end-ms)")
    ap.add_argument("--start-ms", type=int, default=None)
    ap.add_argument("--end-ms",   type=int, default=None)
    ap.add_argument("--alias", default=None)
    args = ap.parse_args(argv)

    if args.date:
        try:
            start_ms, end_ms = utc_day_window(args.date)
        except ValueError as exc:
            sys.stderr.write(f"[pax_turn_audit] {exc}\n")
            return 2
    elif args.start_ms is not None and args.end_ms is not None:
        start_ms, end_ms = int(args.start_ms), int(args.end_ms)
    else:
        sys.stderr.write(
            "[pax_turn_audit] need --date OR both --start-ms and --end-ms\n")
        return 2

    try:
        report = build_turn_audit_report(
            bus_db_path=args.bus_db,
            forecast_db_path=args.forecast_db,
            prompt_archive_root=args.prompt_archive_root,
            start_ms=start_ms, end_ms=end_ms,
            alias=args.alias,
        )
    except ValueError as exc:
        sys.stderr.write(f"[pax_turn_audit] {exc}\n")
        return 2

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True),
                            encoding="utf-8")
    print(f"turn-audit report written: {args.report}", file=sys.stderr)
    return 0


__all__ = [
    "SCHEMA_VERSION",
    "build_turn_audit_report",
    "main",
    "utc_day_window",
]


if __name__ == "__main__":
    raise SystemExit(main())
