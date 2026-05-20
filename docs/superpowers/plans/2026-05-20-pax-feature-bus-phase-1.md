# Pax Feature Bus - Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a passive, disabled-by-default feature bus that captures snapshot features, level/microstructure/trigger events, and Claude AI turns to `D:\BookmapLogs\pax-bus.db` + content-addressed snapshot/digest blob stores - WITHOUT changing any live behavior of Pax AI when `feature_bus.enabled=false` (and with byte-identical chat / whynow output even when `feature_bus.enabled=true`).

**Architecture:** New module `pax-ai/pax_ai/feature_bus.py` owns a single daemon writer thread + SQLite DB + filesystem blob stores. Started from `__main__.py::main` AFTER `poller.start()` and BEFORE `journal.init()`, guarded by `config.feature_bus.enabled`. Three integration hooks: (1) `triggers._emit_edge` gets a lazy-imported fire-and-forget outbound call; (2) `chat.py::handle_chat_stream` assembles an `AiTurnRecord` after the SSE `done` event is flushed; (3) `server.py::_api_pax_health` includes a `feature_bus` status block. No other production code is touched.

**Tech Stack:** Python 3.11, stdlib only (sqlite3, threading, hashlib, json, dataclasses, pathlib, fcntl/msvcrt for advisory lock). pytest for tests.

**Approved spec:** [`docs/superpowers/specs/2026-05-20-pax-feature-bus-design.md`](../specs/2026-05-20-pax-feature-bus-design.md)

---

## Phase 1 Constraints (Non-Negotiable)

- `feature_bus.enabled=false` by default - flip to `true` only on the dev machine first.
- When disabled: zero writes to bus DB, zero blob files created, zero behavior change anywhere.
- When enabled: still no behavior change in chat/UI/prompt/router/trading paths. The bus is a shadow capture.
- `/api/pax/whynow` JSON output must be byte-identical with and without the bus enabled.
- Golden SSE byte stream from `handle_chat_stream` must be byte-identical with and without the bus enabled (cost/usage payload included).
- No commits performed by the plan executor. Final commit is an explicit operator-driven task at the end.

## Files NOT touched in Phase 1 (hard line)

| File | Why off-limits |
|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Snapshot composer is the source of truth, not a Pax AI concern |
| `mcp-server/bookmap_mcp/or_session.py` | OR anchor invariant is owned upstream |
| `mcp-server/bookmap_mcp/pax_weights.json` | No conviction-weight tuning in this phase |
| `mcp-server/bookmap_mcp/pax_replay.py` | Existing CSV-era tool; bus-era replay is a Phase-4 file `pax_bus_replay.py` |
| `mcp-server/bookmap_mcp/pax_outcomes.py` | Existing CSV-era forward-return tracker; bus-era outcome labeler is `pax-ai/pax_ai/outcomes.py` in Phase 4 |
| `pax-ai/pax_ai/prompts.py` | System prompt is frozen + cached on disk; do not touch |
| `pax-ai/pax_ai/claude_stream.py` | `--tools "" --max-turns 1` invariants are locked; on_done callback shape is unchanged |
| `pax-ai/pax_ai/edge_calculus.py` | Math owner; no R-table change |
| `pax-ai/pax_ai/journal.py` | Chat journal stays the way it is; bus is a parallel DB |
| `pax-ai/pax_ai/static/index.html` | UI changes are Phase 2 |
| `indicators/OpenRange/**` | Bookmap addon work is separate |
| `pax-ai/pax_ai/triggers.py::compute_triggers` | Only `_emit_edge` gets a one-line hook; the trigger compute path itself is unchanged |

---

## File Structure

### New files (under `pax-ai/`)

| Path | Responsibility | LOC est. |
|---|---|---|
| `pax-ai/pax_ai/feature_bus.py` | Writer daemon, schema DDL, blob stores, AiTurnRecord, advisory lock, status | ~450 |
| `pax-ai/tests/test_feature_bus_schema.py` | DDL + schema_version + dedup invariants | ~180 |
| `pax-ai/tests/test_feature_bus_blob_stores.py` | Snapshot + digest blob content-addressed round-trip + idempotency | ~120 |
| `pax-ai/tests/test_feature_bus_ai_turn.py` | AiTurnRecord shape + record_ai_turn semantics + null-sha rejection | ~180 |
| `pax-ai/tests/test_feature_bus_triggers.py` | `_emit_edge` hook + `compute_triggers` never called by bus + whynow byte-identical | ~200 |
| `pax-ai/tests/test_feature_bus_delta_detector.py` | Snapshot delta logic for level / micro / state-condition events | ~220 |
| `pax-ai/tests/test_feature_bus_writer.py` | Writer thread loop, back-pressure, disabled = zero writes, concurrent writer lock, live snapshot capture loop, delta-driven row capture, None-snap tolerance, full snapshot_features column persistence, ai_turn writer-thread persistence | ~440 |
| `pax-ai/tests/test_feature_bus_integration.py` | End-to-end SSE byte-identical + health endpoint exposes bus + startup order | ~250 |

### Modified files (small surgical edits)

| Path | What changes |
|---|---|
| `pax-ai/pax_ai/config.py` | Add `feature_bus` block to `DEFAULTS` |
| `pax-ai/pax_ai/triggers.py` | Add optional `alias=None` kwarg to `_emit_edge`; one-line fire-and-forget hook into `feature_bus.record_trigger` at the end of `_emit_edge`; update each caller to pass `alias=str(snap.get("alias") or ALIAS_DEFAULT)` (the real `_trig_*` helpers have `snap` in scope, not `alias`) |
| `pax-ai/pax_ai/chat.py` | After SSE `done` flush, assemble `AiTurnRecord` from in-scope variables and call `feature_bus.record_ai_turn(record)` inside a guarded try/except |
| `pax-ai/pax_ai/server.py::_api_pax_health` | Append `"feature_bus": feature_bus.status()` to response body |
| `pax-ai/pax_ai/__main__.py::main` | After `poller.start()`, before `journal.init()`, add guarded `feature_bus.start()` call |
| `pax-ai/pax_ai/__init__.py` | Bump `__version__` from `"0.0.1"` to `"0.1.0"` |
| `pax-ai/pyproject.toml` | Bump `version = "0.0.1"` to `version = "0.1.0"` |

---

## Data Model + API Shape

### Public API of `pax_ai.feature_bus`

```python
def start() -> None
    """Idempotent. Spawns the writer thread if feature_bus.enabled and not already running.
    Acquires advisory file lock on db_path + ".lock". On lock contention, logs and exits cleanly.
    On any failure (DB open error, disk full, lock contention) the module stays in 'unhealthy' state
    and ALL record_* functions become no-ops; no exception ever escapes start()."""

def stop(timeout_s: float = 2.0) -> None
    """Signal the writer thread to stop and join with timeout. Safe to call when not running."""

def status() -> Dict[str, Any]
    """Return a JSON-serializable status dict:
        {
          "enabled": bool,
          "healthy": bool,
          "running": bool,
          "lastWriteMs": int,        # 0 if no writes yet
          "queueDepth": int,
          "rowsToday": int,
          "blobWritesToday": int,
          "lastError": Optional[str],
          "dbPath": Optional[str],
        }
    Cheap (in-memory counters). Safe to call from any thread."""

def record_trigger(alias: str, trig: Dict[str, Any], now_ms: int) -> None
    """Enqueue an edge trigger for persistence. Fire-and-forget.
    NEVER raises. NEVER blocks. NEVER calls back into triggers.py.
    No-op when feature_bus is disabled or unhealthy."""

def record_ai_turn(rec: "AiTurnRecord") -> None
    """Enqueue an AI turn for persistence. The writer thread (not the caller)
    does the two blob writes + the ai_turns row insert.
    NEVER raises into the caller. NEVER blocks - work happens off-thread.
    No-op when feature_bus is disabled or unhealthy."""

@dataclass(frozen=True)
class AiTurnRecord:
    schema_version:        int                       # always 1 in Phase 1
    ts_ms:                 int                       # time.time_ns() // 1_000_000 at end of chat
    chat_run_id:           str                       # journal.current_run_id()
    deep:                  bool
    model:                 str
    router_primary:        Optional[str]
    router_secondary:      Optional[List[str]]
    user_text_raw:         str                       # NOT NULL
    user_text_normalized:  str                       # NOT NULL
    digest_text:           str                       # full_msg from build_user_message (in-memory only; not stored as a column)
    digest_sha256:         str                       # hashlib.sha256(digest_text.encode("utf-8")).hexdigest()
    snapshot_json:         str                       # canonical JSON of the snapshot the digest was built from (in-memory only)
    snapshot_sha256:       str                       # hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
    snapshot_alias:        Optional[str]
    snapshot_ts_ms:        Optional[int]
    snapshot_age_ms:       int
    pax_text:              str                       # accumulated streamed tokens
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
```

### Storage layout

```
D:\BookmapLogs\
  pax-bus.db                              # 8 tables, WAL
  pax-bus.db-wal                          # WAL log (auto-managed)
  pax-bus.db-shm                          # shared-memory file (auto-managed)
  pax-bus.db.lock                         # advisory lock sentinel file
  pax-snapshots\
    2026-05-20\
      <sha256>.json                       # canonical JSON, one file per unique snapshot
  pax-digests\
    2026-05-20\
      <sha256>.txt                        # raw bytes of digest, one file per unique digest
```

### Config keys (added to `DEFAULTS` in `config.py`)

```python
"feature_bus": {
    "enabled":           False,
    "db_path":           "D:/BookmapLogs/pax-bus.db",
    "snapshot_blob_dir": "D:/BookmapLogs/pax-snapshots",
    "digest_blob_dir":   "D:/BookmapLogs/pax-digests",
    "queue_max":         2000,
    "writer_idle_ms":    100,            # writer loop drain cadence
    "capture_ms":        1000,           # live snapshot-capture cadence (1 Hz; should match config.poll_ms)
    "retention_days":    30,             # not used until Phase 4 prune tool
},
```

---

## Task List

### Task 1: Config keys

**Files:**
- Modify: `pax-ai/pax_ai/config.py` (insert into `DEFAULTS` dict)
- Test: `pax-ai/tests/test_config.py` (add one test)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_config.py`:

```python
def test_feature_bus_defaults_present_and_disabled():
    """feature_bus.* keys must exist with safe Phase-1 defaults."""
    from pax_ai import config
    assert config.get("feature_bus.enabled") is False
    assert isinstance(config.get("feature_bus.db_path"), str)
    assert isinstance(config.get("feature_bus.snapshot_blob_dir"), str)
    assert isinstance(config.get("feature_bus.digest_blob_dir"), str)
    assert config.get("feature_bus.queue_max") == 2000
    assert config.get("feature_bus.writer_idle_ms") == 100
    assert config.get("feature_bus.capture_ms") == 1000
    assert config.get("feature_bus.retention_days") == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_config.py::test_feature_bus_defaults_present_and_disabled -v`
Expected: FAIL with `AssertionError` (feature_bus.enabled returns None / default).

- [ ] **Step 3: Implement the keys**

In `pax-ai/pax_ai/config.py`, inside the `DEFAULTS` dict, after the existing `directional_R_table` block, add:

```python
    "feature_bus": {
        "enabled":           False,
        "db_path":           "D:/BookmapLogs/pax-bus.db",
        "snapshot_blob_dir": "D:/BookmapLogs/pax-snapshots",
        "digest_blob_dir":   "D:/BookmapLogs/pax-digests",
        "queue_max":         2000,
        "writer_idle_ms":    100,
        "capture_ms":        1000,
        "retention_days":    30,
    },
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_config.py -v`
Expected: PASS (existing tests + the new one).

---

### Task 2: feature_bus.py module skeleton + AiTurnRecord dataclass

**Files:**
- Create: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_ai_turn.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_ai_turn.py`:

```python
"""AiTurnRecord shape + null-sha rejection.

Phase 1 invariant: digest_sha256 and snapshot_sha256 are required and
non-empty; constructing an AiTurnRecord without them must raise.
"""
from __future__ import annotations

import pytest

from pax_ai import feature_bus


def _valid_kwargs(**overrides):
    base = dict(
        schema_version=1, ts_ms=0, chat_run_id="r1", deep=False, model="m",
        router_primary=None, router_secondary=None,
        user_text_raw="u", user_text_normalized="u",
        digest_text="d", digest_sha256="d" * 64,
        snapshot_json="{}", snapshot_sha256="s" * 64,
        snapshot_alias=None, snapshot_ts_ms=None, snapshot_age_ms=0,
        pax_text="", exit_code=0, elapsed_ms=0, api_duration_ms=None,
        total_cost_usd=None, input_tokens=None, output_tokens=None,
        cache_creation_tokens=None, cache_read_tokens=None,
        aborted=False, error=None,
    )
    base.update(overrides)
    return base


def test_aiturn_record_constructs_with_full_kwargs():
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    assert rec.digest_sha256 == "d" * 64
    assert rec.snapshot_sha256 == "s" * 64


def test_aiturn_record_rejects_empty_digest_sha256():
    with pytest.raises(ValueError, match="digest_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(digest_sha256=""))


def test_aiturn_record_rejects_empty_snapshot_sha256():
    with pytest.raises(ValueError, match="snapshot_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(snapshot_sha256=""))


def test_aiturn_record_is_frozen():
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    with pytest.raises(Exception):       # FrozenInstanceError
        rec.digest_sha256 = "xxxxxxxx"


def test_module_exports_status_returns_disabled_safe_default():
    s = feature_bus.status()
    assert s["enabled"] is False
    assert s["healthy"] is True          # nothing has gone wrong yet
    assert s["running"] is False
    assert s["queueDepth"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_ai_turn.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pax_ai.feature_bus'`.

- [ ] **Step 3: Implement the skeleton**

Create `pax-ai/pax_ai/feature_bus.py`:

```python
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
import json
import sys
import threading
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

from . import config


SCHEMA_VERSION = 1


# -- Bounded enqueue (basic; Task 7 extends with back-pressure + drop policy) --

_QUEUE: "collections.deque[Dict[str, Any]]" = collections.deque()
_QUEUE_LOCK = threading.Lock()


def _enqueue(evt: Dict[str, Any]) -> None:
    """Append an event to the in-process queue. NEVER raises. NEVER blocks.
    Task 7 replaces this with a back-pressure-aware version that drops
    snapshot_features events when the queue is full and never drops
    ai_turn / level_event / microstructure_event / trigger_event events."""
    if not config.get("feature_bus.enabled", False):
        return
    with _QUEUE_LOCK:
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
    """Idempotent. Spawns the writer thread if feature_bus.enabled. Wired in Task 8."""
    return  # phase-skeleton: stays a no-op until Task 8


def stop(timeout_s: float = 2.0) -> None:
    """Signal stop; join with timeout. Wired in Task 8."""
    return


def status() -> Dict[str, Any]:
    with _STATE_LOCK:
        return {
            "enabled":          bool(config.get("feature_bus.enabled", False)),
            "healthy":          _HEALTHY,
            "running":          _RUNNING,
            "lastWriteMs":      _LAST_WRITE_MS,
            "queueDepth":       _QUEUE_DEPTH,
            "rowsToday":        _ROWS_TODAY,
            "blobWritesToday":  _BLOB_WRITES_TODAY,
            "lastError":        _LAST_ERROR,
            "dbPath":           str(config.get("feature_bus.db_path")) if config.get("feature_bus.enabled", False) else None,
        }


def record_trigger(alias: str, trig: Dict[str, Any], now_ms: int) -> None:
    """Stub. Wired in Task 9."""
    return


def record_ai_turn(rec: AiTurnRecord) -> None:
    """Stub. Wired in Task 5."""
    return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_ai_turn.py -v`
Expected: 5 PASS.

---

### Task 3: SQLite schema + `_open_db` + `_ensure_schema`

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_schema.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_schema.py`:

```python
"""SQLite schema invariants for pax-bus.db.

Tests open a fresh DB in a tmp_path, run _ensure_schema, then introspect.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus


EXPECTED_TABLES = {
    "snapshot_features",
    "level_events",
    "microstructure_events",
    "trigger_events",
    "ai_turns",
    "trade_outcomes",
    "settings_versions",
    "replay_sessions",
}


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "pax-bus-test.db"
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
    return db


def test_eight_tables_present(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES.issubset(names), names


def test_journal_mode_is_wal(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_every_table_has_schema_version_column(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        for tbl in EXPECTED_TABLES:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "schema_version" in cols, f"{tbl} missing schema_version"


def test_ai_turns_requires_both_shas(fresh_db: Path):
    """Both digest_sha256 and snapshot_sha256 are NOT NULL."""
    with sqlite3.connect(fresh_db) as conn:
        info = {row[1]: (row[2], row[3]) for row in
                conn.execute("PRAGMA table_info(ai_turns)").fetchall()}
        # row tuple is (cid, name, type, notnull, dflt_value, pk)
        # but we mapped to (type, notnull)
    assert info["digest_sha256"][1] == 1, "digest_sha256 must be NOT NULL"
    assert info["snapshot_sha256"][1] == 1, "snapshot_sha256 must be NOT NULL"


def test_ai_turns_indexes_on_both_shas(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ai_turns'").fetchall()}
    assert "idx_aiturn_snapshot_sha" in idx
    assert "idx_aiturn_digest_sha" in idx
    assert "idx_aiturn_ts" in idx


def test_ensure_schema_is_idempotent(tmp_path: Path):
    db = tmp_path / "pax-bus-test.db"
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        feature_bus._ensure_schema(conn)        # second call must not raise
    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES.issubset(names)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_schema.py -v`
Expected: FAIL with `AttributeError: module 'pax_ai.feature_bus' has no attribute '_open_db'`.

- [ ] **Step 3: Implement `_open_db` and `_ensure_schema`**

Append to `pax-ai/pax_ai/feature_bus.py`:

```python
import sqlite3
from pathlib import Path


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_schema.py -v`
Expected: 6 PASS.

---

### Task 4: Canonical JSON + blob store helpers

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_blob_stores.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_blob_stores.py`:

```python
"""Blob store invariants: content-addressed, idempotent, atomic, byte-exact round-trip."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pax_ai import feature_bus


def test_canonical_snapshot_json_is_stable_across_dict_iteration():
    a = {"b": 1, "a": 2, "nested": {"y": 3, "x": 4}}
    b = {"nested": {"x": 4, "y": 3}, "a": 2, "b": 1}
    assert feature_bus._canonical_snapshot_json(a) == feature_bus._canonical_snapshot_json(b)


def test_canonical_snapshot_json_no_whitespace():
    s = feature_bus._canonical_snapshot_json({"a": 1})
    assert " " not in s
    assert "\n" not in s


def test_canonical_snapshot_json_handles_non_jsonable_via_default_str():
    """Path objects, datetimes, etc. should serialize to str rather than raising."""
    from pathlib import Path
    s = feature_bus._canonical_snapshot_json({"p": Path("/a/b")})
    assert "/a/b" in s or "\\\\a\\\\b" in s


def test_write_snapshot_blob_returns_sha_and_creates_file(tmp_path: Path):
    snap = {"alias": "NQM6", "book": {"mid": 23450.5}}
    canon = feature_bus._canonical_snapshot_json(snap)
    expected_sha = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    sha = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    assert sha == expected_sha
    assert (tmp_path / "2024-05-06" / f"{sha}.json").exists()       # 2024-05-06 from ts


def test_write_snapshot_blob_idempotent_on_same_content(tmp_path: Path):
    canon = feature_bus._canonical_snapshot_json({"x": 1})
    sha1 = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    sha2 = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    assert sha1 == sha2
    # File written only once; second write is a no-op (mtime can differ).


def test_write_snapshot_blob_byte_exact_roundtrip(tmp_path: Path):
    snap = {"alias": "NQM6", "book": {"mid": 23450.5, "spread": 0.25}}
    canon = feature_bus._canonical_snapshot_json(snap)
    sha = feature_bus._write_snapshot_blob(canon, ts_ms=1715000000000, root=tmp_path)
    blob = (tmp_path / "2024-05-06" / f"{sha}.json").read_bytes()
    assert blob == canon.encode("utf-8")


def test_write_digest_blob_returns_sha_and_creates_file(tmp_path: Path):
    digest = "STATE alias=NQM6 mid=23450.5\nUSER hi\n"
    expected_sha = hashlib.sha256(digest.encode("utf-8")).hexdigest()
    sha = feature_bus._write_digest_blob(digest, ts_ms=1715000000000, root=tmp_path)
    assert sha == expected_sha
    assert (tmp_path / "2024-05-06" / f"{sha}.txt").exists()


def test_write_digest_blob_byte_exact_roundtrip(tmp_path: Path):
    digest = "x\n"
    sha = feature_bus._write_digest_blob(digest, ts_ms=1715000000000, root=tmp_path)
    blob = (tmp_path / "2024-05-06" / f"{sha}.txt").read_bytes()
    assert blob == digest.encode("utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_blob_stores.py -v`
Expected: FAIL (`_canonical_snapshot_json` not found).

- [ ] **Step 3: Implement the helpers**

Append to `pax-ai/pax_ai/feature_bus.py`:

```python
import datetime as _dt
import hashlib
import os
import tempfile


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_blob_stores.py -v`
Expected: 8 PASS.

Note: the date `2024-05-06` in the test corresponds to UTC of `ts_ms=1715000000000`. Verify with `datetime.utcfromtimestamp(1715000000)`.

---

### Task 5: `record_ai_turn` writes blobs + ai_turns row

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Modify: `pax-ai/tests/test_feature_bus_ai_turn.py` (append tests)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_feature_bus_ai_turn.py`:

```python
import sqlite3
from pathlib import Path


@pytest.fixture
def bus_enabled(tmp_path, monkeypatch):
    """Override config to enable bus with tmp_path DB + blob roots."""
    db_path = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir = tmp_path / "digests"

    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    # Reset module-level counters.
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    feature_bus._ROWS_TODAY = 0
    feature_bus._BLOB_WRITES_TODAY = 0
    yield {"db": db_path, "snap_dir": snap_dir, "dig_dir": dig_dir}


import hashlib
import time
import sqlite3


# NOTE: the writer-thread persistence test for record_ai_turn lives in
# Task 7's tests/test_feature_bus_writer.py (see
# test_writer_drains_ai_turn_record_to_blobs_and_db) because it depends
# on the writer thread + the _drain_to_db `ai_turn` branch which Task 7
# introduces. Task 5 covers the synchronous contract only.


def test_record_ai_turn_noop_when_disabled(tmp_path, monkeypatch):
    """With feature_bus.enabled=False, no DB file and no blob files are ever created."""
    db_path = tmp_path / "must-not-exist.db"
    snap_dir = tmp_path / "snapshots-disabled"
    dig_dir  = tmp_path / "digests-disabled"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    feature_bus.record_ai_turn(rec)
    # Hard tmp-path assertions (not "or True"):
    assert not db_path.exists(),  f"DB file must not exist when disabled: {db_path}"
    assert not snap_dir.exists(), f"snapshot dir must not exist when disabled: {snap_dir}"
    assert not dig_dir.exists(),  f"digest dir must not exist when disabled: {dig_dir}"
    assert feature_bus.status()["rowsToday"] == 0


def test_record_ai_turn_never_raises_into_caller(bus_enabled, monkeypatch):
    """Forced enqueue failure must not raise into the caller."""
    def boom(*_, **__): raise RuntimeError("queue boom")
    monkeypatch.setattr(feature_bus, "_enqueue", boom)
    rec = feature_bus.AiTurnRecord(**_valid_kwargs(
        digest_sha256="d" * 64, snapshot_sha256="s" * 64,
    ))
    feature_bus.record_ai_turn(rec)             # MUST NOT raise
    s = feature_bus.status()
    assert s["healthy"] is False
    assert "queue boom" in (s["lastError"] or "")


def test_record_ai_turn_returns_quickly_under_slow_disk(bus_enabled, monkeypatch):
    """The caller must not block on disk I/O - the writer thread owns blob + DB writes.
    Simulate a slow filesystem by patching _write_*_blob with a 500 ms sleep, then
    confirm record_ai_turn returns in << 50 ms."""
    def slow_blob(*_, **__):
        time.sleep(0.5)
        return "x" * 64
    monkeypatch.setattr(feature_bus, "_write_snapshot_blob", slow_blob)
    monkeypatch.setattr(feature_bus, "_write_digest_blob",   slow_blob)
    # Don't start the writer; we just want to measure the caller's return time.
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    t0 = time.monotonic()
    feature_bus.record_ai_turn(rec)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 50, f"record_ai_turn blocked for {elapsed_ms:.1f} ms"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_ai_turn.py -v`
Expected: 3 new tests FAIL (`test_record_ai_turn_noop_when_disabled`, `test_record_ai_turn_never_raises_into_caller`, `test_record_ai_turn_returns_quickly_under_slow_disk`). `_enqueue` is already defined in Task 2's skeleton so the implementation imports cleanly.

- [ ] **Step 3: Implement `record_ai_turn`**

Edit `pax-ai/pax_ai/feature_bus.py`. Replace the stub `record_ai_turn` with:

```python
def record_ai_turn(rec: AiTurnRecord) -> None:
    """Enqueue an AI turn for the writer thread. Caller returns immediately.
    NEVER raises. NEVER blocks - blob writes + DB insert happen on the
    writer thread in _drain_to_db()."""
    if not config.get("feature_bus.enabled", False):
        return
    try:
        _enqueue({"kind": "ai_turn", "payload": rec})
    except Exception as exc:
        with _STATE_LOCK:
            global _HEALTHY, _LAST_ERROR
            _HEALTHY = False
            _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        sys.stderr.write(f"[feature_bus] record_ai_turn enqueue failed: {exc}\n")
```

NOTE: the `ai_turn` queue payload carries the whole `AiTurnRecord` dataclass instance (not a plain dict). The writer thread's `_drain_to_db()` handles the `ai_turn` kind by calling `_write_snapshot_blob`, `_write_digest_blob`, then an INSERT into `ai_turns`. That implementation lands in Task 7 alongside the other `_insert_*` helpers; this task only delivers the enqueue path + AiTurnRecord shape.

For Phase 1 we'd also add `ai_turn` to the set of NON-droppable kinds in `_enqueue` (only `snapshot_features` may be evicted under back-pressure).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_ai_turn.py -v`
Expected: 8 PASS (5 from Task 2 + 3 new). Task 5 is green-by-design - the persistence test that requires the writer thread is in Task 7.

---

### Task 6: Snapshot delta detector (level/micro/state-condition events)

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_delta_detector.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_delta_detector.py`:

```python
"""Snapshot delta detector emits level_events, microstructure_events,
and state-condition trigger_events. Pure function, no DB side-effects."""
from __future__ import annotations

from pax_ai import feature_bus


def _snap(alias="NQM6", **kw):
    base = {
        "alias": alias,
        "health": "ok",
        "or_levels": {"levels": [], "middleLock": False, "inProximity": False},
        "micro_events": {"events": []},
        "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                   "news": {"blocked": False, "label": "clear"}},
    }
    base.update(kw)
    return base


def test_level_event_emitted_on_decision_change():
    prev = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "WAIT", "confidence": 0.3,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    new = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "FOLLOW_LONG", "confidence": 0.7,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    lvl = [e for e in events if e["kind"] == "level_event"]
    assert len(lvl) == 1
    assert lvl[0]["payload"]["prev_decision"] == "WAIT"
    assert lvl[0]["payload"]["new_decision"] == "FOLLOW_LONG"


def test_no_level_event_when_unchanged():
    s = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "WAIT", "confidence": 0.3,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    events = feature_bus._detect_snapshot_deltas(s, s, now_ms=1)
    assert not [e for e in events if e["kind"] == "level_event"]


def test_microstructure_event_emitted_on_new_event():
    prev = _snap(micro_events={"events": []})
    new = _snap(micro_events={"events": [
        {"type": "STOP_SWEEP", "price": 23450.0, "side": "buy",
         "size": 5, "tsMs": 1000}
    ]})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1500)
    micro = [e for e in events if e["kind"] == "microstructure_event"]
    assert len(micro) == 1
    assert micro[0]["payload"]["event_type"] == "STOP_SWEEP"


def test_microstructure_event_dedup_by_type_price_ts():
    """Same (type, price, tsMs) in both snapshots must NOT re-emit."""
    ev = {"type": "ICEBERG", "price": 23450.0, "side": "ask",
          "size": 10, "tsMs": 1000}
    prev = _snap(micro_events={"events": [ev]})
    new = _snap(micro_events={"events": [ev]})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=2000)
    assert not [e for e in events if e["kind"] == "microstructure_event"]


def test_bridge_degraded_state_trigger():
    prev = _snap(health="ok")
    new = _snap(health="offline", bridgeError="connection refused")
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "BRIDGE_DEGRADED" for t in trig)


def test_news_blackout_state_trigger():
    prev = _snap()  # news.blocked=False
    new = _snap(gates={"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": True, "label": "FOMC"}})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "NEWS_BLACKOUT" for t in trig)


def test_level_approach_state_trigger():
    """Crossed inProximity edge."""
    prev = _snap(or_levels={"levels": [], "middleLock": False, "inProximity": False})
    new = _snap(or_levels={"levels": [], "middleLock": False, "inProximity": True})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "LEVEL_APPROACH" for t in trig)


def test_detector_called_on_first_tick_with_none_prev():
    """First tick: prev=None must not raise; should emit no delta-events."""
    events = feature_bus._detect_snapshot_deltas(None, _snap(), now_ms=1)
    assert events == [] or all(e["kind"] == "snapshot_features" for e in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_delta_detector.py -v`
Expected: FAIL (`_detect_snapshot_deltas` undefined).

- [ ] **Step 3: Implement the detector**

Append to `pax-ai/pax_ai/feature_bus.py`:

```python
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
        if (pl.get("decision") != nl.get("decision")
                or float(pl.get("confidence") or 0) != float(nl.get("confidence") or 0)):
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
                "prev_proximity":  None,
                "new_proximity":   None,
                "trigger_reason": "composite_flip",
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_delta_detector.py -v`
Expected: 8 PASS.

---

### Task 7: Writer thread loop + queue + back-pressure

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_writer.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_writer.py`:

```python
"""Writer thread + back-pressure + disabled=zero-writes invariants."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from pax_ai import feature_bus


@pytest.fixture
def bus_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir = tmp_path / "digests"

    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         50,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    feature_bus._ROWS_TODAY = 0
    feature_bus._BLOB_WRITES_TODAY = 0
    yield {"db": db_path}
    feature_bus.stop()


def test_writer_disabled_means_zero_db_writes(tmp_path, monkeypatch):
    """With feature_bus.enabled=False, _enqueue is no-op; no DB ever opened."""
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(tmp_path / "must-not-exist.db"),
            "snapshot_blob_dir": str(tmp_path / "snap"),
            "digest_blob_dir":   str(tmp_path / "dig"),
            "queue_max":         2000,
            "writer_idle_ms":    100,
            "retention_days":    30,
        },
    })
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 1, "alias": "x", "level_label": "OR-H", "level_price": 0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0, "new_confidence": 1,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "test"}})
    assert not (tmp_path / "must-not-exist.db").exists()


def test_writer_drains_enqueued_events(bus_enabled):
    feature_bus.start()
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 1, "alias": "X", "level_label": "OR-H", "level_price": 100.0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0.3, "new_confidence": 0.7,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "composite_flip"}})
    # Wait up to 1s for the writer to drain.
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_back_pressure_drops_snapshots_not_events(bus_enabled, monkeypatch):
    """Fill the queue past queue_max with snapshot_features events; level_event still survives."""
    # Don't start the writer; we want the queue to fill.
    # Push 200 snapshot_features events into a queue capped at 50.
    for i in range(200):
        feature_bus._enqueue({"kind": "snapshot_features",
                               "payload": {"schema_version": 1, "ts_ms": i, "alias": "X",
                                            "health": "ok"}})
    # Now push one critical event.
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 999, "alias": "X", "level_label": "OR-H", "level_price": 100.0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0.3, "new_confidence": 0.7,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "composite_flip"}})

    # Drain by starting the writer.
    feature_bus.start()
    for _ in range(100):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n_lvl = conn.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]
                n_snap = conn.execute("SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
            except sqlite3.OperationalError:
                n_lvl, n_snap = 0, 0
        if n_lvl >= 1:
            break
        time.sleep(0.02)
    assert n_lvl == 1                                  # critical event preserved
    assert n_snap <= 50                                # snapshots were dropped


def test_writer_status_reflects_running(bus_enabled):
    feature_bus.start()
    time.sleep(0.05)
    s = feature_bus.status()
    assert s["enabled"] is True
    assert s["running"] is True


def test_writer_drains_ai_turn_record_to_blobs_and_db(bus_enabled):
    """record_ai_turn enqueues; the writer thread does the blob + DB writes.
    Moved here from Task 5 because the persistence path depends on the
    writer thread + _drain_to_db's `ai_turn` branch which lands in Task 7."""
    feature_bus.start()
    rec = feature_bus.AiTurnRecord(
        schema_version=1, ts_ms=1715000000000, chat_run_id="r1",
        deep=False, model="claude-haiku-4-5",
        router_primary="pax-or", router_secondary=None,
        user_text_raw="hi", user_text_normalized="hi",
        digest_text="DIGEST",
        digest_sha256=hashlib.sha256(b"DIGEST").hexdigest(),
        snapshot_json='{"a":1}',
        snapshot_sha256=hashlib.sha256(b'{"a":1}').hexdigest(),
        snapshot_alias="NQM6", snapshot_ts_ms=1715000000000, snapshot_age_ms=0,
        pax_text="ok",
        exit_code=0, elapsed_ms=5, api_duration_ms=4,
        total_cost_usd=0.001, input_tokens=100, output_tokens=1,
        cache_creation_tokens=0, cache_read_tokens=0,
        aborted=False, error=None,
    )
    feature_bus.record_ai_turn(rec)             # returns immediately (enqueue only)
    rows: list = []
    for _ in range(100):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                rows = conn.execute(
                    "SELECT digest_sha256, snapshot_sha256 FROM ai_turns"
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            break
        time.sleep(0.02)
    assert len(rows) == 1
    assert rows[0][0] == rec.digest_sha256
    assert rows[0][1] == rec.snapshot_sha256

    snap_root = bus_enabled["db"].parent / "snapshots"
    dig_root  = bus_enabled["db"].parent / "digests"
    digest_blob = dig_root  / "2024-05-06" / f"{rec.digest_sha256}.txt"
    snap_blob   = snap_root / "2024-05-06" / f"{rec.snapshot_sha256}.json"
    assert digest_blob.read_text(encoding="utf-8") == "DIGEST"
    assert snap_blob.read_text(encoding="utf-8") == '{"a":1}'


def test_writer_thread_captures_snapshot_features_automatically(bus_enabled, monkeypatch):
    """With the writer running and poller.latest() monkeypatched to return a
    snapshot, snapshot_features rows must appear in pax-bus.db without any
    manual _enqueue() call from the test."""
    from pax_ai import poller
    snap = {"alias": "NQM6", "health": "ok",
             "book": {"mid": 23450.5, "spread": 0.25, "bestBid": 23450.25, "bestAsk": 23450.5},
             "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                            "levels": [], "middleLock": False, "inProximity": False},
             "flow": {"regime": "BALANCED", "regimeConfidence": 0.5,
                       "biasScore": 0.0, "biasTrajectory": "FLAT"},
             "conviction": {"score": 0.0, "trend": "NONE", "anchorMode": "LIVE"},
             "trend_signal": {"kind": "NONE", "eligible": False},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": False, "label": "clear"}},
             "micro_events": {"events": []}}
    monkeypatch.setattr(poller, "latest",
                          lambda: (snap, 1000, 0, 0, None))
    feature_bus.start()
    # Wait up to ~1 s for at least one snapshot_features row to land.
    n_snap = 0
    for _ in range(50):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                n_snap = conn.execute(
                    "SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
        except sqlite3.OperationalError:
            n_snap = 0
        if n_snap >= 1:
            break
        time.sleep(0.02)
    assert n_snap >= 1, "writer thread did not capture any snapshot_features row"


def test_writer_thread_captures_level_event_delta_automatically(bus_enabled, monkeypatch):
    """Writer must drive _detect_snapshot_deltas with snap pair (prev, new)
    and persist a level_event row when a level decision flips."""
    from pax_ai import poller
    snap_a = {"alias": "NQM6", "health": "ok",
               "or_levels": {"levels": [{"label": "OR-H", "price": 23475.0,
                                            "decision": "WAIT", "confidence": 0.3}],
                              "middleLock": False, "inProximity": False},
               "micro_events": {"events": []},
               "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                          "news": {"blocked": False, "label": "clear"}}}
    snap_b = {**snap_a,
                "or_levels": {"levels": [{"label": "OR-H", "price": 23475.0,
                                            "decision": "FOLLOW_LONG", "confidence": 0.7}],
                                "middleLock": False, "inProximity": False}}
    # Yield snap_a on first call, snap_b on every subsequent call.
    state = {"i": 0}
    def fake_latest():
        i = state["i"]
        state["i"] += 1
        return (snap_a if i == 0 else snap_b, 1000 + i, 0, 0, None)
    monkeypatch.setattr(poller, "latest", fake_latest)
    feature_bus.start()
    n_lvl = 0
    for _ in range(80):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                n_lvl = conn.execute(
                    "SELECT COUNT(*) FROM level_events").fetchone()[0]
        except sqlite3.OperationalError:
            n_lvl = 0
        if n_lvl >= 1:
            break
        time.sleep(0.02)
    assert n_lvl >= 1, "writer thread did not capture level_events delta"


def test_writer_thread_persists_full_snapshot_feature_columns(bus_enabled, monkeypatch):
    """The expanded _insert_snapshot_features writes every projected column.
    Verify mid, spread, OR high/low, flow_regime, conviction_score, news_blocked
    all land in the DB (Phase 1's stated useful-passive-capture goal)."""
    from pax_ai import poller
    snap = {"alias": "NQM6", "health": "ok",
             "book": {"mid": 23450.5, "spread": 0.25, "bestBid": 23450.25, "bestAsk": 23450.5},
             "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                            "levels": [], "middleLock": False, "inProximity": False},
             "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.72,
                       "biasScore": 0.4, "biasTrajectory": "RISING"},
             "conviction": {"score": 0.55, "trend": "BULL", "trajectory": "RISING",
                             "anchorMode": "LIVE"},
             "trend_signal": {"kind": "STRONG_BULL", "renderableKind": "STRONG_BULL",
                                "eligible": True},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": True, "label": "FOMC"}},
             "micro_events": {"events": []}}
    monkeypatch.setattr(poller, "latest", lambda: (snap, 1000, 0, 0, None))
    feature_bus.start()
    row = None
    for _ in range(80):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                conn.row_factory = sqlite3.Row
                rs = conn.execute(
                    "SELECT * FROM snapshot_features ORDER BY id DESC LIMIT 1"
                ).fetchall()
            if rs:
                row = rs[0]
                break
        except sqlite3.OperationalError:
            pass
        time.sleep(0.02)
    assert row is not None, "no snapshot_features row landed"
    assert row["alias"]              == "NQM6"
    assert row["mid"]                == 23450.5
    assert row["spread"]             == 0.25
    assert row["or_high"]            == 23475.0
    assert row["or_low"]             == 23440.0
    assert row["or_width_pts"]       == 35.0
    assert row["flow_regime"]        == "TRENDING_UP"
    assert row["flow_regime_conf"]   == 0.72
    assert row["conviction_score"]   == 0.55
    assert row["conviction_trend"]   == "BULL"
    assert row["trend_renderable_kind"] == "STRONG_BULL"
    assert row["news_blocked"]       == 1
    assert row["news_label"]         == "FOMC"
    assert row["session_anchor_mode"] == "LIVE"


def test_writer_thread_tolerates_none_snapshot(bus_enabled, monkeypatch):
    """On cold start poller.latest() returns (None, 0, -1, 0, None).
    The writer must NOT raise; the DB simply gets no snapshot_features
    rows until a real snap arrives."""
    from pax_ai import poller
    monkeypatch.setattr(poller, "latest", lambda: (None, 0, -1, 0, None))
    feature_bus.start()
    time.sleep(0.15)            # let writer tick several times
    s = feature_bus.status()
    assert s["running"] is True
    assert s["healthy"] is True
    with sqlite3.connect(bus_enabled["db"]) as conn:
        try:
            n = conn.execute("SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
    assert n == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_writer.py -v`
Expected: FAIL (`start`/`stop` are no-op stubs from Task 2; `_drain_to_db`, `_writer_loop`, `_DROP_KINDS`, `_PREV_SNAP`, `_writer_tick_live_capture` not yet defined; the back-pressure-aware `_enqueue` not yet replacing the basic one). The basic `_enqueue` from Task 2 imports cleanly; the writer-thread tests just fail because the writer thread isn't started by the stub `start()`.

- [ ] **Step 3: Implement the writer**

NOTE: `_QUEUE`, `_QUEUE_LOCK`, and the basic `_enqueue` were defined in Task 2. This task REPLACES the basic `_enqueue` with a back-pressure-aware version, then adds the writer thread + live snapshot capture loop. Do NOT redeclare `_QUEUE` or `_QUEUE_LOCK`.

Append to `pax-ai/pax_ai/feature_bus.py`:

```python
_STOP_EVT = threading.Event()
_WRITER_THREAD: Optional[threading.Thread] = None
_DROP_KINDS = {"snapshot_features"}   # back-pressure: snapshots droppable; events not
_NEVER_DROP_KINDS = {"ai_turn", "level_event", "microstructure_event", "trigger_event"}

# Live snapshot capture state. Owned exclusively by the writer thread;
# never read or written from outside _writer_loop.
_PREV_SNAP: Optional[Dict[str, Any]] = None
_LAST_CAPTURE_MS: int = 0              # epoch ms of last live-capture pulse
```

Replace the basic `_enqueue` from Task 2 with this back-pressure-aware version:

```python
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
```

And replace the `start` / `stop` stubs:

```python
def start() -> None:
    """Idempotent. Starts the writer thread when feature_bus.enabled=True."""
    global _WRITER_THREAD
    if not config.get("feature_bus.enabled", False):
        return
    with _STATE_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        _STOP_EVT.clear()
        _WRITER_THREAD = threading.Thread(target=_writer_loop,
                                           name="pax-feature-bus-writer",
                                           daemon=True)
        _WRITER_THREAD.start()


def stop(timeout_s: float = 2.0) -> None:
    _STOP_EVT.set()
    global _WRITER_THREAD
    t = _WRITER_THREAD
    if t is not None:
        t.join(timeout=timeout_s)
    with _STATE_LOCK:
        _WRITER_THREAD = None
```

Also update `status()` to include `queueDepth`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_writer.py -v`
Expected: 9 PASS (4 basic + 1 ai_turn persistence + 4 live-capture tests).

---

### Task 8: Advisory file lock (concurrent-writer protection)

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Modify: `pax-ai/tests/test_feature_bus_writer.py` (append test)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_feature_bus_writer.py`:

```python
def test_concurrent_writer_lock_refuses_second_start(bus_enabled):
    """Lock contention prevents start(). Uses feature_bus._try_acquire_lock
    as the portable abstraction; no direct msvcrt / fcntl imports in this
    test, so it runs on both Windows and POSIX CI."""
    db = bus_enabled["db"]
    # Step 1: acquire the lock via the module's own portable primitive
    # (simulates another process holding it).
    assert feature_bus._try_acquire_lock(db) is True, "first acquire must succeed"
    held_handle = feature_bus._LOCK_FILE_HANDLE
    # Step 2: forget the module's handle reference so start() will try a
    # FRESH acquire that contends with the held OS-level lock.
    feature_bus._LOCK_FILE_HANDLE = None
    try:
        feature_bus.start()
        time.sleep(0.05)
        s = feature_bus.status()
        assert s["running"] is False
        assert s["healthy"] is False
        assert "lock" in (s["lastError"] or "").lower()
    finally:
        # Re-attach the held handle and use the module's release path so
        # we never leak file handles even on assertion failure.
        feature_bus._LOCK_FILE_HANDLE = held_handle
        feature_bus._release_lock()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_writer.py::test_concurrent_writer_lock_refuses_second_start -v`
Expected: FAIL (no advisory lock implemented).

- [ ] **Step 3: Implement the lock**

In `pax-ai/pax_ai/feature_bus.py`, add at the top (after imports):

```python
try:
    import msvcrt           # Windows
    _IS_WINDOWS = True
except ImportError:
    import fcntl            # POSIX (CI)
    _IS_WINDOWS = False


_LOCK_FILE_HANDLE = None


def _try_acquire_lock(db_path: Path) -> bool:
    """Acquire an advisory file lock next to the DB. Returns True on success.
    On failure, sets _HEALTHY=False with a descriptive _LAST_ERROR.

    Opens the lock file with mode 'a+' (append + read) rather than 'w' so that
    a second concurrent call does NOT truncate the file out from under the
    first holder. The file is created if missing and otherwise untouched."""
    global _LOCK_FILE_HANDLE, _HEALTHY, _LAST_ERROR
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
        with _STATE_LOCK:
            _HEALTHY = False
            _LAST_ERROR = f"lock acquisition failed: {exc}"
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
```

Update `start()`:

```python
def start() -> None:
    """Idempotent. Starts the writer thread when feature_bus.enabled=True
    and the advisory lock is available."""
    global _WRITER_THREAD
    if not config.get("feature_bus.enabled", False):
        return
    with _STATE_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        db_path = Path(config.get("feature_bus.db_path"))
        if not _try_acquire_lock(db_path):
            return
        _STOP_EVT.clear()
        _WRITER_THREAD = threading.Thread(target=_writer_loop,
                                           name="pax-feature-bus-writer",
                                           daemon=True)
        _WRITER_THREAD.start()
```

Update `stop()`:

```python
def stop(timeout_s: float = 2.0) -> None:
    _STOP_EVT.set()
    global _WRITER_THREAD
    t = _WRITER_THREAD
    if t is not None:
        t.join(timeout=timeout_s)
    with _STATE_LOCK:
        _WRITER_THREAD = None
    _release_lock()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_writer.py -v`
Expected: 10 PASS (9 from Task 7 + 1 lock test).

---

### Task 9: `record_trigger` implementation (fire-and-forget)

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py`
- Create: `pax-ai/tests/test_feature_bus_triggers.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_triggers.py`:

```python
"""record_trigger contract: fire-and-forget, no raise, no compute_triggers call,
no perturbation of triggers.py linger cache."""
from __future__ import annotations

import sqlite3
import time

import pytest

from pax_ai import feature_bus, triggers


@pytest.fixture
def bus_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(tmp_path / "snap"),
            "digest_blob_dir":   str(tmp_path / "dig"),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    yield {"db": db_path}
    feature_bus.stop()


def test_record_trigger_inserts_row_when_enabled(bus_enabled):
    feature_bus.start()
    feature_bus.record_trigger("NQM6", {
        "kind": "TREND_SIGNAL_FIRE",
        "severity": "HIGH",
        "label": "STRONG_BULL",
        "headline": "trend fire",
        "details": "details",
    }, now_ms=1715000000000)
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_record_trigger_noop_when_disabled(tmp_path, monkeypatch):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": str(tmp_path / "no.db"),
            "snapshot_blob_dir": str(tmp_path / "s"), "digest_blob_dir": str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})
    feature_bus.record_trigger("X", {"kind": "X", "severity": "LOW"}, now_ms=1)
    assert not (tmp_path / "no.db").exists()


def test_record_trigger_never_raises(bus_enabled, monkeypatch):
    monkeypatch.setattr(feature_bus, "_enqueue",
                          lambda evt: (_ for _ in ()).throw(RuntimeError("boom")))
    feature_bus.record_trigger("X", {"kind": "X", "severity": "LOW"}, now_ms=1)
    # No exception escaped.


def test_compute_triggers_never_called_by_feature_bus():
    """Static guard: feature_bus.py must NOT import or call triggers.compute_triggers."""
    import ast
    src = (feature_bus.__file__)
    with open(src, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert not (isinstance(node.value, ast.Name) and node.value.id == "triggers"
                          and node.attr == "compute_triggers"), \
                "feature_bus must not reference triggers.compute_triggers"
        if isinstance(node, ast.ImportFrom):
            if node.module and "triggers" in node.module:
                pytest.fail("feature_bus must not import from pax_ai.triggers")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_triggers.py -v`
Expected: FAIL (`record_trigger` is still a stub).

- [ ] **Step 3: Implement `record_trigger`**

In `pax-ai/pax_ai/feature_bus.py`, replace the `record_trigger` stub:

```python
def record_trigger(alias: str, trig: Dict[str, Any], now_ms: int) -> None:
    """Enqueue an edge trigger. Fire-and-forget. NEVER raises. NEVER blocks > 50 ms."""
    if not config.get("feature_bus.enabled", False):
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_triggers.py -v`
Expected: 4 PASS.

---

### Task 10: `triggers._emit_edge` hook (lazy import + alias kwarg)

**Files:**
- Modify: `pax-ai/pax_ai/triggers.py`
- Modify: `pax-ai/tests/test_feature_bus_triggers.py` (append test)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_feature_bus_triggers.py`:

```python
def test_emit_edge_calls_feature_bus_record_trigger(bus_enabled, monkeypatch):
    feature_bus.start()
    state = triggers._new_alias_state()
    trig = {"kind": "TREND_SIGNAL_FIRE", "severity": "HIGH",
            "label": "STRONG_BULL", "headline": "x", "details": "y"}
    triggers._emit_edge(state, trig, 1715000000000, alias="NQM6")
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_emit_edge_does_not_perturb_linger_cache_when_bus_disabled(tmp_path, monkeypatch):
    """With the bus disabled, _emit_edge behavior on the linger cache must be unchanged."""
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": "", "snapshot_blob_dir": "",
            "digest_blob_dir": "", "queue_max": 2000, "writer_idle_ms": 100,
            "retention_days": 30}})
    state = triggers._new_alias_state()
    trig = {"kind": "CONVICTION_FLIP", "label": "+1",
            "severity": "MED", "headline": "", "details": ""}
    triggers._emit_edge(state, trig, 1000, alias="X")
    assert ("CONVICTION_FLIP", "+1") in state["active_edges"]
    assert state["active_edges"][("CONVICTION_FLIP", "+1")]["firstSeenMs"] == 1000


def test_emit_edge_signature_accepts_alias_kwarg_safely():
    """Old call sites without alias kwarg must still work (alias defaults to None)."""
    state = triggers._new_alias_state()
    triggers._emit_edge(state, {"kind": "X", "label": "Y", "severity": "LOW"}, 1)
    # No exception; the bus simply doesn't get called (alias=None branch).
    assert ("X", "Y") in state["active_edges"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_triggers.py -v`
Expected: 3 NEW FAILS (`_emit_edge` doesn't accept alias kwarg yet).

- [ ] **Step 3: Add hook + alias kwarg**

In `pax-ai/pax_ai/triggers.py`, replace `_emit_edge`:

```python
def _emit_edge(state: Dict[str, Any], trig: Dict[str, Any], now_ms: int,
                alias: Optional[str] = None) -> None:
    """Insert / refresh an edge trigger in the per-alias linger cache.

    Same (kind, label) refreshes lingerUntilMs but preserves the original
    firstSeenMs so the UI can sort / age-out consistently.

    Phase-1 feature-bus hook (additive, fire-and-forget): when `alias` is
    provided, call feature_bus.record_trigger() to persist the edge event.
    The bus is lazy-imported to avoid a static circular dep
    (feature_bus is loaded by __main__.py before journal.init()).
    """
    key = (trig["kind"], trig.get("label") or "-")
    cache = state["active_edges"]
    prior = cache.get(key)
    trig["firstSeenMs"] = (prior or {}).get("firstSeenMs", now_ms)
    trig["lingerUntilMs"] = now_ms + _linger_ms()
    trig["bucketMs"] = trig["lingerUntilMs"] - trig["firstSeenMs"]
    cache[key] = trig

    # Feature-bus capture is strictly out-of-band. Failures must NEVER
    # reach the trigger compute path.
    if alias:
        try:
            from . import feature_bus       # lazy import
            feature_bus.record_trigger(alias, dict(trig), now_ms)
        except Exception:
            pass
```

Then update every `_emit_edge(state, ..., now_ms)` call site in triggers.py to pass `alias=str(snap.get("alias") or ALIAS_DEFAULT)`. The real `_trig_*` helpers in triggers.py have `snap` (not `alias`) in scope as a function parameter, so we derive the alias from the snapshot itself. `ALIAS_DEFAULT` is already defined at triggers.py:55 as `"__default__"` and is used elsewhere as the fallback when `snap["alias"]` is missing/empty.

Concretely (grep for `_emit_edge(state,` in triggers.py and modify each in place):

| Line | Containing helper | Change |
|---|---|---|
| 243 | `_trig_middle_lock(state, snap, now_ms)` | `_emit_edge(state, {...}, now_ms, alias=str(snap.get("alias") or ALIAS_DEFAULT))` |
| 250 | `_trig_middle_lock(state, snap, now_ms)` | same |
| 289 | `_trig_trend_signal_fire(state, snap, now_ms)` | same |
| 313 | `_trig_conviction_flip(state, snap, now_ms)` | same |
| 336 | `_trig_regime_change(state, snap, now_ms)` | same |
| 372 | `_trig_micro_event(state, snap, now_ms)` | same |

All six callers have `snap` in scope as a function parameter (their full signatures are `(state, snap, now_ms)`); none have an `alias` local. The state-condition helpers `_trig_level_approach`, `_trig_bridge_degraded`, `_trig_eod_risk`, `_trig_news_t_minus` do NOT call `_emit_edge` - they return event lists directly to `compute_triggers` and are captured by the bus's snapshot-delta path (Task 6), not via this hook.

Verify with: `grep -n "_emit_edge(state," pax-ai/pax_ai/triggers.py` -> every match line should now include `alias=str(snap.get("alias") or ALIAS_DEFAULT)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_triggers.py tests/test_triggers.py -v`
Expected: ALL PASS. The existing `test_triggers.py` tests must still pass (no regression in linger / whynow / dedup behavior).

---

### Task 11: `chat.py::handle_chat_stream` end-of-handler hook

**Files:**
- Modify: `pax-ai/pax_ai/chat.py`
- Modify: `pax-ai/tests/test_chat_handler.py` (append golden-bytes test)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_chat_handler.py`:

```python
def test_chat_path_unchanged_when_bus_disabled(monkeypatch):
    """Phase-1 invariant: SSE byte stream from handle_chat_stream is identical
    whether feature_bus.enabled is False or True."""
    from pax_ai import chat, feature_bus
    from pax_ai import claude_stream as cs

    captures = []
    class _CaptureWfile:
        def __init__(self): self.buf = bytearray()
        def write(self, b):
            self.buf.extend(b if isinstance(b, (bytes, bytearray)) else b.encode())
        def flush(self): pass

    def _fake_stream_chat(user_message, model, system_prompt_path,
                          on_token, on_done, abort, timeout_sec):
        on_token("hello")
        on_done({"exit_code": 0, "elapsed_ms": 5, "tokens_emitted": 1,
                 "aborted": False, "error": None,
                 "total_cost_usd": 0.001, "input_tokens": 100,
                 "output_tokens": 1, "cache_creation_input_tokens": 0,
                 "cache_read_input_tokens": 0, "duration_api_ms": 5})
        return 0
    monkeypatch.setattr(cs, "stream_chat", _fake_stream_chat)
    monkeypatch.setattr(chat.prompts, "write_frozen_prompt", lambda: Path("/tmp/sp.txt"))
    monkeypatch.setattr(chat.prompts, "route", lambda t: {"primary": "pax-or",
                                                            "secondary": [],
                                                            "router_hint": ""})

    # Run with bus disabled.
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": "", "snapshot_blob_dir": "",
            "digest_blob_dir": "", "queue_max": 2000, "writer_idle_ms": 100,
            "retention_days": 30}})
    w1 = _CaptureWfile()
    chat.handle_chat_stream(w1, "test", deep=False)
    captures.append(bytes(w1.buf))

    # Run with bus enabled (any tmp dir; record_ai_turn must not corrupt the SSE).
    import tempfile
    td = tempfile.mkdtemp()
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": str(Path(td) / "bus.db"),
            "snapshot_blob_dir": str(Path(td) / "snap"),
            "digest_blob_dir": str(Path(td) / "dig"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})
    w2 = _CaptureWfile()
    chat.handle_chat_stream(w2, "test", deep=False)
    captures.append(bytes(w2.buf))

    assert captures[0] == captures[1], \
      f"SSE bytes diverge with bus enabled:\nDISABLED: {captures[0]!r}\nENABLED:  {captures[1]!r}"


def test_chat_assembles_ai_turn_record_when_bus_enabled(monkeypatch, tmp_path):
    """When the bus is enabled, an AiTurnRecord is built and record_ai_turn is called."""
    from pax_ai import chat, feature_bus
    from pax_ai import claude_stream as cs
    from pax_ai import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": str(tmp_path / "b.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir": str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})

    captured = []
    monkeypatch.setattr(feature_bus, "record_ai_turn", lambda rec: captured.append(rec))
    monkeypatch.setattr(chat.prompts, "write_frozen_prompt", lambda: Path("/tmp/sp.txt"))
    monkeypatch.setattr(chat.prompts, "route", lambda t: {"primary": "pax-or",
                                                            "secondary": [],
                                                            "router_hint": ""})
    monkeypatch.setattr(cs, "stream_chat", lambda **kw: (kw["on_token"]("ok"),
                                                          kw["on_done"]({"exit_code": 0}), 0)[2])

    class _W:
        def write(self, b): pass
        def flush(self): pass
    chat.handle_chat_stream(_W(), "hi", deep=False)
    assert len(captured) == 1
    rec = captured[0]
    assert len(rec.digest_sha256) == 64
    assert len(rec.snapshot_sha256) == 64
    assert rec.user_text_raw == "hi"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_chat_handler.py -v`
Expected: 2 NEW FAILS.

- [ ] **Step 3: Add the hook in chat.py**

Insertion point in `pax-ai/pax_ai/chat.py::handle_chat_stream`:

```
        try:                          # done-SSE writer (existing)
            wfile.write(_sse_event("done", {...}))
            wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        # <<< INSERT THE NEW BLOCK BELOW HERE >>>
        # (still inside the outer try, before the outer finally)
    finally:                          # existing
        _clear_abort_if_owned(abort)
```

The hook runs entirely after the `done` SSE event has been flushed, so any failure cannot corrupt the SSE stream. Run after the inner `done`-SSE try/except, but still inside the outer try so the outer `finally` (which clears the abort flag) still runs.

Add top-of-file imports (only the ones missing; check first):

```python
import hashlib
import sys
from . import feature_bus
```

Add at the end of the `try:` block (immediately before `finally:`):

```python
        # ------------------------------------------------------------------
        # Phase 1 feature-bus capture. Strictly post-`done`-flush. Any
        # failure logs to stderr but never raises into the chat path.
        # ------------------------------------------------------------------
        try:
            snap, snap_ts_ms, snap_age_ms, _, _ = poller.latest()
            snapshot_json = feature_bus._canonical_snapshot_json(snap or {})
            digest_sha = hashlib.sha256(full_msg.encode("utf-8")).hexdigest()
            snap_sha   = hashlib.sha256(snapshot_json.encode("utf-8")).hexdigest()
            rec = feature_bus.AiTurnRecord(
                schema_version=1,
                ts_ms=int(time.time() * 1000),
                chat_run_id=journal.current_run_id(),
                deep=bool(deep),
                model=model,
                router_primary=meta.get("router_primary"),
                router_secondary=meta.get("router_secondary"),
                user_text_raw=user_text,
                user_text_normalized=meta.get("user_normalized") or user_text,
                digest_text=full_msg,
                digest_sha256=digest_sha,
                snapshot_json=snapshot_json,
                snapshot_sha256=snap_sha,
                snapshot_alias=(snap or {}).get("alias"),
                snapshot_ts_ms=snap_ts_ms if snap_ts_ms > 0 else None,
                snapshot_age_ms=snap_age_ms,
                pax_text=pax_text,
                exit_code=rc,
                elapsed_ms=elapsed_ms,
                api_duration_ms=final_info.get("duration_api_ms"),
                total_cost_usd=final_info.get("total_cost_usd"),
                input_tokens=final_info.get("input_tokens"),
                output_tokens=final_info.get("output_tokens"),
                cache_creation_tokens=final_info.get("cache_creation_input_tokens"),
                cache_read_tokens=final_info.get("cache_read_input_tokens"),
                aborted=bool(final_info.get("aborted", abort.is_set())),
                error=final_info.get("error"),
            )
            feature_bus.record_ai_turn(rec)
        except Exception as exc:
            sys.stderr.write(f"[chat] feature_bus capture failed: {exc}\n")
```

All three imports (`hashlib`, `sys`, `from . import feature_bus`) must be present at the top of chat.py. Check first; only add the ones missing. `time` and `poller` and `journal` are already imported by existing chat.py code.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_chat_handler.py -v`
Expected: ALL PASS, including the byte-identical-SSE assertion and the AiTurnRecord assembly check.

---

### Task 12: `server.py::_api_pax_health` extension

**Files:**
- Modify: `pax-ai/pax_ai/server.py:188-200`
- Modify: `pax-ai/tests/test_server_helpers.py` (append test)

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_server_helpers.py`:

```python
def test_api_pax_health_includes_feature_bus_block():
    from pax_ai.server import _api_pax_health
    status, body = _api_pax_health()
    assert status == 200
    assert "feature_bus" in body
    fb = body["feature_bus"]
    assert "enabled" in fb
    assert "healthy" in fb
    assert "running" in fb
    assert "queueDepth" in fb
    assert "rowsToday" in fb
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_server_helpers.py::test_api_pax_health_includes_feature_bus_block -v`
Expected: FAIL (no feature_bus block yet).

- [ ] **Step 3: Add feature_bus block to _api_pax_health**

In `pax-ai/pax_ai/server.py`, add to the top-of-file imports:

```python
from . import feature_bus
```

In `_api_pax_health` (line 188), append the new key to the returned dict:

```python
def _api_pax_health() -> Tuple[int, Dict[str, Any]]:
    snap, as_of_ms, age_ms, fails, err = poller.latest()
    return 200, {
        "dashboardReachable":   snap is not None and snap.get("health") == "ok",
        "dashboardLastAtMs":    as_of_ms,
        "dashboardAgeMs":       age_ms,
        "dashboardConsecutiveFails": fails,
        "dashboardLastError":   err,
        "pollMs":               int(config.get("poll_ms", 1000)),
        "modelLive":            config.get("models.live"),
        "modelDeep":            config.get("models.deep"),
        "claudeAvailable":      claude_stream.claude_available(),
        "feature_bus":          feature_bus.status(),    # NEW
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_server_helpers.py -v`
Expected: ALL PASS.

---

### Task 13: `__main__.py` startup wiring

**Files:**
- Modify: `pax-ai/pax_ai/__main__.py`
- Create: `pax-ai/tests/test_feature_bus_integration.py`

- [ ] **Step 1: Write the failing test**

Create `pax-ai/tests/test_feature_bus_integration.py`:

```python
"""Startup ordering invariant: feature_bus.start() must run AFTER poller.start()
and BEFORE journal.init(). Verified by reading the module source via AST."""
from __future__ import annotations

import ast
from pathlib import Path


def test_startup_order_main_poller_bus_journal():
    """In __main__.py::main, the call sequence on the success path is:
        poller.start()
        <feature_bus.start() (guarded by config.feature_bus.enabled)>
        journal.init()
    """
    src = Path(__file__).resolve().parent.parent / "pax_ai" / "__main__.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    main_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_fn = node
            break
    assert main_fn, "main() not found in __main__.py"

    # Flatten Calls in textual order.
    calls = []
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                name = (f.value.id if isinstance(f.value, ast.Name) else "") + "." + f.attr
                calls.append((name, node.lineno))

    interesting = [c for c in calls
                    if c[0] in {"poller.start", "feature_bus.start", "journal.init"}]
    names_in_order = [c[0] for c in interesting]
    assert names_in_order == ["poller.start", "feature_bus.start", "journal.init"], names_in_order
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_integration.py::test_startup_order_main_poller_bus_journal -v`
Expected: FAIL (feature_bus.start() is not in __main__.py yet).

- [ ] **Step 3: Wire startup**

In `pax-ai/pax_ai/__main__.py`, after `poller.start()` (line 41) and before `from . import journal; journal.init()`, insert:

```python
    # Phase-1 feature bus: passive capture, guarded by config flag (default
    # false). The call is idempotent; when disabled, start() is a no-op.
    from . import feature_bus
    feature_bus.start()
```

Full replacement of lines 40-47:

```python
    # Snapshot poller starts in both modes - the API endpoints need it.
    poller.start()

    # Phase-1 feature bus: passive capture, guarded by config flag (default
    # false). The call is idempotent; when disabled, start() is a no-op.
    from . import feature_bus
    feature_bus.start()

    # Chat journal init (SQLite at D:\BookmapLogs\pax-chat.db by default).
    # No-ops if PAX_LOG_DIR is unwritable -- chat still works, history is
    # just not preserved for that session.
    from . import journal
    journal.init()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_integration.py -v`
Expected: PASS.

---

### Task 14: `/api/pax/whynow` byte-identical regression test

**Files:**
- Modify: `pax-ai/tests/test_feature_bus_integration.py`

- [ ] **Step 1: Write the failing test**

Append to `pax-ai/tests/test_feature_bus_integration.py`:

```python
import json
import sqlite3

import pytest


def _drive_triggers(monkeypatch, snap):
    """Pump a snapshot through compute_triggers and return the JSON bytes whynow returns."""
    from pax_ai import triggers, poller
    monkeypatch.setattr(poller, "latest", lambda: (snap, 1000, 0, 0, None))
    triggs = triggers.compute_triggers(snap, 0)
    return json.dumps(triggs, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_whynow_byte_identical_with_and_without_bus(monkeypatch, tmp_path):
    """compute_triggers output byte-identical regardless of bus state."""
    snap = {"alias": "NQM6", "health": "ok",
             "or_levels": {"levels": [{"label": "OR-H", "price": 100,
                                          "decision": "FOLLOW_LONG", "confidence": 0.7}],
                            "middleLock": False, "inProximity": True},
             "conviction": {"score": 0.5, "trend": "BULL", "anchorMode": "LIVE"},
             "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
                       "biasScore": 0.4, "biasTrajectory": "RISING"},
             "micro_events": {"events": []},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": False, "label": "clear"}},
             "trend_signal": {"kind": "STRONG_BULL", "eligible": True,
                                "changedSinceLastTick": False,
                                "bucketEnteredMs": 0,
                                "eventMsSource": "trend_analyzer", "mid": 100},
             "session": {"code": "ACTIVE", "anchorMode": "LIVE"}}

    # Reset per-alias state so the two runs start identical.
    from pax_ai import triggers as t
    with t._STATE_LOCK:
        t._PER_ALIAS_STATE.clear()
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": "", "snapshot_blob_dir": "",
            "digest_blob_dir": "", "queue_max": 2000, "writer_idle_ms": 100,
            "retention_days": 30}})
    bytes_disabled = _drive_triggers(monkeypatch, snap)

    # Reset state and run with bus enabled.
    with t._STATE_LOCK:
        t._PER_ALIAS_STATE.clear()
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": str(tmp_path / "b.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir": str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})
    bytes_enabled = _drive_triggers(monkeypatch, snap)

    assert bytes_disabled == bytes_enabled, \
      f"compute_triggers output diverged:\nDISABLED: {bytes_disabled!r}\nENABLED:  {bytes_enabled!r}"
```

- [ ] **Step 2: Run test to verify it fails or passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_integration.py::test_whynow_byte_identical_with_and_without_bus -v`
Expected: PASS - if the implementation in Task 10 is correct (the hook adds zero bytes to the trig dict, the linger cache holds identical state, compute_triggers returns identical list).

If it FAILS: a bug in Task 10 leaked something into the trig dict. Diff `bytes_disabled` vs `bytes_enabled` to find the offending field.

- [ ] **Step 3: No new implementation needed** (the test is the guard).

- [ ] **Step 4: Confirm**

If this passes, Task 14 is done with no code changes.

---

### Task 15: Version bump + cross-link + smoke verification

**Files:**
- Modify: `pax-ai/pax_ai/__init__.py`
- Modify: `pax-ai/pyproject.toml`
- No test file changes.

- [ ] **Step 1: Bump versions**

In `pax-ai/pax_ai/__init__.py`, replace:

```python
__version__ = "0.0.1"
```

with:

```python
__version__ = "0.1.0"
```

In `pax-ai/pyproject.toml`, replace `version = "0.0.1"` with `version = "0.1.0"`.

- [ ] **Step 2: Run the full Pax AI test suite**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v`
Expected: ALL PASS.

- [ ] **Step 3: Byte-compile sanity**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m compileall -q pax_ai`
Expected: silent (no syntax errors).

- [ ] **Step 4: Smoke (manual operator)**

Operator action:

1. Stop any running Pax AI instance.
2. Edit `pax-ai/pax_ai_config.json` to add `{"feature_bus": {"enabled": true}}` (top-level merge with existing keys; the bus reads via `config.get("feature_bus.enabled")`).
3. Run `pax-ai-start.bat server` (headless mode for first verification).
4. `curl http://127.0.0.1:18891/api/pax/health` -> response includes `"feature_bus": {"enabled": true, "healthy": true, "running": true, ...}`.
5. Send a chat: `curl -N -X POST http://127.0.0.1:18891/api/pax/chat/stream -H "Content-Type: application/json" -d "{\"message\":\"tape and iceberg read?\",\"deep\":false}"`.
6. After SSE done, verify:
   - `D:\BookmapLogs\pax-bus.db` exists.
   - `sqlite3 D:\BookmapLogs\pax-bus.db "SELECT id,ts_ms,model,digest_sha256,snapshot_sha256 FROM ai_turns;"` returns at least one row, both SHAs non-empty.
   - `D:\BookmapLogs\pax-digests\<today>\<digest_sha256>.txt` exists.
   - `D:\BookmapLogs\pax-snapshots\<today>\<snapshot_sha256>.json` exists.
7. Confirm `/api/pax/whynow` JSON output is unchanged from a known-good pre-bus capture (operator may keep a captured baseline; not automated in Phase 1).
8. Set `feature_bus.enabled` back to `false` and restart. Confirm `/api/pax/health` shows `enabled=false, running=false`.

---

## Tests Mapped to Risks

| Risk (from spec) | Test name | Phase 1 coverage |
|---|---|---|
| Latency: writer blocks poll thread | `test_writer_drains_enqueued_events` | bounded queue + drain off-thread |
| Data volume | `test_back_pressure_drops_snapshots_not_events` | snapshots dropped first; events preserved |
| Prompt bloat | (n/a Phase 1 - digest unchanged) | digest builder untouched |
| Hallucination | (n/a Phase 1) | n/a |
| Stale snapshot | `test_detector_called_on_first_tick_with_none_prev` | None-prev tolerated; first tick produces no false deltas |
| Overfitting | (n/a Phase 1) | n/a; tuning is Phase 5 |
| Survivorship | (n/a Phase 1) | n/a |
| False edge | (n/a Phase 1) | n/a |
| Operator trust | `test_api_pax_health_includes_feature_bus_block` | bus state visible in health |
| Schema drift | `test_every_table_has_schema_version_column`, `test_ai_turns_requires_both_shas`, `test_ensure_schema_is_idempotent` | schema_version on every table; IF NOT EXISTS DDL |
| Concurrent writers | `test_concurrent_writer_lock_refuses_second_start` | Advisory file lock via portable `_try_acquire_lock` abstraction (Windows msvcrt + POSIX fcntl) |
| Subprocess latency | (n/a Phase 1) | n/a |
| Cache-hit invisibility | `test_record_ai_turn_writes_row_and_both_blobs` (covers cache_creation/read_tokens persistence) | cache columns in ai_turns |
| News staleness | (n/a Phase 1) | n/a; out of scope |
| Skill routing | (n/a Phase 1) | n/a |
| **Phase-1-specific: SSE byte-identical** | `test_chat_path_unchanged_when_bus_disabled` | golden-SSE regression guard |
| **Phase-1-specific: /whynow byte-identical** | `test_whynow_byte_identical_with_and_without_bus` | compute_triggers output guard |
| **Phase-1-specific: disabled=zero writes** | `test_writer_disabled_means_zero_db_writes`, `test_record_trigger_noop_when_disabled`, `test_record_ai_turn_noop_when_disabled` | DB file never created with bus off |
| **Phase-1-specific: record_* never raises** | `test_record_trigger_never_raises`, `test_record_ai_turn_never_raises_into_caller` | guarded try/except in every API surface |
| **Phase-1-specific: bus never calls compute_triggers** | `test_compute_triggers_never_called_by_feature_bus` | AST-scan of feature_bus.py |
| **Phase-1-specific: byte-exact replay readiness** | `test_write_snapshot_blob_byte_exact_roundtrip`, `test_write_digest_blob_byte_exact_roundtrip`, `test_canonical_snapshot_json_is_stable_across_dict_iteration` | blob round-trip; canonical JSON stable |
| **Phase-1-specific: _emit_edge linger cache unperturbed** | `test_emit_edge_does_not_perturb_linger_cache_when_bus_disabled`, `test_emit_edge_signature_accepts_alias_kwarg_safely` | linger cache + signature back-compat |
| **Phase-1-specific: live capture loop drives snapshot_features** | `test_writer_thread_captures_snapshot_features_automatically` | poller.latest -> snapshot_features row, no manual _enqueue |
| **Phase-1-specific: live capture drives delta detector** | `test_writer_thread_captures_level_event_delta_automatically` | (prev, new) snap pair -> level_events row |
| **Phase-1-specific: writer tolerates cold start** | `test_writer_thread_tolerates_none_snapshot` | poller.latest()=(None,...) -> no raise, no rows |
| **Phase-1-specific: ai_turn off-caller-thread** | `test_record_ai_turn_returns_quickly_under_slow_disk` | 500 ms blob stall does not block chat path; caller returns < 50 ms |
| **Phase-1-specific: snapshot_features captures full surface area** | `test_writer_thread_persists_full_snapshot_feature_columns` | mid, spread, OR high/low, flow_regime, conviction_score, news_blocked all land in DB |
| **Phase-1-specific: ai_turn writer-thread persistence** | `test_writer_drains_ai_turn_record_to_blobs_and_db` | record_ai_turn -> enqueue -> writer thread writes blobs + ai_turns row |

---

## Rollback Procedure

Rollback is two operator actions:

1. **In-place toggle (no redeploy):**
   - Edit `pax-ai/pax_ai_config.json` to set `{"feature_bus": {"enabled": false}}`.
   - Restart Pax AI (close pywebview window, run `pax-ai-start.bat` again).
   - Bus stops writing. DB file + blob trees remain for forensics. No data lost.

2. **Full revert (code rollback, if a regression is found):**
   - In git: `git revert <commit-range-for-phase-1>` (operator runs after explicit approval).
   - The reverted commits remove:
     - `pax-ai/pax_ai/feature_bus.py`
     - All `pax-ai/tests/test_feature_bus_*.py`
     - The `feature_bus` block in `config.py::DEFAULTS`
     - The 1-line hook in `triggers.py::_emit_edge` and the 6 call-site `alias=str(snap.get("alias") or ALIAS_DEFAULT)` updates
     - The end-of-handler block in `chat.py::handle_chat_stream`
     - The 1-line addition in `server.py::_api_pax_health`
     - The 2-line startup wiring in `__main__.py`
     - The `__version__` and `pyproject.toml` version bumps
   - Manually delete `D:\BookmapLogs\pax-bus.db`, `pax-bus.db-wal`, `pax-bus.db-shm`, `pax-bus.db.lock` if no longer wanted. Blob trees can be archived or deleted at operator discretion.

3. **Hot disable without restart (operational kill switch):**
   - Edit `pax_ai_config.json` -> `feature_bus.enabled: false` -> save.
   - Config hot-reload on mtime change picks it up within ~1 second.
   - The writer thread keeps running but `_enqueue` / `record_*` short-circuit; queue drains to empty.
   - For a clean shutdown of the thread, call `feature_bus.stop()` from a debugging shell (or restart).

---

## Verification Commands

Run all from `C:\Bookmap\addons\MCP\Bookmap\pax-ai\` (or with `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai &&` prefix in Git Bash).

| Check | Command | Expected |
|---|---|---|
| Full test suite | `python -m pytest -v` | ALL PASS, includes ~25 new feature_bus tests |
| Byte-compile | `python -m compileall -q pax_ai` | silent |
| Schema sanity (after enable + chat) | `python -c "import sqlite3; conn=sqlite3.connect(r'D:\BookmapLogs\pax-bus.db'); print([r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type=\"table\"').fetchall()])"` | List contains all 8 tables |
| AI turn round-trip | `python -c "import sqlite3,hashlib; conn=sqlite3.connect(r'D:\BookmapLogs\pax-bus.db'); rows=conn.execute('SELECT digest_sha256,snapshot_sha256 FROM ai_turns ORDER BY id DESC LIMIT 1').fetchall(); print(rows); assert rows and len(rows[0][0])==64 and len(rows[0][1])==64"` | One row, both SHAs are 64 hex chars |
| Blob round-trip | `python -c "import pathlib,hashlib; p=pathlib.Path(r'D:\BookmapLogs\pax-digests'); n=sum(1 for _ in p.rglob('*.txt')); print(n); assert n>=1"` | Count >= 1 |
| Disabled-state invariant | Set `feature_bus.enabled=false`, restart, check `D:\BookmapLogs\pax-bus.db` mtime is unchanged after a chat turn | mtime unchanged |
| Health endpoint smoke | `curl -s http://127.0.0.1:18891/api/pax/health | python -m json.tool` | response includes `feature_bus` block |

---

## Explicit Out-of-Scope (Phase 1)

The following items are reserved for later phases and MUST NOT be implemented in Phase 1:

1. UI rendering of bus data (Phase 2).
2. Reading from the bus on the live chat path (Phase 3).
3. Outcome labeling daemon (`pax-ai/pax_ai/outcomes.py`) - Phase 4.
4. Replay tool (`mcp-server/bookmap_mcp/pax_bus_replay.py`) - Phase 4.
5. EOD report (`pax_bus_eod.py`) - Phase 4.
6. Retention pruner (`tools/feature_bus_prune.py`) - Phase 4 or operator-only.
7. Tuning recommendations (`pax_bus_tune.py`) - Phase 5.
8. Code-change candidate report (`pax_bus_review.py`) - Phase 5.
9. JSONL/parquet rollups - not before Phase 4.
10. Schema migrations for `schema_version > 1` - not until a column add is needed.
11. News calendar ingestion - separate feature.
12. Snapshot RAW JSON column in `snapshot_features.raw_json_sha256` writing - the column exists but is left NULL in Phase 1; chat-turn snapshot blobs (under `pax-snapshots/`) cover replay needs. Snapshot-per-tick blob storage is too high-volume for Phase 1 and is reserved for an opt-in Phase 2 audit mode.
13. Any change to `compute_triggers` / `_api_pax_whynow` output shape.
14. Any change to Claude CLI invocation flags.
15. Any change to the chat SSE byte stream (the golden-bytes test guards this).
16. Any modification to `pax_replay.py` / `pax_outcomes.py` / `pax_weights.json` / `dashboard.py` / `or_session.py` / `prompts.py` / `claude_stream.py` / `edge_calculus.py` / `journal.py` / `static/index.html` / `indicators/OpenRange/**`.
17. Auto-commit of the Phase 1 work - operator commits after their own review.

---

## Final Operator Task (NOT executed by the plan runner)

After all 15 tasks pass and verification commands succeed, the operator reviews the diff and creates a single commit. The plan runner MUST NOT commit. Example commit message:

```
pax-ai: Phase 1 - passive feature bus (capture only, disabled by default)

- New pax-ai/pax_ai/feature_bus.py with 8-table SQLite schema + snapshot/digest
  content-addressed blob stores at D:\BookmapLogs\pax-snapshots / pax-digests.
- One-line hook in triggers._emit_edge() (lazy import, fire-and-forget) for
  edge-event capture. compute_triggers never called by the bus; /api/pax/whynow
  output byte-identical.
- AiTurnRecord assembled at end of chat.handle_chat_stream after SSE done flush;
  digest_sha256 + snapshot_sha256 both NOT NULL.
- Startup wired in __main__.py::main after poller.start() and before
  journal.init(), guarded by feature_bus.enabled (default false).
- Health endpoint extended with feature_bus block.
- Test pass: ~25 new tests; full suite green.
- No live behavior change. No prompt/router/Claude CLI/UI/trading touched.

Spec: docs/superpowers/specs/2026-05-20-pax-feature-bus-design.md
Plan: docs/superpowers/plans/2026-05-20-pax-feature-bus-phase-1.md
```
