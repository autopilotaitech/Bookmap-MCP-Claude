# Pax Feature Bus - Accelerated Phase 3A + 4A Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the shadow bus-backed digest pipeline (Phase 3A) and the after-hours outcomes/replay foundation (Phase 4A) without changing any live Claude input by default. Both phases ship behind opt-in config flags; the repository default keeps Phase 1+2 behavior bit-identical.

**Architecture:**
- **Phase 3A** adds a parallel digest renderer that reads from `pax-bus.db` and the snapshot/digest blob stores. The shadow render runs ONLY when `feature_bus.enabled=true` (so the disabled path stays bit-identical to Phase 1+2). When the shadow runs, both digests are logged to stderr. The live swap to bus digest as Claude input requires BOTH `feature_bus.enabled=true` AND `chat.use_feature_bus_digest=true`. Bus digest uses a deliberately different block layout from the legacy digest (`[STATE]`/`[ANCHOR]`/... vs. `router_hint + SNAPSHOT DIGEST + USER`); tests assert deterministic rendering + required blocks, not byte-equality with the legacy.
- **Phase 4A** adds two read-only consumers of the bus: an in-process `outcomes.py` daemon that writes `trade_outcomes` rows by re-reading `snapshot_features` at T+60s/180s/300s/900s, and a batch `pax_bus_replay.py` CLI that rebuilds historical digests from blobs and verifies SHA-equality with `ai_turns.digest_sha256`. Optional `pax_bus_eod.py` is gated behind a "replay foundation is stable" check.

**Tech Stack:** Python 3.11+, stdlib only (sqlite3, threading, time, datetime, json, hashlib, argparse). Tests via pytest. No new third-party dependencies.

**Parent commits:**
- Phase 1: `35e8c22 pax-ai: add passive feature bus capture`
- Phase 2: `6021aa9 pax-ai: add feature bus UI observability`

---

## Findings / risks first (read before any task)

| # | Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|---|
| 1 | Phase 3A shadow-digest path computes against stale bus state and diverges from the live snapshot, producing noisy byte-diff logs that mask real regressions | High | Medium | Bus digest reads the SAME snapshot the live digest used (the one captured in `meta["_snapshot_for_capture"]` from Phase 1 chat.py fix). Both digests hash the same source state; divergence MUST be either a rendering bug or a missing field, never a tick race. |
| 2 | `chat.use_feature_bus_digest=true` is flipped to true and silently breaks chat (digest builder bug ships to live Claude) | Medium | High | Default remains false. Flag-flip requires a config edit + restart. Bus digest is INTENTIONALLY a different shape from the legacy digest (new `[STATE]`/`[ANCHOR]`/... block layout vs. legacy `router_hint + SNAPSHOT DIGEST + USER`); tests assert deterministic rendering, required blocks present, and that the flag-gated swap is exercised — NOT byte-equality with the legacy digest. SSE byte-identical regression test (Phase 1) stays pinned for the disabled path. |
| 3 | Phase 4A outcomes daemon thread + Phase 1 writer thread both write to `pax-bus.db` (`trade_outcomes` + everything else); SQLite write contention | Low | Low | WAL mode handles concurrent writers. Outcomes daemon uses a SEPARATE table (`trade_outcomes`); zero write-row contention. Connection-per-batch keeps lock scope tight. Daemon uses 15-minute lag, so it never races the live writer. |
| 4 | `outcomes.enabled=true` runs the daemon in production and silently consumes CPU / fills `trade_outcomes` | Low | Low | Default false. Daemon runs as a daemon thread (dies with the process). Per-tick CPU is negligible (one SELECT every 15 min). |
| 5 | `pax_bus_replay.py` rebuild SHAs don't match `ai_turns.digest_sha256` due to digest-builder drift between Phase 1 capture and Phase 4A replay | Medium | Medium | Phase 4A replay uses the EXACT same digest-builder code path as Phase 3A. If the SHAs diverge for an old row, it's a digest-builder regression — surface it as a test failure, never a silent "warn and continue". |
| 6 | Phase 4A introduces a new `outcomes.enabled` config key — drift risk if not documented | Low | Low | Plan adds exactly two new config keys: `chat.use_feature_bus_digest` and `outcomes.enabled`. Both default false. Both are explicit in the DEFAULTS dict + a test that pins them. |
| 7 | Phase 4A writes to `pax-bus.db` from the OUTCOMES daemon (a writer), violating the "Phase 2 helpers are read-only" expectation | Low | Medium | Phase 4A explicitly introduces a NEW writer scope for outcomes only. The Phase 2 read-only helpers (`recent_events`, `summary_today`) stay read-only. The outcomes daemon writes ONLY to `trade_outcomes`; documented in the helper docstring. |
| 8 | `pax_bus_replay.py` name collides with the existing `mcp-server/bookmap_mcp/pax_replay.py` (CSV-era) | Already mitigated | Low | The Phase-1 spec already chose `pax_bus_replay.py` naming to avoid this. Existing `pax_replay.py` stays untouched. |
| 9 | DB growth — `snapshot_features` writes 1 row/sec, ~3 KB/row -> ~250 MB/day. Phase 4A replay/EOD reads this; large files slow tests | Medium | Low | Retention pruning is explicitly deferred unless growth blocks Phase 4A development. If it does, add `tools/feature_bus_prune.py` as a separate task in this plan (currently absent). |
| 10 | Phase 3A `bus_digest_sha` log is added to the SSE `done` payload, breaking the golden-bytes regression test from Phase 1 | High if naive | High | Bus-digest SHAs are logged to stderr only, NEVER added to any SSE event. The Phase 1 `test_chat_path_unchanged_when_bus_disabled` test must still pass byte-identically. |
| 11 | Phase 4A outcomes daemon competes with the live writer for the advisory lock (Phase 1 used `_try_acquire_lock` with mode `a+`) | Medium | Medium | Outcomes daemon does NOT take the writer lock. It uses a SEPARATE connection in WAL mode for its `trade_outcomes` writes. WAL allows multiple writers via serialization, and `trade_outcomes` has zero overlap with any other table. |
| 12 | "Use bus digest as live input when flag is true" path is never exercised by tests until an operator flips the flag — risk of latent bug | Medium | Medium | Add integration test that runs `chat.handle_chat_stream` with `feature_bus.enabled=true AND chat.use_feature_bus_digest=true` + a controlled snapshot, asserts the SSE `done` event still arrives AND the user_message passed to `claude_stream.stream_chat` equals the deterministic `bus_digest.render_user_message(...)` output (NOT byte-equal to the legacy digest — they have different block layouts by design). |

---

## Phase 3A scope (shadow bus digest only)

### What changes

- New module `pax-ai/pax_ai/bus_digest.py` — pure renderer. Reads from:
  - the in-memory snapshot (already in `meta["_snapshot_for_capture"]` from Phase 1's chat.py hook), and
  - the bus DB (`recent_events("trigger_events", ...)` for the `[RECENT_EVENTS]` block + a new `ai_turns` query for the `[SESSION_MEMORY]` block).
- New helper `feature_bus.recent_ai_turns(limit, alias)` exposed for Phase 3A's SESSION_MEMORY block.
- New config key `chat.use_feature_bus_digest`, default `false`. The shadow render is gated by `feature_bus.enabled` (NOT this flag). Behavior matrix:
  | `feature_bus.enabled` | `chat.use_feature_bus_digest` | Shadow render? | Stderr `[bus-digest]` log? | Claude input |
  |---|---|---|---|---|
  | False | * | No | No | Legacy `full_msg` (bit-identical to Phase 1+2) |
  | True  | False | Yes | Yes | Legacy `full_msg` |
  | True  | True  | Yes | Yes | Bus digest (`bus_digest.render_user_message(...)`) |
- One-line config-driven branch in `chat.py::handle_chat_stream` that swaps the digest only when both flags are true.
- Stderr log line written only when shadow runs: `[bus-digest] live_sha=... bus_sha=... diff_bytes=N`.

### What does NOT change

- Claude CLI args / flags
- Prompts / router / skill bodies
- Trading paths (still off-limits)
- Phase 1 writer thread / record_* / writer-path code
- Phase 2 endpoints / UI

## Phase 4A scope (outcomes + replay foundation)

### What changes

- New module `pax-ai/pax_ai/outcomes.py` — daemon thread that writes `trade_outcomes` rows. Default disabled via `outcomes.enabled=false`.
- Startup wiring in `pax-ai/pax_ai/__main__.py` to call `outcomes.start()` after `feature_bus.start()` and before `journal.init()`. Guarded by the new flag. (Same idempotent-no-op contract as `feature_bus.start()` when disabled.)
- New CLI `mcp-server/bookmap_mcp/pax_bus_replay.py` — batch tool. Reads `pax-bus.db` + snapshot/digest blob stores, rebuilds the digest for each historical `ai_turn` row via the Phase 3A `bus_digest` builder, asserts SHA equality with `ai_turns.digest_sha256`, produces a markdown report.
- New CLI `mcp-server/bookmap_mcp/pax_bus_eod.py` — End-of-day summary. ONLY if `pax_bus_replay.py` ships clean.

### What does NOT change

- Live chat path (digest builder lives in Phase 3A; Phase 4A's replay only RE-RUNS that builder offline)
- Phase 1 writer thread (separate writer-table scope for `trade_outcomes`)
- Phase 2 endpoints
- The existing CSV-era `pax_replay.py` / `pax_outcomes.py` stay untouched (orthogonal pipelines)

---

## Off-limits list (Phase 3A + 4A consolidated)

| Path / scope | Reason |
|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Snapshot composer — bridge-side owner |
| `mcp-server/bookmap_mcp/or_session.py` | OR anchor invariant |
| `mcp-server/bookmap_mcp/pax_weights.json` | Conviction weights frozen |
| `mcp-server/bookmap_mcp/pax_replay.py` | Existing CSV-era replay (Phase 4A ships `pax_bus_replay.py` next to it, untouched) |
| `mcp-server/bookmap_mcp/pax_outcomes.py` | Existing CSV-era outcomes (Phase 4A ships `pax-ai/pax_ai/outcomes.py`, untouched) |
| `pax-ai/pax_ai/prompts.py` | System prompt frozen |
| `pax-ai/pax_ai/claude_stream.py` | `--tools "" --max-turns 1` locked |
| `pax-ai/pax_ai/edge_calculus.py` | Math layer frozen |
| `pax-ai/pax_ai/journal.py` | Chat journal stays as-is |
| `pax-ai/pax_ai/triggers.py` | Phase 1 `_emit_edge` hook stays put |
| `pax-ai/pax_ai/poller.py` | Snapshot polling owned by Phase 1 |
| `pax-ai/pax_ai/static/index.html` | Phase 2 UI section locked; no Phase 3A/4A UI work |
| `indicators/**` | Bookmap addons orthogonal |
| `skills/**` | Skill bodies frozen — no skill edits in this plan |
| `pax-ai/pax_ai/feature_bus.py::start` | Phase 1 writer-thread lifecycle |
| `pax-ai/pax_ai/feature_bus.py::stop` | Same |
| `pax-ai/pax_ai/feature_bus.py::record_trigger` | Phase 1 producer API |
| `pax-ai/pax_ai/feature_bus.py::record_ai_turn` | Same |
| `pax-ai/pax_ai/feature_bus.py::_accepting_events` | Phase 1 gate |
| `pax-ai/pax_ai/feature_bus.py::_writer_loop` | Phase 1 writer thread |
| `pax-ai/pax_ai/feature_bus.py::_drain_to_db` | Phase 1 batch writer |
| `pax-ai/pax_ai/feature_bus.py::_detect_snapshot_deltas` | Phase 1 delta detector |
| `pax-ai/pax_ai/feature_bus.py::_insert_*` | Phase 1 insert helpers |
| `pax-ai/pax_ai/feature_bus.py::_RECENT_PROJECTION` | Phase 2 allowlist — Phase 3A only adds a `recent_ai_turns` helper; the projection dict itself is unchanged |

**Allowed in Phase 3A:**
- `pax-ai/pax_ai/chat.py` (one tightly-scoped block to compute + log shadow digest; flag-gated swap of `full_msg`)
- `pax-ai/pax_ai/config.py` (one new key `chat.use_feature_bus_digest=false`)
- `pax-ai/pax_ai/bus_digest.py` (NEW)
- `pax-ai/pax_ai/feature_bus.py` (APPEND-ONLY new helper `recent_ai_turns`)
- `pax-ai/tests/test_bus_digest.py` (NEW)
- `pax-ai/tests/test_chat_handler.py` (extend with shadow-digest tests)

**Allowed in Phase 4A:**
- `pax-ai/pax_ai/outcomes.py` (NEW)
- `pax-ai/pax_ai/__main__.py` (one guarded `outcomes.start()` call)
- `pax-ai/pax_ai/config.py` (one new key `outcomes.enabled=false`)
- `mcp-server/bookmap_mcp/pax_bus_replay.py` (NEW)
- `mcp-server/bookmap_mcp/pax_bus_eod.py` (NEW; deferred behind stability gate)
- `pax-ai/tests/test_outcomes.py` (NEW)
- `pax-ai/tests/test_pax_bus_replay.py` (NEW)

---

## New config keys (exactly two)

Both default `False`. Both are pinned by tests.

```python
# In pax-ai/pax_ai/config.py DEFAULTS:
"chat": {
    "use_feature_bus_digest": False,   # Phase 3A: shadow-digest by default
},
"outcomes": {
    "enabled": False,                  # Phase 4A: daemon disabled by default
},
```

No other config keys are introduced.

---

## File structure

### Phase 3A new files

| Path | Purpose | LOC est. |
|---|---|---|
| `pax-ai/pax_ai/bus_digest.py` | Pure renderer. Takes (snapshot, alias, ts_ms, bus_db_path) -> str. Same block order as `chat._digest_lines`: STATE / ANCHOR / GATES / LEVELS / MICROSTRUCTURE / RECENT_EVENTS / POSITION / SESSION_MEMORY / USER. RECENT_EVENTS pulled from `feature_bus.recent_events("trigger_events", limit=5)`. SESSION_MEMORY pulled from `feature_bus.recent_ai_turns(limit=3, alias=...)`. | ~250 |
| `pax-ai/tests/test_bus_digest.py` | Unit tests: each block renders correctly, RECENT_EVENTS reads from bus DB, SESSION_MEMORY reads from ai_turns, render is DETERMINISTIC (same inputs -> same output bytes), every required block marker is present, router_hint is included (not silently dropped), bus digest excludes outcomes column (Phase 4A may populate it later). Tests DO NOT compare bus digest to legacy digest — they have different block layouts by design. | ~300 |

### Phase 3A modified files

| Path | Change |
|---|---|
| `pax-ai/pax_ai/config.py` | Append `"chat": {"use_feature_bus_digest": False}` to DEFAULTS |
| `pax-ai/pax_ai/feature_bus.py` | APPEND-ONLY new helper `recent_ai_turns(limit=3, alias=None)` (similar to `recent_events` but projects `(ts_ms, snapshot_alias, model, exit_code, total_cost_usd, user_text_raw, snapshot_sha256, digest_sha256)`). NO writer-path edits. |
| `pax-ai/pax_ai/chat.py` | Two-line block inserted after `full_msg, meta = build_user_message(user_text)` to compute shadow `bus_full_msg` + shadow SHA + log stderr line. ONE-LINE flag-driven swap right before `claude_stream.stream_chat(user_message=full_msg, ...)`. |
| `pax-ai/tests/test_chat_handler.py` | Extend with: `test_chat_sse_bytes_identical_when_feature_bus_disabled` (Phase 1 invariant restated), `test_no_shadow_digest_log_when_feature_bus_disabled`, `test_shadow_digest_logs_when_feature_bus_enabled` (stderr `[bus-digest]` line appears), `test_flag_false_keeps_legacy_full_msg` (Claude receives legacy digest), `test_flag_true_uses_bus_full_msg_only_when_feature_bus_enabled` (both gates required; bus enabled + flag true -> Claude receives bus digest; bus disabled + flag true -> still legacy). Phase 1 `test_chat_path_unchanged_when_bus_disabled` and `test_aiturn_uses_snapshot_captured_at_prompt_build_not_post_done` stay pinned and green. |

### Phase 4A new files

| Path | Purpose | LOC est. |
|---|---|---|
| `pax-ai/pax_ai/outcomes.py` | Daemon thread. Wakes every 15 min, queries `ai_turns` for rows older than 15 min that don't yet have a `trade_outcomes` row, reads `snapshot_features` at T+60/180/300/900 for each (best-effort; missing snapshots tolerated), computes realized_R against the captured snapshot mid, writes `trade_outcomes` rows. `start()` / `stop()` / `status()` mirror feature_bus's lifecycle. Default disabled. | ~280 |
| `mcp-server/bookmap_mcp/pax_bus_replay.py` | argparse CLI: `python -m bookmap_mcp.pax_bus_replay --date YYYY-MM-DD [--alias ALIAS]`. Streams `ai_turns` for the day, loads the matching snapshot blob, rebuilds digest via `pax_ai.bus_digest.render()`, asserts SHA match. Writes `reports/bus-replay-YYYY-MM-DD.md`. | ~200 |
| `mcp-server/bookmap_mcp/pax_bus_eod.py` | argparse CLI: per-day rollup. ONLY after pax_bus_replay.py is stable (Phase 4A.2 gate). | ~180 |
| `pax-ai/tests/test_outcomes.py` | Unit + integration: daemon disabled = no thread + no writes, daemon enabled writes outcome rows at correct T+offsets, tolerates missing snapshots, never raises into the parent process, status() reports correctly. | ~250 |
| `pax-ai/tests/test_pax_bus_replay.py` | Unit: seed a synthetic `ai_turns` row + matching snapshot blob, run replay, assert SHA match. Negative path: seed a row + tampered blob, assert replay reports a mismatch (non-zero exit code, mismatched count in report). | ~180 |

### Phase 4A modified files

| Path | Change |
|---|---|
| `pax-ai/pax_ai/config.py` | Append `"outcomes": {"enabled": False}` to DEFAULTS |
| `pax-ai/pax_ai/__main__.py` | One-line guarded `outcomes.start()` call after `feature_bus.start()` and before `journal.init()` |

---

## Task batches

### Phase 3A — batch 3A-1: read-only helper `recent_ai_turns`

**Files:** `pax-ai/pax_ai/feature_bus.py` (append), `pax-ai/tests/test_feature_bus_summary.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `pax-ai/tests/test_feature_bus_summary.py`:

```python
# -- recent_ai_turns contract ------------------------------------------------

def _seed_ai_turn(db_path, alias="NQM6", n=1):
    import hashlib
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(n):
            conn.execute("""
                INSERT INTO ai_turns
                  (schema_version, ts_ms, chat_run_id, deep, model,
                   user_text_raw, user_text_normalized, pax_text,
                   snapshot_alias, snapshot_sha256, digest_sha256,
                   total_cost_usd, input_tokens, output_tokens, exit_code, aborted)
                VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                        ?, ?, 'LONG PROSE OMITTED',
                        ?, ?, ?, 0.01, 10, 50, 0, 0)
            """, (1_000_000_000_000 + i,
                  f"ping {i}", f"ping {i}",
                  alias,
                  hashlib.sha256(f"s{i}".encode()).hexdigest(),
                  hashlib.sha256(f"d{i}".encode()).hexdigest()))


def test_recent_ai_turns_disabled_returns_empty(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    assert feature_bus.recent_ai_turns() == []


def test_recent_ai_turns_db_missing_returns_empty(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    assert feature_bus.recent_ai_turns() == []


def test_recent_ai_turns_returns_newest_first(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=5)
    rows = feature_bus.recent_ai_turns(limit=5)
    assert len(rows) == 5
    assert [r["ts_ms"] for r in rows] == sorted([r["ts_ms"] for r in rows], reverse=True)


def test_recent_ai_turns_alias_filter(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, alias="NQM6", n=3)
    _seed_ai_turn(db, alias="ESM6", n=2)
    nqm = feature_bus.recent_ai_turns(limit=10, alias="NQM6")
    assert all(r["snapshot_alias"] == "NQM6" for r in nqm)
    assert len(nqm) == 3


def test_recent_ai_turns_clamps_limit(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=20)
    assert len(feature_bus.recent_ai_turns(limit=0)) == 1
    assert len(feature_bus.recent_ai_turns(limit=9999)) == 20  # clamp to 50, only 20 seeded


def test_recent_ai_turns_omits_blob_text_columns(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=1)
    [row] = feature_bus.recent_ai_turns(limit=1)
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row
```

- [ ] **Step 2: Run & confirm 6 FAIL**

`cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_feature_bus_summary.py -v`

Expected: 6 new tests FAIL with `AttributeError: ...recent_ai_turns`.

- [ ] **Step 3: APPEND helper to `pax-ai/pax_ai/feature_bus.py`**

After the existing `summary_today` function (Phase 2):

```python
_AI_TURNS_PHASE3_PROJECTION = (
    "id", "ts_ms", "model", "exit_code", "total_cost_usd",
    "user_text_raw", "snapshot_alias",
    "snapshot_sha256", "digest_sha256",
)


def recent_ai_turns(limit: int = 3,
                     alias: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read-only SELECT of most-recent rows from ai_turns. Used by Phase 3A's
    bus_digest SESSION_MEMORY block. Same safety contract as recent_events()."""
    if not config.get("feature_bus.enabled", False):
        return []
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = 3
    n = max(1, min(50, n))
    cols = _AI_TURNS_PHASE3_PROJECTION
    sql = (f"SELECT {','.join(cols)} FROM ai_turns "
           + ("WHERE snapshot_alias=? " if alias is not None else "")
           + "ORDER BY ts_ms DESC LIMIT ?")
    params = ((alias, n) if alias is not None else (n,))
    db_path = Path(config.get("feature_bus.db_path"))
    try:
        with _open_db_readonly(db_path) as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except (FileNotFoundError, sqlite3.OperationalError):
        return []
```

- [ ] **Step 4: Confirm 6 PASS + full suite green**

`python -m pytest tests/test_feature_bus_summary.py -v` -> 23 passed (17 Phase 2 + 6 new).
`python -m pytest -v` -> 386 passed (380 + 6 new).

### Phase 3A — batch 3A-2: `bus_digest.py` renderer (block-by-block TDD)

**Files:** new `pax-ai/pax_ai/bus_digest.py`, new `pax-ai/tests/test_bus_digest.py`

**Strategy:** Each block (STATE, ANCHOR, GATES, LEVELS, MICROSTRUCTURE, RECENT_EVENTS, POSITION, SESSION_MEMORY, USER) gets its own task with its own failing test, its own implementation function, and its own assertion. The final task wires them via `render(snapshot, user_text, alias, ts_ms) -> str`.

- [ ] **Step 1: Write the failing test for block ordering**

```python
def test_render_block_order_is_stable():
    """Block markers must appear in the documented order so the resulting
    string is hash-stable across snapshots that differ only in inner content."""
    from pax_ai.bus_digest import render
    snap = {"alias": "NQM6", "book": {"mid": 23450.5, "spread": 0.25}}
    out = render(snap, "ping", alias="NQM6", ts_ms=1_000_000_000_000)
    markers = ["[STATE]", "[ANCHOR]", "[GATES]", "[LEVELS]",
                "[MICROSTRUCTURE]", "[RECENT_EVENTS]", "[POSITION]",
                "[SESSION_MEMORY]", "[USER]"]
    positions = [out.find(m) for m in markers]
    assert all(p >= 0 for p in positions), positions
    assert positions == sorted(positions), \
        f"blocks out of order: {list(zip(markers, positions))}"
```

- [ ] **Step 2 through Step N: One sub-task per block.** Each defines the rendering function for that block, the unit test (input snapshot subset -> expected output lines), and a regression test that a missing field collapses to "n/a" (or equivalent quiet text).

Recommended block-by-block schedule (each ~4 steps):
- `_state_block(snap)` — alias, mid, spread, ts-age
- `_anchor_block(snap)` — anchorHHMM, anchorTz, anchorMode (INFORMATIONAL_ONLY when not LIVE)
- `_gates_block(snap)` — session code, news.blocked, vwap_or, stretch
- `_levels_block(snap)` — top-3 by |distance|
- `_microstructure_block(snap)` — tape_flow delta, momentum.flag, vwap sigma_z, vp va_state, conviction
- `_recent_events_block(alias)` — reads `feature_bus.recent_events("trigger_events", limit=3, alias=alias)` + formats with HH:MM:SS prefix
- `_position_block(snap)` — size/entry/pnl/working_count (or "position: FLAT")
- `_session_memory_block(alias)` — reads `feature_bus.recent_ai_turns(limit=3, alias=alias)` + formats; in Phase 3A, outcomes column is absent because `trade_outcomes` won't be populated yet
- `_user_block(user_text)` — verbatim

- [ ] **Final Step: Wire `render()`**

```python
def render(snap: Dict[str, Any], user_text: str,
            alias: Optional[str] = None,
            ts_ms: Optional[int] = None) -> str:
    alias = alias or str(snap.get("alias") or "")
    return "\n\n".join([
        _state_block(snap),
        _anchor_block(snap),
        _gates_block(snap),
        _levels_block(snap),
        _microstructure_block(snap),
        _recent_events_block(alias),
        _position_block(snap),
        _session_memory_block(alias),
        _user_block(user_text),
    ])
```

- [ ] **Final test: deterministic rendering on controlled snapshot**

Construct a fixture snapshot, call `bus_digest.render_user_message(...)`, save the SHA. Re-call with the same fixture: SHA must match (deterministic). Re-call with a perturbed alias: SHA must differ. Tests DO NOT compare against the legacy `chat._digest_lines` output — bus digest uses new block layout by design (the legacy digest is `router_hint + SNAPSHOT DIGEST + USER`; bus digest is `[STATE]/[ANCHOR]/[GATES]/[LEVELS]/[MICROSTRUCTURE]/[RECENT_EVENTS]/[POSITION]/[SESSION_MEMORY]/[USER]`).

`python -m pytest tests/test_bus_digest.py -v` should pass all ~25 tests.

### Phase 3A — batch 3A-3: chat.py shadow-log + flag-driven swap

**Files:** `pax-ai/pax_ai/chat.py`, `pax-ai/pax_ai/config.py`, `pax-ai/tests/test_chat_handler.py` (extend)

- [ ] **Step 1: Add `chat.use_feature_bus_digest=False` to config DEFAULTS** + pin in test_config.py.

- [ ] **Step 2: Write failing tests in test_chat_handler.py**

```python
def test_chat_shadow_logs_bus_digest_sha_to_stderr(monkeypatch, capsys, tmp_path):
    """Phase 3A: every chat turn writes [bus-digest] stderr log line
    with live_sha + bus_sha (irrespective of flag value)."""
    # ... (setup feature_bus enabled, monkeypatch claude_stream, build_user_message returns
    # known full_msg; run handle_chat_stream; capsys.readouterr().err contains "[bus-digest]")


def test_chat_uses_live_digest_when_flag_false(monkeypatch, tmp_path):
    """Phase 3A default: live full_msg unchanged."""
    # Capture user_message arg passed to claude_stream.stream_chat. Assert it
    # matches the build_user_message return value, NOT the bus_digest render.


def test_chat_uses_bus_digest_when_flag_true(monkeypatch, tmp_path):
    """Flag opt-in: user_message arg passed to claude_stream.stream_chat
    matches bus_digest.render() output."""


def test_chat_sse_bytes_identical_when_flag_false(monkeypatch):
    """Regression: Phase 1's golden-bytes SSE invariant stays green with the
    shadow-log on by default. The byte stream MUST equal the Phase 1 fixture."""
    # ... mirrors test_chat_path_unchanged_when_bus_disabled
```

- [ ] **Step 3: Implement gated shadow + flag-gated swap in chat.py**

After `full_msg, meta = build_user_message(user_text)`:

```python
        # Phase 3A shadow bus digest: gated on feature_bus.enabled so the
        # disabled path stays bit-identical to Phase 1+2 (no shadow render,
        # no [bus-digest] stderr log, no behavior change). Live input swap
        # requires BOTH gates: feature_bus.enabled=true AND
        # chat.use_feature_bus_digest=true.
        bus_full_msg = None
        if config.get("feature_bus.enabled", False):
            try:
                from . import bus_digest as _bus_dig
                snap_for_bus = meta.get("_snapshot_for_capture") or {}
                bus_full_msg = _bus_dig.render_user_message(
                    snap=snap_for_bus,
                    user_text=user_text,
                    router_hint=meta.get("router_hint") or "",
                    alias=snap_for_bus.get("alias"),
                    ts_ms=meta.get("_snapshot_ts_ms_capture"),
                )
                live_sha = hashlib.sha256(full_msg.encode("utf-8")).hexdigest()
                bus_sha  = hashlib.sha256(bus_full_msg.encode("utf-8")).hexdigest()
                diff_bytes = abs(len(bus_full_msg) - len(full_msg))
                sys.stderr.write(
                    f"[bus-digest] live_sha={live_sha[:12]} "
                    f"bus_sha={bus_sha[:12]} diff_bytes={diff_bytes}\n")
            except Exception as exc:
                sys.stderr.write(f"[bus-digest] shadow render failed: {exc}\n")

        if (config.get("feature_bus.enabled", False)
                and config.get("chat.use_feature_bus_digest", False)
                and bus_full_msg is not None):
            full_msg = bus_full_msg
```

Note: `build_user_message` returns `meta["router_hint"]` (already present in Phase 1+2); the bus digest must include it so routing context is not silently dropped on flag flip. The Phase 1 AiTurnRecord assembly later in `handle_chat_stream` already captures `full_msg` (whichever digest Claude actually received) into `digest_text` / `digest_sha256`, so the replay path stays correct under both legacy-and-swapped scenarios.

Then `claude_stream.stream_chat(user_message=full_msg, ...)` is unchanged. The `meta`-stored snapshot keeps the existing `_snapshot_for_capture` so the Phase 1 AiTurnRecord assembly continues to use the live snapshot.

- [ ] **Step 4: Run + Phase 1 invariant regression**

```
python -m pytest -v
python -m pytest tests/test_chat_handler.py::test_chat_path_unchanged_when_bus_disabled -v
python -m pytest tests/test_chat_handler.py::test_aiturn_uses_snapshot_captured_at_prompt_build_not_post_done -v
```

All must pass. Full suite ~395.

### Phase 4A — batch 4A-1: outcomes daemon

**Files:** new `pax-ai/pax_ai/outcomes.py`, new `pax-ai/tests/test_outcomes.py`, modify `pax-ai/pax_ai/config.py`, modify `pax-ai/pax_ai/__main__.py`

- [ ] **Step 1: Config key** `outcomes.enabled=False` in DEFAULTS + test pinning.

- [ ] **Step 2: Failing tests**

Tests cover: disabled = no writes, enabled writes rows at correct T+offsets, tolerates missing snapshots (`SELECT mid FROM snapshot_features WHERE ts_ms BETWEEN ? AND ?` returns 0 rows -> outcome row with `mid_at_t60s=NULL`), status reports correctly, daemon thread joins cleanly on `stop()`, never raises into parent.

- [ ] **Step 3: Implement outcomes.py**

Skeleton:

```python
"""Phase 4A outcomes daemon. Default disabled.

Wakes every 15 minutes. For each ai_turn row older than 15 minutes that
doesn't yet have a trade_outcomes row, computes T+60/180/300/900 mids
by reading snapshot_features within +/- 5 second windows. Writes one
trade_outcomes row per ai_turn. Tolerates missing snapshot rows.
"""
# start(), stop(), status() (same shape as feature_bus)
# _outcomes_loop() main daemon body
# _label_one_ai_turn(conn, ai_turn_row) computes and inserts trade_outcomes
# _mid_at_or_near(conn, alias, target_ms) returns mid or None
```

- [ ] **Step 4: Wire into `__main__.py`** (after `feature_bus.start()`, before `journal.init()`):

```python
    from . import outcomes
    outcomes.start()        # idempotent no-op when outcomes.enabled=False
```

- [ ] **Step 5: Full suite green** (~415 tests).

### Phase 4A — batch 4A-2: `pax_bus_replay.py` CLI

**Files:** new `mcp-server/bookmap_mcp/pax_bus_replay.py`, new `pax-ai/tests/test_pax_bus_replay.py` (OR an appropriate location after inspecting repo import patterns - see Step 0)

- [ ] **Step 0: Verify import-path access before writing any code**

`pax_bus_replay.py` lives in `mcp-server/bookmap_mcp/` and needs to `import pax_ai.bus_digest`. That cross-package import is NOT trivially supported because `mcp-server/` is its own Python package with its own pyproject.toml.

Before implementation, inspect:
- Does `mcp-server/pyproject.toml` declare any dependency on `pax-ai`?
- Are existing `mcp-server/bookmap_mcp/pax_*.py` tools (e.g. `pax_replay.py`, `pax_outcomes.py`, `pax_daemon.py`) importing from `pax_ai`?
- What `sys.path` / install pattern does the operator use to invoke them?
- Do any tests currently exist that exercise cross-package imports?

If `pax_ai` is not import-reachable from `mcp-server` without operator shell setup (PYTHONPATH gymnastics, `pip install -e ../pax-ai`, etc.), the test for `pax_bus_replay.py` MUST either:
(a) live in `pax-ai/tests/` and shell out to the CLI as a subprocess with `PYTHONPATH` set to include both packages, OR
(b) live in `mcp-server/tests/` (create the dir if it doesn't exist) with the same PYTHONPATH setup in conftest.py, OR
(c) STOP and report the blocker — cross-package imports are infrastructure work that may merit a separate task.

Do not write `from pax_ai import bus_digest` in `pax_bus_replay.py` until you have proven the import works in a test context.

- [ ] **Step 1: Failing tests**

Tests cover:
- Synthetic ai_turn + matching snapshot blob -> rebuilt digest SHA equals stored sha256.
- Tampered blob -> mismatch reported (non-zero exit + message).
- Missing blob -> "blob missing" line in report, NOT a crash.
- `--date YYYY-MM-DD` filters correctly (UTC).

- [ ] **Step 2: Implement CLI**

```python
"""Phase 4A replay foundation. Reads pax-bus.db, rebuilds historical digests
from snapshot blobs via pax_ai.bus_digest.render(), verifies SHA equality.
Writes reports/bus-replay-YYYY-MM-DD.md and prints a one-line summary.
"""
import argparse, sqlite3, hashlib, sys
from pathlib import Path
from pax_ai import bus_digest, feature_bus


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pax_bus_replay")
    ap.add_argument("--date", required=True, help="UTC day YYYY-MM-DD")
    ap.add_argument("--alias", default=None)
    args = ap.parse_args(argv)
    # ... iterate ai_turns within the UTC day, load snapshot blob,
    # call bus_digest.render(), assert digest_sha256 match,
    # write markdown report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Full suite green** (~425).

### Phase 4A — batch 4A-3: `pax_bus_eod.py` (DEFERRED)

**This batch is NOT in scope for the current execution.** EOD reporting requires:
1. Stable `pax_bus_replay.py` with zero SHA mismatches across 2+ live operator-runs against captured bus data, AND
2. `trade_outcomes` rows populated by the outcomes daemon (meaning operator has run with `outcomes.enabled=true` for at least one trading session).

Neither prerequisite exists at plan time. Defer EOD to a follow-on plan once both gates have been demonstrated empirically.

---

## Parallel work

| Batch | Parallel-safe? | Why |
|---|---|---|
| 3A-1 (recent_ai_turns helper) | Foundational | Required by 3A-2 and 3A-3 |
| 3A-2 (bus_digest.py renderer) | Independent of 4A-1, 4A-2 | No shared files. Could run alongside 4A-1 |
| 3A-3 (chat.py shadow + swap) | Sequential AFTER 3A-2 | Depends on bus_digest.render() existing |
| 4A-1 (outcomes daemon) | Independent of 3A-2 / 3A-3 | Touches separate files (`outcomes.py`, `__main__.py`); no overlap with chat.py |
| 4A-2 (pax_bus_replay CLI) | Sequential AFTER 3A-2 | Depends on bus_digest.render() |
| 4A-3 (pax_bus_eod CLI) | Sequential AFTER 4A-2 + outcomes data | Depends on `trade_outcomes` rows existing |

**Parallel-execution plan if accelerating with parallel agents:**

- Wave 1 (parallel): batch 3A-1 + batch 4A-1's tests/scaffolding
- Wave 2 (sequential): 3A-2 (bus_digest)
- Wave 3 (parallel): 3A-3 (chat.py shadow) + 4A-2 (pax_bus_replay)
- Wave 4 (gated): 4A-3 (eod)

The plan executor should still serialize within each wave to avoid merge conflicts on `feature_bus.py` and `config.py`.

---

## Rollback per flag

| Phase | Flag | Default | Rollback action |
|---|---|---|---|
| 3A shadow log | always on (stderr only) | n/a | Remove the shadow-log block in chat.py via `git revert` of batch 3A-3 |
| 3A live swap | `chat.use_feature_bus_digest` | `false` | Set flag to `false` in `pax_ai_config.json` + restart Pax AI. Live digest path returns to Phase 1+2 behavior. Restart REQUIRED (no runtime hot-disable in Phase 3A). |
| 4A outcomes daemon | `outcomes.enabled` | `false` | Set flag to `false` in `pax_ai_config.json` + restart Pax AI. Daemon thread exits via `_STOP_EVT`; in-flight outcome rows are durable. Restart REQUIRED. |
| 4A replay CLI | n/a | n/a | Not a runtime feature; just stop running the CLI. |
| 4A EOD CLI | n/a | n/a | Same. |
| Full Phase 3A revert | n/a | n/a | `git revert <phase-3A-commits>` removes the new module, the chat.py hook, the config key, and the tests. Bus DB and Phase 1+2 stay intact. |
| Full Phase 4A revert | n/a | n/a | `git revert <phase-4A-commits>` removes outcomes.py, the __main__ wiring, the config key, the replay/eod CLIs, and tests. `trade_outcomes` rows on disk remain (intentional). |

---

## Final verification commands

Run from `pax-ai/` after each batch:

```bash
python -m pytest -v
python -m compileall -q pax_ai
```

And from repo root:

```bash
# Off-limits diff guard (Phase 3A + 4A scope).
git diff --name-only HEAD~3..HEAD | grep -E "(mcp-server/bookmap_mcp/dashboard\.py|or_session\.py|pax_weights\.json|pax_replay\.py|pax_outcomes\.py|prompts\.py|claude_stream\.py|edge_calculus\.py|journal\.py|triggers\.py|poller\.py|static/index\.html|indicators/|skills/)" || echo "OK: zero off-limits"

# Phase 1 writer-path additive-only guard for feature_bus.py.
git diff HEAD~3..HEAD -- pax-ai/pax_ai/feature_bus.py | grep -E "^-.*def (start|stop|record_trigger|record_ai_turn|_accepting_events|_writer_loop|_drain_to_db|_detect_snapshot_deltas|_insert_)" || echo "OK: zero writer-path defs deleted"

# Phase 1 invariant regression (the 5 pinned tests).
cd pax-ai && python -m pytest -v \
  tests/test_chat_handler.py::test_chat_path_unchanged_when_bus_disabled \
  tests/test_chat_handler.py::test_aiturn_uses_snapshot_captured_at_prompt_build_not_post_done \
  tests/test_feature_bus_integration.py::test_whynow_byte_identical_with_and_without_bus \
  tests/test_feature_bus_triggers.py::test_compute_triggers_never_called_by_feature_bus \
  tests/test_feature_bus_ai_turn.py::test_record_methods_noop_when_unhealthy_or_not_running
```

All must pass.

---

## Out-of-scope (Phase 5+ parking lot)

Explicitly deferred per the brief:

- **Phase 5 tuning recommendations** — `pax_bus_tune.py` and any threshold-drift analysis remain unbuilt.
- **Auto-apply of anything** — no automated config / skill / prompt edits. All recommendations remain operator-driven.
- **Skill edits** — `skills/pax-or/SKILL.md` and `skills/hft_microstructure_quant_v1/SKILL.md` stay frozen.
- **News calendar ingestion** — `news-calendar.json` stays operator-curated.
- **Retention pruning** — `tools/feature_bus_prune.py` is NOT in this plan unless DB growth blocks Phase 4A testing. If growth becomes a problem during execution, add a new "Phase 4A.5 retention pruner" task BEFORE proceeding to 4A-3.

---

## Stop here

Plan complete and self-reviewed. No Phase 3/4 implementation has started. Awaiting your approval before invoking `superpowers:subagent-driven-development` for the next batch.
