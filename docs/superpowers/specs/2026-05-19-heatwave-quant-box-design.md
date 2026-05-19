# Heatwave Quant Box — Design Spec

Date: 2026-05-19
Owner: Will (autopilotaitech)
Status: REVISED 2026-05-19 — 7 architectural corrections applied (painter pipeline, lifecycle, snapshot shape, verdict source, distance units, poll cadence, repaint trigger). Ready for implementation.

## 1. Goal

A compact, dark-theme, screen-space HUD in the top-left of Bookmap that shows the live weighted-signal model on one screen. It replaces the current OR signal badge and renders 11 rows distilled from the Python conviction model. It must look like an institutional execution instrument — dense, calm, monospaced, no SaaS decoration — and let the trader glance at it during live trading to instantly read:

1. Where price sits relative to OR / EXT magnets.
2. Whether the model leans LONG / SHORT / WAIT.
3. Which of the 10 supporting factors agree or disagree with the verdict.
4. Whether the signal is fresh enough to trust.

## 2. Constraints (non-negotiable)

- Lives **inside the OpenRange addon** (`indicators/OpenRange/`). Extends the existing `ScreenSpacePainter` on `PaxOpeningRangeModule`. Not a new addon. Not in the MCP bridge.
- **Never blocks Bookmap market-data callbacks** on network or disk. All HTTP I/O runs on a dedicated background thread.
- Read-only. Touches no trading code, no order paths, no model weights, no `pax_weights.json`.
- ASCII-only source code (CLAUDE.md rule for files that are not already Unicode).
- Existing tests must still pass: `indicators/OpenRange/build.ps1` (18 Java tests today) and `mcp-server` pytest.

## 3. Data source audit (from Python model)

The model owns **17 conviction sources** + **8 per-level composite drivers** = 25 raw atoms. The box must distill these to 11 visual rows without lying about model truth.

### 3.1 Conviction sources — `pax_weights.json::conviction_source_weights`

| Cluster | Sources | Effective cap |
| --- | --- | --- |
| Flow | `flow_ofi` (0.14), `flow_cvd` (0.12), `bias_score` (0.08), `regime` (0.08) | 0.35 |
| VWAP | `vwap_dislocation` (0.08), `vwap_slope` (0.08), `vwap_or_gate` (0.04), `anchored_vwap_opening_drive` (0.08) | 0.30 |
| Structure | `volume_profile` (0.08), `ib_context` (0.04) | 0.20 |
| Microstructure | `pull_stack` (0.10), `tape_large_lot` (0.06), `orderbook` (0.05), `lt_liquidity` (0.04), `micro_events` (0.04), `level_reaction` (0.08) | 0.30 |

### 3.2 Level composite drivers — `_level_composite` in `dashboard.py`

`pull_stack`, `tape`, `micro`, `lt_liquidity`, `orderbook`, `vwap`, `volume_profile`, `session_conviction` (weights from `settings.py::SETTINGS_DEFAULTS["level_weights"]`).

### 3.3 Snapshot shape — `GET /api/snapshot` on `http://127.0.0.1:18888`

No auth. Returns one JSON object per call. Relevant top-level keys:

- `conviction`: `{score, trajectory, trend, sourceScores, sourceReliability, effectiveWeights, ...}`
- `or_levels`: list of level rows `{label, price, side, distance, proximity, decision, confidence, composite_score, composite_dir, components}`
- `tape_flow`: `{deltaScore, deltaLabel, ...}`
- `vwap_bias`: `{label, sigma_z, regime, ...}`
- `vp_bias`: `{label, poc_z, va_state, ...}`
- `decision`: `{decision, confidence, ...}` (final ENTER_LONG / ENTER_SHORT / WAIT / STAND_DOWN)
- `pax`: `{decision, size, size_tier, reason, ...}` — verdict from the Pax skill engine (authoritative for the header)
- `ts`: ISO-8601 timestamp string (e.g. `"2026-05-19T11:42:17"`). **The Java side does NOT parse `ts`.** Freshness is measured from `fetchedAtMs` — the local wall-clock time when the HTTP response was received. This measures dashboard availability (the metric we actually care about) and avoids timezone/format parse risk.

`or_levels` is itself a dict, not a list. The level rows live under `or_levels["levels"]` (a list). The nearest level is found by `min(levels, key=lambda l: abs(l["distance"]))`. Each level row exposes: `label, price, side, distance, proximity, decision, confidence, composite_score, composite_dir, components`.

**`distance` is in points, not ticks.** Display as `+12.50p` (signed, two decimals, `p` suffix). Do not attempt to convert to ticks unless a reliable tick size is available — for NQ the dashboard's `distance` is already the human-readable points value.

## 4. Eleven-row mapping (model truth preserved)

Row 1 covers the strongest near-price magnet. Rows 2-11 each map to a real model bucket. No row invents data.

| # | Label  | Source                                                  | Score column                   | Hint column                                  |
| - | ------ | ------------------------------------------------------- | ------------------------------ | -------------------------------------------- |
| 1 | OR/EXT | nearest from `or_levels.levels[]` by `abs(distance)`    | `decision` (FOLLOW/FADE/WAIT)  | `<label>  <price>  +<dist>p  <confidence>%`  |
| 2 | FLOW   | `conviction.sourceScores.regime` + `.bias_score`        | effectiveWeights-weighted avg  | `<regime label>`                             |
| 3 | OFI    | `conviction.sourceScores.flow_ofi`                      | signed score                   | `rel <reliability>`                          |
| 4 | CVD    | `conviction.sourceScores.flow_cvd`                      | signed score                   | trajectory (rising/falling/flat)             |
| 5 | ABSORB | `conviction.sourceScores.level_reaction`                | signed score                   | `none / soft / hard`                         |
| 6 | VWAP   | 4 vwap sources combined                                 | effectiveWeights-weighted avg  | `vwap_bias.sigma_z` + slope tag              |
| 7 | VP     | `volume_profile` + `ib_context`                         | effectiveWeights-weighted avg  | `vp_bias.va_state` (HVN/LVN/inside)          |
| 8 | PS     | `conviction.sourceScores.pull_stack`                    | signed                         | `rot up/down/flat`                           |
| 9 | TAPE   | `conviction.sourceScores.tape_large_lot`                | signed                         | `tape_flow.deltaLabel` (block buy / sell)    |
| 10| BOOK   | `orderbook` + `lt_liquidity`                            | effectiveWeights-weighted avg  | `bid lean / ask lean / balanced`             |
| 11| MICRO  | `conviction.sourceScores.micro_events`                  | signed                         | most recent event tag (e.g. `sweep ask`, `iceberg`, `spoof`), or `none` |

ABSORB is mapped to `level_reaction` per user decision — `level_reaction` is the source that already tracks heavy aggression at OR/EXT magnets without follow-through, which is the absorption signature.

Header verdict is read from `pax.decision` (authoritative); falls back to `decision.decision` only when `pax` is missing, errored, or empty. Header composite score is `conviction.score * 100` rendered as `+NN` / `-NN`.

**Grouped rows** (FLOW, VWAP, VP, BOOK) compute a weighted average: `sum(sourceScores[k] * effectiveWeights[k]) / sum(effectiveWeights[k])` over the row's source keys. If a source is missing OR its `sourceReliability[k]` is 0, that source is excluded. If all sources of a row are excluded, the row shows `--` with neutral tone — never invent a score.

## 5. Visual design

Fixed-size box anchored at top-left. Approx 280px wide x ~190px tall at default font. Monospaced, tabular numbers, near-black background, thin 1px muted border, one-pixel separator under the header. No gradients. No nested cards. The ASCII mockup below is conceptual; the actual paint uses `Graphics2D` primitives (filled rect background, drawString rows, drawLine separator).

```
+----------------------------------+
| PAX HEAT  +42   WAIT       2s    |
+----------------------------------+
| OR +1 EXT  20125.00  +14.00p 68% |
| FLOW       +0.31  TREND_UP       |
| OFI        +0.44  rel .82        |
| CVD        +0.27  rising         |
| ABSORB     -0.10  none           |
| VWAP       +1.8s  slope up       |
| VP         +0.22  HVN near       |
| PS         +0.55  rot up         |
| TAPE       +0.41  block buy      |
| BOOK       -0.08  ask lean       |
| MICRO      +0.30  sweep ask      |
+----------------------------------+
```

### Color logic

- Score sign drives row tint at low alpha (background, not text):
  - `score > +0.20`  -> bullish accent (RGB 0,191,135 @ 28 alpha)
  - `score < -0.20`  -> bearish accent (RGB 220,80,80 @ 28 alpha)
  - `|score| <= 0.20` -> neutral (no tint)
- Row 1 colored by `decision`: FOLLOW_LONG=green, FOLLOW_SHORT=red, FADE_*=amber, WAIT=gray.
- Header verdict text colored by `decision.decision`.
- Header age column tinted amber when stale, red when missing.

### Freshness

The right side of the header shows seconds since the last successful fetch (`fetchedAtMs`). Three states:

| Age (s)   | Display | Color |
| --------- | ------- | ----- |
| < 5       | `Ns`    | gray  |
| 5 - 30    | `Ns`    | amber |
| > 30      | `STALE` | red   |

When no successful fetch yet, header reads `... NO DATA` in red and rows show `--`.

## 6. Architecture

### 6.1 Lifecycle (matches existing module)

`PaxOpeningRangeModule` does NOT have `initialize(...)` / `stop(...)`. It is constructed with `new PaxOpeningRangeModule(Layer1ApiProvider)` and torn down via `finish()` (from `Layer1ApiFinishable`). Settings arrive asynchronously through `acceptSettingsInterface(SettingsAccess)`.

- Construct the fetcher lazily inside `acceptSettingsInterface` once `uiSettings` is loaded: `if (uiSettings.showHeatwaveBox) heatwave.start();`.
- If the user toggles `showHeatwaveBox` from the GUI, `saveSettings(...)` calls `heatwave.applySettings(...)`, which start/stops the background thread accordingly.
- `finish()` calls `heatwave.stop()` which interrupts the worker, joins with 2s timeout, then releases the `HttpClient`.

### 6.2 Threading model

- One background `PaxHeatwaveFetcher` worker thread (daemon) per module. Owns a `java.net.http.HttpClient` (Java 11+, available on the bundled JRE 17). Polls `heatwaveUrl` (default `http://127.0.0.1:18888/api/snapshot`) every `heatwavePollMs` (default 1000ms, clamp `[500, 3000]`) with `Duration.ofMillis(750)` request timeout.
- On success: parses JSON via `PaxHeatwaveSnapshotParser` (small recursive-descent parser; no new jar dependency since Bookmap's classpath ships only `bm-l1api.jar`, `bm-simplified-api-wrapper.jar`, `util.jar`). Distills into an immutable `PaxHeatwaveModel`. Swaps `volatile PaxHeatwaveModel latest` atomically. Then triggers a throttled repaint (see 6.4).
- On failure: increments a fail counter, logs at most once per minute per error class to `Log.warn`, leaves `latest` untouched so the painter can render STALE based on `latest.fetchedAtMs` age.
- Backoff: on `N >= 3` consecutive failures the poll interval doubles up to 5s; resets on first success.
- **Bookmap market callbacks (`onTrade`, `onDepth`, etc.) never block on the fetcher.** They never read from the network or disk. The painter consumes `latest` (volatile reference) only.

### 6.3 Painter pipeline (existing pattern)

OpenRange's `ScreenSpacePainter` does **not** expose `onPaint(Graphics2D)`. The existing pattern is:

1. `PaxPainter.update()` (synchronized) clears all `CanvasIcon` shapes via `canvas.removeShape(...)`.
2. For each visual element (line, label, badge), build a `BufferedImage`, wrap as `PreparedImage`, anchor with `CompositeHorizontalCoordinate` + `CompositeVerticalCoordinate` (using `PIXEL_ZERO` base for screen-anchored UI), call `canvas.addShape(new CanvasIcon(...))`.
3. The current `addSignalStatus(snapshot)` method renders the legacy OR badge this way. We **replace its call** with `addHeatwaveBox()` when `showHeatwaveBox=true`.

The Heatwave Box is rendered as **one** `BufferedImage` (header + 11 rows + border + row tints) wrapped in a single `PreparedImage` / `CanvasIcon`. One shape, one screen-anchored coordinate pair.

### 6.4 Repaint strategy

The fetcher must **never** mutate the `CanvasIcon` shape list from its worker thread — that work happens on Bookmap's market-data callbacks (`onTrade` / `onDepth` / `onMoveEnd`). Mechanism:

- `PaxOpeningRangeModule` owns a single `AtomicBoolean heatwaveDirty = new AtomicBoolean(false)`.
- The fetcher's callback is `() -> heatwaveDirty.set(true)`. On both success (after the volatile `latest` swap) and the first failure of a streak (the NO-DATA edge) the flag is set. The fetcher does nothing else with the canvas.
- `InstrumentState.shouldRepaint(eventTime)` checks `consumeHeatwaveDirty()` first via `compareAndSet(true, false)`. If true, it **bypasses** the existing 1-second nanosecond throttle (`REPAINT_THROTTLE_NANOS`) and forces a repaint on this Bookmap-driven tick. This keeps the box latency low without violating Bookmap's threading model — the actual `canvas.addShape` / `canvas.removeShape` calls still run on the Bookmap market-data thread that fired `onTrade` / `onDepth`.
- `PaxPainter.update()` always reads `heatwave.snapshot()` (a `volatile` ref); the latest model is picked up on every repaint regardless of who triggered it.
- **Quiet-market caveat.** In a completely silent instrument (no trade or depth update for >30 s), no Bookmap callback fires, so the painter does not run, so the age display freezes at the last value it rendered. This is documented and accepted; Bookmap addons do not have a documented safe way to enqueue a repaint from a foreign thread. When market activity resumes, the next tick will repaint the box with the correct age band (FRESH / WARN / STALE / NO_DATA).
- `createScreenSpacePainter()` calls `painter.update()` once at chart open. That paint always runs on the Bookmap thread and uses whatever `heatwave.snapshot()` returns at that moment (typically `null` before the first poll completes, rendering NO_DATA).

### 6.5 New Java classes (all under `com.openrange`)

- `PaxHeatwaveModel` — immutable carrier: `header {verdict, scorePct, ageDescriptor, ageColorState}` + `Row[] rows` (11 rows, each `{label, scoreText, scoreTone, hint}`) + `long fetchedAtMs` + boolean `noData`. Static constants `NO_DATA_MODEL` and `STALE_MODEL`.
- `PaxHeatwaveFetcher` — owns the background thread, HttpClient, and the latest-model volatile reference. Public API: `start()`, `stop()`, `PaxHeatwaveModel snapshot()`, `applySettings(PaxOpeningRangeUiSettings)`.
- `PaxHeatwaveSnapshotParser` — pure functions: `PaxHeatwaveModel parse(String json, long fetchedAtMs)` + JSON tokenizer helpers. Stateless. Unit-testable with fixtures.
- `PaxHeatwavePainter` — pure: `PreparedImage render(PaxHeatwaveModel model, int fontSize)`. Returns the single composite image. Stateless. Unit-testable via `BufferedImage`.
- `PaxHeatwaveColors` — central palette so the dark theme stays consistent.

### 6.6 Integration into existing `PaxOpeningRangeModule`

- Add field: `private final PaxHeatwaveFetcher heatwave;` constructed eagerly in the module constructor (no thread started yet).
- `acceptSettingsInterface(...)` calls `heatwave.applySettings(uiSettings)` after loading; this start/stops the worker thread based on `showHeatwaveBox`.
- `saveSettings(...)` calls `heatwave.applySettings(...)` so GUI toggles take effect immediately.
- `finish()` calls `heatwave.stop()` before existing cleanup.
- `PaxPainter.update()` is extended: when `uiSettings.showHeatwaveBox=true`, call `addHeatwaveBox()` (renders the box, suppresses legacy badge). When `false`, the existing `addSignalStatus(...)` runs.
- `addHeatwaveBox()` queries `heatwave.snapshot()`, calls `PaxHeatwavePainter.render(...)`, anchors at `(heatwaveBoxX, heatwaveBoxY)` from `PIXEL_ZERO`.

### 6.7 No Python changes required

The dashboard already serves `/api/snapshot` on 18888. No new file write path. No model edits. No `pax_weights.json` touch.

## 7. Settings additions (`PaxOpeningRangeUiSettings`)

Bumps `@StrategySettingsVersion(currentVersion = 1)` -> `currentVersion = 2`. Add `compatibleVersions = {1}` so old settings load with defaults for new fields.

| Field                | Type    | Default | Notes                                           |
| -------------------- | ------- | ------- | ----------------------------------------------- |
| `showHeatwaveBox`    | boolean | `true`  | Master toggle. False -> renders legacy badge.   |
| `heatwaveBoxX`       | int     | `12`    | Pixel offset from chart left edge.              |
| `heatwaveBoxY`       | int     | `14`    | Pixel offset from chart top edge.               |
| `heatwaveFontSize`   | int     | `11`    | Clamped to [9, 16].                             |
| `heatwaveCompact`    | boolean | `true`  | Reserved for a future wider variant. No-op now. |
| `heatwavePollMs`     | int     | `1000`  | Clamped to [500, 3000].                         |
| `heatwaveUrl`        | String  | `http://127.0.0.1:18888/api/snapshot` | Override for tests / non-default port. |

## 8. Failure modes

| Condition                          | Box behavior                                           |
| ---------------------------------- | ------------------------------------------------------ |
| Dashboard process down             | `... NO DATA` in red header. Rows show `--`.           |
| HTTP 5xx or timeout repeatedly     | Last good model shown; age tints amber then red.       |
| JSON parse error                   | Fail counter increments. Last good model retained.     |
| Snapshot missing a source key      | That row shows `--` with neutral tone. Rest unaffected.|
| Bookmap closing                    | `stop()` joins fetcher thread with 2s timeout.         |

The painter is reentrant-safe: it operates on a single immutable `HeatwaveModel` snapshot per paint call.

## 9. Test plan

### Java (added to `build.ps1`)

- `PaxHeatwaveSnapshotParserTest` — feeds **four inline JSON fixtures** declared as `String` constants in the test (no resource files): `RICH` (synthetic, all sources present), `REALISTIC` (uses real dashboard shapes — `vwap_bias.components.{sigma_z,regime}`, `vp_bias.components.{va_state,hvn_count,lvn_count}`, `flow.regime`), `SPARSE` (missing sources -> neutral rows), `ZERO_RELIAB` (zero `sourceReliability` -> neutral), `DIRECT_VWAP_KEYS` (components absent -> falls back to top-level `vwap_bias.sigma_z` / `.regime`), and `MALFORMED` (invalid JSON -> `ParseException`). The realistic fixture pins the audit-found bug: VWAP / VP / FLOW hints must read the nested `components` paths and `snap.flow.regime`.
- `PaxHeatwavePainterTest` — render to a `BufferedImage`, assert non-zero pixel count in the box region and that bullish/bearish/neutral tints differ (sample three known coordinates). Snapshot-style assertions, no golden PNG.
- `PaxHeatwaveFetcherTest` — uses a `MockWebServer`-style local `ServerSocket` returning canned bodies; asserts the fetcher swaps `latest` on success, retains last good on failure, and backs off on consecutive errors.

### Python

- No new Python tests required. The `/api/snapshot` endpoint is already covered.

### Verification commands

- `cd indicators/OpenRange; powershell -File build.ps1` — must show "Built build\libs\openrange-release.jar" with all tests passing including the three new ones.
- `cd mcp-server; python -m pytest -q` — unchanged, no regressions.
- Manual: start dashboard (`python -m bookmap_mcp.dashboard`), then start Bookmap with the new jar deployed; confirm the box appears top-left, header verdict matches `/api/snapshot` payload, rows update at ~500ms cadence, freshness counter ticks.

## 10. Enable / disable

In Bookmap: **Settings -> Strategy -> OpenRange -> UI settings**. Toggle `showHeatwaveBox` off to fall back to the legacy badge. The X / Y / fontSize / pollMs fields are tunable in the same panel.

To repoint at a different dashboard port (e.g. running on 18889), set `heatwaveUrl` to `http://127.0.0.1:18889/api/snapshot`.

## 11. Out of scope

- Touching `pax_weights.json` or any model weight.
- Touching live order paths or the `/place_limit_order` and `/cancel_order` bridge endpoints.
- A new floating JFrame, browser HUD, or popup. Heatwave is screen-space-only.
- A redesign of the OR signal lines, labels, or main jar layout.
- Per-source historical sparklines (would push past 280px width).

## 12. Open items

None. All brainstorm + correction decisions are settled:

1. Data bridge -> HTTP poll to `/api/snapshot` on 18888.
2. ABSORB row -> mapped to `level_reaction`.
3. Old badge -> replaced when `showHeatwaveBox` is true.
4. Painter -> `BufferedImage` -> `PreparedImage` -> `CanvasIcon` (single composite shape).
5. Lifecycle -> constructor + `finish()`; fetcher started after `acceptSettingsInterface(...)`.
6. Snapshot keys -> `ts` is ISO string but **not parsed** by Java; freshness uses `fetchedAtMs` (HTTP response receive time). `or_levels.levels[]` (not top-level list).
7. Header verdict -> `pax.decision` first, `decision.decision` fallback.
8. Distance -> points, rendered `+12.50p`.
9. Polling -> 1000 ms default, clamp `[500, 3000]`.
10. Repaint -> fetcher only sets `AtomicBoolean heatwaveDirty`. Repaints happen on Bookmap market-data callbacks (`onTrade` / `onDepth` / `onMoveEnd`); the dirty flag bypasses the existing 1 s throttle for one tick so a fresh snapshot is shown on the very next market event. No canvas mutation from the fetcher thread. Quiet markets freeze the age display until the next tick — documented, not fixed.
11. Hints -> VWAP reads `vwap_bias.components.sigma_z` / `.regime` (falls back to top-level keys). VP reads `vp_bias.components.va_state` (falls back to top-level / `label`). FLOW prefers `snap.flow.regime`, falls back to `conviction.trend`. CVD says `buy` / `sell` / `flat` based on signed score — never `rising` / `falling` (no real per-source trajectory available).
