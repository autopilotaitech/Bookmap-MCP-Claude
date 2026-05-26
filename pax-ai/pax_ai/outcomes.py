"""Outcomes labeler daemon (structured-forecast based).

Wakes every outcomes.wake_interval_ms (default 15 min). For each ai_turns
row older than the largest T-offset (15 min) that doesn't already have a
matching trade_outcomes row, the labeler:

  1. Looks up the structured PAX_FORECAST captured for the same chat turn
     by deterministic join on (chat_run_id, digest_sha256), using
     snapshot_sha256 to break ambiguity. Substring-matching the user's
     raw text is explicitly NOT a fallback -- a forecast-less turn is
     recorded as invalidated=1 / FORECAST_MISSING_OR_AMBIGUOUS.

  2. For PAY_FOR_TRADE + LONG/SHORT:
       - reads mid_at_t0..t900s via time-aligned lookup (see
         ``_mid_aligned``): exact-timestamp match preferred, otherwise
         linear interpolation between bracketing snapshots when their
         inter-snapshot gap is <= outcomes.match_tolerance_ms; one-sided
         data or a wider gap returns None rather than scoring against a
         nearby-but-wrong timestamp;
       - long  realized_r = mid_tN - mid_t0
         short realized_r = mid_t0 - mid_tN   (units: points);
       - missing mid_t0       -> invalidated=1 / SNAPSHOT_MISSING_AT_T0
       - missing forward mid  -> invalidated=1 / HORIZON_DATA_MISSING
         and realized_r columns NULL.

  3. For non-PAY forecasts (WAIT_FOR_CONFIRM / STAND_DOWN / SCRATCH_READY
     + NONE): writes verdict=<execution_read>, realized_r columns NULL,
     invalidated=0, label_method='structured_resampled_v1'.

Daemon NEVER raises into the parent. start() is idempotent. stop() is
safe when not running. Default disabled via outcomes.enabled=False.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import config


_STATE_LOCK = threading.Lock()
_STOP_EVT = threading.Event()
_THREAD: Optional[threading.Thread] = None
_RUNNING = False
_HEALTHY = True
_LAST_ERROR: Optional[str] = None
_LAST_RUN_MS: int = 0
_LABELED_TODAY: int = 0


_T_OFFSETS_S = (0, 60, 180, 300, 900)
_T_COLUMNS = ("mid_at_t0", "mid_at_t60s", "mid_at_t180s",
              "mid_at_t300s", "mid_at_t900s")
_REALIZED_R_COLUMNS = ("realized_r_at_t60s", "realized_r_at_t180s",
                       "realized_r_at_t300s", "realized_r_at_t900s")
_LABEL_METHOD = "structured_resampled_v1"

_REASON_FORECAST = "FORECAST_MISSING_OR_AMBIGUOUS"
_REASON_T0       = "SNAPSHOT_MISSING_AT_T0"
_REASON_HORIZON  = "HORIZON_DATA_MISSING"

_VERDICT_UNKNOWN = "UNKNOWN"


def _mid_aligned(conn: sqlite3.Connection, alias: str,
                  target_ts_ms: int, max_gap_ms: int) -> Optional[float]:
    """Time-aligned snapshot_features.mid lookup. Returns ``None`` rather
    than silently scoring against a nearby-but-wrong timestamp.

    Resolution order:

      1. Exact-timestamp match (alias + ts_ms = target). If present and
         mid IS NOT NULL, return that mid verbatim.
      2. Otherwise find the nearest snapshot strictly BEFORE and the
         nearest snapshot strictly AFTER target_ts_ms (same alias, non-null
         mid). If either side is missing, return ``None`` -- we do NOT
         fall back to one-sided nearest matching.
      3. If both sides exist, check the inter-snapshot gap
         ``(after.ts_ms - before.ts_ms)``. If it exceeds ``max_gap_ms``,
         return ``None`` -- the gap is too wide to interpolate honestly.
      4. Otherwise linearly interpolate by timestamp:
            frac = (target - t_before) / (t_after - t_before)
            mid  = m_before + frac * (m_after - m_before)
    """
    row = conn.execute("""
        SELECT mid FROM snapshot_features
        WHERE alias=? AND ts_ms=? AND mid IS NOT NULL
        ORDER BY ts_ms DESC, id DESC LIMIT 1
    """, (alias, target_ts_ms)).fetchone()
    if row is not None and row[0] is not None:
        return float(row[0])

    before = conn.execute("""
        SELECT ts_ms, mid FROM snapshot_features
        WHERE alias=? AND ts_ms < ? AND mid IS NOT NULL
        ORDER BY ts_ms DESC, id DESC LIMIT 1
    """, (alias, target_ts_ms)).fetchone()
    after = conn.execute("""
        SELECT ts_ms, mid FROM snapshot_features
        WHERE alias=? AND ts_ms > ? AND mid IS NOT NULL
        ORDER BY ts_ms ASC, id DESC LIMIT 1
    """, (alias, target_ts_ms)).fetchone()

    if before is None or after is None:
        return None

    t_before = int(before[0])
    t_after  = int(after[0])
    if (t_after - t_before) > int(max_gap_ms):
        return None

    m_before = float(before[1])
    m_after  = float(after[1])
    span = t_after - t_before
    if span <= 0:
        # Degenerate (same ts on both sides); fall back to before-mid.
        return m_before
    frac = (int(target_ts_ms) - t_before) / float(span)
    return m_before + frac * (m_after - m_before)


def _resolve_forecast_db_path() -> Optional[Path]:
    raw = config.get("forecast.store_path", "") or ""
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None


def _lookup_forecast(forecast_db: Optional[Path],
                      *,
                      chat_run_id: Optional[str],
                      digest_sha256: Optional[str],
                      snapshot_sha256: Optional[str]
                      ) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (forecast_dict, status).

    status:
      'UNIQUE'    -> exactly one matching forecast row;
      'NONE'      -> no forecast DB / no rows / missing linkage keys;
      'AMBIGUOUS' -> >1 row even after disambiguating on snapshot_sha256.

    Read-only on the forecast DB. All sqlite errors degrade to 'NONE'.
    """
    if forecast_db is None or not chat_run_id or not digest_sha256:
        return (None, "NONE")
    try:
        uri = f"file:{forecast_db.resolve().as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return (None, "NONE")
    try:
        rows = conn.execute("""
            SELECT execution_read, direction, horizon_sec, snapshot_sha256
            FROM forecasts
            WHERE chat_run_id = ? AND digest_sha256 = ?
        """, (chat_run_id, digest_sha256)).fetchall()
    except sqlite3.Error:
        conn.close()
        return (None, "NONE")
    conn.close()

    if not rows:
        return (None, "NONE")
    if len(rows) > 1 and snapshot_sha256:
        narrowed = [r for r in rows if r[3] == snapshot_sha256]
        if len(narrowed) == 1:
            rows = narrowed
    if len(rows) != 1:
        return (None, "AMBIGUOUS")

    er, direction, horizon_sec, _snap = rows[0]
    return ({
        "execution_read": er,
        "direction":      direction,
        "horizon_sec":    int(horizon_sec) if horizon_sec is not None else None,
    }, "UNIQUE")


def _insert_outcome(conn: sqlite3.Connection,
                     *,
                     ai_turn_id: int,
                     alias: str,
                     verdict: str,
                     entry_price: Optional[float],
                     mids: Tuple[Optional[float], ...],
                     realized_rs: Tuple[Optional[float], ...],
                     invalidated: int,
                     invalidation_reason: Optional[str]) -> None:
    conn.execute(
        f"""
        INSERT INTO trade_outcomes
          (schema_version, ai_turn_id, alias, verdict, entry_price,
           {','.join(_T_COLUMNS)},
           {','.join(_REALIZED_R_COLUMNS)},
           expected_r, prob_pay, invalidated, invalidation_reason,
           label_method, labeled_at_ms)
        VALUES (1, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                NULL, NULL, ?, ?,
                ?, ?)
        """,
        (
            ai_turn_id, alias, verdict, entry_price,
            mids[0], mids[1], mids[2], mids[3], mids[4],
            realized_rs[0], realized_rs[1], realized_rs[2], realized_rs[3],
            invalidated, invalidation_reason,
            _LABEL_METHOD, int(time.time() * 1000),
        ),
    )


def _label_one(conn: sqlite3.Connection,
                forecast_db: Optional[Path],
                *,
                ai_turn_id: int,
                ai_ts_ms: int,
                alias: str,
                chat_run_id: Optional[str],
                digest_sha256: Optional[str],
                snapshot_sha256: Optional[str],
                tolerance_ms: int) -> None:
    """Compute + insert one trade_outcomes row for the given ai_turn."""
    forecast, status = _lookup_forecast(
        forecast_db,
        chat_run_id=chat_run_id,
        digest_sha256=digest_sha256,
        snapshot_sha256=snapshot_sha256,
    )
    if status != "UNIQUE":
        _insert_outcome(
            conn,
            ai_turn_id=ai_turn_id,
            alias=alias,
            verdict=_VERDICT_UNKNOWN,
            entry_price=None,
            mids=(None, None, None, None, None),
            realized_rs=(None, None, None, None),
            invalidated=1,
            invalidation_reason=_REASON_FORECAST,
        )
        return

    execution_read = forecast["execution_read"]
    direction = forecast["direction"]

    # Non-directional verdict: STAND_DOWN / WAIT_FOR_CONFIRM / SCRATCH_READY.
    if execution_read != "PAY_FOR_TRADE":
        _insert_outcome(
            conn,
            ai_turn_id=ai_turn_id,
            alias=alias,
            verdict=str(execution_read),
            entry_price=None,
            mids=(None, None, None, None, None),
            realized_rs=(None, None, None, None),
            invalidated=0,
            invalidation_reason=None,
        )
        return

    # Directional: PAY_FOR_TRADE + LONG/SHORT.
    if direction not in ("LONG", "SHORT"):
        # Schema invariant says PAY_FOR_TRADE requires LONG/SHORT. Defense
        # in depth: a bad row in the forecast store is treated as ambiguous.
        _insert_outcome(
            conn,
            ai_turn_id=ai_turn_id,
            alias=alias,
            verdict=_VERDICT_UNKNOWN,
            entry_price=None,
            mids=(None, None, None, None, None),
            realized_rs=(None, None, None, None),
            invalidated=1,
            invalidation_reason=_REASON_FORECAST,
        )
        return

    verdict = "ENTER_LONG" if direction == "LONG" else "ENTER_SHORT"
    mids = tuple(_mid_aligned(conn, alias, ai_ts_ms + off * 1000, tolerance_ms)
                 for off in _T_OFFSETS_S)
    m0 = mids[0]

    if m0 is None:
        _insert_outcome(
            conn,
            ai_turn_id=ai_turn_id,
            alias=alias,
            verdict=verdict,
            entry_price=None,
            mids=mids,
            realized_rs=(None, None, None, None),
            invalidated=1,
            invalidation_reason=_REASON_T0,
        )
        return

    forward_mids = mids[1:]
    if any(m is None for m in forward_mids):
        _insert_outcome(
            conn,
            ai_turn_id=ai_turn_id,
            alias=alias,
            verdict=verdict,
            entry_price=float(m0),
            mids=mids,
            realized_rs=(None, None, None, None),
            invalidated=1,
            invalidation_reason=_REASON_HORIZON,
        )
        return

    if direction == "LONG":
        realized_rs = tuple(float(m) - float(m0) for m in forward_mids)
    else:
        realized_rs = tuple(float(m0) - float(m) for m in forward_mids)

    _insert_outcome(
        conn,
        ai_turn_id=ai_turn_id,
        alias=alias,
        verdict=verdict,
        entry_price=float(m0),
        mids=mids,
        realized_rs=realized_rs,
        invalidated=0,
        invalidation_reason=None,
    )


def _outcomes_pass() -> int:
    """One pass over unlabeled ai_turns. Returns number of rows labeled.
    Catches all errors; never raises."""
    global _LAST_RUN_MS, _LABELED_TODAY, _HEALTHY, _LAST_ERROR
    db_path = Path(config.get("feature_bus.db_path") or "")
    if not db_path.exists():
        with _STATE_LOCK:
            _LAST_RUN_MS = int(time.time() * 1000)
        return 0
    tolerance_ms = int(config.get("outcomes.match_tolerance_ms", 5_000))
    largest_offset_ms = max(_T_OFFSETS_S) * 1000
    now_ms = int(time.time() * 1000)
    cutoff_ms = now_ms - largest_offset_ms
    forecast_db = _resolve_forecast_db_path()
    labeled = 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0,
                                isolation_level=None,
                                check_same_thread=False)
        try:
            with conn:
                rows = conn.execute("""
                    SELECT id, ts_ms, snapshot_alias,
                           chat_run_id, digest_sha256, snapshot_sha256
                    FROM ai_turns
                    WHERE ts_ms <= ?
                      AND id NOT IN (SELECT ai_turn_id FROM trade_outcomes)
                    ORDER BY ts_ms ASC
                """, (cutoff_ms,)).fetchall()
                for (ai_id, ai_ts_ms, alias,
                     chat_run_id, digest_sha256, snapshot_sha256) in rows:
                    if not alias:
                        continue
                    _label_one(
                        conn,
                        forecast_db,
                        ai_turn_id=int(ai_id),
                        ai_ts_ms=int(ai_ts_ms),
                        alias=str(alias),
                        chat_run_id=(str(chat_run_id) if chat_run_id else None),
                        digest_sha256=(str(digest_sha256)
                                        if digest_sha256 else None),
                        snapshot_sha256=(str(snapshot_sha256)
                                          if snapshot_sha256 else None),
                        tolerance_ms=tolerance_ms,
                    )
                    labeled += 1
        finally:
            conn.close()
    except Exception as exc:
        with _STATE_LOCK:
            _HEALTHY = False
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[outcomes] pass failed: {exc}\n")
        return 0
    with _STATE_LOCK:
        _LAST_RUN_MS = int(time.time() * 1000)
        _LABELED_TODAY += labeled
    return labeled


def _loop() -> None:
    sys.stderr.write("[outcomes] daemon started\n")
    try:
        while not _STOP_EVT.is_set():
            wake_ms = int(config.get("outcomes.wake_interval_ms", 900_000))
            _outcomes_pass()
            _STOP_EVT.wait(max(0.001, wake_ms / 1000.0))
    finally:
        global _RUNNING
        with _STATE_LOCK:
            _RUNNING = False
        sys.stderr.write("[outcomes] daemon stopped\n")


def start() -> None:
    """Idempotent. NEVER raises into startup."""
    global _THREAD, _RUNNING, _HEALTHY, _LAST_ERROR
    try:
        if not config.get("outcomes.enabled", False):
            return
        with _STATE_LOCK:
            if _THREAD is not None and _THREAD.is_alive():
                return
            _STOP_EVT.clear()
            _RUNNING = True
            _THREAD = threading.Thread(target=_loop,
                                         name="pax-outcomes",
                                         daemon=True)
            _THREAD.start()
    except Exception as exc:
        with _STATE_LOCK:
            _HEALTHY = False
            _LAST_ERROR = f"start failed: {exc}"
        sys.stderr.write(f"[outcomes] start failed: {exc}\n")


def stop(timeout_s: float = 2.0) -> None:
    """Signal stop + join. Safe when not running."""
    _STOP_EVT.set()
    global _THREAD
    t = _THREAD
    if t is not None:
        t.join(timeout=timeout_s)
    with _STATE_LOCK:
        _THREAD = None


def status() -> Dict[str, Any]:
    """{enabled, running, healthy, labeled_today, last_run_ms, last_error}."""
    with _STATE_LOCK:
        return {
            "enabled":       bool(config.get("outcomes.enabled", False)),
            "running":       _RUNNING,
            "healthy":       _HEALTHY,
            "labeled_today": _LABELED_TODAY,
            "last_run_ms":   _LAST_RUN_MS,
            "last_error":    _LAST_ERROR,
        }
