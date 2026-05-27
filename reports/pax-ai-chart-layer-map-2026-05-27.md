# Pax / OpenRange Chart Layer Map

**Date:** 2026-05-27 (after RTH)
**Scope:** Audit-only Phase 1 of the chart-readability work. Inventories every
visible layer the OpenRange addon draws, with class/method, category,
surface, direction states, and the collision risks that drive the Phase 2
visual hierarchy.

Source root: `indicators/OpenRange/src/main/java/com/openrange/`.

Categories:
- **CONTEXT** — always-on trading frame (OR lines, extensions). Quiet.
- **RAW_EVENT** — instantaneous microstructure event (sweep, absorption, pull/stack, acceptance/rejection). Short-lived.
- **TRADE_READ** — actionable directional read for entry. Dominant.
- **DIAGNOSTIC** — status / health / process state. Belongs in HUD, not in the price lane.

Surfaces:
- **price-lane** — chart-anchored shape via `DATA_ZERO` coordinates; occupies the same band as price.
- **HUD** — screen-space `PIXEL_ZERO`-anchored overlay; pinned to the chart corner, not to price.
- **tooltip / data box** — content embedded inside a HUD container, not a free-floating chart marker.

---

## Layer table

| # | Layer ID | Source class / method | Surface | Category | Direction states | Lifetime | Toggle | Collides with |
|---|---|---|---|---|---|---|---|---|
| 1 | `OR_HIGH_LINE` | `PaxOpeningRangeModule.drawDay()` L2211 + `addLine` L2248 | price-lane (line) | CONTEXT | bullish (cyan) | persistent across session | implicit | OR_HIGH_LABEL, dynamic upper extensions |
| 2 | `OR_HIGH_LABEL` | `PaxOpeningRangeModule.drawDay()` L2212 + `addLabel` L2275 | price-lane (text, anchored at lineEnd) | CONTEXT | bullish | persistent | implicit | INS_L_MARKER, LEVEL_EDGE_GLYPH, CHART_EVENT |
| 3 | `OR_LOW_LINE` | `drawDay()` L2214 | price-lane (line) | CONTEXT | bearish (tomato) | persistent | implicit | OR_LOW_LABEL, dynamic lower extensions |
| 4 | `OR_LOW_LABEL` | `drawDay()` L2215 | price-lane (text) | CONTEXT | bearish | persistent | implicit | INS_S_MARKER, LEVEL_EDGE_GLYPH, CHART_EVENT |
| 5 | `OR_MID_LINE` | `drawDay()` L2218 | price-lane (line) | CONTEXT | neutral (gold) | persistent when `showMid=true` | "Show mid line" | OR_MID_LABEL |
| 6 | `OR_MID_LABEL` | `drawDay()` L2219 | price-lane (text) | CONTEXT | neutral | persistent | linked to OR_MID_LINE | low-traffic |
| 7 | `OR_UPPER_EXTENSION_LINES` | `drawDay()` L2222–2226 | price-lane (line + label) | CONTEXT | bullish | persistent | implicit | OR_HIGH labels, INS_L, CHART_EVENT |
| 8 | `OR_LOWER_EXTENSION_LINES` | `drawDay()` L2228–2232 | price-lane (line + label) | CONTEXT | bearish | persistent | implicit | OR_LOW labels, INS_S, CHART_EVENT |
| 9 | `NATIVE_SIGNAL_MARKER` | `PaxOpeningRangeModule.addNativeSignalMarkerIndicator()` L491 | price-lane (Bookmap Indicator API) | TRADE_READ | LONG/SHORT (default white dot) | one-shot, cleared by Bookmap each poll | `gateNativeMarkersOnInstitutional` (default ON → suppressed) | shadows institutional pipeline |
| 10 | `INS_L_MARKER` | `drawInstitutionalMarker()` L2062 (PAY_FOR_TRADE / LONG) | price-lane (small text) | TRADE_READ | bullish (`PaxHeatwaveColors.BULL`) | durable in-deque (cap 30) | "Show Institutional Chart Events" | OR labels, LEVEL_EDGE_GLYPH, CHART_EVENT |
| 11 | `INS_S_MARKER` | `drawInstitutionalMarker()` L2062 (PAY_FOR_TRADE / SHORT) | price-lane (small text) | TRADE_READ | bearish (`PaxHeatwaveColors.BEAR`) | durable in-deque | same | same |
| 12 | `WATCH_LABEL` | `drawInstitutionalMarker()` L2094–2097 (WAIT_FOR_CONFIRM) | **price-lane (text)** | **DIAGNOSTIC** in price lane (mis-classed) | **unknown / yellow** | durable in-deque | "Show Institutional Chart Events" | OR labels, INS_L/INS_S, LEVEL_EDGE_GLYPH |
| 13 | `STND_LABEL` | `drawInstitutionalMarker()` L2081–2089 (STAND_DOWN base) | **price-lane (text)** | **DIAGNOSTIC** mis-classed | **unknown / orange** | durable | same | same |
| 14 | `ICE_LABEL` | `drawInstitutionalMarker()` L2082–2083 (STAND_DOWN / ICEBERG_DEFENSE) | **price-lane (text)** | **DIAGNOSTIC** mis-classed (orange) | unknown / orange | durable | same | same |
| 15 | `SPD_LABEL` | `drawInstitutionalMarker()` L2084–2085 (STAND_DOWN / SPOOF_STAND_DOWN) | **price-lane (text)** | **DIAGNOSTIC** mis-classed | unknown / orange | durable | same | same |
| 16 | `SCR_LABEL` | `drawInstitutionalMarker()` L2091–2092 (SCRATCH_READY) | **price-lane (text)** | **DIAGNOSTIC** mis-classed | unknown / gray | durable | same | same |
| 17 | `CHART_EVENT_TRAIL` | `addAllChartEvents()` L1983 + `drawInstitutionalChartEvent()` L2012 | price-lane (glyph badge ▲/▼/◉ + text) | RAW_EVENT (LOC sub-class) + TRADE_READ (AI / ENTRY sub-class) | LONG=green ▲ / SHORT=red ▼ / neutral=gray ◉ | durable in-deque (cap 50), FIFO; no time decay | "Show Institutional Chart Events" | OR labels, INS markers, LEVEL_EDGE_GLYPH |
| 18 | `LEGACY_TREND_TRIANGLES` | `addTrendTriangles()` L1859, `drawTrendTriangle()` L2125 | price-lane (font glyph ▲/▼) | TRADE_READ (deprecated) | bullish/bearish | bucket dedupped, cap 8 | hardcoded `showTrendTriangles = false` | none (disabled) |
| 19 | `HEATWAVE_BOX` | `addHeatwaveBox()` L2163 + `PaxHeatwavePainter.render()` | **HUD** (top-left, PIXEL_ZERO) | DIAGNOSTIC + summary TRADE_READ verdict | header verdict tone (BULL / BEAR / AMBER) | always renders; age tag goes STALE at 30 s but box stays | "Show Heatwave Quant Box" | none (HUD) |
| 20 | `LEVEL_EDGE_GLYPH` | `addLevelEdges()` L1738 + `PaxLevelEdgePainter.render()` | price-lane (mini-box anchored right of OR label) | TRADE_READ | LONG=green, SHORT=red, no STAND DOWN today | **per-fetch; disappears immediately when `model.hasActionable()` flips false** | **no toggle — hardcoded on at module attach (L767)** | OR labels, INS markers, CHART_EVENT |
| 21 | `SIGNAL_BADGE` | `addSignalStatus()` L2293 | HUD (top-left, alternate to Heatwave) | TRADE_READ (cross-market badge) | bullish/bearish/divergent/neutral | rebuilt per poll | only when `showHeatwaveBox = false` | none |
| 22 | `STATUS_BANNER` | `addStatus()` L2284 | HUD | DIAGNOSTIC | text-only | when called | n/a | none |

---

## Per-layer notes

### CONTEXT layers (1–8) — keep as-is
- Cyan / tomato / gold color encoding for High / Low / Mid is established trader vocabulary. Phase 2 does not change these colors.
- The four extension lines (`UPPER_EXTENSION_LINES`, `LOWER_EXTENSION_LINES`) share parent colors and use a thinner stroke (`ui.levelLineWidth = 2`), which already separates them from the OR lines visually.
- Labels live in the price-lane via `addLabel`. The label text is unbounded length (Section "Phase 2 concerns" below).

### NATIVE_SIGNAL_MARKER (9) — gated off in default config
- `gateNativeMarkersOnInstitutional = true` (default) suppresses Bookmap's native marker indicator. Layer stays wired for fallback debugging.
- Out of scope for Phase 2 visual changes. Leave behavior unchanged.

### INS markers (10–11) — keep, but reframe as a FILLED sub-badge
- `INS-L` (green) and `INS-S` (red) are the only entry markers with clear bull/bear color.
- In Phase 2 these become a small "FILLED" subtitle attached to the Layer C trade-read glyph rather than a standalone label, so they read as confirmation of the read instead of a separate trade signal.
- Phase 2 keeps them rendering for now (they do not cause the operator's chart-readability complaint); the FILLED reframing is a follow-up in slice UI-5.

### Status-code labels (12–16) — primary cleanup target
- **WATCH / STND / ICE / SPD / SCR** are DIAGNOSTIC (process state), not directional signals, but they currently render in the price lane in yellow / orange / gray.
- They satisfy none of the bull/bear language. In the operator screenshot they read as "another marker at the level" and dilute the actual entry signal.
- **Phase 2 removes them from the price lane.** They move to a Heatwave STATUS row (HUD), where the operator can scan process state without it competing for price-lane attention.

### CHART_EVENT_TRAIL (17) — TTL is missing
- The history (`PaxInstitutionalChartEventsHistory`) is append-only with FIFO eviction at 50 entries; the active-set history (`PaxAiChartEventsActiveHistory`) replaces wholesale per fetch.
- **The painter has no time-based filter.** A 90-second-old SWP marker renders identically to one that just fired.
- Collision bucket: `CHART_EVENT_COLLISION_TIME_MS = 5_000L` and `CHART_EVENT_COLLISION_PRICE_TICKS = 8` (`PaxOpeningRangeModule` L225–230). Same-bucket events collapse to the highest-priority one via `collapseChartEventsForDisplay`.
- **Phase 2 adds a 4-second TTL filter at draw time** so old raw events fade off the chart without changing the history schema. Newest event in a price band wins.

### LEGACY_TREND_TRIANGLES (18) — already off
- `showTrendTriangles = false` hard-coded in `PaxOpeningRangeUiSettings.java` L59. No action.

### HEATWAVE_BOX (19) — already persistent across stale states
- `PaxHeatwavePainter.render(...)` returns a non-null `PreparedImage` for every state including `NO_DATA` / `BRIDGE_OFFLINE` / `DASHBOARD_OFFLINE` (`PaxHeatwaveModel.noData(...)`).
- `addHeatwaveBox` always calls render; the box does not vanish on stale.
- The age word "STALE" appears in the header at 30 s (`STALE_AGE_MS`). The operator's "disappears too fast" complaint maps to LEVEL_EDGE_GLYPH (layer 20), not Heatwave.
- **Phase 2 leaves Heatwave behavior unchanged.** Pin / hotkey / auto-slide are deferred (need module-level AWT keybinding work; out of scope this slice).

### LEVEL_EDGE_GLYPH (20) — the disappearing trade read
- `PaxOpeningRangeModule.addLevelEdges()` L1742–1743 short-circuits when `!model.hasActionable()`. When the live row flips non-actionable (level moved out of proximity, confidence dipped under 0.35, thesis gate flipped to NONE), the glyph vanishes on the next 1 s poll.
- The painter has no STAND_DOWN render path: `PaxLevelEdgePainter.render()` returns `null` for any non-actionable or WAIT row.
- **Phase 2:**
  - Add `PaxLevelEdgePainter.renderStandDown(reason, fontSize)` so blocked Layer C state has a visual (gray shield + reason).
  - Add a min-visible cache in `addLevelEdges` keyed by `label`: once an actionable row renders, hold it for at least **15 s** even if the model goes non-actionable; only fade after 15 s of consecutive non-actionable.
  - Visually dominate: heavier border, direction shape prefix (▲ / ▼ / shield), 2× current glyph footprint.
  - No payload changes — uses existing `blocked_reason`, `level_relevant` fields already in `PaxLevelEdgeModel.Row`.

### SIGNAL_BADGE (21) — alternate path, not in current operator setup
- Only renders when `showHeatwaveBox = false`. Default config has Heatwave on, so SIGNAL_BADGE is dormant.
- Out of scope for Phase 2.

---

## Collision risks (the operator's complaint, in code)

The price lane band at OR-H / OR-L can simultaneously hold:

1. `OR_HIGH_LABEL` (Layer 2) at `(lineEnd, OR-H)` via `addLabel` L2275
2. `LEVEL_EDGE_GLYPH` (Layer 20) at `(now, OR-H)` offset 60 px right via `addLevelEdges` L1738
3. `INS_L_MARKER` (Layer 10) at `(fill_ts, OR-H)` offset 2 ticks via `drawInstitutionalMarker` L2062
4. `CHART_EVENT_TRAIL` ACC-L badge (Layer 17) at `(event_ts, OR-H + offset)` via `drawInstitutionalChartEvent` L2012
5. `WATCH_LABEL` / `STND_LABEL` / `SCR_LABEL` (Layers 12–16) at the same price band

All five are price-anchored shapes with overlapping `(price, time)` windows. The collision bucket
(`chartEventCollisionKey` L2414, 5 s × 8 ticks) only deduplicates among CHART_EVENT_TRAIL members
within itself — it does not check across layer types. The Premarket audit observed "4 OR-L breaks
in 22 s" → 4 stacked SHORT-band markers within a single visual lane.

Phase 2 attacks this collision by:
- Removing status-code labels (Layers 12–16) from the price lane entirely.
- Adding a 4 s TTL filter to CHART_EVENT_TRAIL so stale events drop out.
- Making LEVEL_EDGE_GLYPH visually dominant (heavier border, direction shape, larger footprint) so the operator can identify the trade read in a crowded band.
- A min-visible cache so LEVEL_EDGE_GLYPH does not flash for one poll and disappear.

---

## Phase 2 scope summary

In-scope, this slice:
1. New `PaxChartLayerRegistry` — single source of truth for layer IDs + categories. Pure data class. Driven by tests for uniqueness + category coverage.
2. New `PaxChartPalette` — closed color vocabulary (green `#3CDC7D`, red `#EB6464`, gray `#9C9C9C`, yellow severity outline `#FFD040`).
3. `PaxLevelEdgePainter.renderStandDown(...)` — new code path for STAND DOWN state with gray shield.
4. `PaxLevelEdgePainter` direction prefix + bordered dominant treatment.
5. `PaxOpeningRangeModule.addLevelEdges` — 15 s min-visible cache so the glyph holds after a single non-actionable poll.
6. `PaxOpeningRangeModule.drawInstitutionalMarker` — skip WATCH / STND / ICE / SPD / SCR labels from the price lane (status codes move out).
7. `PaxOpeningRangeModule.addAllChartEvents` — 4 s TTL filter at draw time. History schema unchanged.
8. Tests for each change.
9. Build only — no jar deploy.

Out of scope, deferred:
- Hotkey `P` / `Shift+P` pin + screenshot mode (needs module-level AWT keybinding; non-trivial Bookmap integration).
- Right-click context menu pin.
- Auto-slide when overlapping `book.mid` (geometry calc + book.mid feed across painter boundary).
- The "FILLED" sub-badge reframe for INS-L / INS-S (would couple level-edge and institutional pipelines; safer in its own slice).
- Heatwave 6-row compression (no operator-blocking issue today).
- Slice UI-1 layer registry usage inside the painter (registry built in this slice; consumed in a follow-up).

Hard constraints honored:
- No edits to `level_edge.py`, `edge_calculus.py`, or JSONL log schema.
- No new signal sources.
- No threshold tuning.
- No payload-shape changes.

---

## Files in scope this slice

New:
- `indicators/OpenRange/src/main/java/com/openrange/PaxChartLayerRegistry.java`
- `indicators/OpenRange/src/main/java/com/openrange/PaxChartPalette.java`
- `indicators/OpenRange/src/test/java/com/openrange/PaxChartLayerRegistryTest.java`
- `indicators/OpenRange/src/test/java/com/openrange/PaxChartPaletteTest.java`
- `indicators/OpenRange/src/test/java/com/openrange/PaxLevelEdgeMinVisibleTest.java`
- `indicators/OpenRange/src/test/java/com/openrange/PaxChartEventTtlTest.java`

Modified:
- `indicators/OpenRange/src/main/java/com/openrange/PaxLevelEdgePainter.java`
  (add `renderStandDown`, direction prefix, bordered dominant treatment)
- `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
  (`addLevelEdges` min-visible cache, `drawInstitutionalMarker` skip-status-codes, `addAllChartEvents` TTL filter)
- `indicators/OpenRange/src/test/java/com/openrange/PaxLevelEdgePainterTest.java`
  (extend with STAND DOWN render test, direction prefix test)
- `indicators/OpenRange/build.ps1`
  (register new test class names)

Untouched (by intent):
- `level_edge.py` / `level_edge_log.py` / `level_edge_report.py`
- `edge_calculus.py`
- `PaxLevelEdgeFetcher.java` / `PaxLevelEdgeSnapshotParser.java` / `PaxLevelEdgeModel.java`
- `PaxHeatwave*.java` (Heatwave already persistent on stale)
- All `PaxInstitutional*ChartEvent*History*.java` (history schema unchanged; TTL is a draw-time filter)
