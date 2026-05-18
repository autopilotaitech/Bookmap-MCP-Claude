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
`_load_pax_weights()`. Restart the dashboard process to flush cached state.

## Code layout pointers

- `mcp-server/bookmap_mcp/dashboard.py` — snapshot composer (`fetch_snapshot`),
  `compute_or_levels` (per-magnet `composite` via `_level_composite`),
  `compute_tape_flow`, `compute_vwap_bias`, `compute_vp_bias`, `trade_decision`,
  `compute_session_conviction`, `_sync_magnet_levels`. ~3500 lines, audited heavily.
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

- Don't introduce live order-placement code paths. The Pax agent is
  CSV-logged and read-only by design.
- Don't add LLM-driven math to decision skills. Skills are deterministic
  decision trees over the snapshot.
- Don't refactor `dashboard.py` without backing up. It's the production hub.
- Don't reformat or "tidy" Unicode in files that already use it; the user
  diffs these manually.
- Don't reinvent upstream classifications in Python. If FlowRegime,
  MomentumSnapshot, or any Java handler already emits the derived field
  (trajectory, regime, z-score, σ-band), READ it. Python-side caches
  belong to dashboard-process-local state only.
- Don't add trajectory modulation to direct-read static sources. The
  pattern fits slow/anchored signals (`conviction`, `bias_score`,
  `level_reaction`). Adding it to `orderbook` / `lt_liquidity` /
  `volume_profile` would be bloat — they're snapshots, not processes.
