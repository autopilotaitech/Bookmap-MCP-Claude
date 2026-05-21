# Institutional Chart Markers — Research-Grounded Spec

> Purpose: a chart-event taxonomy and emission contract for
> `snap["institutional_chart_events"]` so the Bookmap chart shows the FULL
> evidence trail of institutional activity (sweeps, absorption, iceberg
> defense, spoof risk, pull/stack, acceptance, rejection, watch/touch),
> not just final buy/sell entries. Entries are ONE marker type. The trader
> reads evidence first.

## 1. Research grounding

### 1.1 CME Rule 575 — spoofing / layering / disruptive practices

Rule 575 covers four categories of disruptive order-entry behaviour. Direct
text:

- **575.A — Spoofing:** "No person shall enter or cause to be entered an
  order with the intent, at the time of order entry, to cancel the order
  before execution or to modify the order to avoid execution."
- **Layering:** "A market participant places a buy (or sell) order that he
  intends to have executed; and then subsequently enters a large sell (or
  buy) order, or multiple sell (or buy) orders adding up to a large size,
  for the purpose of attracting interest to the initial buy (or sell)
  order ... Immediately after the execution against the initial order,
  the market participant cancels the remaining orders."
- **Quote stuffing:** submitting/cancelling bids or offers to overload the
  exchange quotation system, or to delay another person's execution.

These are the regulatory definitions. We cannot prove intent from outside
the firm, so our SPOOF_RISK marker labels the **behavioural signature**
(large size, brief life, ~zero fills, repeated cancels at level) — not a
finding of misconduct. The marker is risk-management context: "trust the
displayed depth less while this pattern is present."

### 1.2 CME MBO (MDP 3.0)

- Each order carries an anonymous **OrderID** (consistent for the order's
  lifetime) and a **MDOrderPriority** (tag 37707, lowest→highest, used
  for queue position).
- Native iceberg orders are managed by the exchange: when the visible peak
  is filled, the exchange replaces it from the hidden remainder. The
  original OrderID is preserved across refills — this is the unambiguous
  detection signature.
- We do **not** see synthetic icebergs (ISV-managed at the participant)
  via OrderID; each refill is a new order. They are inferred behaviourally.
- We do **not** see true market-maker identity. We infer institutional
  activity from order-flow behaviour.

### 1.3 Iceberg detection (Zotikov & Antonov, arXiv:1909.09495)

Empirical study on E-mini S&P 500 (ESU19) full order-depth data.

- **Native iceberg signature:** "trades against these orders may sometimes
  be larger in volume than the current resting size, as indicated by trade
  summary messages" and "the original order ID is preserved throughout the
  whole lifetime of the iceberg." Peak size solved arithmetically from
  `V_peak = (V_T + V_L)/(k+1)`.
- **Synthetic iceberg signature:** "Synthetic icebergs are detected by
  observing limit orders arriving within a short time frame after a
  trade." Constraints: same price level, same volume as initial tranche,
  refill within delay `dt = 0.3 s` (for ESU19).
- **Minimum-tranche cutoff:** default 3. "Raising the floor ... we
  decrease the false positive rate at the cost of disregarding all
  icebergs of shorter lengths."
- **Empirical share:** ~4% of ESU19 traded volume is native iceberg;
  3.3–14.3% synthetic depending on tranche cutoff.

**Our ICEBERG_DEFENSE marker fires when:** the bridge's MBO detector
reports an `ICEBERG` event near a chart level AND the iceberg side matches
the defending side of the level (ASK iceberg defending OR-H, BID iceberg
defending OR-L). The Python bridge's detector already uses ≥3 refills +
executed volume > baseline EWMA, mirroring the paper's signature.

### 1.4 Order-Flow Imbalance (Cont/Kukanov/Stoikov, arXiv:1011.6402)

- **OFI definition (per event n):**
  `e_n = 1{P_n^B ≥ P_{n-1}^B}·q_n^B − 1{P_n^B ≤ P_{n-1}^B}·q_{n-1}^B − 1{P_n^A ≤ P_{n-1}^A}·q_n^A + 1{P_n^A ≥ P_{n-1}^A}·q_{n-1}^A`
- **Headline result:** linear `ΔP_k = α + β·OFI_k + ε_k` at 10-second
  buckets has **R² = 65%** (across 50 stocks, range 35%–79%); trade
  imbalance alone gets R² = 32%. OFI subsumes trade imbalance — in a
  joint regression the trade-imbalance coefficient is significant in only
  31% of subsamples.
- **Time-scale stability:** results hold from sub-second to 10-minute
  aggregations.
- **`β ∝ 1 / depth`** (estimated exponent λ ≈ 0.98, can't reject λ = 1
  for 35/50 stocks).

**Our ABSORPTION marker is grounded directly in this finding:** if the
aggressor-flow proxy (CVD/OFI-direction) is large at a level and yet
`ΔP ≈ 0`, the implied β at that micro-interval is ~0 — under the OFI
model this can only occur if hidden/replenishing passive size is
cancelling out the aggressor drain. ABSORPTION = "OFI-implied
displacement >> realised displacement at the level." We can flag it on
the chart even though we cannot identify the specific market maker.

### 1.5 Bookmap Absorption page

- "The Absorption Indicator will display any passive limit order
  transactions that meet the user inputted setting values."
- Operator semantics described:
  - Bullish absorption on pullbacks → trend continuation.
  - Heavy sell liquidity that price dips into → "first tranche of passive
    buying" → reversal once liquidity flips to bid.
  - Selling absorption that "cannot turn price around" → "capitulation of
    sellers and absorption of savvy buyers" → reversal long.

These are EVIDENCE patterns the operator reads. Bookmap doesn't auto-
convert absorption to buy/sell. We mirror that: ABSORPTION = context, not
entry.

### 1.6 Bookmap MBO bundle

Three indicators in Bookmap's MBO bundle:
- **Stops & Icebergs Sub-Chart** — subchart with order-size filtering.
- **Liquidity Tracker Pro** — full-depth bid/ask liquidity with size
  filtering.
- **Stops & Icebergs On-Chart** — on-chart display of CME iceberg
  transaction areas, historical and live.

Terminology to mirror in our marker text: "Stop & Iceberg", "Native
Iceberg", "Aggregated Orders". Data sourced from Rithmic's CME MBO feed.

---

## 2. Event taxonomy (authoritative)

Eight event types. Each has: meaning, data sources we already have,
plotted marker text, color hint, severity, and whether it can produce an
entry direction.

| # | event_type | marker_text | severity | source signal | direction | rationale |
|---|---|---|---|---|---|---|
| 1 | WATCH_LEVEL | `WATCH` | WATCH | `level.proximity=true` AND `state=APPROACHING` | NONE | price within ~12.5p of an OR/extension level but not touched. Visual heads-up. |
| 2 | TOUCHED_LEVEL | `TCH` | WATCH | `state=TOUCHED` | NONE | price within 1 tick of level; reaction unconfirmed. |
| 3 | LIQUIDITY_SWEEP | `SWP↑` / `SWP↓` | WARNING | `micro_events.STOP_SWEEP` near level | NONE | aggressor burst through known stops at OR-H/OR-L/extension. Watch event, not entry, per Rule 575 spoofing-avoidance + Bookmap "stops & icebergs" semantics. |
| 4 | ABSORPTION | `ABS-B` / `ABS-A` | WARNING | strong aggressor-flow (tape `\|delta\| ≥ 0.8`) AGAINST breakout direction at level | NONE | OFI-implied displacement >> realised → hidden passive absorbing aggressor (Cont/Kukanov/Stoikov; Bookmap absorption page). Context only. |
| 5 | ICEBERG_DEFENSE | `ICE-B` / `ICE-A` | WARNING | `micro_events.ICEBERG` on defender side near level | NONE | hidden replenishing liquidity defending the level (Zotikov & Antonov signature). Blocks breakout entry; supports fade context. |
| 6 | SPOOF_RISK | `SPD` | WARNING | `micro_events.SPOOF` near level | NONE | depth flash-and-cancel pattern (Rule 575 behavioural signature). Blocks any entry while present. |
| 7 | PULLING | `PULL` | INFO | `pull_stack.rotation` away from level + level proximate | NONE | book pulling. Context. |
| 7 | STACKING | `STACK` | INFO | `pull_stack.rotation` toward level + level proximate | NONE | book stacking. Context. |
| 8 | ACCEPTANCE | `ACC-L` / `ACC-S` | ENTRY | `state=ACCEPTED_ABOVE/BELOW` AND `execution_read=PAY_FOR_TRADE` | LONG/SHORT | confirmed acceptance with aligned aggressor flow. ENTRY. |
| 9 | REJECTION | `REJ-L` / `REJ-S` | ENTRY | `state=REJECTED` AND `execution_read=PAY_FOR_TRADE` | LONG/SHORT | confirmed rejection with aligned counter-flow. ENTRY. |
| 10 | SCRATCH | `SCR` | EXIT | `state=RETEST_FAIL` OR `INVALIDATED` AND `execution_read=SCRATCH_READY` | NONE | prior thesis invalidated. Exit signal. |

**Hard rule:** Only ACCEPTANCE / REJECTION (and the failure-flip of a
prior stop sweep) with `execution_read == PAY_FOR_TRADE` can carry
`direction in (LONG, SHORT)`. Everything else is `direction = NONE`.

---

## 3. Python payload contract

`snap["institutional_chart_events"]` — top-level array of event objects:

```json
{
  "id":                  "NQM6.CME@RITHMIC|OR-H|above|WATCH_LEVEL|1779385123456",
  "alias":               "NQM6.CME@RITHMIC",
  "label":               "OR-H",
  "price":               20000.0,
  "side":                "above",
  "event_type":          "WATCH_LEVEL",
  "direction":           "NONE",
  "execution_read":      "WAIT_FOR_CONFIRM",
  "marker_text":         "WATCH",
  "marker_color_hint":   "#E5C100",
  "severity":            "WATCH",
  "timestamp_ms":        1779385123456,
  "source":              "institutional_thesis",
  "confidence":          0.35,
  "reason_codes":        ["state=APPROACHING", "within proximity"],
  "invalidation_price":  null,
  "payline_price":       null
}
```

### 3.1 Dedup id contract

`id = "{alias}|{label}|{event_type}|{state_since_ms_or_event_ms}"`

Same id across polls = same event. Java history dedupes by id (already
built in the prior round: `PaxInstitutionalSignalsHistory`).

### 3.2 Severity ordering (for marker stacking)

`ENTRY (0) > EXIT (1) > WARNING (2) > WATCH (3) > INFO (4)`

When multiple events share the same `(label, timestamp_ms)`, Java
staggers their y-offset using the severity rank so labels don't overlap.

### 3.3 Source classification

| event_type | source |
|---|---|
| WATCH_LEVEL / TOUCHED_LEVEL | `institutional_thesis` |
| LIQUIDITY_SWEEP | `micro_events` |
| ABSORPTION | `tape_flow` |
| ICEBERG_DEFENSE | `micro_events` |
| SPOOF_RISK | `micro_events` |
| PULLING / STACKING | `pull_stack` |
| ACCEPTANCE / REJECTION / SCRATCH | `composite` |

### 3.4 What NOT to do (negative requirements)

- **NEVER** convert `trend_signal` (broad conviction projection) into a
  chart event. Trend is context, not evidence at a level.
- **NEVER** convert `pax.decision` directly into a chart event. The
  acceptance/rejection composite is the trader's view; `pax.decision`
  is the agent's view (gated, may downgrade further).
- **NEVER** emit ACCEPTANCE/REJECTION outside `execution_read == PAY_FOR_TRADE`.
- **NEVER** emit any direction != NONE for WATCH/TOUCHED/SWEEP/ABS/ICE/SPD/PULL/STACK/SCR.
- **NEVER** spam — dedup by id.

---

## 4. Java consumption contract

`PaxInstitutionalChartEvent` carrier + parser + per-instrument
`PaxInstitutionalChartEventsHistory` (mirrors the existing institutional-
signals history pattern; append-only by id; bounded at 50).

Painter draws every event in history on every repaint via `labelImage` +
`addTriangleShape`, anchored at `event.price` + `event.timestamp_ms`. No
native one-shot `Marker` API.

Markers on chart:
- `WATCH` / `TCH` — muted gray-yellow `#E5C100`
- `SWP↑` / `SWP↓` — orange `#FF9900`
- `ABS-B` / `ABS-A` — cyan `#00BFFF`
- `ICE-B` / `ICE-A` — purple `#A86DEC`
- `SPD` — red-orange `#FF6633`
- `PULL` / `STACK` — gray `#B0B0B0`
- `ACC-L` — bull green `#2BD25B`
- `ACC-S` — bear red `#FF4D4D`
- `REJ-L` — bull green `#2BD25B`
- `REJ-S` — bear red `#FF4D4D`
- `SCR` — gray `#B0B0B0`

Stacking offset: each event gets a y-offset proportional to its severity
rank so multiple events at the same level/time don't overlap.

---

## 5. Tests

### Python

- Proximate APPROACHING → `WATCH_LEVEL` event, marker_text=`WATCH`, direction=`NONE`.
- TOUCHED → `TOUCHED_LEVEL` event, marker_text=`TCH`, direction=`NONE`.
- STOP_SWEEP near OR-H → `LIQUIDITY_SWEEP`, `SWP↑`, direction=`NONE`.
- STOP_SWEEP near OR-L → `LIQUIDITY_SWEEP`, `SWP↓`, direction=`NONE`.
- ICEBERG defender at OR-H → `ICEBERG_DEFENSE`, `ICE-A`, direction=`NONE`.
- ICEBERG defender at OR-L → `ICEBERG_DEFENSE`, `ICE-B`, direction=`NONE`.
- SPOOF at level → `SPOOF_RISK`, `SPD`, direction=`NONE`.
- Strong counter-flow at level → `ABSORPTION`, marker=`ABS-A` or `ABS-B`.
- pull_stack rotation at proximate level → `PULLING` or `STACKING`.
- ACCEPTANCE PAY → direction LONG/SHORT, severity=ENTRY.
- REJECTION PAY → direction LONG/SHORT, severity=ENTRY.
- All non-entry events → direction=NONE.
- Middle of OR (no proximity) → no WATCH/TOUCHED, but micro events near a
  known level still emit (sweeps don't depend on proximity at mid).
- `trend_signal=STRONG_BULL` alone → no event.
- `pax.decision=ENTER_LONG` alone → no event (no thesis transition).

### Java

- Parser reads all 10 event types.
- History append-only: empty `institutional_chart_events` on later poll
  does not clear prior markers.
- Duplicate id → no replot.
- All marker_text values render to canvas shapes.
- `showInstitutionalChartEvents=true` default.
- `trend_signal` alone draws nothing.
- `pax.decision` alone draws nothing.
- Native CVD/depth markers stay gated.

---

## 6. Sources

1. **CME Rule 575 — Disruptive Practices Prohibited (Spoofing).**
   https://www.cmegroup.com/education/courses/market-regulation/disruptive-practices-prohibited/disruptive-practices-prohibited-spoofing
2. **CME Rule 575 PDF.**
   https://www.cmegroup.com/rulebook/files/cme-group-Rule-575.pdf
3. **CME Market By Order (MBO).**
   https://www.cmegroup.com/education/market-by-order-mbo.html
4. **CME MDP 3.0 — Market By Order Book Management** (Confluence).
   https://cmegroupclientsite.atlassian.net/wiki/display/EPICSANDBOX/MDP+3.0+-+Market+by+Order+-+Book+Management
5. **Zotikov & Antonov (2019). CME Iceberg Order Detection and Prediction.**
   arXiv:1909.09495. https://arxiv.org/abs/1909.09495
6. **Cont, Kukanov & Stoikov (2011). The Price Impact of Order Book Events.**
   arXiv:1011.6402; *Journal of Financial Econometrics* 12(1):47–88 (2014).
   https://arxiv.org/abs/1011.6402
7. **Bookmap — Absorption.** https://bookmap.com/absorption/
8. **Bookmap — MBO Bundle.** https://bookmap.com/mbo-bundle/

CME pages 1, 3 are IP-rate-limited against scripted access. Content
quoted above was retrieved via Google's snippet cache and verified
against the regulator-mirror domains.
