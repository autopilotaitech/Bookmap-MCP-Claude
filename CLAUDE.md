# Bookmap MCP — Claude instructions

This file is loaded automatically by Claude Code at session start. Conventions
below override the default system prompt where they conflict.

## What this repo is

- Java Bookmap addon (under `addons/`) plus a Python MCP server
  (`mcp-server/bookmap_mcp/`) that exposes live order flow to Claude via skills
  like `momentum-scan`, `or-bias`, `risk-check`, `trade-decision`,
  `absorption-watch`, `vwap-or-gate`, `session-clock`, `news-blackout`.
- Production-grade trading systems work. Treat specs like an audit-friendly
  engineering doc: explicit definitions, anchored statistics, deterministic
  decision trees, pinned sign conventions in tests. No LLM math inside trading
  skills.
- Live-money runtime; bias toward small, auditable functions and one
  reversible commit per change.

## Workflow rules

### Backup before substantial refactors

Before any change that replaces an existing function, model, or config:

1. Create `_phase_backups/pre_<phase_name>_<YYYYMMDD_HHMMSS>/` at repo root.
2. Copy every file you will touch into the backup, preserving the relative
   path under `mcp-server/...`.
3. Write `BACKUP_MANIFEST.md` listing files and a PowerShell rollback snippet.

`_phase_backups/` is already in `.gitignore`. Don't try to commit it.

### ASCII unless the file already uses Unicode

Some files use Unicode (`dashboard.py` has sigma, arrows, box-drawing) — keep
their style. Files that are strictly ASCII (`pax_weights.json`, `README.md`,
JSON configs) must stay ASCII. Don't add em-dashes, smart quotes, sigma, or
arrows to an ASCII-only file.

### Single-author for tightly coupled work

When implementation, config, and tests must match each other exactly, do the
work yourself sequentially. Dispatch parallel agents only for genuinely
independent domains (different test files for unrelated subsystems, parallel
research across separate parts of the codebase). The
`dispatching-parallel-agents` skill describes the condition.

### Output style

- Be terse. Don't narrate your internal deliberation.
- End-of-turn summary: one or two sentences. What changed, what's next.
- Don't write multi-paragraph docstrings or comment blocks. Prefer named
  identifiers over comments. Only comment WHY when it's non-obvious.
- Never reference "the current task" or "this fix" in code comments.

## Running things

### Python tests

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest
```

Pyproject sets `testpaths: tests`. If `pytest` is missing:
`python -m pip install pytest --quiet`.

### Byte-compile check (fast syntax sanity)

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp
```

### Java tests + addon jar build

`java` is usually NOT on PATH. Bookmap ships a full Temurin JDK 17 at
`C:\Program Files\Bookmap\jre\` — point `JAVA_HOME` there. In PowerShell:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Bookmap\jre'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat test           # JUnit 5 tests
.\gradlew.bat build          # produces build/libs/bookmap-mcp-bridge-v<N>.jar
```

If `gradlew clean` fails with "Unable to delete ... bookmap-mcp-bridge-v<N>.jar",
Bookmap has the file open. Either close Bookmap, or skip `clean` — the jar
name is keyed to `version` in `build.gradle` so a new version produces a new
file alongside the old one.

### Version + build policy

Every commit that changes Java source bumps `version` in `build.gradle` AND
the `archiveFileName` to `bookmap-mcp-bridge-v<N>.jar`, then rebuilds. Reasons:

- Bookmap loads addons by filename. Same filename = stale cache risk.
- Version bump is a hard audit trail for which jar shipped which fix.
- Tested-and-built artifact lives at `build/libs/bookmap-mcp-bridge-v<N>.jar`.

Deploy by copying that jar into the addons folder Bookmap reads from and
restarting Bookmap. Confirm via the startup line:
`Bookmap MCP bridge listening on http://127.0.0.1:18888`.

### Hot-loaded config

`mcp-server/bookmap_mcp/pax_weights.json` reloads automatically on mtime
change. `_*`-prefixed keys are metadata comments stripped by
`_load_pax_weights()`. **The cache dict is mutated in place** (clear +
update) — never reassigned — so `signal_engine`'s re-export reference
stays valid across reloads. Restart the dashboard process only to flush
non-config caches.

### Paper-trading daemon (live data + sim)

Run-it-all launcher at the repo root:

```cmd
pax-start.bat                       (defaults: alias NQM6.CME@RITHMIC, port 18890)
pax-start.bat ESM6.CME@RITHMIC      (override alias)
pax-stop.bat                        (taskkill by window title, soft then /F)
```

Manual invocation:

```powershell
$env:BOOKMAP_ALLOW_TRADING = $null      # daemon refuses to start with =1
python -m bookmap_mcp.pax_daemon --source bookmap --alias 'NQM6.CME@RITHMIC' --poll-ms 500
# in another terminal:
python -m bookmap_mcp.overview_ui       # http://127.0.0.1:18890
```

First-time setup: `cd mcp-server && python -m pip install -e .` so the
system Python can `import bookmap_mcp` from anywhere. (The MCP server
itself uses the venv at `mcp-server/.venv/Scripts/python.exe`; this
install is for the daemon CLI.)

### Bridge port

The Java bridge port is configured in `~/.bookmap-mcp/bridge.properties`
(NOT in the repo). Default on this machine: **8765**. The CLAUDE.md
example port `18888` is wrong for this user's setup — always read the
actual file. The Python client (`config.py::BridgeConfig.load`) reads
`port=` from this file and constructs the URL.

### Aliases

Bookmap aliases include the broker route, e.g.
`NQM6.CME@RITHMIC`, not just `NQM6`. The daemon uses the full alias;
the launcher defaults to the Rithmic NQ alias.

## Code layout pointers

- `mcp-server/bookmap_mcp/dashboard.py` — live HUD snapshot composer
  (`fetch_snapshot`), `compute_or_levels` (per-magnet `composite` via
  `_level_composite`), `compute_tape_flow`, `compute_vwap_bias`,
  `compute_vp_bias`, `trade_decision`, `compute_session_conviction`,
  `_sync_magnet_levels`. ~3500 lines, audited heavily.
- `mcp-server/bookmap_mcp/signal_engine.py` — pure-Python facade that
  re-exports every signal helper from dashboard.py with no bridge
  dependency. Non-dashboard consumers (pax_daemon, replay tools,
  research notebooks) import from here.
- `mcp-server/bookmap_mcp/sim_engine.py` — local SQLite-backed paper
  broker. Bracket children gated by `armed_after_parent_fill` (Phase 0
  fix). EOD auto-flatten at 15:00 CT; pass `eod_close_hour_ct=None`
  to disable (tests do this so wall-clock can't trip the EOD path).
- `mcp-server/bookmap_mcp/pax_daemon.py` — background paper-trading
  daemon. Adapter -> signal_engine -> decide_and_act -> SimEngine ->
  journal. Refuses to start if `BOOKMAP_ALLOW_TRADING=1`.
- `mcp-server/bookmap_mcp/adapters/` — DataAdapter Protocol +
  CsvReplayAdapter / FileTailAdapter / BookmapLiveAdapter.
- `mcp-server/bookmap_mcp/journal.py` — SQLite journal (runs,
  snapshots, signals, orders, fills, positions, daily_stats,
  adapter_health, events, outcomes). WAL mode; daemon writes, UI reads.
  `_event_seq` is process-local and resets to 0 in `__init__`. When
  `begin_run` recovers an unended prior run (cross-process crash
  recovery), it MUST reseed `_event_seq` from
  `COALESCE(MAX(seq), 0)` for that run_id before writing the
  `RUN_CRASHED` event — otherwise the insert collides on `(run_id,
  seq)`, the implicit sqlite3 transaction rolls back the
  `UPDATE runs SET ended_ms=…`, and every subsequent daemon restart
  hits the same row → permanent deadlock. Pinned by
  `test_crash_recovery_across_process_restart` (close + reopen the
  Journal between runs; reusing one instance hides the bug).
- `mcp-server/bookmap_mcp/overview_ui.py` — read-only HTTP dashboard
  at `:18890` with 9 collapsible `<details>` sections (state persisted
  in localStorage).
- `_level_composite(side, price, mid, snap)` returns `{score, direction,
  confidence, drivers[], warnings[]}` per OR / extension magnet. 8 drivers,
  weights in `_LVL_W`. Direction = FOLLOW_LONG / FOLLOW_SHORT / FADE_LONG /
  FADE_SHORT / WAIT by row side. Slow-prior conviction comes from
  `_LAST_CONVICTION[alias]` cache to break the single-poll cycle.
- Session conviction is the v2 anchored multi-source engine. State per alias
  in `_CONVICTION_STATE`. Source helpers under the `_source_*` prefix return
  `{score, reliability, raw, reason}`. 17 sources after v7. See
  `mcp-server/README.md` for the cluster table.
- Legacy `_regime_to_signal`, `_slope_to_signal`, `_level_to_signal` are
  preserved and reused by v2 sources — don't refactor them away without
  updating the pinned helper-signal tests.

### Bridge HTTP endpoints

All endpoints are guarded by `BridgeAuth` (token from `bridge.properties`).
Read endpoints are GET; state-changing endpoints are POST.

- `GET  /ping`, `/instruments`
- `GET  /orderbook`, `/recent_trades`, `/recent_fills`, `/working_orders`,
       `/position`, `/balance`
- `GET  /vwap`, `/momentum`, `/volume_profile`, `/tape_buckets`,
       `/lt_liquidity`, `/book_dynamics`, `/pull_stack`,
       `/microstructure_events`
- `POST /magnet_levels` — configures stop-sweep magnet prices.
       Query params: `alias`, `levels` (comma-separated display prices, empty
       string clears). Returns `{alias, count, levels}`. Required for
       `STOP_SWEEP` events to fire — without magnets, `detectStopSweep`
       early-returns.
- `POST /place_limit_order`, `/cancel_order` — gated by
       `BOOKMAP_ALLOW_TRADING=1` in Bookmap's environment.
- `GET  /screenshot`

Trade `side` follows `TradeInfo.isBidAggressor`: `true` = bid was the
aggressor (buy aggressor / lifted offer) → `"side":"buy"`. `false` = sell
aggressor / hit bid → `"side":"sell"`. CVD = buy − sell.

### Dashboard → bridge state sync pattern

`_sync_magnet_levels` in `dashboard.py` is the template for any future
dashboard-driven bridge configuration call. Pattern:

1. Module-level cache keyed by `alias`, value = `(sorted_payload_tuple,
   last_post_monotonic_secs)`.
2. Lock-guarded read/write of the cache.
3. Short-circuit when the tuple is identical AND the timestamp is younger
   than a refresh TTL (`_MAGNET_REFRESH_SECS = 60.0`). TTL bounds the
   "Bookmap restart wiped state, dashboard cache thinks it's still set"
   failure mode.
4. Short-lived `BridgeClient(cfg, timeout_s=2.0)`. Cache updated ONLY on
   successful 2xx; failures log one stderr line and leave the cache empty
   so the next snapshot retries.
5. Outer try/except at the call site in `fetch_snapshot` guarantees a sync
   failure can never break snapshot composition.

### Trajectory modulation (V8 / V9 / V10)

Three conviction sources modulate their score+reliability using a trajectory
label. Same shape everywhere:

```text
nudge: ±0.10 max additive to score
       RISING_STRONG +0.10, RISING +0.05, FLAT 0, FALLING -0.05, FALLING_STRONG -0.10
divergence: when score sign disagrees with trajectory sign,
            reliability × 0.5 (strong) or × 0.75 (mild)
```

Where the trajectory comes from per source:

- V8 `_conviction_at_level` — reads `conviction.trajectory` (FlowRegime upstream).
- V9 `_source_level_reaction` — own poll-to-poll score delta via
  `_LAST_LEVEL_REACTION[alias]`, 60s stale window. **Source-specific
  trajectory; no upstream equivalent.**
- V10 `_source_bias_score` — reads `flow.biasTrajectory` (FlowRegime upstream).

**Don't add trajectory awareness to every source.** Direct-read static
sources (`orderbook`, `lt_liquidity`, `volume_profile`, `vwap_dislocation`,
`ib_context`) do not benefit — they're snapshots, not processes. And before
adding a Python-side delta tracker, check whether the upstream payload
already includes the classifier (FlowRegime emits `biasTrajectory`,
`regime`, `regimeConfidence`, z-scores). The V10 fix removed a duplicate
classifier; net -34 lines, no functionality change.

## Git etiquette

- Single-line commit subject in conventional style (e.g. `conviction: rebuild
  as anchored multi-source engine`). Body for context.
- Never commit `bridge.properties` (the bridge auth token) or files under
  `_phase_backups/`, `__pycache__/`, `.venv/`, `*.pyc`.
- Don't skip pre-commit hooks. If a hook fails, fix the underlying issue.

## What NOT to do

- Don't introduce live order-placement code paths in the daemon or any
  non-`server.py` module. The Pax agent + `pax_daemon` are paper-sim
  only; live order routing only exists in `server.py`'s two MCP tools
  behind the `confirm=True` + `BOOKMAP_ALLOW_TRADING=1` two-gate guard.
- Don't import `bookmap_place_limit_order` or `bookmap_cancel_order`
  outside of `server.py`. AST/grep tests in
  `tests/test_safety_boundaries.py` and `tests/test_bookmap_live.py`
  pin this contract.
- Don't add LLM-driven math to decision skills. Skills are deterministic
  decision trees over the snapshot. `pax_daemon` passes `use_claude=False`
  to `decide_and_act` so unattended runs don't spawn subprocesses.
- Don't refactor `dashboard.py` without backing up. It's the production hub.
- Don't reformat or "tidy" Unicode in files that already use it; the user
  diffs these manually.
- Don't reinvent upstream classifications in Python. If FlowRegime,
  MomentumSnapshot, or any Java handler already emits the derived field
  (trajectory, regime, z-score, sigma-band), READ it. Python-side caches
  belong to dashboard-process-local state only.
- Don't add trajectory modulation to direct-read static sources. The
  pattern fits slow/anchored signals (`conviction`, `bias_score`,
  `level_reaction`). Adding it to `orderbook` / `lt_liquidity` /
  `volume_profile` would be bloat — they're snapshots, not processes.
- Don't construct `SimEngine(...)` in tests without `eod_close_hour_ct=None`
  unless you specifically want to exercise the EOD auto-flatten path.
  At arbitrary wall-clock times (15:01 CT through midnight), the default
  EOD logic will cancel working orders before the test can fill them.
