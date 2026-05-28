# Institutional Flow Tracker -- Design Spec

Date: 2026-05-27 (drafted during NQ ETH session)
Status: DRAFT FOR OPERATOR RED-PEN. No code exists until this is signed off.

## 1. Purpose and non-goals

**Purpose.** A continuous, deterministic regime aggregator that synthesizes
existing book/tape primitives into one read: ACCUMULATION / DISTRIBUTION /
BALANCED / TRANSITION, with conviction, duration, drivers, and per-level
stance. Plus one on-chart screen-space box displaying it, sibling to the
existing Heatwave Quant Box.

**Why.** Per-rung composite scoring today is proximity-gated -- it only
produces meaningful direction/confidence when price is near a rung. For the
2.5 hours tonight when price was 30-100 pts past OR-L drifting toward -2/-3,
the system effectively went silent because nothing was "near a level." The
operator's read: institutional flow must be tracked CONTINUOUSLY between
levels, not only when price arrives at one, so the read at a level approach
is informed by what the book has been doing for the last N minutes.

**Non-goals.**
- No order placement. No paper-trade decisions. Plotting signals only.
- No browser dashboard changes. The browser UI is liked and stays.
- No replacement of the LLM-emitted `pax_ai_chart_events` stream in this
  iteration.
- No re-encoding of entry/stop/target rules from section 4. This spec is the
  tracker only.
- No `dashboard.py` refactor beyond a single 3-line addition.

## 2. What already exists (do not re-build)

Two chart-event channels exist today, both consumed by the OpenRange Java
addon and rendered on the Bookmap chart:

1. `snap["pax_ai_chart_events"]` -- LLM-emitted Pax AI chart signals. Source:
   `pax-ai/pax_ai/ai_chart_signal_store.py:43-71` appends to
   `D:\BookmapLogs\pax-ai-chart-signals.jsonl` after shape-validation in
   `pax-ai/pax_ai/ai_chart_signal.py:91-141`. Polled on each `/api/snapshot`
   by `mcp-server/bookmap_mcp/pax_ai_chart_events.py:245-265`. Consumed by
   `indicators/OpenRange/src/main/java/com/openrange/PaxAiChartEventsActiveHistory.java:49-61`.
   See section 7 -- this stream is the cause of the inconsistency the
   operator described.

2. `snap["institutional_chart_events"]` -- deterministic, dashboard-computed
   evidence markers (SPOOF_RISK, ICEBERG_DEFENSE, ABSORPTION, ACCEPTANCE,
   REJECTION, PULL, STACK, WATCH, TOUCHED). Research-grounded per the prior
   spec `docs/superpowers/specs/institutional-chart-markers.md` (CME Rule
   575, Cont/Kukanov OFI, Zotikov/Antonov iceberg detection). Already fires
   today.

Continuous per-tick primitives in `/api/snapshot` (all live, verified in
captured snapshots tonight): `pull_stack`, `lt_liquidity`, `flow.ofi`,
`flow.cvdDeltaZ`, `flow.vptAbsorption`, `flow.biasScore`,
`flow.biasTrajectory`, `flow.regime`, `flow.vwapSlope`, `momentum.i10/50/200`,
`volume_profile`, `vwap_obj`, `or_levels.levels[]`, `trend_analyzer`,
`conviction`, `micro_events.events[]`. None need rebuilding.

## 3. The gap this spec fills

We have a rich evidence layer (institutional_chart_events) and a noisy LLM
narration layer (pax_ai_chart_events). What is missing is a
**continuous regime aggregator** that synthesizes the evidence into one
plain-language read of the current state and exposes a per-level stance the
trader can act on. Today the operator must read the evidence markers
individually and form their own gestalt. We can compute the gestalt.

## 4. Pax Group OR strategy invariants (research, cited)

Distilled from Lane 1 web research and the operator's historical
`pax-agent-signals-*.csv`.

**Operator's actual setup mix (4 CSV days, ~1763 rows):**

| Decision | Count |
|---|---:|
| WAIT | 855 |
| STAND_DOWN | 729 |
| ENTER_LONG_FADE | 152 |
| ENTER_SHORT_FADE | 20 |
| ENTER_SHORT_FOLLOW | 5 |
| ENTER_LONG_FOLLOW | 1 |

FADE setups outnumber FOLLOW setups roughly 25 to 1. The operator does NOT
predominantly trade orthodox Pax break-with; they trade fades at extensions
where Pax would scale out. The tracker must optimize for the FADE case --
i.e. correctly identifying when an upward-extension test is exhaustion vs
continuation -- not just the orthodox break.

**Strategy facts from public Pax sources:**

| Item | Pax-stated rule | Source |
|---|---|---|
| Entry side | Above OR-H for longs, below OR-L for shorts. DOM-staged, 3-4 contracts split into thirds/quarters. | thepaxgroup.org/the-opening-range/, nexusfi.com/showthread.php?t=56281 |
| Commit signal | "Large increase in traded volume" during algo OR establishment; "sustained bids above / offers below." No specific volume or time threshold published. | thepaxgroup.org |
| Rung stride | NQ = 65 pts, ES = 15 pts. | ninjatraderecosystem.com PAX30OpeningRange listing, github.com/wallstwillie/The-Pax-Group-Superdom |
| First target | 4 pts (ES) used to "pay for the trade"; tiered scale-out at proprietary daily targets. | thepaxgroup.org |
| Stop | Scratch stop = entry price (OR-H or OR-L). Breakeven stop after first 4 pts paid. No wider dollar stop published. | thepaxgroup.org |
| Re-entry | Permissive. "If price comes back to OR and stops us out for scratch and moves back out we can always put the trade back on." No cap. | thepaxgroup.org |
| Sit | Only explicit Pax rule: "While price is inside the OR we do not want a position on." | thepaxgroup.org |

**Gaps in published Pax material:** specific commit threshold, rotation
trigger (commit then 1st ext vs reverse), re-entry cap, chop-window sit-out,
post-loss cool-down. All these are operator-added discipline, not Pax core.
Section 13 asks the operator to fill them in.

## 5. Dead / stuck in the existing signal path

From Lane 2 audit, cited:

| Item | File:line | State | Recommendation |
|---|---|---|---|
| `_source_anchored_vwap_opening_drive` | dashboard.py:3957-3967 | reliability hardlocked to 0; bridge never ships `flow.avwap` | CUT: remove from `_CONVICTION_SOURCES`, remove weight 0.08 from pax_weights.json. 10 lines deleted, zero behavior change. |
| `_source_ib_context` | dashboard.py:3991-4023 | reliability=0 unless bridge sends `ibComplete=true`, which it does not today. IB (Initial Balance) = first 60 minutes of RTH, a different concept from Pax OR and not part of the operator's strategy. | **CUT.** Operator confirmed 2026-05-27 they did not know what it was. Pax OR is the strategy; IB is unrelated auction-theory machinery added without justification. Remove from `_CONVICTION_SOURCES` + weights JSON. |
| Trajectory modulation V8/V9/V10 | bias_score: dashboard.py:3582-3583. level_reaction: dashboard.py:3926-3927. conviction_at_level: V8 path. | Active, no off-switch, no A/B evidence of lift. | LEAVE for this iteration. Revisit after the tracker measurement loop has data to argue with. |

## 6. Critical architectural finding

The chart popup the operator saw at 19:45 CT tonight (`v SHORT 0.42 R~0.42`,
setup label "OR_BREAK", components `Pull + Orderbook + Tape`, STOP 30127.00)
is an LLM-emitted Pax AI chart signal. Trace:

- Pax AI's chat-handling Claude call emits a `<<PAX_AI_CHART_SIGNAL>>` block
  in its response.
- `pax-ai/pax_ai/ai_chart_signal.py:91-141` validates only the SHAPE: action
  enum is one of `{PAY_FOR_TRADE, WAIT_FOR_CONFIRM, STAND_DOWN, SCRATCH_READY}`,
  direction is `LONG|SHORT|NONE`, price within +/- 1.25 ticks of a matching
  `or_levels[]` entry.
- `pax-ai/pax_ai/ai_chart_signal_store.py:43-71` appends to the JSONL.
- `mcp-server/bookmap_mcp/pax_ai_chart_events.py:71-137` maps to chart event
  format on each snapshot poll.

The label `OR_BREAK` and the components text `Pull + Orderbook + Tape` are
strings the LLM wrote into its response. They are not computed by a
deterministic detector. The STOP price 30127 may also be LLM-generated;
there is no Python or Java code that produces a 104-pt stop from any
algorithm we have.

This explains:
1. Why fires are inconsistent across the night (Claude's narration varies).
2. Why the operator sees signals that contradict the deterministic per-rung
   composite (Claude can write whatever passes the shape gate).
3. Why my 5-min snapshots missed the fires (they live in the JSONL, not in
   the `decision` field).
4. Why the stop placement contradicts Pax-orthodox scratch-stop discipline
   (LLM hallucinated a custom stop).

**Design implication:** the institutional flow tracker is a strictly
deterministic, citable, replayable Python helper. It writes ONE new field to
the snapshot. It does NOT use Claude. The on-chart readout in section 8 is
a direct render of that field. This is the right place for the operator to
trust the read, because every regime decision has named drivers with file:line
provenance.

## 7. Proposed institutional flow tracker

### 7.1 New module

`mcp-server/bookmap_mcp/institutional_flow.py` (new file, ~150 lines, pure
Python, no LLM). Same authoring pattern as existing `compute_*` helpers in
dashboard.py: state cached in a module-level dict keyed by alias.

### 7.2 Output schema

Added to `/api/snapshot` as `snap["institutional_flow"]`:

```
{
  "alias": "NQM6.CME@RITHMIC",
  "asOfMs": 1779928948017,
  "regime": "ACCUMULATION" | "DISTRIBUTION" | "BALANCED" | "TRANSITION",
  "conviction": 0.0 to 1.0,
  "duration_sec": <how long this regime has held continuously>,
  "drivers": [
    {"name": "lt_ratio_falling",       "weight": 0.18, "evidence": "ratio -0.085, slope -3 over 5 snaps"},
    {"name": "pull_stack_3m_bearish",  "weight": 0.22, "evidence": "3m window bias=BEARISH zScore=-1.6"},
    {"name": "cvd_z_trending_down",    "weight": 0.16, "evidence": "cvdDeltaZ=-1.2, slope negative"}
  ],
  "divergence": {
    "regime_vs_price":   true | false,   // regime sign disagrees with mid 5-min slope
    "cvd_vs_price":      true | false,   // CVD (aggressor delta) sign disagrees with price slope (Cont/Kukanov gold standard)
    "rotation_stall":    true | false    // regime directional but no new 65pt rotation covered in last 5 min
  },
  "rotation_state": {
    "commit_level":              "OR-L",          // or "OR-H", or null if no commit yet this session
    "commit_price":              30100.0,
    "commit_time_ms":            1779928000000,
    "rotations_completed":       2,               // number of 65pt blocks covered in regime direction since commit
    "current_extreme_price":     29970.0,         // furthest mid in regime direction since commit
    "next_rotation_target":      29905.0,         // commit_price + (rotations_completed + 1) * 65 * sign
    "time_since_last_rotation_sec": 480
  },
  "level_stance": {
    "OR-H": "FADE_LEAN",
    "OR-L": "ACCEPT_LEAN",
    "+1":   "FADE_LEAN",
    "-1":   "ACCEPT_LEAN"
  }
}
```

Stance semantics (4 labels, derived from regime + level position + approach direction):
- `FADE_LEAN`: regime opposes the approach -- when price tests this level,
  lean to fade. Highest-frequency label for this operator (CSV shows
  ENTER_*_FADE >> ENTER_*_FOLLOW).
- `ACCEPT_LEAN`: regime aligns with continuation through the level. Lean to
  hold position past the level into the next rotation.
- `BREAKOUT_LEAN`: regime aligns with approach direction AND conviction
  > 0.70 AND no opposing micro_event (absorption, iceberg defense) at the
  level within last 60s. High bar -- this is the orthodox Pax break-with
  case the operator trades rarely but with high conviction.
- `WAIT`: no informative lean at this level (regime BALANCED, or conviction
  too low, or chop window active).

### 7.3 Inputs and weights (research-grounded)

Rolling 5-minute window, state-cached per alias. Weights are NOT
gut-guessed -- they are ranked by academic predictive power of each input
class. Sums to 1.00. Calibration loop (section 10) tunes magnitudes; the
RANK ORDER is what the research supports.

| Input | Weight | Sign convention | Research grounding |
|---|---:|---|---|
| `flow.ofi` (Cont/Kukanov OFI z) | 0.20 | sign(value), magnitude min(abs(value)/2.0, 1.0) | Cont/Kukanov/Stoikov arXiv:1011.6402: OFI explains R-squared = 65% of 10-sec price changes across 50 stocks (range 35-79%). Time-scale stability sub-second to 10-min. **Strongest single signal in the literature.** |
| `flow.cvdDeltaZ` (CVD aggressor z) | 0.15 | sign(value), magnitude scaled | Trade-imbalance is a subset of OFI per same paper; significant in only 31% of joint subsamples (lower than OFI alone, but still independent info beyond what's already in OFI). |
| `pull_stack.windows[1m]` zScore + bias | 0.15 | +1 BULLISH, -1 BEARISH, 0 NEUTRAL; scaled by min(abs(zScore)/2.0, 1.0) | Book-pressure (stack/pull rates) is the cancel-replace side of OFI; CME Rule 575 + Cont layering literature treat it as direct microstructure evidence of intent. |
| `pull_stack.windows[3m]` zScore + bias | 0.15 | same | Longer window captures sustained institutional book-building rather than HFT churn. |
| `flow.biasScore` + `biasTrajectory` | 0.10 | sign(biasScore), scaled by abs(biasScore) | Already an SMA-slope integrative summary; lower marginal info given OFI/CVD are above it. |
| `flow.regime` label | 0.10 | TRENDING_UP=+1.0, TRENDING_DOWN=-1.0, EXHAUSTION_UP=-0.5, EXHAUSTION_DOWN=+0.5, ABSORPTION_BID=+0.5, ABSORPTION_ASK=-0.5, BALANCED/WARMUP/MIXED=0.0 | Slow, integrative classifier from FlowRegime; redundant with above but cheap to include as a tiebreaker. |
| `lt_liquidity.ratio` slope over last 5 snaps | 0.10 | +1 rising, -1 falling, 0 flat | Resting-order ratio less academically established as a price predictor; included because Pax operator explicitly cites "liquidity on books" as central. |
| `pull_stack.windows[BBO]` zScore + bias | 0.05 | same as 1m/3m | BBO scale dominated by HFT noise; small weight as sub-second confirmation only. |

`micro_events.events[]` are NOT direct inputs to the regime vote -- they
are already surfaced separately on chart as evidence markers via
`institutional_chart_events`. The tracker reads them only to populate the
`rotation_state` and to gate `BREAKOUT_LEAN` (section 7.2 stance semantics:
absorption / iceberg defense within 60s blocks BREAKOUT_LEAN).

### 7.4 Decision logic

Pure deterministic vote. NO LLM. NO probabilistic confidence inference.

```
sign_of_input(i)  =  signed scalar in [-1.0, +1.0] per section 7.3 row i
reliability(i)    =  1.0 if the source produced a value this snapshot,
                     0.0 if missing / stale / NaN / sentinel
weighted_vote     =  sum_i( sign_of_input(i) * weight(i) * reliability(i) )
                     / max(1e-9, sum_i( weight(i) * reliability(i) ))

regime =
  ACCUMULATION  if weighted_vote > +0.30 and no opposing flow.regime label in last 60s
  DISTRIBUTION  if weighted_vote < -0.30 and no opposing flow.regime label in last 60s
  TRANSITION    if abs(weighted_vote) > 0.10 but the sign of weighted_vote flipped in last 30s
  BALANCED      otherwise

conviction = min( 1.0, abs(weighted_vote) / 0.50 )
```

"Opposing flow.regime label" means: when computing ACCUMULATION, any
TRENDING_DOWN / EXHAUSTION_UP / ABSORPTION_ASK observed in the last 60s
counts as opposing; symmetric for DISTRIBUTION.

Thresholds 0.30, 0.10, 60s, 30s are PLACEHOLDERS for the measurement loop
in section 10 to tune from data.

### 7.4a Rotation tracking (Q3 answer)

NQ rotation unit = **65 points** anywhere on the ladder (OR-H to +1, +1 to
+2, OR-L to -1, etc -- always 65 pts on NQ; ES = 15 pts). Operator-stated
2026-05-27; cross-confirmed by Lane 1 web sources (PAX30OpeningRange
NinjaTrader indicator, wallstwillie/The-Pax-Group-Superdom GitHub).

The tracker maintains `rotation_state` per alias:

```
on commit  (mid crosses OR-H or OR-L by 1 tick):
    commit_level      = "OR-H" or "OR-L"
    commit_price      = orHigh or orLow
    commit_time_ms    = snap.ts_ms
    rotations_completed = 0
    current_extreme_price = mid
    sign = +1 if OR-H break, -1 if OR-L break

on each subsequent snapshot:
    extreme = max/min(current_extreme_price, mid)  (in regime direction)
    rotations_completed = floor( abs(extreme - commit_price) / 65 )
    next_rotation_target = commit_price + (rotations_completed + 1) * 65 * sign
    time_since_last_rotation_sec = wall-clock time since rotations_completed last incremented

reset on:  mid retraces past OR-H/OR-L back inside OR.
```

This is purely descriptive. The regime decision in section 7.4 above does
NOT depend on rotation state. Rotation feeds two things: the divergence
flag `rotation_stall` (section 7.2 schema), and the on-chart readout
(section 8).

### 7.4b Divergence (Q6 answer, 3 flags)

The single `divergence_vs_price` flag in the original draft expanded into
three distinct flags, each backed by separate evidence:

| Flag | Definition | Grounding |
|---|---|---|
| `regime_vs_price` | regime sign disagrees with sign of mid 5-min slope; both magnitudes non-trivial | Generic momentum-vs-price exhaustion check |
| `cvd_vs_price` | sign(flow.cvdDeltaZ) disagrees with sign of mid 5-min slope; both non-trivial | Cont/Kukanov: aggressor delta is the strongest microstructure predictor; price-vs-CVD divergence is the academic-gold-standard exhaustion signal |
| `rotation_stall` | regime is directional (ACCUMULATION or DISTRIBUTION) but `rotation_state.time_since_last_rotation_sec` > 300s | Pax-specific: regime says move is on, but price is not covering 65pt rotations -- momentum exhausting |

Any one true = caution; two or three true = strong exhaustion signal.

### 7.4c Chop windows (Q8 answer)

When the current time-of-day falls inside any of these windows on the
operator's CT clock, the tracker forces `regime = BALANCED` regardless of
the weighted vote, with `conviction = 0.0` and a single driver
`{"name": "chop_window", "evidence": "<window name>"}`. Pax does not
trade in chop -- the tracker should not pretend it sees a regime that the
operator will not act on.

| Window | CT | ET | Rationale |
|---|---|---|---|
| US lunch chop | 11:00-12:30 CT | 12:00-13:30 ET | Documented in existing `session-clock` skill |
| US close risk | 14:30-15:00 CT | 15:30-16:00 ET | Documented in existing `session-clock` skill |
| CME maintenance | 16:00-17:00 CT | 17:00-18:00 ET | Daily Globex break; minimal flow |
| Asia-EU dead zone | 22:00-02:00 CT | 23:00-03:00 ET | Post-Asia-peak, pre-EU; thin volume |

Operator can override any window via `pax_weights.json::chop_windows`
(future enhancement; v1 hardcodes the table above).

`divergence_vs_price` is true when the regime sign disagrees with the sign
of mid's 5-minute slope.

`level_stance` derived per visible OR level. Approach direction matters:
"approaching from below" means `mid < level.price`, "from above" means
`mid > level.price`.

| Level position | Approach | Regime | Stance |
|---|---|---|---|
| Above-OR (OR-H, +1, +2 ...) | from below | ACCUMULATION, conviction > 0.70, no opposing micro_event in 60s | `BREAKOUT_LEAN` |
| Above-OR | from below | ACCUMULATION, conviction <= 0.70 | `ACCEPT_LEAN` |
| Above-OR | from below | DISTRIBUTION | `FADE_LEAN` |
| Above-OR | from above (retest) | ACCUMULATION | `ACCEPT_LEAN` (defend the level for longs) |
| Above-OR | from above | DISTRIBUTION | `FADE_LEAN` |
| Below-OR (OR-L, -1, -2 ...) | from above | DISTRIBUTION, conviction > 0.70, no opposing micro_event in 60s | `BREAKOUT_LEAN` |
| Below-OR | from above | DISTRIBUTION, conviction <= 0.70 | `ACCEPT_LEAN` |
| Below-OR | from above | ACCUMULATION | `FADE_LEAN` |
| Below-OR | from below (retest) | DISTRIBUTION | `ACCEPT_LEAN` (defend the level for shorts) |
| Below-OR | from below | ACCUMULATION | `FADE_LEAN` |
| Any | any | BALANCED or TRANSITION or in-chop-window | `WAIT` |

(Sign conventions and stance table pinned by tests in
`tests/test_institutional_flow.py`.)

### 7.5 dashboard.py change (the only edits there)

Two insertions in `dashboard.py`, no refactors:

1. Top-of-file import block (4 lines):
   ```python
   from .institutional_flow import (
       compute_institutional_flow,
       build_flow_chart_events,
   )
   ```
2. In `fetch_snapshot`, after `compute_institutional_signals` and before
   `compute_institutional_chart_events`: call `compute_institutional_flow`
   via the existing `_safe_call` wrapper. After `institutional_chart_events`
   is populated, append the flow's chart markers into that same list
   (so the existing OpenRange consumer renders them with zero Java change).
   Total ~13 lines in `fetch_snapshot` including the try/except guard
   that protects snapshot composition.

Net delta to dashboard.py: ~17 lines (4 import + 13 inline). All other
logic lives in `institutional_flow.py` (the new module).

### 7.6 What is and is not in v1

**In v1 (ships tonight):**
- `compute_institutional_flow` Python module + unit tests.
- New field `snap["institutional_flow"]` exposed via `/api/snapshot`.
- **Chart marker emission** into existing `snap["institutional_chart_events"]`
  array (see section 8a). Renderer (OpenRange Java) requires no changes --
  it already consumes that array. Operator sees regime as a chart triangle
  trail at NY open tomorrow.

**Not in v1 (deferred to later iterations, each its own spec):**
- Per-rung composite reading the tracker as a prior. Defer until measurement
  shows regime correlates with forward R.
- v2 screen-space text box (section 8b). Defer until operator validates
  v1 marker against eye.
- Any change to `pax_ai_chart_events`. Untouched.

## 8. Chart-display side

Two parts. **v1 = chart marker (this iteration). v2 = screen-space text box
(deferred until operator validates v1 against eye).** Reason: the chart
marker is Python-only (no new Java, reuses the existing
`institutional_chart_events` -> OpenRange renderer); the text box requires
~250 lines of new Java + a jar rebuild (Bookmap must be closed -- friction).
Ship the cheap visible signal first, measure it, then add the richer view.

### 8a. v1 chart marker (Python only, ships tonight)

The tracker emits a marker into `snap["institutional_chart_events"]` on each
snapshot when regime is directional. Marker schema (matches the existing
`institutional_chart_events` contract, no schema change):

| Field | Value | Notes |
|---|---|---|
| `id` | `instflow:<alias>:<regime>:<60s_bucket_ms>` | Deduped by 60s bucket -- one marker per minute per regime while sustained |
| `alias` | snap.alias | |
| `label` | `IFL` (institutional flow) | Distinct from existing `REG`/`TRD`/`CNV` codes |
| `price` | snap.book.mid | Marker plots at current mid -- operator sees position vs OR lines they already watch |
| `side` | `above` if mid > orHigh, `below` if mid < orLow, `inside` otherwise | |
| `event_type` | `LOCAL_ANCHORED` | per chart-marker-legend.md source family |
| `action` | `BIAS_SIGNAL` | direction-bearing context, not entry-quality |
| `direction` | `LONG` if ACCUMULATION, `SHORT` if DISTRIBUTION, `NONE` if BALANCED/TRANSITION/chop | |
| `marker_text` | `L^IFL{conf*100}` / `LvIFL{conf*100}` / `L.IFL{conf*100}` | Up-arrow long, down-arrow short, dot neutral, per legend pattern |
| `marker_color_hint` | `#2BD25B` for LONG, `#FF4D4D` for SHORT, `#B0B0B0` for NONE | Green / red / gray per existing local-anchored palette |
| `severity` | `WATCH` (priority 3) if conviction <= 0.70, `WARNING` (priority 2) if > 0.70 | Context, not entry. WARNING only when high conviction. |
| `timestamp_ms` | snap.ts_ms | |
| `source` | `institutional_flow` | distinct from `pax_ai` and other local emitters |
| `confidence` | snap.institutional_flow.conviction | |
| `reason` | first 240 chars of "regime / top-3 drivers / rotation_state.rotations_completed" | enough for hover-tooltip context |

Emission rules:
- **Emit only when regime is ACCUMULATION or DISTRIBUTION.** No marker for
  BALANCED, TRANSITION, or chop-window. Operator should not see clutter
  when there is no edge to read.
- **One marker per 60-second bucket per regime.** Dedup by id. Same regime
  sustained for 5 min = 5 markers, evenly spaced.
- **TTL 300s** in the existing chart-events renderer. Operator sees last
  5 min of regime history on chart -- a trail showing how long the regime
  has held and whether it has flipped.
- **Color matches direction**, not regime label: bullish-side = green
  up-triangle, bearish-side = red down-triangle. Operator reads it the
  same way they read existing acceptance / rejection markers.

What this looks like on chart: a green up-triangle every minute at mid
during sustained ACCUMULATION; red down-triangle every minute during
DISTRIBUTION; nothing during BALANCED/chop. Across a 30-min sustained
distribution, the operator sees a column of red triangles trailing down
with price -- visual confirmation of regime persistence.

### 8b. v2 screen-space text box (deferred)

New OpenRange screen-space painter sibling to `PaxHeatwavePainter`. Same
threading rule (load-bearing, per CLAUDE.md):

- A `PaxInstitutionalFlowFetcher` daemon thread polls `/api/snapshot` at 1s.
- On a new snapshot, it sets an `AtomicBoolean flowDirty` flag and stores
  the parsed model. **Does not mutate the canvas.**
- `InstrumentState.shouldRepaint(eventTime)` reads `flowDirty.compareAndSet`
  and bypasses the 1s repaint throttle for that one tick, exactly as the
  Heatwave Box does.
- Canvas mutation (`canvas.addShape` / `canvas.removeShape`) happens only on
  Bookmap-driven callbacks (`onTrade`, `onDepth`, `onMoveEnd`).

Four new Java classes under `indicators/OpenRange/src/main/java/com/openrange/`
(palette reuses existing `PaxHeatwaveColors`, no new color class):

- `PaxInstitutionalFlowModel` -- immutable carrier (regime, conviction,
  duration, top-3 drivers, divergence flag, per-level stance map, fetchedAtMs)
- `PaxInstitutionalFlowSnapshotParser` -- recursive-descent JSON parser, no
  jar deps (same pattern as `PaxHeatwaveSnapshotParser`)
- `PaxInstitutionalFlowPainter` -- renders model + `Graphics2D` into a
  single `BufferedImage` -> `PreparedImage` -> `CanvasIcon`
- `PaxInstitutionalFlowFetcher` -- daemon thread + `java.net.http.HttpClient`,
  1000 ms poll default, exponential backoff after 3 fails, cap 5s

**Display location (Q7 answer):** When v2 ships, docked directly BELOW the
existing Heatwave Quant Box, same ~280-320 px width, same dark theme, same
font. Operator scans top-left vertically: Heatwave (conviction composite)
-> Institutional Flow (regime + rotation). Rationale: the two boxes answer
adjacent questions ("what does the conviction model say" vs "what's the
book doing right now"), so vertical stacking lets the operator read them
as one coherent column without eye-jumping across the chart.

**v2 is NOT shipping in this iteration.** Only the v1 chart marker
(section 8a) ships now. Operator validates the chart marker visually at
the next NY open (08:30 CT). If the marker matches the operator's read of
the book, then v2 (text box) gets built so the WHY of each marker is
readable on chart without hover.

Visual layout (monospaced, dark theme, ~280px wide):

```
FLOW: DISTRIBUTION                conv 0.74
held 23m02s        rotations: 2 of regime
+ ofi z=-1.8 (Cont/Kukanov, strongest)
+ pull 3m bias BEARISH z=-1.6
+ cvd z=-1.2 trending
divergence: cvd_vs_price=YES rotation_stall=NO
next 65pt target: 29905.0
OR-H stance: FADE_LEAN
OR-L stance: ACCEPT_LEAN
```

Freshness measured from `fetchedAtMs` (HTTP response receive time), not
the snapshot `ts` string -- same convention as Heatwave Box.

No new addon, no changes to OpenRange existing painters, no changes to
`PaxOpeningRangeModule` beyond instantiating the new fetcher/painter pair.

## 9. Build and deploy

- Python module + tests in `mcp-server/bookmap_mcp/institutional_flow.py`
  and `mcp-server/tests/test_institutional_flow.py`.
- Java classes added under `indicators/OpenRange/src/main/java/com/openrange/`.
- OpenRange jar bumped per the existing build script
  (`indicators/OpenRange/build.ps1`). **Bookmap must be CLOSED before
  rebuild** (jar lock per memory `openrange_deploy_lazy_classload`).
- Bridge jar UNCHANGED -- no Java bridge work in this spec.
- dashboard.py 3-line delta does not require a Bookmap restart, just a
  dashboard process restart.

## 10. Measurement loop

`feature_bus` is already on and writing to `D:\BookmapLogs\pax-bus.db`
(verified: file modified within 2 minutes during tonight's session). Every
snapshot captured from here on will carry the new `institutional_flow` field
automatically once shipped.

After ~2-3 weeks of NY-open captures:

- Query: `for each ai_turn or trigger event at level L, what was regime at
  approach (snap.institutional_flow.regime) and what was realized R at
  +60s / +180s / +300s ?`
- Calibration table: regime label x level x time-of-day x conviction
  quintile -> forward R distribution.
- Tune thresholds 0.30 / 0.10 / 60s / 30s from data.
- Operator approves every threshold change in writing before it goes live.

## 11. Promotion gates

In order. No skipping:

1. Operator red-pens this spec. Open questions in section 13 answered.
2. Spec re-saved with operator's edits.
3. Python module written + unit tests against snapshots captured tonight in
   `_session_snapshots/`. Tests pin sign conventions and threshold behavior.
4. Live calibration run, BEFORE the chart box exists. The new Python field
   `snap.institutional_flow` is wired in but rendered nowhere on chart.
   Operator opens a terminal, runs a small read-only PowerShell loop that
   pulls `/api/snapshot` every 5s and prints `regime / conviction /
   duration / top-3 drivers`. Operator visually validates the regime label
   matches their book read for at least 30 continuous minutes during a NY
   open. The terminal print is intentional: it forces the operator to read
   the regime in plain English against their chart, with no pretty UI to
   anchor on.
5. ONLY after step 4 passes: build the OpenRange box (section 8). Deploy.
6. 2-3 weeks of NY-open captures with the box live.
7. Forward-R measurement query (section 10).
8. ONLY after step 7 shows regime correlates with forward R: discuss either
   (a) wiring tracker as a prior into per-rung composite, or (b) emitting
   regime transitions as deterministic `institutional_chart_events`. Each
   of those is a separate spec.

## 12. Out of scope (explicit not-doing list)

- Replacing or modifying the LLM-emitted `pax_ai_chart_events` stream.
- Encoding Pax entry / stop / target / re-entry / sit rules from section 4.
  Those become a separate spec after the tracker is dialed in.
- Removing V8/V9/V10 trajectory modulation. Defer to after measurement.
- Changing the browser dashboard UI or any HTTP response shape other than
  adding the new field.
- Touching the execution path (`server.py` two-gate trading tools).
- Adding new conviction sources to `_CONVICTION_SOURCES`.
- Refactoring dashboard.py beyond the 2-line addition in section 7.5.
- Bridge changes.

## 13. Operator red-pen status

Answered 2026-05-27 (this session):

| Q | Topic | Answer | Where applied |
|---|---|---|---|
| Q1 | NQ rung stride | **65 pts confirmed.** Fixed stride off OR-H / OR-L, not range-multiples. | §4 (research), §7.4a (rotation logic), saved as memory `pax_nq_rotation_unit`. |
| Q2 | `ib_context` | **CUT.** Operator did not recognize the source; IB is auction-theory machinery not part of Pax OR. | §5 cuts list. |
| Q3 | Rotation criteria | **65 pts per rotation, anywhere on the ladder** (OR to 1st ext = 65, ext to ext = 65). | §7.2 schema (`rotation_state`), §7.4a (rotation tracking logic), §7.4b (divergence `rotation_stall` flag), §8 (next-target display). |
| Q4 | Weights / thresholds | Operator said "Jane Street logic, research it." Weights now ranked by academic predictive power: OFI (Cont/Kukanov R-squared=65%) > CVD > pull_stack > bias/regime > lt_liquidity > BBO-pull. Thresholds 0.30 / 0.10 / 60s / 30s remain placeholders for the measurement loop -- magnitudes are tuned from data, but rank order is research-grounded. | §7.3 (table with citations). |
| Q5 | Level-stance taxonomy | **4 labels: `FADE_LEAN` / `ACCEPT_LEAN` / `BREAKOUT_LEAN` / `WAIT`.** BREAKOUT_LEAN gated by conviction > 0.70 AND no opposing micro_event in 60s -- high bar for the orthodox Pax break-with case. | §7.2 stance semantics, §7.4 stance table. |
| Q6 | Divergence | **3 flags, not one:** `regime_vs_price`, `cvd_vs_price` (Cont/Kukanov gold standard), `rotation_stall` (Pax-specific: directional regime but no new 65pt covered in 5 min). | §7.2 schema, §7.4b. |
| Q7 | Display location | **Below the Heatwave Box**, same width, same theme. Top-left vertical column lets the operator read conviction (Heatwave) then book regime (this) as one coherent stack. | §8. |
| Q8 | Chop windows | 4 windows on CT: US lunch 11:00-12:30, close risk 14:30-15:00, CME maintenance 16:00-17:00, Asia-EU dead zone 22:00-02:00. Inside any window, tracker forces `regime = BALANCED`. | §7.4c. |

Remaining open (operator to red-pen if needed):

- None blocking. Operator can override the chop window table or the
  weight magnitudes at any time; the structure is in place to absorb tuning.
- Recommendation: re-read sections **§7.3, §7.4 (a/b/c), §8** end-to-end
  before code starts. These hold the actual logic.

## 14. Sources

Lane 1 web + CSV:

- thepaxgroup.org/the-opening-range/
- github.com/wallstwillie/The-Pax-Group-Superdom
- ninjatraderecosystem.com PAX30OpeningRange listing
- tradingview.com/script/T2glZuWc-NoProcess-PAX-Opening-Range/
- tradingview.com/chart/NQ1!/dc4CmBnC-Pax-Plan-12-2-2019-The-Opening-Range/
- nexusfi.com/showthread.php?t=56281
- thewallstreetcoach.com/blog/2024/05/trader-matt-pax-kenah/
- futures.anthonycrudele.com/podcast/343/
- D:\BookmapLogs\pax-agent-signals-20260520.csv (148834 bytes)
- D:\BookmapLogs\pax-agent-signals-20260522.csv
- D:\BookmapLogs\pax-agent-signals-20260526.csv
- D:\BookmapLogs\pax-agent-signals-20260527.csv

Lane 2 codebase audit:

- mcp-server/bookmap_mcp/dashboard.py (`fetch_snapshot`,
  `compute_session_conviction`, `_source_*` helpers)
- mcp-server/bookmap_mcp/pax_ai_chart_events.py
- pax-ai/pax_ai/ai_chart_signal_store.py
- pax-ai/pax_ai/ai_chart_signal.py
- indicators/OpenRange/src/main/java/com/openrange/PaxAiChartEventsActiveHistory.java
- indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalChartEvent.java

Prior specs (referenced, not modified):

- docs/superpowers/specs/chart-marker-legend.md
- docs/superpowers/specs/institutional-chart-markers.md
- docs/superpowers/specs/2026-05-19-heatwave-quant-box-design.md
- docs/superpowers/specs/2026-05-18-institutional-tape-flow-design.md
