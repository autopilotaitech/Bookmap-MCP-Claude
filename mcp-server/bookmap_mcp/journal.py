"""Phase 3: durable SQLite journal for the paper-trading daemon.

One DB at `D:\\BookmapLogs\\pax-journal.db` (or wherever the CLI points).
WAL mode; single writer (the daemon), concurrent readers (the Phase 4
overview UI and Phase 5 batch jobs).

Tables (see plan §7):
  runs            — one row per daemon process instance
  snapshots       — compact summary per poll (NOT the full snap blob)
  signals         — one row per Pax decision
  orders          — sim order placements
  fills           — sim fill events
  positions       — position snapshots at fill boundaries
  daily_stats     — per-session aggregates
  adapter_health  — heartbeats
  events          — RUN_CRASHED, ERROR, WARN, INFO

Append-only from the daemon's perspective. Pruning lives in
`tools/journal_prune.py` (later) — never inside the daemon.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id          TEXT PRIMARY KEY,
  started_ms      INTEGER NOT NULL,
  ended_ms        INTEGER,
  adapter_name    TEXT NOT NULL,
  adapter_config  TEXT,
  signal_version  TEXT NOT NULL DEFAULT '',
  weights_hash    TEXT NOT NULL DEFAULT '',
  notes           TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_ms);

CREATE TABLE IF NOT EXISTS snapshots (
  run_id          TEXT NOT NULL,
  ts_ms           INTEGER NOT NULL,
  alias           TEXT NOT NULL,
  mid             REAL,
  best_bid        REAL,
  best_ask        REAL,
  spread          REAL,
  vwap            REAL,
  vwap_stddev     REAL,
  or_high         REAL,
  or_low          REAL,
  regime          TEXT,
  bias_score      REAL,
  bias_trajectory TEXT,
  health          TEXT,
  synthetic       TEXT,
  PRIMARY KEY (run_id, ts_ms, alias)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_alias_ts ON snapshots(alias, ts_ms);

CREATE TABLE IF NOT EXISTS signals (
  run_id          TEXT NOT NULL,
  ts_ms           INTEGER NOT NULL,
  alias           TEXT NOT NULL,
  decision        TEXT NOT NULL,
  size_tier       TEXT,
  confidence      REAL,
  level_label     TEXT,
  level_price     REAL,
  composite_score REAL,
  composite_dir   TEXT,
  conviction_score      REAL,
  conviction_trajectory TEXT,
  components_json TEXT NOT NULL,
  reasons_json    TEXT,
  PRIMARY KEY (run_id, ts_ms, alias)
);
CREATE INDEX IF NOT EXISTS idx_signals_decision_ts ON signals(decision, ts_ms);

CREATE TABLE IF NOT EXISTS orders (
  run_id          TEXT NOT NULL,
  sim_order_id    INTEGER NOT NULL,
  alias           TEXT NOT NULL,
  ts_ms           INTEGER NOT NULL,
  side            TEXT NOT NULL,
  kind            TEXT NOT NULL,
  parent_id       INTEGER,
  armed           INTEGER NOT NULL,
  price           REAL,
  stop_price      REAL,
  size            INTEGER NOT NULL,
  PRIMARY KEY (run_id, sim_order_id)
);

CREATE TABLE IF NOT EXISTS fills (
  run_id          TEXT NOT NULL,
  fill_id         INTEGER NOT NULL,
  sim_order_id    INTEGER NOT NULL,
  ts_ms           INTEGER NOT NULL,
  price           REAL NOT NULL,
  size            INTEGER NOT NULL,
  PRIMARY KEY (run_id, fill_id)
);

CREATE TABLE IF NOT EXISTS positions (
  run_id          TEXT NOT NULL,
  ts_ms           INTEGER NOT NULL,
  alias           TEXT NOT NULL,
  position        INTEGER NOT NULL,
  avg_price       REAL,
  realized_pnl    REAL,
  unrealized_pnl  REAL,
  PRIMARY KEY (run_id, ts_ms, alias)
);

CREATE TABLE IF NOT EXISTS daily_stats (
  run_id          TEXT NOT NULL,
  session_date    TEXT NOT NULL,
  alias           TEXT NOT NULL,
  trades_count    INTEGER NOT NULL DEFAULT 0,
  wins            INTEGER NOT NULL DEFAULT 0,
  losses          INTEGER NOT NULL DEFAULT 0,
  gross_pnl       REAL NOT NULL DEFAULT 0,
  max_drawdown    REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (run_id, session_date, alias)
);

CREATE TABLE IF NOT EXISTS adapter_health (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          TEXT NOT NULL,
  ts_ms           INTEGER NOT NULL,
  status          TEXT NOT NULL,
  detail          TEXT,
  snapshots_emitted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_adapter_health_run_ts ON adapter_health(run_id, ts_ms);

CREATE TABLE IF NOT EXISTS events (
  run_id          TEXT NOT NULL,
  seq             INTEGER NOT NULL,
  ts_ms           INTEGER NOT NULL,
  kind            TEXT NOT NULL,
  source          TEXT NOT NULL,
  message         TEXT NOT NULL,
  payload_json    TEXT,
  PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts_ms);
"""


def _now_ms() -> int:
    return int(time.time() * 1000)


class Journal:
    """Append-only durable log for the Pax daemon."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._conn: Optional[sqlite3.Connection] = None
        self.run_id: Optional[str] = None
        self._event_seq: int = 0

    # ─── lifecycle ──────────────────────────────────────────────────

    def open(self) -> None:
        if self._conn is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False,
                                       timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.commit()
            self._conn.close()
        finally:
            self._conn = None

    def __enter__(self) -> "Journal":
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ─── run lifecycle ───────────────────────────────────────────────

    def begin_run(self, adapter_name: str,
                   adapter_config: Optional[Dict[str, Any]] = None,
                   signal_version: str = "",
                   weights_hash: str = "",
                   notes: Optional[str] = None) -> str:
        """Open a new run. If a prior run is still unended (daemon crashed),
        log RUN_CRASHED in that run and mark it closed."""
        assert self._conn is not None, "open() the journal first"
        now = _now_ms()
        prev = self._conn.execute(
            "SELECT run_id FROM runs WHERE ended_ms IS NULL "
            "ORDER BY started_ms DESC LIMIT 1").fetchone()
        if prev is not None:
            self._conn.execute(
                "UPDATE runs SET ended_ms=?, "
                "notes=COALESCE(notes,'') || ' | unclean-shutdown-recovered' "
                "WHERE run_id=?",
                (now, prev["run_id"]))
            # Log a RUN_CRASHED event in the OLD run.
            self.run_id = prev["run_id"]
            self.write_event("RUN_CRASHED", "daemon",
                              "previous run did not end gracefully")
        # Open new run.
        self.run_id = str(uuid.uuid4())
        self._event_seq = 0
        self._conn.execute(
            "INSERT INTO runs(run_id, started_ms, adapter_name, "
            "adapter_config, signal_version, weights_hash, notes) "
            "VALUES(?,?,?,?,?,?,?)",
            (self.run_id, now, adapter_name,
             json.dumps(adapter_config or {}),
             signal_version, weights_hash, notes))
        self._conn.commit()
        return self.run_id

    def end_run(self, notes: Optional[str] = None) -> None:
        """Mark the current run ended cleanly."""
        assert self._conn is not None
        if not self.run_id:
            return
        if notes:
            self._conn.execute(
                "UPDATE runs SET ended_ms=?, "
                "notes=COALESCE(notes,'') || ? WHERE run_id=?",
                (_now_ms(), f" | {notes}", self.run_id))
        else:
            self._conn.execute(
                "UPDATE runs SET ended_ms=? WHERE run_id=?",
                (_now_ms(), self.run_id))
        self._conn.commit()

    # ─── writers ─────────────────────────────────────────────────────

    def write_snapshot(self, snap: Dict[str, Any]) -> None:
        assert self._conn is not None and self.run_id
        book = snap.get("book") or {}
        vwap_obj = snap.get("vwap_obj") or {}
        or_row = snap.get("or_row") or {}
        flow = snap.get("flow") or {}
        ts_ms = _snap_ts_ms(snap)
        synthetic = ",".join(snap.get("_synthetic") or [])

        def _of(d, k):
            v = d.get(k) if isinstance(d, dict) else None
            try: return float(v) if v is not None else None
            except (TypeError, ValueError): return None

        self._conn.execute(
            "INSERT OR REPLACE INTO snapshots(run_id, ts_ms, alias, mid, "
            "best_bid, best_ask, spread, vwap, vwap_stddev, or_high, "
            "or_low, regime, bias_score, bias_trajectory, health, synthetic) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, ts_ms, snap.get("alias") or "UNKNOWN",
             _of(book, "mid"), _of(book, "bestBid"), _of(book, "bestAsk"),
             _of(book, "spread"),
             _of(vwap_obj, "vwap"), _of(vwap_obj, "stddev"),
             _of(or_row, "orHigh"), _of(or_row, "orLow"),
             flow.get("regime") if isinstance(flow, dict) else None,
             _of(flow, "biasScore"),
             flow.get("biasTrajectory") if isinstance(flow, dict) else None,
             snap.get("health"), synthetic))

    def write_signal(self, snap: Dict[str, Any],
                      decision: Dict[str, Any]) -> None:
        """Persist a Pax decision. `decision` is the dict from `pax_decision`."""
        assert self._conn is not None and self.run_id
        ts_ms = _snap_ts_ms(snap)
        or_levels = snap.get("or_levels") or {}
        conv = snap.get("conviction") or {}
        # Look up the level the decision is about (matching label) for composite.
        composite_score = None
        composite_dir = None
        for lvl in (or_levels.get("levels") or []):
            if lvl.get("label") == decision.get("level_label"):
                comp = lvl.get("composite") or {}
                composite_score = comp.get("score")
                composite_dir = comp.get("direction")
                break
        self._conn.execute(
            "INSERT OR REPLACE INTO signals(run_id, ts_ms, alias, decision, "
            "size_tier, confidence, level_label, level_price, "
            "composite_score, composite_dir, conviction_score, "
            "conviction_trajectory, components_json, reasons_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.run_id, ts_ms, snap.get("alias") or "UNKNOWN",
             decision.get("decision") or "NONE",
             decision.get("size_tier"),
             decision.get("confidence"),
             decision.get("level_label"),
             decision.get("entry"),
             composite_score, composite_dir,
             conv.get("score") if isinstance(conv, dict) else None,
             conv.get("trajectory") if isinstance(conv, dict) else None,
             json.dumps(decision.get("components") or {}),
             json.dumps(decision.get("reasons") or [])))

    def write_adapter_health(self, health) -> None:
        """Record an AdapterHealth row (or any dict with the same shape)."""
        assert self._conn is not None and self.run_id
        status = getattr(health, "status", None) or health.get("status", "ok")
        detail = getattr(health, "detail", None) or (
            health.get("detail", "") if isinstance(health, dict) else "")
        emitted = getattr(health, "snapshots_emitted", 0) or 0
        self._conn.execute(
            "INSERT INTO adapter_health(run_id, ts_ms, status, "
            "detail, snapshots_emitted) VALUES(?,?,?,?,?)",
            (self.run_id, _now_ms(), status, detail, emitted))

    def write_event(self, kind: str, source: str, message: str,
                     payload: Optional[Dict[str, Any]] = None) -> None:
        assert self._conn is not None and self.run_id
        self._event_seq += 1
        self._conn.execute(
            "INSERT INTO events(run_id, seq, ts_ms, kind, source, message, "
            "payload_json) VALUES(?,?,?,?,?,?,?)",
            (self.run_id, self._event_seq, _now_ms(), kind, source, message,
             json.dumps(payload) if payload is not None else None))

    def commit(self) -> None:
        if self._conn is not None:
            self._conn.commit()


def _snap_ts_ms(snap: Dict[str, Any]) -> int:
    """Best-effort extraction of a snapshot timestamp in ms."""
    ts = snap.get("ts")
    if isinstance(ts, str):
        try:
            import datetime as _dt
            t = _dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return int(t.timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    return _now_ms()
