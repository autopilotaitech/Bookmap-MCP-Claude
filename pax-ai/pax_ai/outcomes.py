"""Phase 4A outcomes labeler daemon.

Wakes every outcomes.wake_interval_ms (default 15 min). For each ai_turns
row older than the largest T-offset (15 min) that doesn't yet have a
matching trade_outcomes row, computes mid_at_t0 / mid_at_t60s / t180 / t300
/ t900 by reading the NEAREST snapshot_features row within
+/- outcomes.match_tolerance_ms (default 5 s). Missing snapshots produce
NULL mid columns (no crash). Writes one trade_outcomes row per ai_turn.

Daemon NEVER raises into the parent. start() is idempotent. stop() is
safe when not running. Default disabled via outcomes.enabled=False.

verdict column is a Phase-4A placeholder derived heuristically from
user_text_raw; Phase 5 will replace with a real verdict-extraction model.
"""

from __future__ import annotations

import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

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
_T_COLUMNS   = ("mid_at_t0", "mid_at_t60s", "mid_at_t180s",
                 "mid_at_t300s", "mid_at_t900s")


def _classify_verdict(user_text: str) -> str:
    """Phase-4A heuristic: rough verdict guess from user text."""
    t = (user_text or "").lower()
    if "enter long" in t or "buy" in t or " long" in t:
        return "ENTER_LONG"
    if "enter short" in t or "sell" in t or " short" in t:
        return "ENTER_SHORT"
    return "INFO"


def _mid_near(conn: sqlite3.Connection, alias: str,
               target_ts_ms: int, tolerance_ms: int) -> Optional[float]:
    """Find the snapshot_features.mid nearest to target_ts_ms within
    +/- tolerance_ms. Returns None if no row in window."""
    row = conn.execute("""
        SELECT mid FROM snapshot_features
        WHERE alias=? AND ts_ms BETWEEN ? AND ?
        ORDER BY ABS(ts_ms - ?) ASC
        LIMIT 1
    """, (alias, target_ts_ms - tolerance_ms,
                target_ts_ms + tolerance_ms, target_ts_ms)).fetchone()
    return row[0] if row else None


def _label_one(conn: sqlite3.Connection, ai_turn_id: int,
                ai_ts_ms: int, alias: str, user_text: str,
                tolerance_ms: int) -> None:
    """Compute + insert one trade_outcomes row for the given ai_turn."""
    mids = [_mid_near(conn, alias, ai_ts_ms + off * 1000, tolerance_ms)
            for off in _T_OFFSETS_S]
    verdict = _classify_verdict(user_text)
    conn.execute(
        f"""
        INSERT INTO trade_outcomes
          (schema_version, ai_turn_id, alias, verdict, entry_price,
           {','.join(_T_COLUMNS)},
           realized_r_at_t60s, realized_r_at_t180s,
           realized_r_at_t300s, realized_r_at_t900s,
           expected_r, prob_pay, invalidated, invalidation_reason,
           label_method, labeled_at_ms)
        VALUES (1, ?, ?, ?, NULL,
                ?, ?, ?, ?, ?,
                NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL,
                'phase4a_heuristic_v1', ?)
        """,
        (ai_turn_id, alias, verdict, *mids, int(time.time() * 1000)),
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
    labeled = 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0,
                                isolation_level=None,
                                check_same_thread=False)
        try:
            with conn:
                rows = conn.execute("""
                    SELECT id, ts_ms, snapshot_alias, user_text_raw FROM ai_turns
                    WHERE ts_ms <= ?
                      AND id NOT IN (SELECT ai_turn_id FROM trade_outcomes)
                    ORDER BY ts_ms ASC
                """, (cutoff_ms,)).fetchall()
                for ai_id, ai_ts_ms, alias, user_text in rows:
                    if not alias:
                        continue
                    _label_one(conn, ai_id, int(ai_ts_ms), str(alias),
                                user_text or "", tolerance_ms)
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
    """Returns {enabled, running, healthy, labeled_today, last_run_ms,
    last_error}. Cheap; safe from any thread."""
    with _STATE_LOCK:
        return {
            "enabled":       bool(config.get("outcomes.enabled", False)),
            "running":       _RUNNING,
            "healthy":       _HEALTHY,
            "labeled_today": _LABELED_TODAY,
            "last_run_ms":   _LAST_RUN_MS,
            "last_error":    _LAST_ERROR,
        }
