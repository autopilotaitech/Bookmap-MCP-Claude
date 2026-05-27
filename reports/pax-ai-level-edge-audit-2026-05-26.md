# Pax AI Level-Edge Audit (2026-05-26)

Owner: Will
Scope: per-level edge math, the existing `/api/pax/level/<label>` route, and
the OpenRange chart overlay. Question being answered: can this code put a
real expected-edge number on the chart at each OR / extension level, and
if so, what is the smallest reversible path.

Source reads: `pax-ai/pax_ai/edge_calculus.py`, `pax-ai/pax_ai/server.py`,
`pax-ai/pax_ai/config.py`, `pax-ai/pax_ai/playbook.py`,
`mcp-server/bookmap_mcp/dashboard.py`, `pax-ai/tests/test_edge_calculus*.py`,
`indicators/OpenRange/src/main/java/com/openrange/PaxHeatwave*.java`,
`PaxTrendTriangle*`, `PaxOpeningRangeModule.java`.

---

## 1. Current Edge Math

Function-by-function read of `edge_calculus.py`:

### What is real

- `invalidation_price` (lines 192-208). Deterministic. FOLLOW long stop =
  `orLow - 1 tick`; FOLLOW short stop = `orHigh + 1 tick`; FADE stops are
  1 tick beyond the touched boundary. **Executable.** A trader can route
  to it.
- `payline_price` (lines 211-223). Entry +/- a per-product constant
  (10.0 pts NQ, 2.5 pts ES, from `config.py:43-46`). **Executable.** It is
  not "an alpha" - it is a fixed ladder rung.
- `rung1_price` (lines 226-238). Entry +/- 65 pts NQ / 15 pts ES rung.
  Same shape as payline. **Executable.**
- `max_heat_pts` (lines 176-189). FADE -> 1 tick. FOLLOW -> `or_width + 1
  tick`. Sensible heuristic. Not anchored to realized vol / ATR; the
  missing-OR fallback is a hard-coded `8.0` pts. **Usable but not measured.**
- `size_tier` (lines 88-104). Threshold buckets on `confidence`
  (FULL >= 0.50, HALF >= 0.35, else NONE). **Honest bucketing, no edge
  claim by itself.**

### What is not real

- **`expected_R` (lines 134-144) is `confidence * constant_lookup`. It is
  NOT expected value.** No probability, no payoff vs. loss tradeoff, no
  feedback against realized outcomes. The function body:

  ```python
  base = directional_r(composite_dir, regime, level_kind)
  return _clamp(confidence * base, 0.0, base)
  ```

  If a trader plots `expected_R` next to a level, they are plotting
  `confidence * 1.8` (or 1.6 / 2.4 depending on the lookup row). The curve
  is identical to `confidence` scaled by a constant. There is no extra
  information.

- `directional_r` (lines 111-131) reads `config.directional_R_table`. The
  table is hand-tuned (`config.py:51-71`). The comment says "calibrated
  median R-multiples observed historically" - **there is no calibration
  artifact in the repo. No backtest, no sample size, no time window.**

- `prob_pay_for_trade` (lines 151-158) is the linear transform
  `0.30 + 0.50 * confidence`, clipped to `[0.30, 0.80]`. **Not a calibrated
  probability.** Same confidence input as `expected_R`. It does not
  decorrelate from confidence.

- `prob_reach_next_rung` (lines 161-169) is `0.10 + 0.40 * confidence *
  regime_confidence`, clipped to `[0.10, 0.50]`. Same problem. Two inputs
  multiplied; output is monotonic in confidence.

- `level_edge` (lines 268-344) only reads structural fields:
  `level.label`, `level.decision`, `level.confidence`, `flow.regime`,
  `flow.regimeConfidence`, `or_levels.orHigh / orLow / orWidthPts`. It
  does NOT read the order-book microstructure (`pull_stack`, `tape`,
  `lt_liquidity`, `micro_events`, `vwap_bias.sigma_z`). Those are already
  baked into `composite.confidence` upstream in `dashboard.py`, but
  `level_edge` itself is just structure on top of a single scalar.

### Tests don't validate edge

`test_edge_calculus.py` and `test_edge_calculus_thesis.py` pin
formula behavior (clipping, monotonicity, struct keys, thesis gating).
**No test checks that `expected_R` predicts the next 60s / 300s move at
the level.** No realized-outcome data is referenced.

### Verdict

The honest summary:

| Field                   | Status                                              |
|-------------------------|-----------------------------------------------------|
| `expected_R`            | confidence x constant. **Misnamed. Not EV.**        |
| `prob_pay_for_trade`    | linear transform of confidence. Not calibrated.     |
| `prob_reach_next_rung`  | confidence x regime_confidence. Not calibrated.     |
| `max_heat_pts`          | heuristic. Usable; not vol-anchored.                |
| `invalidation_price`    | OR boundary +/- 1 tick. Real and executable.        |
| `payline_price`         | entry +/- 10 pts NQ. Real ladder, not alpha.        |
| `rung1_price`           | entry +/- 65 pts NQ. Real ladder.                   |
| `size_tier`             | threshold bucket on confidence. Honest bucketing.   |

There is no Jane-Street-style alpha in this file. Pretending otherwise
will mislead the operator. There is however a usable trade-structure
ladder (entry / stop / payline / next rung) and a confidence-derived
ranking we can plot honestly, IF we relabel.

---

## 2. Problems / Fake-Edge Risks

What will trip the operator if we render the current numbers as-is:

1. **The `expected_R` label implies EV when it isn't.** Plotting `1.44 R`
   next to OR-H suggests the system has measured that 1.44 R is the
   forward-looking expected return. It hasn't - that is just
   `0.80 * 1.8`. Rename or relabel.
2. **Two probability fields that aren't probabilities.** Showing
   `prob_pay 0.70 / prob_rung 0.32` invites the operator to size as if
   those were Kelly inputs. They are linear scaling of the same
   confidence. Either hide them or relabel as "confidence band, not
   measured probability".
3. **Directional-R table is hand-tuned without a date stamp.** Drift over
   time is invisible. If we display R values from this table, they
   should be tagged "config, not measured" at minimum.
4. **No falsification path today.** Even the honest numbers
   (invalidation, payline, size_tier) are never logged against what
   actually happened next. If `confidence` is good for nothing, we have
   no way to prove it.
5. **No order-flow input feeds the edge math directly.** The microstructure
   features (sweep, absorption, pull/stack, sigma_z) are all in the
   snapshot but `level_edge` does not read them. They reach `confidence`
   via the composite engine in `dashboard.py`, but at the level-card layer
   they are a single scalar. **Information has been crushed before
   `edge_calculus` runs.**
6. **Plot space could mislead.** A green "+1.44 R" next to a level looks
   actionable even when the underlying confidence is 0.40 and the
   regime is WARMUP. The plot must downgrade to gray when not eligible.

We will not invent EV math we don't have. The plot will show only what
is honest, named for what it is, and logged so the operator can decide.

---

## 3. Level Plot Contract

For each level returned by the snapshot (OR-H, OR-L, +1..+N, -1..-N),
the plot displays a small chart-anchored text block. Fields, in order:

```
LBL  : OR-H   (4-char label, fixed-width)
DIR  : LONG | SHORT | WAIT
CONF : 0.62                    (composite confidence, 0-1, 2dp)
R    : 1.44                    (confidence x directional_R; labeled as score, NOT EV)
STOP : 20111.50                (invalidation price; blank if WAIT)
TIER : FULL | HALF | -         (size tier from composite confidence)
```

Six lines, total ~14 chars wide, monospaced. Anchored at
`(eventMs_of_now, level.price)` in chart space, offset 2-3 px right of
the existing OR line endpoint so it floats with the level.

### Color rules

- Green: `DIR=LONG` AND `actionable` (see below).
- Red:   `DIR=SHORT` AND `actionable`.
- Gray:  `DIR=WAIT` OR NOT actionable.

`actionable` is computed at the Python side, NOT inferred client-side:

```
actionable = (
    size_tier in {"HALF", "FULL"}
    AND confidence >= 0.35
    AND composite_dir in {"FOLLOW_LONG","FOLLOW_SHORT","FADE_LONG","FADE_SHORT"}
    AND anchorMode == "LIVE"
    AND news.blocked is False
    AND health == "ok"
)
```

If any of those is false, `direction = "WAIT"`, color = gray, STOP is
blank, R is shown but de-emphasized. The hard gates (anchorMode, news,
health) suppress the whole plot - render only a single line "BRIDGE
OFFLINE" or "STALE" or "NEWS BLACKOUT" in the OR-line area.

### Honesty rule

The on-chart `R` label is suffixed `~` or rendered in lower
contrast and the tooltip / nearby legend says "score, not measured EV".
We do not draw `prob_pay` / `prob_rung` on the chart at all.

---

## 4. Endpoint Contract

`GET /api/pax/level/<label>` exists and returns 14 fields including
`edge_calculus{...}`. It is per-label - the chart would need to poll 7-15
times per tick to cover all active levels. That is wrong shape for the
overlay.

Proposed single new endpoint:

```
GET /api/pax/levels/edge
```

Composes once per snapshot. No per-label query.

Response:

```json
{
  "alias":     "NQM6.CME@RITHMIC",
  "asOfMs":    1748277123456,
  "ageMs":     412,
  "stale":     false,
  "mid":       20104.25,
  "anchorMode": "LIVE",
  "blocked":   {"news": false, "session": false, "health_ok": true},
  "levels": [
    {
      "label":       "OR-H",
      "price":       20112.00,
      "side":        "high",
      "distance":    7.75,
      "proximity":   true,
      "direction":   "LONG",
      "composite_dir": "FOLLOW_LONG",
      "confidence":  0.62,
      "score_R":     1.44,
      "stop_price":  20087.75,
      "size_tier":   "HALF",
      "actionable":  true,
      "color_hint":  "positive",
      "reasons":     ["composite=FOLLOW_LONG", "thesis=WAIT_FOR_CONFIRM"]
    },
    ...
  ]
}
```

Fields:

- `score_R` not `expected_R`. The label states what it is.
- `stop_price` not `invalidation_price`. Shorter, traders speak this.
- `color_hint` computed server-side from the gate set. The Java overlay
  does not redo the logic.
- `prob_pay_for_trade` and `prob_reach_next_rung` are **omitted** from
  this endpoint. They live on the per-label endpoint for any text
  consumer that wants them; the chart does not get them.
- `reasons` is a short list (top 2-3) for tooltip / log; not rendered as
  prose on the chart.

The endpoint is composition of existing `edge_calculus.level_edge(level,
snap)` over `snap["or_levels"]["levels"]` plus the gate logic above. No
new math.

The existing `/api/pax/level/<label>` endpoint stays untouched so the
current UI clients (index.html) keep working.

---

## 5. Chart Rendering Plan

Three render-target choices were considered:

| Option                                         | LOC est. | Risk        |
|------------------------------------------------|----------|-------------|
| Extend `PaxHeatwave` model + painter           | ~50-80   | crowds box  |
| New `PaxLevelEdgePainter` (TrendTriangle pattern) | ~100  | one extra painter, isolated |
| Piggyback on existing OR-line drawing in `PaxOpeningRangeModule.drawDay()` | ~60-80 | small touch in a hot path |

**Choice: new `PaxLevelEdgePainter` + new `PaxLevelEdgeFetcher` mirroring
the `PaxHeatwave*` pair.** Reasons:

- Isolation. The chart overlay is the most visible piece; bundling it
  with `PaxHeatwave` mixes screen-space and chart-space rendering and
  invites threading mistakes.
- Threading rule stays clean. Fetcher thread sets
  `AtomicBoolean levelEdgeDirty = true` (mirror of `heatwaveDirty` at
  `PaxOpeningRangeModule:297`). Painter mutation only on Bookmap
  callback (`InstrumentState.shouldRepaint(eventTime)` consumes the
  flag).
- Chart-space anchoring via the existing `CompositeCoordinate` pattern
  already used by `PaxTrendTrianglePainter` (`PaxChartTimeCoords.
  epochMsToChartNanos` for x, `price / tickSize` for y).
- Off-screen `BufferedImage` -> `PreparedImage` -> `CanvasIcon`. Same
  pattern as `PaxHeatwavePainter.render()`.

Smallest concrete shape:

1. `PaxLevelEdgeModel` (immutable record): `alias`, `asOfMs`, `mid`,
   `anchorMode`, `blocked`, `List<LevelRow>` where each `LevelRow` is
   `label / price / direction / confidence / scoreR / stopPrice /
   sizeTier / actionable / colorHint`.
2. `PaxLevelEdgeSnapshotParser`: same recursive-descent JSON parsing
   shape as `PaxHeatwaveSnapshotParser`. Defaults `actionable = false`
   when missing for safety. ~120 lines.
3. `PaxLevelEdgeFetcher`: HttpClient against
   `http://127.0.0.1:18888/api/pax/levels/edge` at 1000 ms, backoff
   after 3 fails. ~150 lines. Mirror of `PaxHeatwaveFetcher`.
4. `PaxLevelEdgePainter.render(model, fontSize, tickSize) ->
   List<DrawnIcon>`: one small text glyph per level, anchored at
   `(now_nanos, level.price)`. Six lines monospaced, color by
   `colorHint`. ~180 lines.
5. Wire-up in `PaxOpeningRangeModule`: new
   `levelEdgeDirty` AtomicBoolean, fetcher constructed with callback,
   painter invoked from the same Bookmap-callback path that consumes
   `heatwaveDirty`. ~30 lines edit.

Total Java: ~480 LOC new, ~30 LOC edited. One file edited
(`PaxOpeningRangeModule.java`), four files new under `com.openrange`.

Build / deploy: `indicators/OpenRange/build.ps1` writes
`openrange-release.jar`. Bookmap holds it open while running, so
**Bookmap must be closed before each rebuild**. This is the same
constraint the existing chart additions live with.

Operator preference: chart-space, anchored to the level line. Floats
with pan/zoom. The Heatwave box stays in the screen-space top-left for
the system-wide read.

---

## 6. Falsification Log Plan

The point: prove or disprove that `score_R` / `confidence` / `direction`
at a level predicts the next 60 s and 300 s mid-price move.

### Trigger

A row is appended **only when a level transitions
`actionable = false -> true`** (rising edge). Re-arming requires a
`true -> false -> true` cycle. No throttling beyond that - the dashboard
already debounces composite output, and the actionable flag is
deterministic.

### Append-shape

Single JSONL file, append-only, one row per event. Path:

```
%LOCALAPPDATA%/pax-ai/level-edge-log/<YYYY-MM-DD>.jsonl
```

Row:

```json
{"ts_ms": 1748277123456, "alias": "NQM6.CME@RITHMIC",
 "label": "OR-H", "level_price": 20112.00, "mid_at_signal": 20104.25,
 "direction": "LONG", "composite_dir": "FOLLOW_LONG",
 "confidence": 0.62, "score_R": 1.44, "stop_price": 20087.75,
 "size_tier": "HALF", "anchorMode": "LIVE",
 "mid_at_plus_60s": null, "mid_at_plus_300s": null,
 "realized_R_60s": null, "realized_R_300s": null}
```

### Backfill

The same writer maintains a small in-memory list of "open events" -
those whose 60s / 300s backfill is not yet done. On every new snapshot:

- Walk open events. If `now >= ts_ms + 60_000` and `mid_at_plus_60s` is
  null, set it to `snap.book.mid` and compute `realized_R_60s` as
  `(mid_60s - mid_at_signal) / abs(mid_at_signal - stop_price)` signed
  by direction. Same for 300s.
- When both backfills are filled, the event is "closed" - rewrite the
  row in place is overkill; instead, write a closing row in
  `<date>.closed.jsonl` and drop from memory. Two-file approach keeps
  the writer append-only and simple.

### Reader

A single CLI script `pax-ai/pax_ai/level_edge_report.py`:

```
python -m pax_ai.level_edge_report --date 2026-05-26
```

Reads the closed JSONL, groups by `(direction, size_tier)`, prints
mean / median realized R at 60s and 300s, hit rate (positive R count /
total), and per-confidence-bucket breakdown (0.35-0.50 / 0.50-0.65 /
0.65+). Plain text output. No HTML, no DB, no UI panel.

### What this is NOT

- Not a research framework.
- Not a calibration pipeline.
- Not a tuning loop.
- Not a SQLite schema.

It is **one JSONL file per day + one stdout report**. The operator reads
the report once after the session and decides whether the plot is
predicting anything. If yes, we keep it. If no, the plot comes off the
chart.

---

## 7. Implementation Slices

Ordered. Each is small, reversible, and stands on its own.

### Slice A - Python aggregate endpoint + tests (NO Java, NO Claude)

- New: `pax-ai/pax_ai/level_edge.py`. Pure function
  `compute_level_edge_payload(snap, as_of_ms, age_ms, stale_threshold_ms,
  now_ms=None) -> dict`. Composes per-level cards from
  `edge_calculus.level_edge`, applies the gate set, sets `direction`,
  `color_hint`, `actionable`.
- Edit: `server.py`. Add `_api_pax_levels_edge` handler + dispatch on
  `GET /api/pax/levels/edge`.
- New: `pax-ai/tests/test_level_edge.py`. Pin: closed vocab on
  `direction` and `color_hint`; gates downgrade to WAIT/gray; missing
  optional thesis/sigma/micro does not gate; `score_R` field is named
  `score_R` not `expected_R`; `prob_pay` / `prob_rung` are absent.

Estimated: ~150 LOC new + ~20 LOC edit + ~250 LOC tests. One commit.

### Slice B - Falsification log (Python only)

- New: `pax-ai/pax_ai/level_edge_log.py`. Two functions:
  `record_rising_edges(payload)` (append open events when level
  transitions false->true), and `backfill(snap)` (close events when
  mid is available at +60 s / +300 s).
- Edit: `server.py` route `_api_pax_levels_edge` to call `record_rising_edges` AND `backfill` on each request. This keeps
  the writer driven by client polling (no new thread).
- New: `pax-ai/pax_ai/level_edge_report.py`. CLI summarizer.
- New: `pax-ai/tests/test_level_edge_log.py`. Pin rising-edge dedup,
  backfill math, closed-row shape.

Estimated: ~250 LOC new + ~10 LOC edit + ~300 LOC tests. One commit.

### Slice C - Manual inspection

No code. Operator workflow:

```
curl http://127.0.0.1:18891/api/pax/levels/edge
type "%LOCALAPPDATA%\pax-ai\level-edge-log\2026-05-26.jsonl"
python -m pax_ai.level_edge_report --date 2026-05-26
```

Sit with it for one session. Decide whether the Python side is
producing values that the operator would have used on the chart.

### Slice D - Java chart overlay (only after C is positive)

- New: `PaxLevelEdgeModel.java`
- New: `PaxLevelEdgeSnapshotParser.java`
- New: `PaxLevelEdgeFetcher.java`
- New: `PaxLevelEdgePainter.java`
- Edit: `PaxOpeningRangeModule.java` (one AtomicBoolean, one fetcher
  construction, one painter invocation in the existing
  `consumeHeatwaveDirty()` pattern).

Estimated: ~480 LOC new + ~30 LOC edit. One commit. Requires Bookmap
close + rebuild + restart cycle.

### Slice E - Cleanup of `expected_R` naming on the existing endpoint
(optional)

Only if Slice C confirms the operator finds the relabel useful. Rename
`expected_R` -> `score_R` in the `/api/pax/level/<label>` payload too,
and add a `score_R_disclaimer` field. Backward-compat shim keeps
`expected_R` for one release cycle. Touch radius: server.py +
edge_calculus.py + UI client.

A, B, C must clear before D. D requires C to have been positive (i.e.
the report shows the plot is tracking something). E is gated on
operator preference, not on technical results.

---

## 8. Stop Conditions

Pull the plot if any of these hold after one session:

- Realized-R distribution at +60s is statistically indistinguishable
  from zero (Wilcoxon p > 0.20 over >= 20 rising-edge events). Pax is
  not predicting forward movement.
- The chart text is unreadable - too small, overlapping with OR lines,
  too much per-level clutter. The operator turns it off.
- The fetcher thread or painter introduces a measurable hitch (Bookmap
  paint frame drop) during chart pan/zoom.
- The JSONL log is empty or near-empty - either no actionable signals
  fired (composite confidence stuck low) or the writer is broken.
- The operator says "I'm reading the heatwave box, not the level text"
  - then we wasted the chart real estate and the screen-space box was
  the right call after all.
- The Python aggregate endpoint takes more than 50 ms per call (it
  shouldn't; it's pure math over <=15 levels). Latency means we're
  doing the wrong thing.

If any of these hits, we revert Slice D (Java) immediately and decide
whether the JSONL log alone is worth keeping.

---

## 9. Do Not Touch

These are off the table for this work. They were either intentionally
deferred or are out of scope.

- Claude / LLM path: `prompts.py`, `chat.py`, `claude_stream.py`,
  `bus_digest.py`, the system prompt file, `--tools ""` /
  `--max-turns 1` invariants. No change.
- Self-training / research stack: `feature_bus.py`, `outcomes.py`,
  `pax_bus_replay.py`, `pax_bus_tune.py`, `pax_bus_eod.py`,
  `pax_bus_prune.py`, `pax_calibration.py`, `pax_policy_replay.py`,
  `pax_research_claude.py`, `pax_research_preflight.py`. The
  falsification JSONL is NOT a feature-bus replacement; it stands
  alone and ignores the existing stack.
- Promotion gates: no auto-application of any score back into
  `pax_weights.json`. The operator reads the report by hand.
- `pax_weights.json` tuning: no change. The composite weights stay where
  they are.
- Existing dashboard composer (`dashboard.py`): no schema change. The
  snapshot stays as-is. The aggregate endpoint reads from it.
- Existing `/api/pax/verdict/instant`: stays as the safety wrapper.
  Not the home of edge math. Don't merge the two endpoints.
- Existing `/api/pax/level/<label>`: stays for the index.html UI. The
  new aggregate endpoint is additive.
- Java OpenRange threading invariant: fetcher writes AtomicBoolean
  only; canvas mutates on Bookmap-callback only. Pinned by every
  existing painter; the new painter must follow the same rule.
- OpenRange jar filename (`openrange-release.jar`): no rename. Builds
  still require Bookmap to be closed.
- Auto-fire / trigger_engine: not in scope. The plot is a read-only
  display.
- Per-magnet sync to bridge (`_sync_magnet_levels`): not touched.

---

## Bottom line

The existing `edge_calculus` ladder (entry / stop / payline / next rung)
is real and usable. The `expected_R` / `prob_pay_for_trade` /
`prob_reach_next_rung` fields are not edge - they are linear scalings
of one confidence number. We plot the honest parts (direction, stop,
size tier) and rename the scaled-confidence number `score_R` with no
EV claim. We log every actionable rising edge to a single JSONL file
per day with +60 s and +300 s mid-backfill and a one-CLI report. After
one session, the report either shows the score tracks forward movement
or it doesn't. If it doesn't, the chart text comes off and we have
learned something real about the composite engine without having shipped
more framework.

Slice A (Python aggregate endpoint) and Slice B (JSONL log) can ship
today without any Java change. Slice D (the chart overlay) ships only
if Slice C says the numbers are worth looking at.
