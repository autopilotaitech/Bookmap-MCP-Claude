"""Pax Feature Bus -- passive structured capture.

Phase 1 contract:
  - feature_bus.enabled=false default; when disabled, every record_* is a no-op.
  - When enabled, writes go to a separate SQLite DB + content-addressed
    blob stores; NO behavior change to chat/UI/whynow/Claude CLI paths.
  - record_trigger / record_ai_turn NEVER raise into callers. NEVER block.
    They enqueue; the writer thread drains.

See docs/superpowers/specs/2026-05-20-pax-feature-bus-design.md for the design.
"""

from __future__ import annotations

import collections
import datetime as _dt
import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

try:
    import msvcrt           # Windows
    _IS_WINDOWS = True
except ImportError:
    import fcntl            # POSIX (CI)
    _IS_WINDOWS = False


SCHEMA_VERSION = 1


# -- Bounded enqueue (basic; Task 7 extends with back-pressure + drop policy) --

_QUEUE: "collections.deque[Dict[str, Any]]" = collections.deque()
_QUEUE_LOCK = threading.Lock()


def _enqueue(evt: Dict[str, Any]) -> None:
    """Enqueue an event for the writer. Fire-and-forget. NEVER raises.
    When the queue is full, snapshot_features events are DROPPED;
    all NEVER_DROP_KINDS events (ai_turn, level_event, microstructure_event,
    trigger_event) are preserved by evicting an older snapshot. If the queue
    is full of only never-drop events and the new event is also never-drop,
    the new event is appended (bounded growth past queue_max) and a warning
    is logged - data integrity beats memory pressure in Phase 1."""
    if not config.get("feature_bus.enabled", False):
        return
    queue_max = int(config.get("feature_bus.queue_max", 2000))
    with _QUEUE_LOCK:
        if len(_QUEUE) >= queue_max:
            # Try to evict the oldest droppable event.
            for i, q in enumerate(_QUEUE):
                if q.get("kind") in _DROP_KINDS:
                    del _QUEUE[i]
                    break
            else:
                # No droppable events. If the new event is also never-drop,
                # accept temporary overflow rather than lose audit data.
                if evt.get("kind") in _DROP_KINDS:
                    return
                sys.stderr.write(
                    f"[feature_bus] queue overflow (depth={len(_QUEUE)}); "
                    f"accepting {evt.get('kind')} past queue_max\n")
        _QUEUE.append(evt)


# -- Module-local state ------------------------------------------------------

_STATE_LOCK = threading.Lock()
_RUNNING = False
_HEALTHY = True
_LAST_ERROR: Optional[str] = None
_LAST_WRITE_MS: int = 0
_QUEUE_DEPTH: int = 0
_ROWS_TODAY: int = 0
_BLOB_WRITES_TODAY: int = 0
_LOCK_FILE_HANDLE = None

_STOP_EVT = threading.Event()
_WRITER_THREAD: Optional[threading.Thread] = None
_DROP_KINDS = {"snapshot_features"}   # back-pressure: snapshots droppable; events not
_NEVER_DROP_KINDS = {"ai_turn", "level_event", "microstructure_event", "trigger_event"}

# Live snapshot capture state. Owned exclusively by the writer thread;
# never read or written from outside _writer_loop.
_PREV_SNAP: Optional[Dict[str, Any]] = None
_LAST_CAPTURE_MS: int = 0              # epoch ms of last live-capture pulse


# -- AiTurnRecord ------------------------------------------------------------

@dataclass(frozen=True)
class AiTurnRecord:
    schema_version:        int
    ts_ms:                 int
    chat_run_id:           str
    deep:                  bool
    model:                 str
    router_primary:        Optional[str]
    router_secondary:      Optional[List[str]]
    user_text_raw:         str
    user_text_normalized:  str
    digest_text:           str
    digest_sha256:         str
    snapshot_json:         str
    snapshot_sha256:       str
    snapshot_alias:        Optional[str]
    snapshot_ts_ms:        Optional[int]
    snapshot_age_ms:       int
    pax_text:              str
    exit_code:             Optional[int]
    elapsed_ms:            Optional[int]
    api_duration_ms:       Optional[int]
    total_cost_usd:        Optional[float]
    input_tokens:          Optional[int]
    output_tokens:         Optional[int]
    cache_creation_tokens: Optional[int]
    cache_read_tokens:     Optional[int]
    aborted:               bool
    error:                 Optional[str]

    def __post_init__(self) -> None:
        if not self.digest_sha256:
            raise ValueError("digest_sha256 required, NOT NULL")
        if not self.snapshot_sha256:
            raise ValueError("snapshot_sha256 required, NOT NULL")


# -- Public API stubs (filled in later tasks) --------------------------------

def start() -> None:
    """Idempotent. Starts the writer thread when feature_bus.enabled=True
    and the advisory lock is available. Sets _RUNNING=True synchronously
    so record_* callers don't race the not-yet-scheduled writer thread."""
    global _WRITER_THREAD, _RUNNING
    if not config.get("feature_bus.enabled", False):
        return
    with _STATE_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        db_path = Path(config.get("feature_bus.db_path"))
        if not _try_acquire_lock(db_path, hold_state_lock=True):
            return
        _STOP_EVT.clear()
        _RUNNING = True
        _WRITER_THREAD = threading.Thread(target=_writer_loop,
                                           name="pax-feature-bus-writer",
                                           daemon=True)
        _WRITER_THREAD.start()


def _accepting_events() -> bool:
    """Public-API gate. True iff feature_bus is enabled, healthy, AND the
    writer thread is running. record_trigger / record_ai_turn use this so
    they no-op cleanly on lock failure or after start() never succeeded.

    Internal callers (e.g. the writer's own _writer_tick_live_capture)
    enqueue directly via _enqueue and intentionally bypass this gate."""
    if not config.get("feature_bus.enabled", False):
        return False
    with _STATE_LOCK:
        return _HEALTHY and _RUNNING


def stop(timeout_s: float = 2.0) -> None:
    _STOP_EVT.set()
    global _WRITER_THREAD
    t = _WRITER_THREAD
    if t is not None:
        t.join(timeout=timeout_s)
    with _STATE_LOCK:
        _WRITER_THREAD = None
    _release_lock()


def status() -> Dict[str, Any]:
    with _STATE_LOCK:
        with _QUEUE_LOCK:
            depth = len(_QUEUE)
        return {
            "enabled":          bool(config.get("feature_bus.enabled", False)),
            "healthy":          _HEALTHY,
            "running":          _RUNNING,
            "lastWriteMs":      _LAST_WRITE_MS,
            "queueDepth":       depth,
            "rowsToday":        _ROWS_TODAY,
            "blobWritesToday":  _BLOB_WRITES_TODAY,
            "lastError":        _LAST_ERROR,
            "dbPath":           str(config.get("feature_bus.db_path")) if config.get("feature_bus.enabled", False) else None,
        }


def record_trigger(alias: str, trig: Dict[str, Any], now_ms: int) -> None:
    """Enqueue an edge trigger. Fire-and-forget. NEVER raises. NEVER blocks > 50 ms.
    No-op when feature_bus is disabled, unhealthy, or the writer thread is not running."""
    if not _accepting_events():
        return
    try:
        _enqueue({"kind": "trigger_event", "payload": {
            "schema_version": SCHEMA_VERSION,
            "ts_ms":          int(now_ms),
            "alias":          str(alias or ""),
            "kind":           str(trig.get("kind") or "UNKNOWN"),
            "severity":       str(trig.get("severity") or "LOW"),
            "label":          trig.get("label"),
            "headline":       trig.get("headline"),
            "details":        trig.get("details"),
            "snapshot_ts_ms": int(now_ms),
        }})
    except Exception as exc:
        with _STATE_LOCK:
            global _HEALTHY, _LAST_ERROR
            _HEALTHY = False
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[feature_bus] record_trigger failed: {exc}\n")


def record_ai_turn(rec: AiTurnRecord) -> None:
    """Enqueue an AI turn for the writer thread. Caller returns immediately.
    NEVER raises. NEVER blocks - blob writes + DB insert happen on the
    writer thread in _drain_to_db().
    No-op when feature_bus is disabled, unhealthy, or the writer thread is not running."""
    if not _accepting_events():
        return
    try:
        _enqueue({"kind": "ai_turn", "payload": rec})
    except Exception as exc:
        with _STATE_LOCK:
            global _HEALTHY, _LAST_ERROR
            _HEALTHY = False
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[feature_bus] record_ai_turn enqueue failed: {exc}\n")


def _try_acquire_lock(db_path: "Path", hold_state_lock: bool = False) -> bool:
    """Acquire an advisory file lock next to the DB. Returns True on success.
    On failure, sets _HEALTHY=False with a descriptive _LAST_ERROR.

    Opens the lock file with mode 'a+' (append + read) rather than 'w' so that
    a second concurrent call does NOT truncate the file out from under the
    first holder. The file is created if missing and otherwise untouched.

    If hold_state_lock=True, assumes _STATE_LOCK is already held by the caller
    and does NOT attempt to reacquire it when setting error state."""
    global _LOCK_FILE_HANDLE, _HEALTHY, _LAST_ERROR
    import sqlite3
    from pathlib import Path
    lock_path = Path(str(db_path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = None
    try:
        f = open(lock_path, "a+")
        if _IS_WINDOWS:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_FILE_HANDLE = f
        return True
    except OSError as exc:
        if f is not None:
            try:
                f.close()
            except OSError:
                pass
        err_msg = f"lock acquisition failed: {exc}"
        if hold_state_lock:
            # Caller holds _STATE_LOCK; update directly
            _HEALTHY = False
            _LAST_ERROR = err_msg
        else:
            # Acquire the lock ourselves
            with _STATE_LOCK:
                _HEALTHY = False
                _LAST_ERROR = err_msg
        sys.stderr.write(f"[feature_bus] could not acquire {lock_path}: {exc}\n")
        return False


def _release_lock() -> None:
    global _LOCK_FILE_HANDLE
    f = _LOCK_FILE_HANDLE
    if f is None:
        return
    try:
        if _IS_WINDOWS:
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        f.close()
    except OSError:
        pass
    _LOCK_FILE_HANDLE = None


_DDL: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS snapshot_features (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      alias TEXT NOT NULL,
      health TEXT NOT NULL,
      bridge_error TEXT,
      session_code TEXT, session_anchor_mode TEXT, session_anchor_source TEXT,
      session_anchor_hhmm TEXT, session_anchor_tz TEXT, session_anchor_range_s INTEGER,
      mid REAL, spread REAL, best_bid REAL, best_ask REAL,
      or_high REAL, or_low REAL, or_width_pts REAL,
      middle_lock INTEGER, in_proximity INTEGER,
      flow_regime TEXT, flow_regime_conf REAL,
      flow_bias_score REAL, flow_bias_traj TEXT,
      tape_flow_delta REAL, tape_flow_fast30 REAL, tape_flow_slow5m REAL,
      momentum_i10 REAL, momentum_i50 REAL, momentum_i200 REAL, momentum_flag TEXT,
      vwap REAL, vwap_sigma REAL, vwap_sigma_z REAL, vwap_regime TEXT,
      vp_poc REAL, vp_vah REAL, vp_val REAL, va_state TEXT, hvn_count INTEGER, lvn_count INTEGER,
      conviction_score REAL, conviction_trend TEXT, conviction_trajectory TEXT,
      trend_kind TEXT, trend_renderable_kind TEXT, trend_eligible INTEGER,
      pax_decision TEXT, pax_size INTEGER, pax_size_tier TEXT, pax_confidence REAL,
      decision_verdict TEXT,
      position_size INTEGER, position_entry REAL, position_pnl REAL,
      news_blocked INTEGER, news_label TEXT,
      raw_json_sha256 TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_snap_alias_ts ON snapshot_features (alias, ts_ms)",
    """
    CREATE TABLE IF NOT EXISTS level_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      alias TEXT NOT NULL,
      level_label TEXT NOT NULL,
      level_price REAL,
      prev_decision TEXT, new_decision TEXT,
      prev_confidence REAL, new_confidence REAL,
      prev_proximity INTEGER, new_proximity INTEGER,
      trigger_reason TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lvl_alias_ts ON level_events (alias, ts_ms)",
    """
    CREATE TABLE IF NOT EXISTS microstructure_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      alias TEXT NOT NULL,
      event_type TEXT NOT NULL,
      price REAL, side TEXT, size REAL,
      raw_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_micro_alias_ts ON microstructure_events (alias, ts_ms)",
    """
    CREATE TABLE IF NOT EXISTS trigger_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      alias TEXT NOT NULL,
      kind TEXT NOT NULL,
      severity TEXT NOT NULL,
      label TEXT, headline TEXT, details TEXT,
      snapshot_ts_ms INTEGER NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_trig_alias_ts ON trigger_events (alias, ts_ms)",
    """
    CREATE TABLE IF NOT EXISTS ai_turns (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      chat_run_id TEXT NOT NULL,
      deep INTEGER NOT NULL,
      model TEXT NOT NULL,
      router_primary TEXT, router_secondary TEXT,
      user_text_raw TEXT NOT NULL,
      user_text_normalized TEXT NOT NULL,
      pax_text TEXT,
      snapshot_alias TEXT, snapshot_ts_ms INTEGER, snapshot_age_ms INTEGER,
      snapshot_sha256 TEXT NOT NULL,
      digest_sha256 TEXT NOT NULL,
      exit_code INTEGER,
      elapsed_ms INTEGER, api_duration_ms INTEGER,
      total_cost_usd REAL,
      input_tokens INTEGER, output_tokens INTEGER,
      cache_creation_tokens INTEGER, cache_read_tokens INTEGER,
      aborted INTEGER NOT NULL, error TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_aiturn_ts ON ai_turns (ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_aiturn_snapshot_sha ON ai_turns (snapshot_sha256)",
    "CREATE INDEX IF NOT EXISTS idx_aiturn_digest_sha ON ai_turns (digest_sha256)",
    """
    CREATE TABLE IF NOT EXISTS trade_outcomes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ai_turn_id INTEGER NOT NULL,
      alias TEXT NOT NULL,
      verdict TEXT NOT NULL,
      entry_price REAL,
      mid_at_t0 REAL,
      mid_at_t60s REAL, mid_at_t180s REAL, mid_at_t300s REAL, mid_at_t900s REAL,
      realized_r_at_t60s REAL, realized_r_at_t180s REAL,
      realized_r_at_t300s REAL, realized_r_at_t900s REAL,
      expected_r REAL, prob_pay REAL,
      invalidated INTEGER, invalidation_reason TEXT,
      label_method TEXT, labeled_at_ms INTEGER
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_outc_aiturn ON trade_outcomes (ai_turn_id)",
    """
    CREATE TABLE IF NOT EXISTS settings_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      ts_ms INTEGER NOT NULL,
      file_path TEXT NOT NULL,
      full_sha256 TEXT NOT NULL,
      diff_summary TEXT,
      actor TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS replay_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      schema_version INTEGER NOT NULL,
      created_ms INTEGER NOT NULL,
      alias TEXT NOT NULL,
      start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
      label TEXT, notes TEXT,
      ai_turn_ids_json TEXT
    )
    """,
]


def _open_db(path: Path) -> sqlite3.Connection:
    """Open SQLite with WAL + bounded busy timeout. Caller must use as context manager."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=2.0, isolation_level=None,
                            check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=2000")
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    """Run all DDL statements. Idempotent (IF NOT EXISTS everywhere)."""
    for stmt in _DDL:
        conn.execute(stmt)


def _canonical_snapshot_json(snap: Dict[str, Any]) -> str:
    """Stable, whitespace-free JSON. sort_keys=True; default=str for non-JSON-able types."""
    return json.dumps(snap, sort_keys=True, separators=(",", ":"), default=str)


def _date_partition(ts_ms: int) -> str:
    """UTC date partition for blob paths. UTC so the partition matches across timezones."""
    return _dt.datetime.utcfromtimestamp(ts_ms / 1000.0).strftime("%Y-%m-%d")


def _atomic_write_text(target: Path, content: str) -> None:
    """Write content to target via temp + rename. No-op if target already exists (idempotent)."""
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile + rename = atomic on the same filesystem.
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _write_snapshot_blob(canonical_json: str, ts_ms: int, root: Path) -> str:
    """Write a snapshot blob content-addressed. Returns the SHA-256.
    Idempotent: same content => same path, repeated calls are cheap no-ops."""
    sha = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    target = Path(root) / _date_partition(ts_ms) / f"{sha}.json"
    _atomic_write_text(target, canonical_json)
    return sha


def _write_digest_blob(digest_text: str, ts_ms: int, root: Path) -> str:
    """Write a digest blob content-addressed. Returns the SHA-256.
    Idempotent."""
    sha = hashlib.sha256(digest_text.encode("utf-8")).hexdigest()
    target = Path(root) / _date_partition(ts_ms) / f"{sha}.txt"
    _atomic_write_text(target, digest_text)
    return sha


def _level_key(lvl: Dict[str, Any]) -> str:
    return str(lvl.get("label") or "?")


def _micro_key(ev: Dict[str, Any]) -> tuple:
    return (str(ev.get("type") or ""),
            float(ev.get("price") or 0.0),
            int(ev.get("tsMs") or ev.get("ts") or 0))


def _detect_snapshot_deltas(prev: Optional[Dict[str, Any]],
                             new: Dict[str, Any],
                             now_ms: int) -> List[Dict[str, Any]]:
    """Pure delta detector. Returns a list of event dicts:
       [{"kind": "level_event"|"microstructure_event"|"trigger_event",
         "payload": {...}}, ...]
    The first tick (prev=None) emits zero delta events; the next tick can fire."""
    events: List[Dict[str, Any]] = []
    alias = str(new.get("alias") or "")
    if prev is None:
        return events

    # -- Level decision / confidence / proximity flips ---------------------
    prev_levels = {_level_key(l): l for l in
                    (prev.get("or_levels") or {}).get("levels") or []}
    new_levels = (new.get("or_levels") or {}).get("levels") or []
    for nl in new_levels:
        k = _level_key(nl)
        pl = prev_levels.get(k)
        if not pl:
            continue
        composite_changed = (
            pl.get("decision") != nl.get("decision")
            or float(pl.get("confidence") or 0) != float(nl.get("confidence") or 0)
        )
        # Per-level proximity flip. The level dict may omit `proximity` (older
        # snapshot shapes); only treat it as a flip when at least one side has
        # the key and the boolean values differ.
        pl_prox_raw = pl.get("proximity")
        nl_prox_raw = nl.get("proximity")
        pl_prox = bool(pl_prox_raw) if pl_prox_raw is not None else None
        nl_prox = bool(nl_prox_raw) if nl_prox_raw is not None else None
        proximity_changed = (
            (pl_prox is not None or nl_prox is not None)
            and pl_prox != nl_prox
        )
        if composite_changed or proximity_changed:
            events.append({"kind": "level_event", "payload": {
                "schema_version": SCHEMA_VERSION,
                "ts_ms":          now_ms,
                "alias":          alias,
                "level_label":    k,
                "level_price":    nl.get("price"),
                "prev_decision":  pl.get("decision"),
                "new_decision":   nl.get("decision"),
                "prev_confidence": pl.get("confidence"),
                "new_confidence":  nl.get("confidence"),
                "prev_proximity": (1 if pl_prox else 0) if pl_prox is not None else None,
                "new_proximity":  (1 if nl_prox else 0) if nl_prox is not None else None,
                "trigger_reason": "composite_flip" if composite_changed else "proximity_flip",
            }})

    # -- Microstructure new events ----------------------------------------
    prev_micro = {_micro_key(e) for e in
                   (prev.get("micro_events") or {}).get("events") or []}
    for ev in (new.get("micro_events") or {}).get("events") or []:
        if _micro_key(ev) in prev_micro:
            continue
        events.append({"kind": "microstructure_event", "payload": {
            "schema_version": SCHEMA_VERSION,
            "ts_ms":          int(ev.get("tsMs") or ev.get("ts") or now_ms),
            "alias":          alias,
            "event_type":     str(ev.get("type") or ""),
            "price":          ev.get("price"),
            "side":           ev.get("side"),
            "size":           ev.get("size"),
            "raw_json":       _canonical_snapshot_json(ev),
        }})

    # -- State-condition triggers (edges of conditions) -------------------
    prev_health = prev.get("health")
    new_health = new.get("health")
    if prev_health != new_health and new_health == "offline":
        events.append({"kind": "trigger_event", "payload": {
            "schema_version": SCHEMA_VERSION,
            "ts_ms":          now_ms,
            "alias":          alias,
            "kind":           "BRIDGE_DEGRADED",
            "severity":       "HIGH",
            "label":          "bridge offline",
            "headline":       new.get("bridgeError") or "",
            "details":        "",
            "snapshot_ts_ms": now_ms,
        }})

    prev_news = bool(((prev.get("gates") or {}).get("news") or {}).get("blocked"))
    new_news = bool(((new.get("gates") or {}).get("news") or {}).get("blocked"))
    if prev_news != new_news and new_news:
        events.append({"kind": "trigger_event", "payload": {
            "schema_version": SCHEMA_VERSION,
            "ts_ms":          now_ms,
            "alias":          alias,
            "kind":           "NEWS_BLACKOUT",
            "severity":       "HIGH",
            "label":          ((new.get("gates") or {}).get("news") or {}).get("label") or "",
            "headline":       "",
            "details":        "",
            "snapshot_ts_ms": now_ms,
        }})

    prev_prox = bool((prev.get("or_levels") or {}).get("inProximity"))
    new_prox = bool((new.get("or_levels") or {}).get("inProximity"))
    if prev_prox != new_prox and new_prox:
        events.append({"kind": "trigger_event", "payload": {
            "schema_version": SCHEMA_VERSION,
            "ts_ms":          now_ms,
            "alias":          alias,
            "kind":           "LEVEL_APPROACH",
            "severity":       "MED",
            "label":          "level approach",
            "headline":       "",
            "details":        "",
            "snapshot_ts_ms": now_ms,
        }})

    return events


def _drain_to_db() -> int:
    """Drain the queue into the DB. Returns number of rows written.
    Handles ai_turn events by writing both blobs FIRST (idempotent, content-
    addressed) then inserting the ai_turns row."""
    global _LAST_WRITE_MS, _ROWS_TODAY, _BLOB_WRITES_TODAY, _HEALTHY, _LAST_ERROR
    drained = 0
    with _QUEUE_LOCK:
        batch = list(_QUEUE)
        _QUEUE.clear()
    if not batch:
        return 0
    try:
        db_path   = Path(config.get("feature_bus.db_path"))
        snap_root = Path(config.get("feature_bus.snapshot_blob_dir"))
        dig_root  = Path(config.get("feature_bus.digest_blob_dir"))
        with _open_db(db_path) as conn:
            _ensure_schema(conn)
            conn.execute("BEGIN")
            for evt in batch:
                kind = evt.get("kind")
                p = evt.get("payload") or {}
                if kind == "snapshot_features":
                    _insert_snapshot_features(conn, p)
                elif kind == "level_event":
                    _insert_level_event(conn, p)
                elif kind == "microstructure_event":
                    _insert_microstructure_event(conn, p)
                elif kind == "trigger_event":
                    _insert_trigger_event(conn, p)
                elif kind == "ai_turn":
                    # `p` is an AiTurnRecord instance (not a plain dict).
                    _write_snapshot_blob(p.snapshot_json, p.ts_ms, snap_root)
                    _write_digest_blob(p.digest_text,    p.ts_ms, dig_root)
                    _insert_ai_turn(conn, p)
                    with _STATE_LOCK:
                        _BLOB_WRITES_TODAY += 2
                drained += 1
            conn.execute("COMMIT")
        with _STATE_LOCK:
            _LAST_WRITE_MS = int(time.time() * 1000)
            _ROWS_TODAY += drained
    except Exception as exc:
        with _STATE_LOCK:
            _HEALTHY = False
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[feature_bus] drain failed: {exc}\n")
    return drained


def _insert_ai_turn(conn: sqlite3.Connection, rec: "AiTurnRecord") -> None:
    """Insert one ai_turns row from an AiTurnRecord."""
    conn.execute("""
        INSERT INTO ai_turns (
          schema_version, ts_ms, chat_run_id, deep, model,
          router_primary, router_secondary,
          user_text_raw, user_text_normalized, pax_text,
          snapshot_alias, snapshot_ts_ms, snapshot_age_ms,
          snapshot_sha256, digest_sha256,
          exit_code, elapsed_ms, api_duration_ms,
          total_cost_usd, input_tokens, output_tokens,
          cache_creation_tokens, cache_read_tokens,
          aborted, error
        ) VALUES (?,?,?,?,?,  ?,?,  ?,?,?,  ?,?,?,  ?,?,  ?,?,?,  ?,?,?,  ?,?,  ?,?)
    """, (
        rec.schema_version, rec.ts_ms, rec.chat_run_id, int(rec.deep), rec.model,
        rec.router_primary,
        json.dumps(rec.router_secondary) if rec.router_secondary else None,
        rec.user_text_raw, rec.user_text_normalized, rec.pax_text,
        rec.snapshot_alias, rec.snapshot_ts_ms, rec.snapshot_age_ms,
        rec.snapshot_sha256, rec.digest_sha256,
        rec.exit_code, rec.elapsed_ms, rec.api_duration_ms,
        rec.total_cost_usd, rec.input_tokens, rec.output_tokens,
        rec.cache_creation_tokens, rec.cache_read_tokens,
        int(rec.aborted), rec.error,
    ))


# -- Live snapshot capture (writer-thread-owned) ----------------------------

def _project_snapshot_features(snap: Dict[str, Any], now_ms: int) -> Dict[str, Any]:
    """Build a minimal snapshot_features payload from the dashboard snapshot.
    Phase 1: only fills the columns we have cheap access to. Missing columns
    stay NULL in the DB. Extend in a future schema_version bump if needed."""
    book  = snap.get("book") or {}
    ors   = snap.get("or_levels") or {}
    flow  = snap.get("flow") or {}
    gates = snap.get("gates") or {}
    sess  = (gates.get("session") or {}) or (snap.get("session") or {})
    news  = (gates.get("news")    or {}) or (snap.get("news")    or {})
    conv  = snap.get("conviction") or {}
    trend = snap.get("trend_signal") or {}
    pax   = snap.get("pax") or {}
    return {
        "schema_version":          SCHEMA_VERSION,
        "ts_ms":                   now_ms,
        "alias":                   str(snap.get("alias") or ""),
        "health":                  str(snap.get("health") or "ok"),
        "bridge_error":            snap.get("bridgeError"),
        "session_code":            sess.get("code"),
        "session_anchor_mode":     sess.get("anchorMode"),
        "session_anchor_source":   sess.get("anchorSource"),
        "session_anchor_hhmm":     sess.get("anchorHHMM"),
        "session_anchor_tz":       sess.get("anchorTimezone"),
        "session_anchor_range_s":  sess.get("anchorRangeSeconds"),
        "mid":                     book.get("mid"),
        "spread":                  book.get("spread"),
        "best_bid":                book.get("bestBid"),
        "best_ask":                book.get("bestAsk"),
        "or_high":                 ors.get("orHigh"),
        "or_low":                  ors.get("orLow"),
        "or_width_pts":            ors.get("orWidthPts"),
        "middle_lock":             1 if ors.get("middleLock") else 0,
        "in_proximity":            1 if ors.get("inProximity") else 0,
        "flow_regime":             flow.get("regime"),
        "flow_regime_conf":        flow.get("regimeConfidence"),
        "flow_bias_score":         flow.get("biasScore"),
        "flow_bias_traj":          flow.get("biasTrajectory"),
        "conviction_score":        conv.get("score"),
        "conviction_trend":        conv.get("trend"),
        "conviction_trajectory":   conv.get("trajectory"),
        "trend_kind":              trend.get("kind"),
        "trend_renderable_kind":   trend.get("renderableKind"),
        "trend_eligible":          1 if trend.get("eligible") else 0,
        "pax_decision":            pax.get("decision"),
        "pax_size":                pax.get("size"),
        "pax_size_tier":           pax.get("size_tier"),
        "pax_confidence":          pax.get("confidence"),
        "decision_verdict":        (snap.get("decision") or {}).get("verdict"),
        "news_blocked":            1 if news.get("blocked") else 0,
        "news_label":              news.get("label"),
    }


def _writer_tick_live_capture(now_ms: int) -> None:
    """One pulse of live snapshot capture. Writer-thread-only.
    Reads poller.latest(). Tolerates snap=None on cold start (no fetch yet).
    Enqueues a snapshot_features row + any delta events vs the prior tick."""
    global _PREV_SNAP
    try:
        from . import poller       # lazy import keeps test mocks scoped
        snap, _as_of_ms, _age_ms, _fails, _err = poller.latest()
    except Exception as exc:
        sys.stderr.write(f"[feature_bus] poller.latest failed: {exc}\n")
        return
    if snap is None:
        return                     # cold start; poller has no fetch yet

    # Always enqueue a snapshot_features row (subject to back-pressure drop).
    _enqueue({"kind": "snapshot_features",
                "payload": _project_snapshot_features(snap, now_ms)})

    # Delta events vs prior snap.
    for ev in _detect_snapshot_deltas(_PREV_SNAP, snap, now_ms):
        _enqueue(ev)

    _PREV_SNAP = snap


_SNAPSHOT_FEATURES_COLUMNS = (
    "schema_version", "ts_ms", "alias", "health", "bridge_error",
    "session_code", "session_anchor_mode", "session_anchor_source",
    "session_anchor_hhmm", "session_anchor_tz", "session_anchor_range_s",
    "mid", "spread", "best_bid", "best_ask",
    "or_high", "or_low", "or_width_pts",
    "middle_lock", "in_proximity",
    "flow_regime", "flow_regime_conf",
    "flow_bias_score", "flow_bias_traj",
    "conviction_score", "conviction_trend", "conviction_trajectory",
    "trend_kind", "trend_renderable_kind", "trend_eligible",
    "pax_decision", "pax_size", "pax_size_tier", "pax_confidence",
    "decision_verdict",
    "news_blocked", "news_label",
)


def _insert_snapshot_features(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    """Insert one snapshot_features row. Persists every column projected by
    _project_snapshot_features. Phase 1 intentionally writes the human-readable
    surface area (mid/spread/OR/flow/conviction/news/etc.); raw tape/momentum/VWAP
    columns stay NULL until a Phase-3 consumer asks for them."""
    cols   = _SNAPSHOT_FEATURES_COLUMNS
    values = tuple(p.get(c) for c in cols)
    placeholders = ",".join(["?"] * len(cols))
    conn.execute(
        f"INSERT INTO snapshot_features ({','.join(cols)}) VALUES ({placeholders})",
        values,
    )


def _insert_level_event(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    conn.execute("""
        INSERT INTO level_events (schema_version, ts_ms, alias, level_label, level_price,
                                    prev_decision, new_decision, prev_confidence, new_confidence,
                                    prev_proximity, new_proximity, trigger_reason)
        VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?)
    """, (p["schema_version"], p["ts_ms"], p["alias"], p["level_label"], p.get("level_price"),
          p.get("prev_decision"), p.get("new_decision"),
          p.get("prev_confidence"), p.get("new_confidence"),
          p.get("prev_proximity"), p.get("new_proximity"),
          p.get("trigger_reason")))


def _insert_microstructure_event(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    conn.execute("""
        INSERT INTO microstructure_events (schema_version, ts_ms, alias, event_type,
                                              price, side, size, raw_json)
        VALUES (?,?,?,?, ?,?,?,?)
    """, (p["schema_version"], p["ts_ms"], p["alias"], p["event_type"],
          p.get("price"), p.get("side"), p.get("size"), p.get("raw_json")))


def _insert_trigger_event(conn: sqlite3.Connection, p: Dict[str, Any]) -> None:
    conn.execute("""
        INSERT INTO trigger_events (schema_version, ts_ms, alias, kind, severity,
                                       label, headline, details, snapshot_ts_ms)
        VALUES (?,?,?,?,?, ?,?,?,?)
    """, (p["schema_version"], p["ts_ms"], p["alias"], p["kind"], p["severity"],
          p.get("label"), p.get("headline"), p.get("details"),
          int(p["snapshot_ts_ms"])))


def _writer_loop() -> None:
    """Writer thread. Two responsibilities, two cadences:
      (1) Drain the queue into the DB every writer_idle_ms (default 100 ms).
          This is fast and bounded so events land in the DB promptly.
      (2) Live snapshot capture every capture_ms (default 1000 ms).
          This reads poller.latest() and enqueues a snapshot_features row
          + any delta events vs the prior captured tick. Capture is at most
          once per capture_ms regardless of how often the writer wakes up.
    Capture cadence should match config.poll_ms so the bus and the poller
    are in phase; misalignment just causes a few extra polls reading the
    same cached snapshot and is harmless."""
    global _RUNNING, _PREV_SNAP, _LAST_CAPTURE_MS
    with _STATE_LOCK:
        _RUNNING = True
    _PREV_SNAP = None
    _LAST_CAPTURE_MS = 0
    sys.stderr.write("[feature_bus] writer started\n")
    idle_ms = int(config.get("feature_bus.writer_idle_ms", 100))
    try:
        while not _STOP_EVT.is_set():
            now_ms = int(time.time() * 1000)
            capture_ms = int(config.get("feature_bus.capture_ms", 1000))
            if (now_ms - _LAST_CAPTURE_MS) >= capture_ms:
                _writer_tick_live_capture(now_ms)
                _LAST_CAPTURE_MS = now_ms
            _drain_to_db()
            _STOP_EVT.wait(idle_ms / 1000.0)
    finally:
        # Final drain on shutdown so we don't lose buffered events.
        _drain_to_db()
        with _STATE_LOCK:
            _RUNNING = False
        sys.stderr.write("[feature_bus] writer stopped\n")
