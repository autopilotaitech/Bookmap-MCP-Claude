# Pax Feature Bus - Phase 2 UI Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 1 passive feature bus observable to the operator via three new read-only HTTP endpoints and a collapsible "Today's Bus" section in the Pax AI UI - without changing any chat, prompt, router, Claude CLI, trading, or off-limits behavior, and keeping `feature_bus.enabled=False` as the repository default.

**Architecture:** Three new `GET /api/pax/bus/*` endpoints (status / recent / summary) backed by two additive read-only helpers in `feature_bus.py` that issue plain SELECT queries against the existing `pax-bus.db`. UI adds one collapsible `<details id="todays-bus">` block to `static/index.html` with vanilla-JS polling of those endpoints when the section is expanded. No writer-path code is touched. No new config keys are added. Phase 1 invariants (chat SSE byte-identical, whynow byte-identical, bus never calls compute_triggers, etc.) remain pinned.

**Tech Stack:** Python 3.11+, stdlib only (sqlite3, datetime, urllib.parse, http.server, json). Tests use pytest. UI uses vanilla HTML/JS already present in `static/index.html` (no new frameworks).

**Approved Phase 1 commit (parent of this work):** `35e8c22 pax-ai: add passive feature bus capture`

---

## Phase 2 Constraints (Non-Negotiable)

1. `feature_bus.enabled=False` stays the repository default. No new config keys.
2. When `feature_bus.enabled=False`, every new endpoint returns a quiet, well-formed "disabled" payload (not 404, not 500, no error indicator in the UI).
3. Red status indicator is shown ONLY when `enabled=True AND (healthy=False OR running=False)`. Disabled state renders a grey/quiet indicator with an informational message.
4. The chat SSE byte stream, `/api/pax/whynow` JSON output, the writer thread, and every existing Phase 1 test must remain unchanged.
5. The `/api/pax/bus/recent` endpoint allowlists exactly FOUR tables: `level_events`, `trigger_events`, `microstructure_events`, `ai_turns`. `snapshot_features` is **summary-only** in Phase 2 (it's a 1 Hz high-volume table; recent-row listing is reserved for Phase 3+).
6. The summary endpoint operates on UTC days. Parameter is `?date=YYYY-MM-DD` interpreted as UTC. Default is the current UTC date. The UI label reads "UTC day" (not "today" alone).
7. UI tests are STATIC HTML/JS string guards only. No browser test, no claim of browser-level polling proof, no assertion of `setInterval` actually firing - only that the strings/structure that WOULD drive polling are present in the served HTML.
8. No commits performed by the plan executor. Final commit is an explicit operator-driven task at the end.
9. No runtime stop semantics introduced. Rollback for a running process is `feature_bus.enabled=false` in `pax_ai_config.json` + **restart** of the Pax AI process. Hot-disable without restart is NOT a Phase 2 feature.

## Files NOT touched in Phase 2 (hard line)

| Path | Reason |
|---|---|
| `mcp-server/bookmap_mcp/**` | Bridge / dashboard tree - entirely off-limits |
| `pax-ai/pax_ai/prompts.py` | System prompt is frozen + cached on disk |
| `pax-ai/pax_ai/claude_stream.py` | `--tools "" --max-turns 1` invariants are locked |
| `pax-ai/pax_ai/edge_calculus.py` | R-table math owner; no changes |
| `pax-ai/pax_ai/journal.py` | Chat journal is independent of the bus |
| `pax-ai/pax_ai/triggers.py` | Phase 1 added the lazy `_emit_edge` hook; Phase 2 does NOT touch it again |
| `pax-ai/pax_ai/chat.py` | Phase 1 wired `AiTurnRecord` assembly; Phase 2 does NOT touch the chat path |
| `pax-ai/pax_ai/poller.py` | Snapshot polling is owned by Phase 1 |
| `pax-ai/pax_ai/__main__.py` | Startup wiring is frozen at Phase 1 |
| `pax-ai/pax_ai/feature_bus.py::start` | Phase 1 writer-thread lifecycle |
| `pax-ai/pax_ai/feature_bus.py::stop` | Same |
| `pax-ai/pax_ai/feature_bus.py::record_trigger` | Phase 1 producer API |
| `pax-ai/pax_ai/feature_bus.py::record_ai_turn` | Same |
| `pax-ai/pax_ai/feature_bus.py::_accepting_events` | Phase 1 gate |
| `pax-ai/pax_ai/feature_bus.py::_writer_loop` | Phase 1 writer thread |
| `pax-ai/pax_ai/feature_bus.py::_drain_to_db` | Phase 1 batch writer |
| `pax-ai/pax_ai/feature_bus.py::_detect_snapshot_deltas` | Phase 1 delta detector |
| `pax-ai/pax_ai/feature_bus.py::_insert_*` | Phase 1 insert helpers |
| `pax-ai/pax_ai/feature_bus.py::status()` | Phase 1 status shape - kept stable; new endpoint wraps it |
| `pax-ai/pyproject.toml` | No version bump in Phase 2 |
| `pax-ai/pax_ai/__init__.py` | No version bump in Phase 2 |
| `indicators/OpenRange/**` | Bookmap addon work is separate |
| `skills/**` | Pax AI prompt skill bodies are frozen |

If any task in this plan needs to modify a file or function above, STOP and escalate.

---

## File Structure

### New files

| Path | Responsibility | LOC est. |
|---|---|---|
| `pax-ai/tests/test_feature_bus_endpoints.py` | Endpoint contract tests (3 endpoints, ~12 tests covering disabled / unknown table / clamp / alias / blob-text omission / UTC-day bounds / DB-missing) | ~280 |
| `pax-ai/tests/test_feature_bus_summary.py` | Helper-level tests for `recent_events()` + `summary_today()` (correctness + safety) | ~180 |
| `pax-ai/tests/test_ui_static_guards.py` | Static HTML string guards on `static/index.html` (NEW Phase-2 section markers only; no browser test) | ~90 |

### Modified files (additive surgical edits)

| Path | Change | LOC delta |
|---|---|---|
| `pax-ai/pax_ai/feature_bus.py` | Append two new public read-only helpers (`recent_events`, `summary_today`) + an internal `_RECENT_PROJECTION` allowlist constant. NOT touching any Phase 1 writer-path code. | +~140 |
| `pax-ai/pax_ai/server.py` | Add three handler functions (`_api_pax_bus_status`, `_api_pax_bus_recent`, `_api_pax_bus_summary`) + three route entries in the URL dispatcher. | +~80 |
| `pax-ai/pax_ai/static/index.html` | Insert one collapsible `<details id="todays-bus">` block + small JS poller bound to its `toggle` event. Uses existing CSS variables. | +~140 |

**Total Phase 2 surface:** ~910 LOC, 25-30 new tests, no schema changes, no production behavior change when `feature_bus.enabled=False` (verified by tests).

---

## Data Model + API Shape

### Helper contracts (additive to `feature_bus.py`)

```python
_RECENT_PROJECTION: Dict[str, Tuple[str, ...]] = {
    "level_events":          ("id", "ts_ms", "alias", "level_label", "level_price",
                              "prev_decision", "new_decision",
                              "prev_confidence", "new_confidence",
                              "prev_proximity", "new_proximity",
                              "trigger_reason"),
    "trigger_events":        ("id", "ts_ms", "alias", "kind", "severity",
                              "label", "headline", "details", "snapshot_ts_ms"),
    "microstructure_events": ("id", "ts_ms", "alias", "event_type",
                              "price", "side", "size"),
    # ai_turns excludes pax_text (multi-KB), digest_text, snapshot_json (blob-only).
    "ai_turns":              ("id", "ts_ms", "model", "deep", "router_primary",
                              "user_text_raw", "snapshot_sha256", "digest_sha256",
                              "total_cost_usd", "input_tokens", "output_tokens",
                              "exit_code"),
}

# snapshot_features is INTENTIONALLY ABSENT. It is the high-rate 1 Hz table.
# Phase 2 exposes it via summary counters only; recent-row listing is reserved
# for Phase 3+ to avoid bloating UI responses.


def recent_events(table: str, limit: int = 10,
                  alias: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read-only SELECT against pax-bus.db. Returns most-recent rows newest first
    (ORDER BY ts_ms DESC). For each row, returns ONLY the columns listed in
    _RECENT_PROJECTION[table] - NEVER full row.

    Args:
      table: must be in _RECENT_PROJECTION (snapshot_features is rejected).
      limit: clamped to [1, 50]. Defaults to 10.
      alias: optional WHERE filter; if None, returns rows across all aliases.

    Returns:
      List of dicts with the projected columns. Empty list when:
        - bus disabled
        - DB file missing (writer hasn't created it yet)
        - DB locked / IO error

    NEVER raises. NEVER blocks > 50 ms. Uses sqlite3 mode=ro + busy_timeout=200ms.

    Phase 2 contract:
      - feature_bus.enabled=False -> returns [] regardless of args.
      - Unknown table name -> raises KeyError. Endpoint converts to HTTP 400.
        (The endpoint, not the helper, owns the HTTP error shape.)
    """


def summary_today(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Aggregate counters for one UTC day. date_str defaults to current UTC date.

    Args:
      date_str: "YYYY-MM-DD" interpreted as UTC. None -> today UTC.

    Returns:
      {
        "enabled":        bool,
        "date":           "YYYY-MM-DD",
        "counts":         {table: int} for the 5 tables (snapshot_features
                          IS included here; counters are cheap and harmless),
        "topAlias":       str | None,   # alias with most rows across all tables
        "lastEventMs":    int | None,   # MAX(ts_ms) across event tables
        "lastEventAgeMs": int | None,   # now_utc_ms - lastEventMs, or None
      }

    SQL bounds: ts_ms >= start_of_utc_day_ms AND ts_ms < start_of_next_utc_day_ms.
    start_of_utc_day_ms = int(datetime(Y,M,D, 0,0,0, tzinfo=timezone.utc).timestamp() * 1000)

    Disabled-state response:
      {"enabled": False, "date": "<date>", "counts": {},
       "topAlias": None, "lastEventMs": None, "lastEventAgeMs": None}

    NEVER raises (catches sqlite3.OperationalError, FileNotFoundError, and
    returns the disabled-shaped or zero-counts response with a "warning" key
    when the bus is enabled but the DB is missing or unreadable).
    """
```

### Endpoint contracts

All three endpoints are `GET`, return `200` always (never 4xx/5xx for normal disabled / db-missing states), and produce JSON with content-type `application/json`.

#### `GET /api/pax/bus/status`

Thin wrapper around `feature_bus.status()` (Phase 1) plus one derived field.

**Response (enabled + running):**
```json
{
  "enabled": true,
  "healthy": true,
  "running": true,
  "lastWriteMs": 1779304418329,
  "lastWriteAgeMs": 1240,
  "queueDepth": 0,
  "rowsToday": 296,
  "blobWritesToday": 2,
  "lastError": null,
  "dbPath": "C:/Users/autop/AppData/Local/Temp/pax-bus-smoke/pax-bus.db"
}
```

**Response (disabled):**
```json
{
  "enabled": false,
  "healthy": true,
  "running": false,
  "lastWriteMs": 0,
  "lastWriteAgeMs": null,
  "queueDepth": 0,
  "rowsToday": 0,
  "blobWritesToday": 0,
  "lastError": null,
  "dbPath": null
}
```

`lastWriteAgeMs` is `now_utc_ms - lastWriteMs` when `lastWriteMs > 0`, otherwise `null`.

#### `GET /api/pax/bus/recent?table=<name>&limit=<int>&alias=<str?>`

| Query param | Type | Default | Validation |
|---|---|---|---|
| `table` | str | required | must be in `{"level_events","trigger_events","microstructure_events","ai_turns"}`; otherwise HTTP 400 |
| `limit` | int | 10 | clamped to `[1, 50]` |
| `alias` | str | None | optional; literal-equals filter |

**Response (enabled, rows present):**
```json
{
  "enabled": true,
  "table": "level_events",
  "rows": [
    {"id": 12, "ts_ms": 1779304403541, "alias": "NQM6.CME@RITHMIC",
     "level_label": "OR-H", "level_price": 23475.0,
     "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
     "prev_confidence": 0.3, "new_confidence": 0.7,
     "prev_proximity": 0, "new_proximity": 1,
     "trigger_reason": "composite_flip"}
  ]
}
```

**Response (disabled):**
```json
{"enabled": false, "table": "level_events", "rows": []}
```

**Response (enabled, DB file missing):**
```json
{"enabled": true, "table": "level_events", "rows": [],
 "warning": "db not yet created"}
```

**Response (unknown table):** HTTP **400** with body:
```json
{"error": "unknown table 'X'",
 "allowed": ["level_events","trigger_events","microstructure_events","ai_turns"]}
```

Note: `snapshot_features` is rejected with the same 400 shape. The error message explicitly lists it as not allowed in Phase 2 (see Task 4 step 1).

#### `GET /api/pax/bus/summary?date=YYYY-MM-DD`

`date` is interpreted as a UTC day. Default = current UTC date.

**Response (enabled):**
```json
{
  "enabled": true,
  "date": "2026-05-20",
  "counts": {
    "snapshot_features":     16432,
    "level_events":            421,
    "microstructure_events":    18,
    "trigger_events":            7,
    "ai_turns":                  3
  },
  "topAlias": "NQM6.CME@RITHMIC",
  "lastEventMs": 1779304403541,
  "lastEventAgeMs": 1240
}
```

**Response (disabled):**
```json
{
  "enabled": false,
  "date": "2026-05-20",
  "counts": {},
  "topAlias": null,
  "lastEventMs": null,
  "lastEventAgeMs": null
}
```

**Response (enabled, DB missing):**
```json
{
  "enabled": true,
  "date": "2026-05-20",
  "counts": {"snapshot_features":0,"level_events":0,"microstructure_events":0,"trigger_events":0,"ai_turns":0},
  "topAlias": null,
  "lastEventMs": null,
  "lastEventAgeMs": null,
  "warning": "db not yet created"
}
```

### UI contract (`static/index.html`)

**Placement:** between the existing Edge Calculus drawer and the Playbook drawer (search the current HTML for those `<details>` blocks; insert the new one alphabetically by id - `todays-bus` falls between them).

**Element:**

```html
<details id="todays-bus">
  <summary>Today's Bus  <span id="todays-bus-state-dot"></span></summary>
  <div id="todays-bus-body">
    <div id="todays-bus-summary"><!-- populated by JS --></div>
    <div id="todays-bus-recent-levels"><!-- last 10 level_events --></div>
    <div id="todays-bus-recent-triggers"><!-- last 10 trigger_events --></div>
    <div id="todays-bus-disabled" style="display:none">
      <p>Capture disabled. Set <code>feature_bus.enabled=true</code> in
         <code>pax_ai_config.json</code> and restart Pax AI to enable
         passive capture.</p>
    </div>
  </div>
</details>
```

**State machine for the dot indicator** (`#todays-bus-state-dot`):

| Bus state                                                      | Dot character / color |
|----------------------------------------------------------------|------------------------|
| `enabled=false`                                                | grey `o` (or styled `.dot-grey`) - "(capture disabled)" tooltip |
| `enabled=true AND healthy=true AND running=true AND lastWriteAgeMs < 5000` | green `o` - "live" tooltip |
| `enabled=true AND healthy=true AND running=true AND lastWriteAgeMs >= 5000` | yellow `o` - "idle - bridge offline or no snapshot changes" tooltip |
| `enabled=true AND (healthy=false OR running=false)`            | red `o` - tooltip surfaces `lastError` |

**Red status is shown ONLY when enabled AND broken.** Disabled is NEVER red.

**Polling behavior:** (NOT verified by browser test - only by static guards)

- The `<details>` element's `toggle` event listener triggers polling start/stop.
- When `open`, fetches `/api/pax/bus/status` + `/api/pax/bus/summary` every 2000 ms, and `/api/pax/bus/recent?table=level_events&limit=10` + `/api/pax/bus/recent?table=trigger_events&limit=10` every 5000 ms.
- When `open=false`, all polling intervals are cleared.

**Disabled-state rendering:**

When `bus/status` reports `enabled=false`, JS shows the `#todays-bus-disabled` paragraph and hides the summary/recent panels. No error styling, no spinner, no retry indicator.

**Summary header text:**

The summary panel renders: `Rows today (UTC day {date}): {snapshot_features} snapshots, {level_events} level events, {trigger_events} triggers, {ai_turns} AI turns. Last write: {ago}.`

Note explicit "UTC day {date}" copy.

---

## Task List

### Task 1: Helper `feature_bus.recent_events()` (+ projection allowlist)

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py` (append-only - new function + module-level `_RECENT_PROJECTION` dict)
- Create: `pax-ai/tests/test_feature_bus_summary.py`

- [ ] **Step 1: Write the failing tests**

Create `pax-ai/tests/test_feature_bus_summary.py` with this content:

```python
"""Phase-2 read-only helpers: recent_events + summary_today.

Tests here cover the helper layer only - the endpoint wrappers and
their HTTP-shape assertions live in test_feature_bus_endpoints.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus


def _enable_bus_at(tmp_path, monkeypatch):
    """Configure the bus pointing at a tmp_path DB. Caller must populate
    rows via sqlite3 directly; this fixture does NOT start the writer."""
    db_path  = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
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
    return db_path


def _disable_bus(tmp_path, monkeypatch):
    db_path = tmp_path / "must-not-be-used.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max":         2000,
            "writer_idle_ms":    100,
            "capture_ms":        1000,
            "retention_days":    30,
        },
    })
    return db_path


def _seed_level_events(db_path: Path, rows: int):
    """Create the bus schema in db_path and insert N level_events rows."""
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(rows):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 23475.0,
                        'WAIT', 'FOLLOW_LONG', 0.3, 0.7,
                        0, 1, 'composite_flip')
            """, (1_000_000_000_000 + i, 'NQM6' if i % 2 == 0 else 'ESM6'))


# -- recent_events contract --------------------------------------------------

def test_recent_events_disabled_returns_empty(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    assert feature_bus.recent_events("level_events") == []


def test_recent_events_db_missing_returns_empty(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    # DB file deliberately not created.
    assert feature_bus.recent_events("level_events") == []


def test_recent_events_rejects_snapshot_features_table(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        feature_bus.recent_events("snapshot_features")


def test_recent_events_rejects_arbitrary_table(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        feature_bus.recent_events("sqlite_master")


def test_recent_events_returns_newest_first(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=5)
    rows = feature_bus.recent_events("level_events", limit=5)
    assert [r["ts_ms"] for r in rows] == sorted([r["ts_ms"] for r in rows], reverse=True)


def test_recent_events_clamps_limit_to_1(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=5)
    assert len(feature_bus.recent_events("level_events", limit=0)) == 1
    assert len(feature_bus.recent_events("level_events", limit=-99)) == 1


def test_recent_events_clamps_limit_to_50(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=120)
    assert len(feature_bus.recent_events("level_events", limit=9999)) == 50


def test_recent_events_alias_filter(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=10)
    nqm = feature_bus.recent_events("level_events", limit=50, alias="NQM6")
    esm = feature_bus.recent_events("level_events", limit=50, alias="ESM6")
    assert all(r["alias"] == "NQM6" for r in nqm)
    assert all(r["alias"] == "ESM6" for r in esm)
    assert len(nqm) + len(esm) == 10


def test_recent_events_projection_columns_only(tmp_path, monkeypatch):
    """Each returned row contains exactly the columns in _RECENT_PROJECTION,
    nothing more."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=1)
    [row] = feature_bus.recent_events("level_events", limit=1)
    expected = set(feature_bus._RECENT_PROJECTION["level_events"])
    assert set(row.keys()) == expected


def test_recent_events_ai_turns_excludes_blob_text_columns(tmp_path, monkeypatch):
    """ai_turns projection MUST NOT include digest_text, snapshot_json, pax_text.
    These columns either don't exist (digest_text, snapshot_json - blob-only) or
    are large (pax_text). Projection allowlist is the guard."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_sha256, digest_sha256, aborted)
            VALUES (1, 1000, 'r1', 0, 'claude-haiku-4-5',
                    'ping', 'ping', 'POTENTIALLY LONG PROSE TEXT HERE',
                    ?, ?, 0)
        """, ('s' * 64, 'd' * 64))
    [row] = feature_bus.recent_events("ai_turns", limit=1)
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row
    assert row["digest_sha256"]   == "d" * 64
    assert row["snapshot_sha256"] == "s" * 64
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_summary.py -v`

Expected: ALL fail with `AttributeError: module 'pax_ai.feature_bus' has no attribute 'recent_events'` (and `_RECENT_PROJECTION`).

- [ ] **Step 3: Implement the helper**

Append to `pax-ai/pax_ai/feature_bus.py` (at the end of the file, after the existing append-only helpers - do NOT touch any earlier writer-path code):

```python
# ---------------------------------------------------------------------------
# Phase 2 read-only helpers - UI observability only.
# These NEVER touch the writer thread, the queue, or any record_* path.
# They open the bus DB in read-only mode and return projected rows. The
# endpoint layer in server.py wraps them with HTTP shape; the helpers
# themselves can be unit-tested without HTTP.
# ---------------------------------------------------------------------------

from datetime import datetime, timedelta, timezone


_RECENT_PROJECTION: Dict[str, tuple] = {
    "level_events":          ("id", "ts_ms", "alias", "level_label", "level_price",
                              "prev_decision", "new_decision",
                              "prev_confidence", "new_confidence",
                              "prev_proximity", "new_proximity",
                              "trigger_reason"),
    "trigger_events":        ("id", "ts_ms", "alias", "kind", "severity",
                              "label", "headline", "details", "snapshot_ts_ms"),
    "microstructure_events": ("id", "ts_ms", "alias", "event_type",
                              "price", "side", "size"),
    "ai_turns":              ("id", "ts_ms", "model", "deep", "router_primary",
                              "user_text_raw", "snapshot_sha256", "digest_sha256",
                              "total_cost_usd", "input_tokens", "output_tokens",
                              "exit_code"),
}
# snapshot_features is INTENTIONALLY omitted - high-rate 1Hz table; recent-row
# listing is a Phase 3+ concern. Summary counters are in summary_today().


def _open_db_readonly(db_path: Path) -> sqlite3.Connection:
    """Open the bus DB in read-only mode with a short busy timeout.
    Raises FileNotFoundError when the DB file doesn't exist yet."""
    if not Path(db_path).exists():
        raise FileNotFoundError(str(db_path))
    uri = f"file:{Path(db_path).as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=0.2,
                            isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA query_only=ON")
    conn.row_factory = sqlite3.Row
    return conn


def recent_events(table: str, limit: int = 10,
                   alias: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read-only SELECT of most-recent rows from one of the four
    projection-allowlisted tables. NEVER raises (except KeyError for
    unknown tables - the endpoint converts that to HTTP 400)."""
    if table not in _RECENT_PROJECTION:
        raise KeyError(table)
    if not config.get("feature_bus.enabled", False):
        return []
    # Clamp limit to [1, 50].
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 10
    n = max(1, min(50, n))
    cols = _RECENT_PROJECTION[table]
    sql = (f"SELECT {','.join(cols)} FROM {table} "
           + ("WHERE alias=? " if alias is not None else "")
           + "ORDER BY ts_ms DESC LIMIT ?")
    params = ((alias, n) if alias is not None else (n,))
    db_path = Path(config.get("feature_bus.db_path"))
    try:
        with _open_db_readonly(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except (FileNotFoundError, sqlite3.OperationalError):
        return []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_summary.py -v`

Expected: 10 passed.

ALSO: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v` -> 332 prior + 10 new = **342 passed** (no Phase 1 regression).

---

### Task 2: Helper `feature_bus.summary_today()`

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py` (append-only)
- Modify: `pax-ai/tests/test_feature_bus_summary.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `pax-ai/tests/test_feature_bus_summary.py`:

```python
# -- summary_today contract --------------------------------------------------

def _all_tables_count_zero(d):
    return {
        "snapshot_features":     0,
        "level_events":          0,
        "microstructure_events": 0,
        "trigger_events":        0,
        "ai_turns":              0,
    } == d


def test_summary_today_disabled_returns_quiet_payload(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    s = feature_bus.summary_today()
    assert s["enabled"] is False
    assert s["counts"] == {}
    assert s["topAlias"] is None
    assert s["lastEventMs"] is None
    assert s["lastEventAgeMs"] is None
    # Even disabled, the date echoes the requested (or default) UTC date.
    assert s["date"]


def test_summary_today_default_date_is_current_utc_date(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    expected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = feature_bus.summary_today()
    assert s["date"] == expected


def test_summary_today_db_missing_returns_zero_counts_with_warning(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    s = feature_bus.summary_today()
    assert s["enabled"] is True
    assert _all_tables_count_zero(s["counts"])
    assert s["warning"] == "db not yet created"


def test_summary_today_counts_rows_within_utc_day(tmp_path, monkeypatch):
    """Rows whose ts_ms falls inside the UTC day [start, next_start) count.
    Rows outside (yesterday, tomorrow) are excluded."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    tomorrow  = today + timedelta(days=1)
    ts = lambda d: int(d.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for d in (yesterday, today, today, tomorrow):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (ts(d),))
    s = feature_bus.summary_today()
    assert s["counts"]["level_events"] == 2  # only the two `today` rows


def test_summary_today_topAlias_uses_most_active_alias(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    base_ts = int(today.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(7):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (base_ts + i, "NQM6" if i < 5 else "ESM6"))
    s = feature_bus.summary_today()
    assert s["topAlias"] == "NQM6"


def test_summary_today_lastEventMs_is_max_across_event_tables(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    base_ts = int(today.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO level_events
              (schema_version, ts_ms, alias, level_label, level_price,
               prev_decision, new_decision, prev_confidence, new_confidence,
               prev_proximity, new_proximity, trigger_reason)
            VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                    0.3, 0.7, 0, 1, 'composite_flip')
        """, (base_ts + 100,))
        conn.execute("""
            INSERT INTO trigger_events
              (schema_version, ts_ms, alias, kind, severity, snapshot_ts_ms)
            VALUES (1, ?, 'NQM6', 'TREND_SIGNAL_FIRE', 'HIGH', ?)
        """, (base_ts + 200, base_ts + 200))
    s = feature_bus.summary_today()
    assert s["lastEventMs"] == base_ts + 200
    assert s["lastEventAgeMs"] is not None
    assert s["lastEventAgeMs"] >= 0


def test_summary_today_explicit_date_param(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    # Insert at a specific UTC date.
    from datetime import datetime, timezone
    d = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    ts = int(d.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO level_events
              (schema_version, ts_ms, alias, level_label, level_price,
               prev_decision, new_decision, prev_confidence, new_confidence,
               prev_proximity, new_proximity, trigger_reason)
            VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                    0.3, 0.7, 0, 1, 'composite_flip')
        """, (ts,))
    s = feature_bus.summary_today("2026-01-15")
    assert s["date"] == "2026-01-15"
    assert s["counts"]["level_events"] == 1
    # And a different UTC day finds zero.
    s2 = feature_bus.summary_today("2026-01-16")
    assert s2["counts"]["level_events"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_summary.py -v`

Expected: 7 new tests FAIL (`summary_today` undefined). 10 tests from Task 1 still pass.

- [ ] **Step 3: Implement the helper**

Append to `pax-ai/pax_ai/feature_bus.py`:

```python
def _utc_day_bounds_ms(date_str: Optional[str]) -> tuple:
    """Return (start_ms, next_start_ms, date_str_normalized).
    date_str None or invalid -> current UTC date."""
    if date_str:
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            d = datetime.now(timezone.utc)
    else:
        d = datetime.now(timezone.utc)
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    next_start = start + timedelta(days=1)
    return (int(start.timestamp() * 1000),
            int(next_start.timestamp() * 1000),
            start.strftime("%Y-%m-%d"))


def _disabled_summary(date_str_norm: str) -> Dict[str, Any]:
    return {
        "enabled":        False,
        "date":           date_str_norm,
        "counts":         {},
        "topAlias":       None,
        "lastEventMs":    None,
        "lastEventAgeMs": None,
    }


_SUMMARY_TABLES = (
    "snapshot_features", "level_events", "microstructure_events",
    "trigger_events", "ai_turns",
)
_EVENT_TABLES_FOR_LAST_TS = (
    "level_events", "microstructure_events", "trigger_events", "ai_turns",
)


def summary_today(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Counters for one UTC day. See module docstring for full contract."""
    start_ms, next_ms, date_norm = _utc_day_bounds_ms(date_str)
    if not config.get("feature_bus.enabled", False):
        return _disabled_summary(date_norm)
    db_path = Path(config.get("feature_bus.db_path"))
    out: Dict[str, Any] = {
        "enabled":        True,
        "date":           date_norm,
        "counts":         {t: 0 for t in _SUMMARY_TABLES},
        "topAlias":       None,
        "lastEventMs":    None,
        "lastEventAgeMs": None,
    }
    try:
        conn = _open_db_readonly(db_path)
    except (FileNotFoundError, sqlite3.OperationalError):
        out["warning"] = "db not yet created"
        return out
    try:
        with conn:
            for t in _SUMMARY_TABLES:
                try:
                    n = conn.execute(
                        f"SELECT COUNT(*) FROM {t} WHERE ts_ms>=? AND ts_ms<?",
                        (start_ms, next_ms)).fetchone()[0]
                except sqlite3.OperationalError:
                    n = 0
                out["counts"][t] = int(n)
            # topAlias: union all rows across tables WHERE ts_ms in [start, next),
            # GROUP BY alias, take max count.
            union_parts = []
            params: List[int] = []
            for t in _SUMMARY_TABLES:
                union_parts.append(
                    f"SELECT alias FROM {t} WHERE ts_ms>=? AND ts_ms<?")
                params.extend([start_ms, next_ms])
            top_sql = (f"SELECT alias FROM ({' UNION ALL '.join(union_parts)}) "
                       "GROUP BY alias ORDER BY COUNT(*) DESC LIMIT 1")
            try:
                row = conn.execute(top_sql, params).fetchone()
                if row and row[0]:
                    out["topAlias"] = row[0]
            except sqlite3.OperationalError:
                pass
            # lastEventMs across event tables (NOT snapshot_features).
            last_ts: Optional[int] = None
            for t in _EVENT_TABLES_FOR_LAST_TS:
                try:
                    r = conn.execute(
                        f"SELECT MAX(ts_ms) FROM {t} WHERE ts_ms>=? AND ts_ms<?",
                        (start_ms, next_ms)).fetchone()
                    if r and r[0] is not None:
                        last_ts = max(last_ts or 0, int(r[0]))
                except sqlite3.OperationalError:
                    pass
            if last_ts:
                out["lastEventMs"] = last_ts
                out["lastEventAgeMs"] = int(time.time() * 1000) - last_ts
    finally:
        conn.close()
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_summary.py -v`

Expected: 17 passed (10 from Task 1 + 7 new).

Full suite: `python -m pytest -v` -> 349 passed.

---

### Task 3: Endpoint `/api/pax/bus/status`

**Files:**
- Modify: `pax-ai/pax_ai/server.py` (add handler + route)
- Create: `pax-ai/tests/test_feature_bus_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Create `pax-ai/tests/test_feature_bus_endpoints.py`:

```python
"""Phase-2 endpoint contract tests.

These exercise the handler functions directly (status, body) - the same
pattern used by tests/test_server_helpers.py for the existing Pax AI
endpoints. HTTP routing is intentionally not exercised here; the route
table is small and inspected by static review."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus
from pax_ai.server import (
    _api_pax_bus_status,
    _api_pax_bus_recent,
    _api_pax_bus_summary,
)


def _enable(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": True, "db_path": str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 20, "capture_ms": 20,
            "retention_days": 30,
        },
    })
    return db_path


def _disable(tmp_path, monkeypatch):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": False, "db_path": str(tmp_path / "nope.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 30,
        },
    })


# -- /api/pax/bus/status ---------------------------------------------------

def test_status_endpoint_returns_200_and_status_block(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_status()
    assert status == 200
    for key in ("enabled", "healthy", "running", "lastWriteMs",
                "lastWriteAgeMs", "queueDepth", "rowsToday",
                "blobWritesToday", "lastError", "dbPath"):
        assert key in body


def test_status_endpoint_disabled_dbpath_is_null(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_status()
    assert status == 200
    assert body["enabled"] is False
    assert body["dbPath"] is None
    assert body["lastWriteAgeMs"] is None


def test_status_endpoint_lastWriteAgeMs_null_when_no_writes(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    feature_bus._LAST_WRITE_MS = 0
    status, body = _api_pax_bus_status()
    assert body["lastWriteAgeMs"] is None


def test_status_endpoint_lastWriteAgeMs_is_now_minus_last(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    import time
    feature_bus._LAST_WRITE_MS = int(time.time() * 1000) - 1500
    status, body = _api_pax_bus_status()
    assert 1400 < body["lastWriteAgeMs"] < 1700
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: ALL fail with `ImportError: cannot import name '_api_pax_bus_status' from 'pax_ai.server'`.

- [ ] **Step 3: Implement the handler + route**

In `pax-ai/pax_ai/server.py`:

(a) After the existing `_api_pax_health` function (around line 200), insert:

```python
def _api_pax_bus_status() -> Tuple[int, Dict[str, Any]]:
    """Phase-2 read-only endpoint: writer status + derived lastWriteAgeMs."""
    body = feature_bus.status()
    last_ms = body.get("lastWriteMs") or 0
    if last_ms > 0:
        import time as _t
        body["lastWriteAgeMs"] = int(_t.time() * 1000) - int(last_ms)
    else:
        body["lastWriteAgeMs"] = None
    return 200, body
```

(b) In the URL dispatcher block (find the existing `if path == "/api/pax/health":` branch around line 335), add a new branch just below it:

```python
            if path == "/api/pax/bus/status":
                status, body = _api_pax_bus_status()
                self._send_json(status, body); return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: 4 passed.

Full suite: `python -m pytest -v` -> 353 passed.

---

### Task 4: Endpoint `/api/pax/bus/recent`

**Files:**
- Modify: `pax-ai/pax_ai/server.py`
- Modify: `pax-ai/tests/test_feature_bus_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `pax-ai/tests/test_feature_bus_endpoints.py`:

```python
# -- /api/pax/bus/recent ---------------------------------------------------

def _seed_level(db_path, n=3, alias="NQM6"):
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(n):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (1_000_000_000_000 + i, alias))


def test_recent_endpoint_disabled_returns_quiet_empty(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is False
    assert body["table"] == "level_events"
    assert body["rows"] == []


def test_recent_endpoint_unknown_table_returns_400_with_allowed(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "sqlite_master"})
    assert status == 400
    assert "unknown table" in body["error"]
    assert set(body["allowed"]) == {
        "level_events", "trigger_events",
        "microstructure_events", "ai_turns",
    }


def test_recent_endpoint_rejects_snapshot_features(tmp_path, monkeypatch):
    """snapshot_features is summary-only in Phase 2 - explicit rejection."""
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "snapshot_features"})
    assert status == 400
    assert "snapshot_features" in body["error"]
    assert "snapshot_features" not in body["allowed"]


def test_recent_endpoint_missing_table_param_returns_400(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({})
    assert status == 400
    assert "table" in body["error"].lower()


def test_recent_endpoint_db_missing_returns_warning(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is True
    assert body["rows"] == []
    assert body.get("warning") == "db not yet created"


def test_recent_endpoint_default_limit_is_10(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=25)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert len(body["rows"]) == 10


def test_recent_endpoint_clamps_limit_to_50(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=120)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "9999"})
    assert len(body["rows"]) == 50


def test_recent_endpoint_clamps_limit_to_1(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=5)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "0"})
    assert len(body["rows"]) == 1
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "-99"})
    assert len(body["rows"]) == 1


def test_recent_endpoint_garbage_limit_falls_back_to_default(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=25)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "abc"})
    assert len(body["rows"]) == 10


def test_recent_endpoint_alias_filter(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=5, alias="NQM6")
    _seed_level(db, n=3, alias="ESM6")
    status, body = _api_pax_bus_recent({"table": "level_events",
                                          "limit": "50", "alias": "ESM6"})
    assert all(r["alias"] == "ESM6" for r in body["rows"])
    assert len(body["rows"]) == 3


def test_recent_endpoint_ai_turns_omits_blob_text_columns(tmp_path, monkeypatch):
    """Regression: even if a future schema migration adds digest_text / snapshot_json
    / pax_text columns, the projection allowlist keeps them out of the API."""
    db = _enable(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_sha256, digest_sha256, aborted)
            VALUES (1, 1, 'r1', 0, 'claude-haiku-4-5',
                    'ping', 'ping', 'LONG PROSE',
                    ?, ?, 0)
        """, ('s' * 64, 'd' * 64))
    status, body = _api_pax_bus_recent({"table": "ai_turns", "limit": "1"})
    assert status == 200
    [row] = body["rows"]
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row
    assert row["digest_sha256"] == "d" * 64


def test_recent_endpoint_response_shape_when_rows_present(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=2)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is True
    assert body["table"] == "level_events"
    assert "rows" in body
    assert "warning" not in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: 12 new tests FAIL (`_api_pax_bus_recent` undefined). 4 from Task 3 still pass.

- [ ] **Step 3: Implement the handler + route**

In `pax-ai/pax_ai/server.py`:

(a) After `_api_pax_bus_status`, add:

```python
_RECENT_ALLOWED = ("level_events", "trigger_events",
                   "microstructure_events", "ai_turns")


def _api_pax_bus_recent(qs: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Phase-2 read-only endpoint: most-recent N rows from one allowlisted table.

    qs is a flat dict of query-string params (already URL-decoded). Values are
    strings. Missing/garbage values fall back to documented defaults.

    snapshot_features is summary-only in Phase 2; rejected with HTTP 400."""
    table = qs.get("table")
    if not table:
        return 400, {"error": "missing 'table' query param",
                      "allowed": list(_RECENT_ALLOWED)}
    if table not in _RECENT_ALLOWED:
        return 400, {"error": f"unknown table '{table}' (snapshot_features is "
                                 "summary-only in Phase 2)",
                      "allowed": list(_RECENT_ALLOWED)}
    if not config.get("feature_bus.enabled", False):
        return 200, {"enabled": False, "table": table, "rows": []}
    # limit clamping + garbage tolerance
    try:
        limit = int(qs.get("limit") or 10)
    except (TypeError, ValueError):
        limit = 10
    alias = qs.get("alias") or None
    db_path = Path(config.get("feature_bus.db_path"))
    if not db_path.exists():
        return 200, {"enabled": True, "table": table, "rows": [],
                      "warning": "db not yet created"}
    rows = feature_bus.recent_events(table, limit=limit, alias=alias)
    return 200, {"enabled": True, "table": table, "rows": rows}
```

(b) Update the urllib import at the top of `pax-ai/pax_ai/server.py` to include `parse_qs`. The existing line is:

```python
from urllib.parse import urlparse, unquote
```

Change it to:

```python
from urllib.parse import urlparse, unquote, parse_qs
```

(c) In the URL dispatcher, after the `bus/status` branch, add:

```python
            if path == "/api/pax/bus/recent":
                raw = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                qs = {k: v[0] for k, v in raw.items()}
                status, body = _api_pax_bus_recent(qs)
                self._send_json(status, body); return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: 16 passed (4 from Task 3 + 12 new).

Full suite: 365 passed.

---

### Task 5: Endpoint `/api/pax/bus/summary`

**Files:**
- Modify: `pax-ai/pax_ai/server.py`
- Modify: `pax-ai/tests/test_feature_bus_endpoints.py`

- [ ] **Step 1: Write the failing tests**

Append to `pax-ai/tests/test_feature_bus_endpoints.py`:

```python
# -- /api/pax/bus/summary --------------------------------------------------

def test_summary_endpoint_disabled_returns_quiet(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({})
    assert status == 200
    assert body["enabled"] is False
    assert body["counts"] == {}
    assert body["topAlias"]    is None
    assert body["lastEventMs"] is None
    assert body["lastEventAgeMs"] is None


def test_summary_endpoint_default_date_is_utc_today(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    status, body = _api_pax_bus_summary({})
    assert body["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_summary_endpoint_explicit_date_param_passes_through(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({"date": "2026-01-15"})
    assert body["date"] == "2026-01-15"


def test_summary_endpoint_garbage_date_falls_back_to_utc_today(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    status, body = _api_pax_bus_summary({"date": "not-a-date"})
    assert body["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_summary_endpoint_db_missing_returns_zero_counts_warning(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({})
    assert status == 200
    assert body["enabled"] is True
    assert body["counts"]["level_events"] == 0
    assert body.get("warning") == "db not yet created"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: 5 new tests FAIL (`_api_pax_bus_summary` undefined).

- [ ] **Step 3: Implement the handler + route**

In `pax-ai/pax_ai/server.py`, after `_api_pax_bus_recent`:

```python
def _api_pax_bus_summary(qs: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """Phase-2 read-only endpoint: UTC-day rollup counters."""
    date_str = qs.get("date") or None
    body = feature_bus.summary_today(date_str)
    return 200, body
```

In the URL dispatcher, after the `bus/recent` branch:

```python
            if path == "/api/pax/bus/summary":
                raw = parse_qs(urlparse(self.path).query, keep_blank_values=True)
                qs = {k: v[0] for k, v in raw.items()}
                status, body = _api_pax_bus_summary(qs)
                self._send_json(status, body); return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_endpoints.py -v`

Expected: 21 passed (16 + 5 new).

Full suite: 370 passed.

---

### Task 6: UI section + static HTML guards

**Files:**
- Modify: `pax-ai/pax_ai/static/index.html`
- Create: `pax-ai/tests/test_ui_static_guards.py`

- [ ] **Step 1: Write the failing tests**

Create `pax-ai/tests/test_ui_static_guards.py`:

```python
"""Static HTML/JS string guards for the Phase 2 'Today's Bus' UI section.

These are STATIC GUARDS only. They assert that the strings + element
structure that WOULD drive UI polling and disabled-state rendering exist
in the served HTML. They do NOT execute JavaScript, do NOT render the page
in a browser, do NOT verify polling cadence in real time, and do NOT assert
visible pixels. Browser-level verification belongs to operator smoke runs."""
from __future__ import annotations

import re
from pathlib import Path


HTML_PATH = Path(__file__).resolve().parent.parent / "pax_ai" / "static" / "index.html"


def _html() -> str:
    return HTML_PATH.read_text(encoding="utf-8")


def test_index_html_exists():
    assert HTML_PATH.exists()


def test_index_html_has_todays_bus_details_element():
    """The collapsible <details> block with id='todays-bus' must exist."""
    html = _html()
    assert re.search(r'<details\s+id="todays-bus"', html), \
        "expected <details id=\"todays-bus\"> element"


def test_index_html_todays_bus_is_collapsed_by_default():
    """The <details> tag must NOT carry the 'open' attribute - default collapsed."""
    html = _html()
    m = re.search(r'<details\s+id="todays-bus"([^>]*)>', html)
    assert m, "details element missing"
    attrs = m.group(1)
    assert "open" not in attrs.lower(), \
        "todays-bus must not have 'open' attribute (default collapsed)"


def test_index_html_uses_utc_day_label_in_summary_panel():
    """The summary panel template/markup must include the literal 'UTC day' label."""
    html = _html()
    assert "UTC day" in html, "summary panel must label the date as 'UTC day'"


def test_index_html_has_disabled_state_block():
    """When bus disabled, UI shows the #todays-bus-disabled paragraph with
    instructions to enable + restart."""
    html = _html()
    assert 'id="todays-bus-disabled"' in html
    assert "feature_bus.enabled=true" in html
    assert "restart" in html.lower(), \
        "disabled state copy should mention restarting Pax AI"


def test_index_html_references_three_bus_endpoints():
    """The static HTML/JS must contain the three Phase-2 endpoint URLs as
    string literals. (This proves WIRING intent only - actual polling
    behavior is an operator-smoke concern, not a Python-test concern.)"""
    html = _html()
    assert "/api/pax/bus/status"  in html
    assert "/api/pax/bus/recent"  in html
    assert "/api/pax/bus/summary" in html


def test_index_html_has_state_dot_element():
    """The status-dot indicator element must exist with the expected id."""
    html = _html()
    assert 'id="todays-bus-state-dot"' in html


def test_index_html_phase_1_strip_section_unchanged():
    """Regression: the existing strip / WhyNow / Edge / Playbook sections
    are not accidentally renamed or removed. (Smoke-level guard - exact
    inner content is operator concern.)"""
    html = _html()
    # Sanity: a few existing Phase 1 markers should still be present.
    # If these break, Phase 2 has touched off-limits markup.
    for marker in ('id="strip"', "Pax AI"):
        assert marker in html, f"Phase 1 marker missing: {marker}"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ui_static_guards.py -v`

Expected: Most fail (no `todays-bus` markup yet). `test_index_html_exists` and `test_index_html_phase_1_strip_section_unchanged` should pass (Phase 1 markers still present).

- [ ] **Step 3: Add the HTML + JS section**

Open `pax-ai/pax_ai/static/index.html`. Locate the existing `<details id="edge-calculus">` block and the `<details id="playbook">` block. The new block goes BETWEEN them (alphabetically: edge-calculus < playbook < todays-bus does NOT hold; "playbook" < "todays-bus" - so place the new block AFTER playbook, OR more practically: put it directly below the Edge Calculus drawer for visual proximity to the bus-relevant info).

For concreteness, the implementing engineer should:
- Search for `<details id="playbook">` in `index.html`.
- Insert the new `<details id="todays-bus">` block immediately after the closing `</details>` of the playbook drawer.

Markup to insert:

```html
    <details id="todays-bus">
      <summary>Today's Bus <span id="todays-bus-state-dot" title="status">o</span></summary>
      <div id="todays-bus-body">
        <div id="todays-bus-summary">UTC day -- (loading)</div>
        <div id="todays-bus-recent-levels">Recent level events: (loading)</div>
        <div id="todays-bus-recent-triggers">Recent triggers: (loading)</div>
        <div id="todays-bus-disabled" style="display:none">
          <p>Capture disabled. Set <code>feature_bus.enabled=true</code> in
             <code>pax_ai_config.json</code> and restart Pax AI to enable
             passive capture.</p>
        </div>
      </div>
    </details>
    <script>
    (function() {
      const det = document.getElementById("todays-bus");
      const dot = document.getElementById("todays-bus-state-dot");
      const sum = document.getElementById("todays-bus-summary");
      const lvl = document.getElementById("todays-bus-recent-levels");
      const trg = document.getElementById("todays-bus-recent-triggers");
      const dis = document.getElementById("todays-bus-disabled");
      const body = document.getElementById("todays-bus-body");
      let timers = [];

      function clearTimers() {
        timers.forEach(clearInterval);
        timers = [];
      }

      function setDot(color, tip) {
        if (!dot) return;
        dot.textContent = "o";
        dot.style.color = color;
        dot.title = tip || "";
      }

      function setDisabled(yes) {
        if (!dis || !sum || !lvl || !trg) return;
        dis.style.display = yes ? "" : "none";
        sum.style.display = yes ? "none" : "";
        lvl.style.display = yes ? "none" : "";
        trg.style.display = yes ? "none" : "";
      }

      async function pollStatusAndSummary() {
        try {
          const [statusR, sumR] = await Promise.all([
            fetch("/api/pax/bus/status").then(r => r.json()),
            fetch("/api/pax/bus/summary").then(r => r.json()),
          ]);
          if (!statusR.enabled) {
            setDot("#888", "capture disabled");
            setDisabled(true);
            return;
          }
          setDisabled(false);
          if (!statusR.healthy || !statusR.running) {
            setDot("#d33", statusR.lastError || "unhealthy");
          } else if ((statusR.lastWriteAgeMs ?? 0) >= 5000) {
            setDot("#cc0", "idle - no recent writes");
          } else {
            setDot("#3c3", "live");
          }
          const c = sumR.counts || {};
          const ago = statusR.lastWriteAgeMs == null ? "never"
                      : (statusR.lastWriteAgeMs/1000).toFixed(1) + " s ago";
          sum.textContent =
            "UTC day " + sumR.date + ": " +
            (c.snapshot_features||0) + " snapshots, " +
            (c.level_events||0) + " level events, " +
            (c.trigger_events||0) + " triggers, " +
            (c.ai_turns||0) + " AI turns. Last write: " + ago + ".";
        } catch (e) {
          setDot("#d33", "fetch failed: " + e);
        }
      }

      async function pollRecent() {
        try {
          const [lvR, trR] = await Promise.all([
            fetch("/api/pax/bus/recent?table=level_events&limit=10").then(r => r.json()),
            fetch("/api/pax/bus/recent?table=trigger_events&limit=10").then(r => r.json()),
          ]);
          if (lvR.enabled === false) return;
          if (lvl && (lvR.rows||[]).length) {
            lvl.innerHTML = "Recent level events:<br>" +
              lvR.rows.map(r =>
                new Date(r.ts_ms).toISOString().slice(11,19) + " " +
                r.alias + " " + r.level_label + " " +
                (r.prev_decision||"-") + "&#8594;" + (r.new_decision||"-") +
                " (" + (r.prev_confidence||0).toFixed(2) + "&#8594;" +
                (r.new_confidence||0).toFixed(2) + ")"
              ).join("<br>");
          } else if (lvl) {
            lvl.textContent = "Recent level events: (none today)";
          }
          if (trg && (trR.rows||[]).length) {
            trg.innerHTML = "Recent triggers:<br>" +
              trR.rows.map(r =>
                new Date(r.ts_ms).toISOString().slice(11,19) + " " +
                r.alias + " " + r.kind + " " +
                (r.label||"") + " " + r.severity
              ).join("<br>");
          } else if (trg) {
            trg.textContent = "Recent triggers: (none today)";
          }
        } catch (e) {
          // Quiet: leave previous content in place.
        }
      }

      function start() {
        pollStatusAndSummary();
        pollRecent();
        timers.push(setInterval(pollStatusAndSummary, 2000));
        timers.push(setInterval(pollRecent, 5000));
      }

      if (det) {
        det.addEventListener("toggle", () => {
          clearTimers();
          if (det.open) start();
        });
      }
    })();
    </script>
```

- [ ] **Step 4: Run static guards to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ui_static_guards.py -v`

Expected: 8 passed.

Full suite: `python -m pytest -v` -> 378 passed.

---

### Task 7: Final verification

**Files:** No new files; verification only.

- [ ] **Step 1: Run the full test suite**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v`

Expected: ALL pass (~378 tests). Phase 1 tests must remain green: every existing test in `test_chat_handler.py`, `test_feature_bus_*.py`, `test_triggers.py`, `test_server_helpers.py`, etc. must continue to pass without modification.

- [ ] **Step 2: Byte-compile sanity**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m compileall -q pax_ai`

Expected: exit 0 (silent).

- [ ] **Step 3: Off-limits diff guard**

Run from repo root: `git diff --name-only HEAD | grep -E "(mcp-server/|pax-ai/pax_ai/prompts\.py|pax-ai/pax_ai/claude_stream\.py|pax-ai/pax_ai/edge_calculus\.py|pax-ai/pax_ai/journal\.py|pax-ai/pax_ai/triggers\.py|pax-ai/pax_ai/chat\.py|pax-ai/pax_ai/poller\.py|pax-ai/pax_ai/__main__\.py|indicators/|skills/)" || echo "OK: zero off-limits files modified"`

Expected: `OK: zero off-limits files modified`.

- [ ] **Step 4: Phase-1 writer-path guard**

Run: `git diff HEAD -- pax-ai/pax_ai/feature_bus.py | grep -E "^\-.*def (start|stop|record_trigger|record_ai_turn|_accepting_events|_writer_loop|_drain_to_db|_detect_snapshot_deltas|_insert_)" || echo "OK: zero writer-path functions deleted/modified"`

Also: `git diff HEAD -- pax-ai/pax_ai/feature_bus.py | grep -E "^\+.*def (start|stop|record_trigger|record_ai_turn|_accepting_events|_writer_loop|_drain_to_db|_detect_snapshot_deltas|_insert_)" || echo "OK: zero writer-path functions re-defined"`

Expected: both echo OK lines.

(Phase 2 only APPENDS `recent_events`, `summary_today`, `_open_db_readonly`, `_utc_day_bounds_ms`, `_disabled_summary`, `_RECENT_PROJECTION`, `_SUMMARY_TABLES`, `_EVENT_TABLES_FOR_LAST_TS`. The diff must show those as additions only; the writer-path defs must remain untouched in their original positions.)

- [ ] **Step 5: Chat / whynow byte-identical regression check (Phase 1 invariants still pinned)**

Run:
```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v \
  tests/test_chat_handler.py::test_chat_path_unchanged_when_bus_disabled \
  tests/test_chat_handler.py::test_aiturn_uses_snapshot_captured_at_prompt_build_not_post_done \
  tests/test_feature_bus_integration.py::test_whynow_byte_identical_with_and_without_bus \
  tests/test_feature_bus_triggers.py::test_compute_triggers_never_called_by_feature_bus \
  tests/test_feature_bus_ai_turn.py::test_record_methods_noop_when_unhealthy_or_not_running
```

Expected: 5 passed.

- [ ] **Step 6: Report and STOP**

Produce a short report:
- Files changed (modified + new)
- Test counts (Phase 1 baseline / Phase 2 added / total)
- Confirm: `feature_bus.enabled=False` default unchanged (grep `pax-ai/pax_ai/config.py` for `"enabled":           False`)
- Confirm: no Phase 2 implementation touched `prompts.py`, `claude_stream.py`, `edge_calculus.py`, `journal.py`, `triggers.py`, `chat.py`, `poller.py`, `__main__.py`, or any file under `mcp-server/`, `indicators/`, `skills/`.
- HALT before committing. Final commit is a separate operator-driven task (see Task 8).

---

### Task 8: Operator-driven commit (NOT executed by the plan runner)

**The plan runner MUST NOT commit.** This task documents the suggested commit message for the operator to review and run manually.

Suggested commit:

```bash
git add \
  pax-ai/pax_ai/feature_bus.py \
  pax-ai/pax_ai/server.py \
  pax-ai/pax_ai/static/index.html \
  pax-ai/tests/test_feature_bus_summary.py \
  pax-ai/tests/test_feature_bus_endpoints.py \
  pax-ai/tests/test_ui_static_guards.py \
  docs/superpowers/plans/2026-05-20-pax-feature-bus-phase-2-ui.md

git commit -m "pax-ai: add feature bus UI observability (Phase 2)"
```

Operator confirms `git status --short` shows zero unstaged off-limits files, `git log --oneline -1` shows the new HEAD, and the parent commit is still `35e8c22 pax-ai: add passive feature bus capture`.

---

## Tests Mapped to Phase 2 Risks

| Risk | Test name(s) | Coverage |
|---|---|---|
| `/api/pax/bus/recent` opens a DB connection on every poll - lock contention with writer | (manual: smoke during Phase 2 release) | covered by Phase 1's WAL semantics; readers don't block writers. Helper uses `mode=ro` + `query_only=ON` + 200 ms busy timeout. |
| UI polls when collapsed, wasting CPU | (static guard only; behavior is browser-runtime) | `static/index.html` JS gates `setInterval` on `det.open === true`; visual proof is operator-smoke concern |
| `digest_text` / `snapshot_json` / `pax_text` leak via `/api/pax/bus/recent?table=ai_turns` | `test_recent_endpoint_ai_turns_omits_blob_text_columns`, `test_recent_events_ai_turns_excludes_blob_text_columns` | projection allowlist enforced at helper level |
| Disabled-state UI looks broken (spinner, red dot, etc.) | `test_status_endpoint_disabled_dbpath_is_null`, `test_recent_endpoint_disabled_returns_quiet_empty`, `test_summary_endpoint_disabled_returns_quiet`, `test_index_html_has_disabled_state_block` | grey/quiet copy enforced at HTML + endpoint level |
| Phase 2 endpoints become a Claude-digest hot path by accident | (process / review) | docstring on each helper says "UI consumption only"; Phase 3 plan must explicitly decide whether to reuse or read DB directly |
| `summary_today()` cross-day count error at UTC midnight | `test_summary_today_counts_rows_within_utc_day` | half-open interval `[start_ms, next_start_ms)` |
| Endpoint accidentally leaks production DB path in disabled-state response | `test_status_endpoint_disabled_dbpath_is_null` | `dbPath: null` when disabled |
| Phase 2 changes Claude/chat/router behavior | `test_chat_path_unchanged_when_bus_disabled` (Phase 1) | Phase 1 golden-SSE-bytes test pinned to remain green |
| Phase 2 changes `/api/pax/whynow` output | `test_whynow_byte_identical_with_and_without_bus` (Phase 1) | pinned to remain green |
| `snapshot_features` accidentally listed in /recent endpoint | `test_recent_endpoint_rejects_snapshot_features` | explicit 400 with allowlist that does NOT include it |

---

## Rollback

Phase 2 can be rolled back two ways:

1. **`feature_bus.enabled=false` in `pax_ai_config.json` + restart Pax AI.** The bus stops writing, the new endpoints return quiet `enabled=false` payloads, and the UI section renders its disabled-state message. Restart is REQUIRED because Phase 2 does NOT introduce runtime stop semantics; the writer thread started by `__main__.py::main` does not poll the config for live disable. (Hot-disable without restart is reserved for a possible future phase and is NOT in Phase 2 scope.)

2. **`git revert <phase-2-commit>`.** Removes the three endpoints, the two helpers, the static-guard tests, the UI section, and this plan file. The bus DB on disk remains untouched (intentional - operator can keep the captured rows for forensics).

Neither rollback path requires touching `mcp-server/`, `prompts.py`, `claude_stream.py`, or any other off-limits file.

---

## Out-of-Scope (Phase 3+ parking lot)

- Hot disable / runtime stop semantics (would require new lifecycle code in `feature_bus.py::stop` + a poll-and-honor in `_writer_loop`; Phase 3 if needed).
- Reading bus data into Claude's chat digest.
- `snapshot_features` recent-row listing (high volume; SSE or per-alias filtering may be needed).
- After-hours replay tool, outcome labeling daemon, EOD report, tuning recommendations - all Phase 4-5 per the original design.
- Charts / sparklines / cumulative graphs in the UI - text-only in Phase 2.
- Server-sent-events stream of bus events to the UI (poll-based is sufficient at Phase 2 cadence).
- Retention pruner.
- Any change to `pax_ai_config.json` defaults or shape.
- Any new config key.
