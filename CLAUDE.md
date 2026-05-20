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
`Bookmap MCP bridge listening on http://127.0.0.1:8765` (port from
`~/.bookmap-mcp/bridge.properties`, not 18888).

### Runtime operator scripts

Three PowerShell entry points at the repo root + `scripts/`:

- `.\build-and-deploy.ps1` — runs `gradlew clean test jar`, locates the
  newest `bookmap-mcp-bridge-v<N>.jar` in `build/libs/`, installs it as
  the canonical `C:\Bookmap\addons\bookmap-mcp-bridge.jar`, and
  **quarantines every other `bookmap-mcp-bridge*.jar` anywhere under
  `C:\Bookmap\addons` (including this repo's `build/libs/` and
  `_phase_backups/`)** into `C:\Bookmap\addons-archive\bookmap-mcp-bridge\`.
  Bookmap scans addons recursively and this repo lives under that tree,
  so build artefacts left in `build/libs/` are load candidates. Hard
  invariant at the end: exactly one bridge jar may remain under
  `C:\Bookmap\addons` and it must be the canonical install — script
  throws otherwise. Waits for Bookmap to close before the copy.
- `.\dashboard-start.ps1` — clears stale `__pycache__`, launches
  `python -B -u -m bookmap_mcp.dashboard --port 18888` in a new window
  using `mcp-server\.venv\Scripts\python.exe`. **Verifies port owner**:
  if 18888 is already listening, probes `/api/snapshot`; only continues
  if the response is valid dashboard JSON. Hard error if some other
  process owns the port. Prints DASHBOARD ONLINE / DASHBOARD ONLINE -
  BRIDGE OFFLINE with structured nextSteps after start.
- `.\scripts\verify-runtime.ps1` — four-layer diagnostic.
  - Layer -1: **addon jar hygiene** — recursive scan, must be exactly
    one match at the canonical path.
  - Layer 0: direct Java bridge probe (`/ping` / `/instruments` /
    `/trend_analyzer`) using URL + token from `BOOKMAP_BRIDGE_URL/TOKEN`
    env or `~/.bookmap-mcp/bridge.properties`. Token never printed.
    Distinguishes refused / timeout / 401 / 404 / no-instruments.
  - Layer 1: Python dashboard `/api/snapshot` reachable.
  - Layer 2: dashboard reports `health=ok` (vs structured offline
    diagnostic).
  - Layer 3: OpenRange's poll URL matches the dashboard URL.
  Final summary prints `FULL CHAIN HEALTHY` only when every layer
  passes.

### Bridge-offline diagnostic shape

When `/api/snapshot` cannot reach the bridge, `fetch_snapshot` returns
a structured payload (NOT a bare `{"health":"offline"}`):
```
{
  "health": "offline", "bridgeUrl": "http://127.0.0.1:8765",
  "bridgeReachable": false, "bridgeError": "...",
  "dashboardPort": 18888,
  "expectedBridgeConfigPath": "<path to bridge.properties>",
  "tokenConfigured": true|false,
  "nextSteps": [ ... actionable steps ... ],
  "error": "<legacy mirror of bridgeError>",
}
```
First entry of `nextSteps` is failure-class-specific (`"timed out"`,
`"connection refused"`, `"401 unauthorized"`). The token is NEVER
included. Pinned by `test_offline_snapshot_shape.py` (5 tests including
a token-leak guard). OpenRange's `PaxHeatwaveSnapshotParser` detects
`health=offline` and produces a `PaxHeatwaveModel.State.BRIDGE_OFFLINE`
carrier so the chart overlay shows `BRIDGE OFFLINE` + reason instead
of generic `NO DATA`. `PaxHeatwaveFetcher.effectiveModel(nowMs)`
escalates to `State.DASHBOARD_OFFLINE` when the dashboard HTTP endpoint
has been unreachable for `STALE_FAIL_THRESHOLD = 5` consecutive ticks
even after a prior success (so the chart never silently shows a frozen
"live" overlay).

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

### TrendAnalyzer port → conviction source + chart triangles

`com.bookmapmcp.trend` is a minimal port of the trendanalyzer-mvp core engine
(11 classes: Candle, OrderflowSnapshot, TrendDirection, SwitchCondition,
TrendConfig, RollingAverage, TrendSnapshot, TrendEngine, TrendRegimeFilter,
StableTrendSnapshot, StableTrendEngine), driven from
`InstrumentState.onTrade` via `TimeBucketTrendAccumulator`. The upstream
`BarAggregator` is COUNT-based (expects pre-formed Bookmap bars); the bridge
gets irregular `onTrade` events, so it uses a time-bucket accumulator with
bounded gap handling (`MAX_CARRY_FORWARD_BUCKETS = 240` = 1 hour at 15s) so
overnight resumes never block the callback thread.

`GET /trend_analyzer?alias=...` returns `{eventMs, updatedAtMs, asOfNanos,
fast, slow, score, reliabilityHint, warmedUp, lastClose}`. **Never conflate
`eventMs` (market-event time, for chart anchoring) with `updatedAtMs`
(bridge wall-clock, for staleness).** Under playback they can diverge by
hours.

Dashboard side: `_source_trend_analyzer(snap)` in `dashboard.py` normalizes
to `[-1,+1]` (blended `0.4·fast + 0.6·slow` of direction × confidence/100).
Reliability gating: warmup / stale / chop / disagreement.

**Anti-domination via per-source share cap** (NOT cluster cap). The composite
normalizes by `sum(|effective|)`, so cluster caps cannot bound a single
source's *share* when other sources go silent. `_conv_apply_source_share_caps`
solves for `max |w| ≤ cap × other / (1 - cap)` and zeros `w` entirely when
`other == 0`. Config in `pax_weights.json::conviction_source_share_caps`,
with `trend_analyzer: 0.10`. Pinned by `test_source_share_caps.py` and
`test_trend_analyzer_conviction_integration.py::test_trend_analyzer_alone_cannot_move_composite`.

`compute_trend_signal(snap)` projects the composite `snap["conviction"].trend`
onto `{STRONG_BULL, WEAK_BULL, STRONG_BEAR, WEAK_BEAR, NONE}` and exposes
`snap["trend_signal"]`. Fading states map to NONE. Per-alias bucket-entered
state in `_LAST_TREND_SIGNAL` for dedup.

OpenRange side: `PaxTrendSignalFetcher` (mirrors `PaxHeatwaveFetcher`) polls
the same `/api/snapshot` endpoint at 1s, parses `snap["trend_signal"]`,
sets `trendSignalDirty` AtomicBoolean. Same threading rule as Heatwave:
fetcher NEVER mutates canvas; `PaxPainter.update()` reads the latest model
on Bookmap callbacks. `PaxTrendTrianglePainter` renders STRONG_BULL/WEAK_BULL
as green up-triangles below mid, STRONG_BEAR/WEAK_BEAR as red down-triangles
above mid. Triangles use **DATA_ZERO** x/y with `PaxChartTimeCoords.epochMsToChartNanos`
(epoch ms → LocalDateTime in CT → chart nanos, same convention as the
existing OR line `toNanos(LocalDateTime)`). Dedup by `(kind, bucketEnteredMs)`
via `PaxTrendTriangleDedup.shouldEmit`; max 8 visible triangles in
`InstrumentState.liveTriangles`. UI toggle `showTrendTriangles` (default ON)
in settings panel.

**Signal policy (debounce + eligibility gate).** `compute_trend_signal`
maintains per-alias state with separate raw and renderable tracking:
`rawKind / renderableKind / renderableBucketEnteredMs / renderableEmittedMs /
renderableEventMs`. A NONE tick NEVER resets renderable state — it just
records `rawKind=NONE`. Bucket only advances when:
(a) renderable kind differs from previous renderable kind (legitimate
transition: WEAK_BULL → STRONG_BULL, BULL → BEAR — immediate), OR
(b) same renderable kind returns after `TREND_SIGNAL_REENTRY_COOLDOWN_MS = 15s`
of non-renderable (genuine re-entry, not flicker spam).
The signal payload carries `eligible / blockedReason / eventMsSource`:
- `eligible=true` requires renderable kind AND `book.mid > 0` (finite)
  AND `eventMs > 0`. Otherwise final kind is downgraded to NONE with
  `blockedReason` set (`"invalid_mid"` / `"invalid_event_ms"`).
- `eventMsSource` is `"trend_analyzer"` when `trend_analyzer.eventMs > 0`,
  else `"wall_clock_fallback"`. Chart anchors use the value as-is.
OpenRange's `PaxTrendTriangleDedup.shouldEmit` short-circuits on
`!signal.eligible` first — the dashboard is the authoritative gate; local
field checks are defense in depth. **Parser defaults missing `eligible` to
FALSE** for safety; a partial dashboard payload cannot accidentally render.
Pinned by `test_trend_signal_policy.py` (13 tests) + `PaxTrendSignalSnapshotParserTest`
(`eligibleFlagParsesTrue` / `…ParsesFalseExplicit` / `missingEligibleFieldDefaultsFalseForSafety` /
`blockedReasonAndEventMsSourceParse`) + `PaxTrendTriangleDedupTest`
(`eligibleFalseBlocksEmitEvenWhenStrongBull` / `eligibleTrueWithValidFieldsEmits`).
- Session conviction is the v2 anchored multi-source engine. State per alias
  in `_CONVICTION_STATE`. Source helpers under the `_source_*` prefix return
  `{score, reliability, raw, reason}`. 17 sources after v7. See
  `mcp-server/README.md` for the cluster table.
- Legacy `_regime_to_signal`, `_slope_to_signal`, `_level_to_signal` are
  preserved and reused by v2 sources — don't refactor them away without
  updating the pinned helper-signal tests.

### Heatwave Quant Box (OpenRange screen-space HUD)

Top-left native Bookmap box that visualizes the live conviction model on one
screen. Five Java classes under `indicators/OpenRange/src/main/java/com/openrange/`:

- `PaxHeatwaveModel` — immutable carrier (header + 11 rows + `fetchedAtMs`).
- `PaxHeatwaveSnapshotParser` — recursive-descent JSON parser (no jar deps)
  + distillation logic. **Paths must match real dashboard shape**:
  - `pax.decision` -> `decision.decision` -> `"WAIT"` (verdict fallback chain).
  - `or_levels.levels[]` (not top-level list); pick nearest by `abs(distance)`.
  - `distance` is in points; render as `+12.50p`.
  - `vwap_bias.components.{sigma_z, regime}` -> fallback `vwap_bias.{sigma_z, regime}`.
  - `vp_bias.components.{va_state, hvn_count, lvn_count}` -> fallback
    `vp_bias.va_state` -> `vp_bias.label`.
  - `flow.regime` for FLOW hint -> fallback `conviction.trend` -> `.trajectory`.
  - CVD hint is `buy` / `sell` / `flat` from signed `flow_cvd` score — NEVER
    `rising` / `falling` (no real per-source trajectory exists).
  - Grouped rows (FLOW, VWAP, VP, BOOK) use `effectiveWeights`-weighted average
    with `sourceReliability > 0` filter. Missing source OR zero reliability ->
    row shows `--` neutral, never invent a score.
- `PaxHeatwavePainter` — renders the model + `Graphics2D` into a single
  `BufferedImage` -> `PreparedImage` -> `CanvasIcon` (Bookmap's screen-space
  pattern). Monospaced font, near-black bg, dark theme, fixed 260-320 px wide.
- `PaxHeatwaveFetcher` — daemon thread, `java.net.http.HttpClient`, polls
  `heatwaveUrl` (default `http://127.0.0.1:18888/api/snapshot`, no auth). Default
  poll 1000 ms clamped to `[500, 3000]`. Backoff 2x after `>=3` consecutive
  failures, cap 5 s. Last-good model retained on failure; box header shows
  `STALE` (age ≥30s) or `NO DATA` (no successful fetch yet).
- `PaxHeatwaveColors` — central dark palette.

**Threading rule (load-bearing).** The fetcher worker thread MUST NOT call
`canvas.addShape` / `canvas.removeShape` / `PaxPainter.update()`. Canvas
mutation only happens on Bookmap-driven callbacks (`onTrade`, `onDepth`,
`onMoveEnd`). The fetcher signals new data via `AtomicBoolean heatwaveDirty`
on `PaxOpeningRangeModule`. `InstrumentState.shouldRepaint(eventTime)`
consumes the flag with `compareAndSet(true, false)` and **bypasses the 1 s
throttle for that one tick** so a fresh snapshot is shown on the very next
market event. Quiet-market caveat (>30 s of no ticks): age display freezes
until the next tick — documented, not fixed; Bookmap's addon SDK has no
documented safe way to enqueue UI work from a foreign thread.

**Freshness** is measured from `fetchedAtMs` (HTTP response receive time),
not the snapshot's `ts` string. The Java side does not parse `ts`; that
removes timezone-format risk and measures dashboard availability (which is
the thing that actually matters).

### OpenRange jar lock

`indicators/OpenRange/build.ps1` writes a fixed-name jar
`build/libs/openrange-release.jar`. Bookmap holds this file open while the
addon is loaded. Trying to rebuild while Bookmap is running fails with
`FileSystemException: ... The process cannot access the file because it is
being used by another process` at the `jar.exe --create` step (tests still
pass; only the packaging step fails). **Close Bookmap before rebuilding.**
This differs from the MCP bridge addon, which uses a versioned jar name
and so allows a new file to land alongside the old one.

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

## Pax AI production invariants (locked 2026-05-20)

Pax AI shipped production-ready on 2026-05-20 after a multi-round audit
and human visual verification of the floating pywebview window. The
invariants below are permanent contracts — future sessions must respect
them.

### Pax AI status

- Pax AI is production-ready.
- Launch path: `pax-ai-start.bat` defaults to the floating `--shell`
  pywebview window. Server-only mode is `pax-ai-start.bat server`
  (optionally followed by a port).
- The `PaxAILauncher` Bookmap addon (`indicators/PaxAILauncher/`) also
  spawns Pax AI with `--shell` when the addon is enabled in Bookmap's
  Add-ons panel.
- `/api/pax/health` returns 200 whenever the Pax AI HTTP server is up,
  independent of dashboard reachability — the launcher uses this as
  its health probe so a dashboard-down state does NOT flag Pax AI as
  failed.

### OR anchor invariant (Pax AI)

The operator-configured Static OR in the OpenRange UI is the **only**
active OR anchor source for Pax AI reasoning. The Pax-AI skill bodies
and chat must NEVER imply RTH/08:30 is the active OR unless the live
snapshot/operator config says so.

Authoritative snapshot fields:

- `snap.session.anchorHHMM`
- `snap.session.anchorTimezone`
- `snap.session.anchorRangeSeconds`
- `snap.session.anchorMode`
- `snap.or_session_config`
- `snap.or_levels.orHigh` / `snap.or_levels.orLow`

RTH/08:30 language is allowed ONLY in explicitly-historical / fallback
context (e.g. `skills/pax-or/SKILL.md` §1.2 default-exchange table or
upstream snapshot-field descriptions like `vwap_bias.components.rth_eth`
and `volume_profile` RTH-session-bounded POC). The active definition in
§1 of the Pax OR skill must say the OR is the operator-configured
Static OR window.

When `snap.session.anchorMode != "LIVE"` (LAST_KNOWN_STALE / FALLBACK)
Pax AI output is **informational only** — no FOLLOW/FADE recommendations,
no new entries. The base preamble in `pax-ai/pax_ai/prompts.py::_BASE_PREAMBLE`
and the Pax-OR skill both spell this gate out; do not weaken either.

Tests `pax-ai/tests/test_prompts.py::test_render_system_prompt_no_stale_active_anchor_claims`
and `..._defines_or_as_operator_configured_static_or` pin this contract.
Re-introducing "When RTH opens", "Regular Trading Hours" near the OR
definition, or "For NQ that is 08:30" will fail those tests.

### HFT skill invariant (Pax AI)

`skills/hft_microstructure_quant_v1/SKILL.md` is **reference context only**
inside Pax AI. It must NEVER force JSON output in Pax AI chat, even when
the router picks `hft_microstructure_quant_v1` as the primary skill
(which happens for keywords like "tape", "iceberg", "absorption").
Pax AI chat responses are conversational prose over the snapshot digest.

- The historical "If the USER MESSAGE began with ROUTER ... mode 1"
  selector is gone and must stay gone.
- The unconditional bigram "Output only JSON" must not appear.
- The skill explicitly disclaims itself as Pax AI's output shape
  ("never Pax AI"). The JSON schema is documented for external
  standalone HFT-filter callers only.

Verified live 2026-05-20 against `claude-haiku-4-5` with the prompt
"tape and iceberg read?" — `router_primary == hft_microstructure_quant_v1`
and the response was prose, not JSON. Tests
`pax-ai/tests/test_prompts.py::test_render_system_prompt_no_router_mode_selector`,
`..._no_unconditional_output_only_json`, and `..._pax_ai_json_exclusion_is_explicit`
pin this contract.

### Claude CLI invariant (Pax AI)

Pax AI's Claude calls are **read-only**:

- `--tools ""` — unconditional. No Bash, no Read, no Edit, no MCP
  tool access from the chat path.
- `--max-turns 1` — unconditional. Chat is a single response, not
  an agentic loop.
- `--output-format stream-json --verbose --include-partial-messages` —
  required combination for line-by-line SSE streaming.
- `--bare` — **conditional**, set ONLY when `ANTHROPIC_API_KEY` is
  present in env (API-key mode) or `PAX_AI_CLAUDE_BARE=1` is set to
  opt in. For OAuth subscription auth (typical individual user),
  `--bare` is omitted because it skips OAuth/keychain reads and the
  CLI returns "Not logged in". See `pax-ai/pax_ai/claude_stream.py::_use_bare()`.

If you add new chat code paths or non-Pax-AI Claude calls, do NOT
re-enable tools or remove `--max-turns 1` without an explicit audit
finding.

### Chat-handler test isolation invariant

Pax AI chat handler tests (`pax-ai/tests/test_chat_handler.py`) must
NOT touch the live chat journal at `D:\BookmapLogs\pax-chat.db`. The
`_isolate_journal` autouse fixture in that file replaces
`chat.journal.record` with a no-op spy and installs a tripwire on
`journal._connect` so any accidental real-DB I/O is a loud failure.

When you add a new chat-handler test, either reuse the existing
fixture or add an equivalent monkeypatch. Live-mode smokes that
intentionally exercise the real DB are OK provided they clean up
via `POST /api/pax/chat/forget` at the end.

### Addon jar hygiene (recurring operational item)

Bookmap scans `C:\Bookmap\addons` recursively. The repo lives under
that tree, so `indicators/*/build/libs/*.jar` outputs from standalone
`indicators/*/build.ps1` runs are **load candidates that race the
canonical install**.

Canonical addon jars (the only ones that should be loaded):

- `C:\Bookmap\addons\bookmap-mcp-bridge.jar`
- `C:\Bookmap\addons\openrange-release.jar`
- `C:\Bookmap\addons\paxai-launcher-release.jar`

Operational rule: after any standalone `indicators/*/build.ps1` run,
clear the duplicate `paxai-launcher-release.jar` /
`openrange-release*.jar` from `indicators/PaxAILauncher/build/libs/`
and `indicators/OpenRange/build/libs/` before any Bookmap restart.
The bridge addon uses `build-and-deploy.ps1` which already handles
this quarantine; the indicator builds do not.

Background: the build.ps1 scripts output into `build/libs/` which sits
under `C:\Bookmap\addons\**`. Until those scripts are changed to write
outside the scan tree, this stays a manual hygiene step.
