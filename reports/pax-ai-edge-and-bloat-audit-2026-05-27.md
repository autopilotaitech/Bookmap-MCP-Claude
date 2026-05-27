# Pax AI Edge and Bloat Audit (2026-05-27)

Scope: live Bookmap/Pax AI edge path after market, with Bookmap still running. This audit inspected source code and live endpoints. It does not assume the chart display is correct.

## Runtime State

- Source tree was clean before cleanup.
- Bookmap is running with `C:\Bookmap\addons\openrange-release.jar` size `209602`, SHA prefix `E7E1E8DBF6BFF539`.
- That installed jar contains `PaxStateHudModel` and `PaxStateHudPainter`.
- Clean source no longer contains `PaxStateHud*`; the runtime jar and source are intentionally out of sync because the operator wants the second HUD to stay visible for now.
- Staged clean-source jar after cleanup: `C:\Bookmap\addons-staging\openrange\openrange-release.jar`, size `188937`, SHA prefix `24F5122E38FFCD75`. Not deployed.

## Live Edge Verdict

Current system is not producing usable edge. It is correctly refusing to call trades under its own rules.

Live snapshot:

- `health=ok`
- `alias=NQM6.CME@RITHMIC`
- `mid=30053.5`
- `orHigh=30192.0`
- `orLow=30145.0`
- `orWidthPts=47.0`
- `inProximity=false`
- `paxDecision=STAND_DOWN`
- `paxReason=OR width 47.0 out of [3.0,25.0]`
- `convictionScore=0.061`
- `convictionTrend=CHOP`
- `flowRegime=BALANCED`

Live `/api/pax/levels/edge`:

- Every row has `actionable=false`.
- Most rows are blocked by `no_direction`.
- One row has `raw_direction=LONG` at `-2`, but it is blocked by `not_near_level`.
- No row is eligible for a green/red trade glyph.

Today's logged outcome sample is negative:

- 33 closed signals.
- +60s hit rate `0.36`, mean `-0.15R`, median `-0.02R`.
- OR-L signals: `n=4`, +60s hit rate `0.00`, mean `-0.69R`, invalidated `1.00`.
- Driver bundle `micro+orderbook+tape`: `n=6`, +60s hit rate `0.17`, mean `-0.46R`, invalidated `0.67`.

Conclusion: lowering gates or forcing plots would only make false positives more visible. It would not create edge.

## Code Findings

### 1. OR-width gate is the active live blocker

File: `mcp-server/bookmap_mcp/dashboard.py`

- Lines 4404-4405 define `PAX_MIN_OR_WIDTH_PTS = 3.0` and `PAX_MAX_OR_WIDTH_PTS = 25.0`.
- Lines 4473-4480 read `orWidthPts` and return `STAND_DOWN` when width is outside `[min,max]`.
- Today's width is `47.0`, so the dashboard is doing exactly what the code says.

This is not a UI problem. The backend says the OR is too wide for the current Pax trade model.

### 2. Level-edge actionability is strict and mostly blocking

File: `pax-ai/pax_ai/level_edge.py`

- Lines 167-202 define blocker priority:
  `health -> stale -> anchor -> news -> session -> no_direction -> not_near_level -> low_confidence -> size_tier -> thesis_gate -> missing_stop`.
- Lines 349-358 require all of these for `actionable=true`:
  directional composite, level proximity, size tier, thesis not gated, confidence >= threshold, stop price.
- Lines 360-362 force chart direction to `WAIT` unless `actionable=true`.

That is why raw directional hints do not become bull/bear plots.

### 3. The so-called R score is not calibrated edge

File: `pax-ai/pax_ai/edge_calculus.py`

- Lines 88-104 map confidence to size tier by static thresholds.
- Lines 134-144 compute `expected_r = confidence * directional_R`.
- Lines 322-336 return `expected_R`, probability-like fields, stop/payline/rung data.

File: `pax-ai/pax_ai/config.py`

- Lines 47-50 set `FULL=0.50`, `HALF=0.35`.
- Lines 61-70 define a hand-written `directional_R_table`.

This is structure scoring, not Jane-Street-style learned expectancy. The chart should not imply measured EV.

### 4. Research/self-training stack exists but is not driving live edge

File: `pax-ai/pax_ai/config.py`

- Lines 72-83: feature bus and bus digest defaults.
- Lines 85-89: outcomes daemon default off.
- Lines 90-99: forecast capture default off.
- Lines 100-115: trigger engine default off.

File: `pax-ai/pax_ai/__main__.py`

- Lines 43-64 import/start feature_bus, outcomes, journal, trigger_engine; disabled systems no-op unless config enables them.

These modules add code surface and operational confusion. They do not currently improve the plotted edge.

### 5. Runtime/source mismatch is real

Installed jar contains:

- `PaxStateHudModel.class`
- `PaxStateHudPainter.class`

Clean source contains neither. That explains why the chart shows the second HUD while the current repo does not. Keeping that HUD means keeping the installed jar for now.

### 6. Java display bloat still exists

File: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`

- Lines 386 and 591-606 register a native signal marker indicator.
- Lines 737-842 implement `NativeSignalMarkerIndicator`.
- Lines 796-837 implement `publishTrend`, but the audited source has no caller.
- Lines 1025-1032 render a disabled legacy trend-triangle checkbox.
- Lines 1993-2024 and 2256-2292 keep the legacy trend triangle renderer.
- Line 867 starts `levelEdge` unconditionally with no operator toggle.

This is why the chart has many always-on Pax visual paths and few controls.

## Cleanup Performed

Removed one proven dead source-only registry:

- Deleted `indicators/OpenRange/src/main/java/com/openrange/PaxChartLayerRegistry.java`.
- Deleted `indicators/OpenRange/src/test/java/com/openrange/PaxChartLayerRegistryTest.java`.
- Removed its build invocation from `indicators/OpenRange/build.ps1`.

Why this was safe:

- `PaxChartLayerRegistry` had no production callers.
- Only its own test and `build.ps1` referenced it.
- OpenRange build passed after removal.

Net cleanup: `264` deleted lines.

## Verification

Command:

`powershell.exe -ExecutionPolicy Bypass -File build.ps1`

Result:

- Build passed.
- Staged jar built at `C:\Bookmap\addons-staging\openrange\openrange-release.jar`.
- Hygiene check passed: exactly one `openrange*.jar` under `C:\Bookmap\addons`, the installed canonical jar.
- No deployment performed.

## What Not To Do Next

- Do not lower `pax_max_or_width_pts` gates blindly.
- Do not force raw_direction into green/red plots while `actionable=false`.
- Do not add another HUD/card.
- Do not treat `score_R` as measured EV.
- Do not deploy the staged clean jar unless the operator accepts losing the current runtime-only second HUD.

## Next Correct Work

1. Split display controls:
   - Heatwave HUD toggle.
   - Pax state HUD toggle.
   - Level-edge price glyph toggle.
   - Raw event pulse toggle.

2. Build an evidence tape, not another box:
   - Plot sweep/absorption/iceberg/pull-stack as short-lived raw event pulses.
   - Encode direction with shape and color.
   - Keep raw events visually separate from trade entries.

3. Turn logged data into a real edge filter:
   - Use the existing JSONL closed rows.
   - Rank by setup + level_label + driver bundle + OR width bucket + proximity distance.
   - Promote only buckets with positive out-of-sample 60s/300s expectancy.
   - Until that exists, the backend is a rules/gates system, not a learned edge model.

