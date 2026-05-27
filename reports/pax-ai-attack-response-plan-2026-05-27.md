# Pax AI Attack-Response Plan (2026-05-27)

Audit + plan only. No edits. This report covers the chart-first
"attack-response" overhaul: cleaner per-event glyphs on the chart, a new
deterministic state classifier in Python, evidence logging that records
WATCH as well as actionable rows, and an opt-in tiny WATCH label painter.
EDGE promotion remains gated on logged statistics, not on plotting.

ASCII only. Single human author (Claude in this session).

## 1. Live runtime state (probed 2026-05-27)

- `git status --short`:
    - `M  indicators/OpenRange/build.ps1`
    - `D  indicators/OpenRange/src/main/java/com/openrange/PaxChartLayerRegistry.java`
    - `D  indicators/OpenRange/src/test/java/com/openrange/PaxChartLayerRegistryTest.java`
    - `??  reports/pax-ai-edge-and-bloat-audit-2026-05-27.md` (earlier-session report; kept)
- HEAD: `eccbf76 snapshot: level-edge + chart UI Phase 2 + parse_qs fix`.
- Dashboard `/api/snapshot` -> HTTP 200 but `health=offline`.
  `bridgeError=[WinError 10061] connection refused at 127.0.0.1:8765`.
  Bookmap is not running with the MCP Bridge attached, so live chain
  cannot be tested in this session.
- Pax AI `/api/pax/health` -> connection refused (server not running).
- Pax AI `/api/pax/levels/edge` -> connection refused.
- Live verification of Stage 2-7 will need a follow-up session with the
  bridge + Pax AI both up. Source + tests can be implemented now.

## 2. Code findings (with exact paths and line numbers)

### 2.1 Dashboard (mcp-server/bookmap_mcp/dashboard.py)

- `_micro_at_level`           - lines 467-501. Reads `snap["micro_events"]`,
  filters by tick-band around price, returns directional `(score, reason)`
  from ICEBERG / SPOOF / STOP_SWEEP near the level. Already classifies
  ASK vs BID via `isBid`.
- `_level_composite`          - lines 789-927. Per-magnet 8-driver composite
  (pull_stack, tape, micro, lt_liquidity, orderbook, vwap, volume_profile,
  session_conviction). Direction map by side. Anti-domination via
  effective-weight share caps. WAIT below `level_directional_threshold`.
- `_score_level`              - line 930. Pre-V5 per-level scorer; still
  emits the row's `decision` and `components` fields.
- `_thesis_select_thesis`     - lines 1273-1303. State + liquidity +
  microstructure -> thesis label (ACCEPTANCE / REJECTION / STOP_SWEEP_*
  / ABSORPTION_FADE / ICEBERG_DEFENSE / NONE).
- `_thesis_execution_read`    - lines 1346-1370. Thesis -> execution_read
  (PAY_FOR_TRADE / WAIT_FOR_CONFIRM / STAND_DOWN / SCRATCH_READY).
  PAY_FOR_TRADE requires aggressor alignment (WITH for acceptance, AGAINST
  for rejection / sweep-failure).
- `_thesis_for_level`         - lines 1411-1527. Per-(alias, label, side)
  state machine with rolling history, touched_at_ms anchor, polls-since-touch.
- `compute_institutional_signals`       - lines 1624-1752. Entry-only payload
  (`institutional_signals`). Only PAY_FOR_TRADE carries direction. Closed
  vocabulary of signal_type. Dedup id keyed on `state_since_ms`.
- `compute_institutional_chart_events`  - lines 2088-2203. The evidence
  trail: WATCH, TCH, SWP, ICE, SPD, ABS, PULL, STACK, ACC, REJ, SCR. Per
  level walk with helpers `_chart_emit_watch_touched`,
  `_chart_emit_micro`, `_chart_emit_absorption`, `_chart_emit_pull_stack`,
  `_chart_emit_thesis_decision`. Dedup on `(alias, label, event_type,
  anchor_ms)`.
- `compute_or_levels`         - lines 2239-2343. Dynamic extension grid
  (-N..OR-L..OR-H..+N), proximity flag in tick band, middleLock when no
  level is in proximity and price is inside the OR.
- `pax_decision` OR-width gate - lines 4404-4480. `pax_min_or_width_pts=3`
  / `pax_max_or_width_pts=25`. Today's 47-pt OR is correctly outside the
  band, so live decision is STAND_DOWN. Proximity / middleLock /
  institutional-thesis gates - lines 4481-4541.
- `_sync_magnet_levels`       - line 4827. Template for the cached
  dashboard -> bridge POST pattern (TTL + lock + swallow errors).
- `fetch_snapshot`            - line 4947. Composes the snap.

### 2.2 Pax AI level edge (pax-ai/pax_ai/level_edge.py)

- Blocker priority - lines 167-202: `health -> stale -> anchor -> news ->
  session -> no_direction -> not_near_level -> low_confidence -> size_tier
  -> thesis_gate -> missing_stop`.
- Actionability gate - lines 349-358: requires directional composite,
  proximity, size_tier in `(HALF, FULL)`, confidence >= 0.35, stop_price.
- Direction forced WAIT when `actionable=false` - lines 360-362. This
  is why raw composite hints never become bull/bear plots until every
  gate clears. Correct for entries; wrong for raw evidence display.
- `score_R` honesty rename - lines 309-313. `score_R` is
  `confidence x directional_R_constant`, NOT measured EV.

### 2.3 Pax AI level edge log (pax-ai/pax_ai/level_edge_log.py)

- `record_payload` - lines 319-391. Logs ONLY rising-edge actionable=true
  transitions. WAIT / non-actionable rows do not log.
- Append-only on both `YYYY-MM-DD.open.jsonl` and `YYYY-MM-DD.closed.jsonl`
  under `%LOCALAPPDATA%\pax-ai\level-edge-log`.
- Horizon backfill at 15s / 60s / 300s - lines 214-262. R-multiple via
  `realized_r` against `stop_price`.
- `record_payload_safe` swallow wrapper - lines 394-409.

### 2.4 Pax AI level edge report (pax-ai/pax_ai/level_edge_report.py)

- Groups: setup / direction / size_tier / level_label / confidence_bucket
  / top_drivers - lines 29-36.
- Stats: hit_rate / mean_R / median_R per horizon; invalidated rate.
- CLI `--date YYYY-MM-DD [--json] [--root ...]` - lines 296-329.

### 2.5 Java chart pipeline (indicators/OpenRange)

- `PaxInstitutionalChartEvent.java` - immutable carrier. Severity rank,
  color-from-hint helper. Renderable iff id + finite price + positive ts
  + non-empty markerText.
- `PaxTrendSignalSnapshotParser.parseChartEvents` - lines 65-114. Reads
  `snap["institutional_chart_events"]`, honors `health=ok`. No fallback
  to other arrays.
- `PaxOpeningRangeModule.updateTriangles` - lines 1750-1807. Gates the
  chart-event layer on `ui.showInstitutionalChartEvents`. Calls
  `addAllChartEvents`.
- `PaxOpeningRangeModule.addAllChartEvents` - lines 2117-2139. TTL filter
  + collision-bucket collapse + sort by timestamp + draw.
- `PaxOpeningRangeModule.drawInstitutionalChartEvent` - lines 2148-2191.
  Builds the icon image, anchors at DATA_ZERO via `epochMsToChartNanos`,
  staggers by severity rank.
- `chartEventRenderText` / `compactChartEventRenderText` - lines 2622-2670.
  Current format is `<source><dir><code><conf>`, e.g. `L^SWP35`,
  `C.STK44`, `Cv ICE12`. The 3-letter code (`STK / PUL / WCH / SWP / ACC
  / REJ / TCH / ICE / ABS / SPD / SCR`) is the problem: it does NOT
  encode bid/ask side or attack direction, so the chart reads as
  noise.
- `chartEventIconImage` - lines 3027-3085. Draws a rounded box,
  direction triangle (LONG up, SHORT down, NONE oval), then the
  source+code+conf text alongside.
- `PaxOpeningRangeUiSettings.showInstitutionalChartEvents` - line 73.
  Default true; user toggle in the settings dialog.

### 2.6 Feature bus / outcomes (pax-ai/pax_ai/feature_bus.py)

The bus persists `ai_turn` / `snapshot` / `trigger` rows for the
research loop. It is NOT a chart-render pipeline and not in scope for
this work. We will not touch any feature_bus writer-path function
per the CLAUDE.md writer-freeze invariant.

## 3. The problem

1. `compactChartEventCode` collapses every event to a generic 3-letter
   code. The chart cannot tell:
   - sweep above OR-H vs sweep below OR-L,
   - bid iceberg defending support vs ask iceberg capping resistance,
   - bid stacking under price vs ask stacking above price,
   - rejection that flipped direction vs a touch with no follow-through.
2. `level_edge_log` logs only `actionable=true` rising-edge rows. The
   evidence chains that PRECEDE an edge (sweep + reclaim + iceberg + bid
   stack) never reach disk, so calibration can never learn them.
3. `edge_calculus.score_R = confidence * directional_R_constant`. It is
   structure scoring, not measured edge. The current chart and reports
   surface this without statistical backing.
4. There is no explicit "attack vs response" axis in either Python or
   Java. The system has the raw evidence (institutional_chart_events)
   but no classifier that turns "below OR-L + bid iceberg + bid stack +
   buy tape" into `OR_L_SWEEP_RECLAIM / BULL_WATCH`.

The operator example is the canonical case: dip below OR-L, liquidation
sweep before -1, volume in, bullish iceberg/bid absorption, buy stack ->
the system must call this BULL_WATCH / OR_L_SWEEP_RECLAIM, not "bearish
because price is below OR-L."

## 4. Research grounding (citations)

Order-flow imbalance (OFI) is the foundation for treating sweep +
absorption + stack as evidence of incoming displacement.

- Cont, Kukanov, Stoikov, "The Price Impact of Order Book Events" -
  empirical linear OFI -> short-term price-change relation across 50
  NYSE stocks; slope inversely proportional to depth; robust across
  intraday seasonality and time scale.
  https://arxiv.org/abs/1011.6402
  https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1712822
  https://academic.oup.com/jfec/article-abstract/12/1/47/816163
- Order Book Imbalance literature for short-horizon prediction (Queue
  Imbalance, ML LOB features) - imbalance strongly predicts moves at
  seconds-to-tens-of-seconds horizons.
  https://arxiv.org/pdf/1512.03492
  https://arxiv.org/pdf/1901.10534
  https://www.federalreserve.gov/econres/notes/feds-notes/order-flow-imbalances-and-amplification-of-price-movements-evidence-from-u-s-treasury-markets-20251103.html

Stop runs, liquidity sweeps, and failed continuation - the difference
between a continuation sweep and a reclaim sweep is THE distinction the
operator wants. Practitioner write-ups (Bookmap blogs are domain
references for futures order-flow traders):

- "Running the Stops: How It Happens-and How to Trade the Aftermath" -
  https://bookmap.com/blog/running-the-stops-how-it-happens-and-how-to-trade-the-aftermath
- "Stop Runs and Liquidity Traps" -
  https://bookmap.com/blog/stop-runs-liquidity-traps-how-the-market-flushes-out-weak-hands
- "Detecting Stop Runs with CVD and Iceberg Absorption" -
  https://bookmap.com/blog/detecting-stop-runs-using-cvd-and-iceberg-absorption-for-strategic-trading

Iceberg / absorption defense:

- "How to Read and Trade Iceberg Orders" -
  https://bookmap.com/blog/how-to-read-and-trade-iceberg-orders-hidden-liquidity-in-plain-sight
- "Bookmap Icebergs and Absorption" -
  https://grizzlyparrottrading.com/platforms-tutorials/bookmap-icebergs-and-absorption.html

Opening range as location, not direction-by-itself:

- Zarattini, Barbon, Aziz (2024) - the breakout strategy works on stocks
  with abnormal volume; otherwise breakout-without-volume often fails and
  the failed break + reclaim is a separate trade.
  https://chartswatcher.com/pages/blog/master-the-opening-range-breakout-strategy-in-trading
  https://www.quantconnect.com/research/18444/opening-range-breakout-for-stocks-in-play/

This research supports the design: location (OR / extension / VWAP)
gates which trade is possible; the attack + response classification
decides which one is actually happening; aggregated outcomes (not
hand-tuned confidence) decide which buckets earn `proven_edge=true`.

## 5. Design - attack-response layer

A new closed-vocabulary Python module reads existing snapshot fields and
emits one structured `state` per proximate level. No new event detector
is required - `institutional_chart_events` already carries every
primitive (sweep / iceberg / spoof / absorption / pull / stack / accept
/ reject / scratch / watch / touched).

### 5.1 Vocabulary

- LOCATION: `OR-H`, `OR-L`, `+1..+N`, `-1..-N`, `VWAP` (latter optional,
  added later if dashboard exposes it).
- ATTACK: `SWEEP_HIGH`, `SWEEP_LOW`, `BREAK_UP`, `BREAK_DOWN`, `TOUCH`.
- RESPONSE: `ACCEPTED`, `REJECTED`, `RECLAIMED`, `FAILED_CONTINUATION`,
  `HOLDING`.
- PASSIVE: `BID_ICEBERG`, `ASK_ICEBERG`, `BID_ABSORB`, `ASK_ABSORB`.
- BOOK: `BID_STACK`, `ASK_STACK`, `BID_PULL`, `ASK_PULL`.
- ACTIVE: `BUY_TAPE`, `SELL_TAPE`, `MIXED_TAPE`, `THIN_TAPE`.

### 5.2 Output states + biases (closed set)

| state                  | bias        |
|------------------------|-------------|
| `OR_L_SWEEP_RECLAIM`   | BULL_WATCH  |
| `OR_H_SWEEP_FAIL`      | BEAR_WATCH  |
| `OR_L_ABSORB_HOLD`     | BULL_WATCH  |
| `OR_H_ABSORB_HOLD`     | BEAR_WATCH  |
| `OR_L_BREAK_ACCEPT`    | BEAR_WATCH  |
| `OR_H_BREAK_ACCEPT`    | BULL_WATCH  |
| `EXT_LOW_EXHAUST`      | BULL_WATCH  |
| `EXT_HIGH_EXHAUST`     | BEAR_WATCH  |
| `VWAP_RECLAIM`         | BULL_WATCH  |
| `VWAP_REJECT`          | BEAR_WATCH  |
| `NO_EDGE`              | NEUTRAL     |

### 5.3 Decision tree (deterministic, no LLM math)

For each level present in `snap["or_levels"]["levels"]` with
`proximity=true` (and for swept levels that are not proximate at mid
but were anchor-touched in this poll's event set):

1. Collect attack from `institutional_chart_events`:
   - LIQUIDITY_SWEEP at this label + side=below -> ATTACK=SWEEP_LOW.
   - LIQUIDITY_SWEEP at this label + side=above -> ATTACK=SWEEP_HIGH.
   - ACCEPTANCE with direction=LONG -> ATTACK=BREAK_UP.
   - ACCEPTANCE with direction=SHORT -> ATTACK=BREAK_DOWN.
   - TOUCHED_LEVEL without sweep -> ATTACK=TOUCH.
2. Collect passive at the level:
   - ICEBERG_DEFENSE marker_text=`ICE-B` -> BID_ICEBERG.
   - ICEBERG_DEFENSE marker_text=`ICE-A` -> ASK_ICEBERG.
   - ABSORPTION marker_text=`ABS-B` -> BID_ABSORB.
   - ABSORPTION marker_text=`ABS-A` -> ASK_ABSORB.
3. Collect book intent (proximate level only):
   - STACKING + side=above -> ASK_STACK.
   - STACKING + side=below -> BID_STACK.
   - PULLING + side=above -> ASK_PULL.
   - PULLING + side=below -> BID_PULL.
4. Collect active flow from `snap["tape_flow"]`:
   - deltaScore >= +0.5 with thin_n above floor -> BUY_TAPE.
   - deltaScore <= -0.5 with thin_n above floor -> SELL_TAPE.
   - abs(delta) < 0.1 -> MIXED_TAPE.
   - n30 < tape_thin_floor -> THIN_TAPE.
5. Map (ATTACK + RESPONSE proxy) to STATE:
   - SWEEP_LOW at OR-L AND (BID_ICEBERG OR BID_ABSORB OR BID_STACK)
     AND NOT (SELL_TAPE alone): `OR_L_SWEEP_RECLAIM / BULL_WATCH`.
   - SWEEP_LOW at OR-L AND SELL_TAPE AND NOT BID_*: 
     `OR_L_BREAK_ACCEPT / BEAR_WATCH`.
   - Symmetric for OR-H / SWEEP_HIGH (ASK_ICEBERG / ASK_ABSORB /
     ASK_STACK + BUY_TAPE).
   - BREAK_UP at OR-H AND BUY_TAPE AND NOT ASK_*: `OR_H_BREAK_ACCEPT
     / BULL_WATCH`.
   - BREAK_DOWN at OR-L AND SELL_TAPE AND NOT BID_*: `OR_L_BREAK_ACCEPT
     / BEAR_WATCH`.
   - TOUCH at OR-L AND (BID_ICEBERG OR BID_ABSORB) AND NOT
     FAILED_CONTINUATION: `OR_L_ABSORB_HOLD / BULL_WATCH`. Symmetric at
     OR-H.
   - At extension labels (`-N` / `+N`) the same SWEEP+absorb+stack pattern
     becomes `EXT_LOW_EXHAUST / EXT_HIGH_EXHAUST` with bias toward the
     mean (i.e., reclaim toward the OR).
   - Conflicting evidence (both BID_* and SELL_TAPE, or both ASK_* and
     BUY_TAPE) -> `NO_EDGE / NEUTRAL` with drivers listed.
6. Output one row per state. Never invent a state when no attack +
   response evidence is present; emit nothing (or `NO_EDGE` with reasons
   only if a touch was seen).

### 5.4 Gate behavior

- `snap["health"] != "ok"` -> set `blocked.health=true`, emit no states.
- `snap["session"]["anchorMode"] != "LIVE"` -> set `blocked.anchor=true`,
  emit no states (matches the existing OR-anchor invariant).
- Stale snapshot (poller age > stale threshold) -> set `blocked.stale=true`,
  emit no states.

### 5.5 Edge promotion

Endpoint always emits `proven_edge=false`, `sample_n=null`,
`edge_R_60s=null` for this build. Future work: the report (Stage 6) +
calibration over the new JSONL can compute per-(state, bias) hit-rate +
mean_R and produce a separate config that the endpoint may consult to
flip `proven_edge=true`. Stage 7 paints WATCH only; EDGE styling is
gated on that future config (no auto-promotion in this build).

## 6. Java glyph vocabulary (Stage 2)

Same payload (`institutional_chart_events`); only render text changes.
The current 3-letter compactChartEventCode collapses bid/ask context.
New mapping for `compactChartEventCode` + the icon image:

| event_type           | side / direction signal                         | glyph |
|----------------------|-------------------------------------------------|-------|
| LIQUIDITY_SWEEP      | event.side=below                                | `SL`  |
| LIQUIDITY_SWEEP      | event.side=above                                | `SH`  |
| ICEBERG_DEFENSE      | marker_text contains `ICE-B`                    | `BI`  |
| ICEBERG_DEFENSE      | marker_text contains `ICE-A`                    | `AI`  |
| ABSORPTION           | marker_text contains `ABS-B`                    | `BA`  |
| ABSORPTION           | marker_text contains `ABS-A`                    | `AA`  |
| STACKING             | side=below                                      | `BS`  |
| STACKING             | side=above                                      | `AS`  |
| PULLING              | side=below                                      | `BP`  |
| PULLING              | side=above                                      | `AP`  |
| ACCEPTANCE           | direction=LONG                                  | `AL`  |
| ACCEPTANCE           | direction=SHORT                                 | `AS-` (`A.` if AS collides) |
| REJECTION            | direction=LONG                                  | `RL`  |
| REJECTION            | direction=SHORT                                 | `RS`  |
| WATCH_LEVEL          | -                                               | `W`   |
| TOUCHED_LEVEL        | -                                               | `T`   |
| SPOOF_RISK           | -                                               | `SP`  |
| SCRATCH              | -                                               | `X`   |

ACCEPTANCE_SHORT collides with ASK_STACK on a 2-letter `AS` key. Use
`AC-S` / `AC-L` for ACCEPTANCE if shape disambiguation is not strong
enough; keep `AS` reserved for ASK_STACK. Final tests will pin the
chosen forms.

Colors (extend PaxChartPalette):
- bullish evidence (BI / BA / BS / RL): green (`#3CDC7D` BULL, already
  in palette).
- bearish evidence (AI / AA / AS / RS): red (`#EB6464` BEAR, already in
  palette).
- sweep (SL / SH): amber outline (`#FFD040` SEVERITY_OUTLINE, already
  in palette) on a near-black fill.
- context / unknown / WATCH / TOUCHED / SPOOF / SCRATCH: gray
  (`#9C9C9C` STAND_DOWN, already in palette).

Sweep is amber - it is evidence, not a direction. Direction emerges
from the attack + response state (Stage 3+).

Glyph height target: 14-18 px (existing icon is 17 px - keep that;
just change text vocabulary).

Toggles: `showInstitutionalChartEvents` already gates this layer.
No new toggle for Stage 2.

## 7. Implementation stages (recap from spec)

- Stage 1: this report. No edits.
- Stage 2: Java render of institutional_chart_events. New glyph table,
  bid/ask side inference, color rules. Tests pin the mapping, the
  toggle gating, dense-cluster cap.
- Stage 3: `pax-ai/pax_ai/attack_response.py`. Pure
  `compute_attack_response(snap, now_ms)`. Closed vocabulary. Health /
  anchor / stale gates. Tests cover every output state, conflict ->
  NO_EDGE, health=offline -> blocked, location-alone -> NO_EDGE.
- Stage 4: `/api/pax/attack-response` endpoint in
  `pax-ai/pax_ai/server.py`. 503 cold, 200 with snapshot, never calls
  Claude, never mutates the snapshot, swallows logging failures.
- Stage 5: `pax-ai/pax_ai/attack_response_log.py`. Append-only JSONL,
  WATCH rows included, 15s / 60s / 300s mid backfill, optional
  realized_R when a stop is known.
- Stage 6: `python -m pax_ai.attack_response_report --date YYYY-MM-DD
  [--json]`. Read-only over JSONL. Stable JSON shape. Skips malformed.
- Stage 7 (gated on 3-6 green): tiny Java WATCH label painter.
  Independent toggle (or under the existing chart-events toggle until
  proven needed). WATCH styling only; EDGE only when endpoint reports
  `proven_edge=true`.
- Stage 8: run all tests, build OpenRange to STAGING only, write the
  implementation report.

## 8. Hard constraints (operator)

- Build to staging only. The deploy step must remain commented or
  guarded. `indicators/OpenRange/build.ps1` already targets
  `C:\Bookmap\addons-staging\openrange\openrange-release.jar` and
  enforces no stray `openrange*.jar` under `C:\Bookmap\addons`.
- The installed jar at `C:\Bookmap\addons\openrange-release.jar` is NOT
  touched (it contains the runtime-only PaxStateHud the operator wants
  visible).
- No live order code. No edits to `mcp-server/bookmap_mcp/server.py`
  trading tools.
- No Claude calls inside Stage 3-6 logic.
- No new HUD / box.
- ASCII only; no Unicode in new files.
- Tests required at every stage.

## 9. Rollback plan

Every stage is a separate commit and a separate file (or one file +
tests). Rollback is `git revert <hash>` per stage; no schema changes,
no on-disk migrations, no installed-jar replacement.

- Stage 2 rollback: revert
  `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
  + `PaxChartPalette.java` (if extended) + tests. Rebuild to staging.
- Stage 3 rollback: delete `pax-ai/pax_ai/attack_response.py` +
  `pax-ai/tests/test_attack_response.py`.
- Stage 4 rollback: revert the route block in
  `pax-ai/pax_ai/server.py` + `pax-ai/tests/test_server_helpers.py`
  additions.
- Stage 5 rollback: delete `pax-ai/pax_ai/attack_response_log.py` +
  tests; on-disk JSONL stays (operator can delete the day directory
  manually).
- Stage 6 rollback: delete the report CLI + tests.
- Stage 7 rollback: revert the new fetcher + painter classes; no
  installed-jar change anyway.

## 10. Open questions / deferred

- VWAP location: deferred until the dashboard exposes VWAP-as-a-level
  the same way as OR / extensions. Current `vwap_bias` is a regime, not
  a per-level reaction.
- VP node location: same. The volume_profile snapshot exposes POC / VAH
  / VAL but not as a reactor in the per-level pipeline.
- EDGE promotion config: out of scope for Stages 1-8. WATCH only.
- Live verification: blocked this session (bridge offline). Operator
  must restart Bookmap + Pax AI for end-to-end smoke once Stage 2-7
  source is in.

End.
