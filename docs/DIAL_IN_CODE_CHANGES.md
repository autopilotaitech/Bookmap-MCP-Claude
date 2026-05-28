# Dial-In Code Changes Punch List

**Source:** Live dial-in session 2026-05-28 (Pax Group OR, NQ).
**Status:** All items observed against live tape with operator confirmation.
**Audience:** Developer.

---

## 1. Bridge tape fragmentation (HIGH — data quality root cause)

**Observation:** Operator MBO tape (Bookmap settings: Min size=10, "Show
individual trade events") clearly shows single prints at 10, 14, 25+
contracts. Bridge `/recent_trades` reports max size = 9 across last 200
prints. The `tape_buckets` 26-50 / 51-99 / 100+ rows have been ALL ZERO
across the entire dial-in session despite visible 25+ MBO prints on
operator's chart.

**Hypothesis:** Bridge's Bookmap `onTrade` callback receives MBP-aggregated
or sub-divided trade events. The full atomic MBO print arrives as multiple
small `TradeInfo` events on the bridge side.

**Why it matters:** every "big-bucket" filter built on top of
`tape_buckets` is currently a false negative. Today the rule
"no entry without 26-50 bucket activation" would have blocked the +60pt
short-through-+3-ext setup at 13:38 CT (operator confirmed level break
with liquidity-void chart). Pretend SHORT taken on operator's call WITHOUT
big-bucket confirmation; trade live and managed.

**Fix candidates (Java bridge):**
1. Inspect `bookmap-mcp-bridge` `onTrade` listener wiring — does it
   subscribe to MBO events when MBO is available, or always MBP?
2. Add an aggregation buffer: same `(price, side, nanos within 10ms)`
   collapse to single print before exposing on `/recent_trades`. This
   reconstructs the MBO atomic print from the MBP fragments.
3. Add a `/recent_trades` query param `?aggregated=true|false` so
   consumers can pick.

**Test surface:**
- Sample of operator's MBO Tape settings (size>=10 filter, show individual)
  during the same window as a bridge `/recent_trades` snapshot. Diff the
  print counts and max sizes.
- Add unit test in `BridgeServer` for the aggregation buffer behaviour.

**Workaround for Python side (until bridge fixed):**
- `institutional_flow.py` proposed big-bucket whipsaw filter must NOT use
  bridge `tape_buckets` alone as the institutional-presence test. Use
  side-imbalance ratio (`buyVol / (buyVol + sellVol)` across all buckets
  for the same trailing window) plus regime peak_favorable as proxies.

---

## 2. `ifl_outcomes` verdict undercounts winning setups (HIGH)

**Observation:** Trade #3 (LONG 30305.50, partial 30313.50 +8pt, runner
30320.75 +15.25pt = +23.25pt total = +$11.63 WIN). The ACCUMULATION
episode that triggered the entry closed verdict **MISS** because no
65pt rotation rung was advanced.

**Why it matters:** the verdict tier doesn't distinguish "real partial
move that paid for the trade" from "did nothing." Today's lunchtime
4-of-4 STALE/MISS episodes had peak < +1.5pt — real noise. Trade #3's
ACCUM ep had peak +8.875pt — real partial move, but still verdict-stamped
MISS.

**Fix (`ifl_outcomes.py`):**
- Either lower PARTIAL_HIT threshold from 32.5pt (half-rotation NQ) to
  ~20pt (quarter rotation), OR
- Add new tier `WORKING_PARTIAL` for peak_favorable >= 5pt AND
  adverse_pts > peak_favorable (i.e. mean-reverted but had real life)
  so downstream tuning can separate "good ep that mean-reverted" from
  "ep that never had a pulse".
- Pinned by `test_ifl_outcomes.py` — add tier verification.

---

## 3. `institutional_flow` signal-fires-at-exhaustion problem (HIGH)

**Observation:** ACCUMULATION at 12:55 fired at 30333 = top of the
30260→30341 push. Immediately went STALE without follow-through. Same
pattern at 13:04 DIST fired at 30331 (peak push area). Same morning
09:40-09:49 whipsaw cluster. All three required conditions to fire are
LAGGING:

1. `raw_vote` crossing threshold (needs vote to BUILD over many ticks)
2. `micro_events` aggregation (180s window, persistence after price flip)
3. `trend_filter` ALIGNED (15s candle trend, lags 4-5 min behind real
   price reversals — live-confirmed today, see trade #3 entry analysis)

**Fix (proposal):** add Stage A / Stage B model to
`institutional_flow.py::compute_institutional_flow`:

- **Stage A (initiation):** loose conditions, fires earlier
  (raw_vote delta > 0.15 / tick, single strong micro event with size>=25,
  vwap-band-touch, level-test entry). Stage A signals trade with TIGHT
  stops, smaller size.
- **Stage B (confirmation):** current strict gates. Stage B signals
  trade with full size or scale-up on Stage A position.

`ifl_outcomes` verdict gets `stage="A"|"B"` field so tuning can compare
Stage A win rate vs Stage B win rate independently.

**Acceptance test:** the 12:55 ACCUM at the top of the push should NOT
have fired Stage A (it was at exhaustion). The 13:24 absorption LONG
should have fired Stage A at the +3 ext bounce (trade #3 thesis).

---

## 4. `micro_events` decay window too slow (MEDIUM)

**Observation:** at 13:00 CT trend FLIPPED DOWN, lt_ratio flipped negative,
price -23pt off high, but `micro_signed` stayed +0.85 for 3+ minutes
afterward. micro_events is THE LAST signal to flip on a reversal.

**Current:** `_MICRO_EVENT_WINDOW_SEC = 180.0` (widened today from 90s
during the morning whipsaw retune).

**Fix candidates:**
- Reduce window to 60s, OR
- Replace flat-window decay with exponential decay (30s half-life), OR
- Cap micro contribution when trend just flipped against it
  (`if abs(time_since_trend_flip) < 90: micro_weight *= 0.5`).

Pinned by `test_institutional_flow.py` — add reversal-decay test.

---

## 5. Whipsaw lockout (MEDIUM)

**Observation:** today had two whipsaw clusters: morning 09:40-09:49 (4
fires in 9 min, all MISS), lunchtime 12:55-13:13 (5 consecutive
STALE/MISS with peak < +1.5pt). In both clusters, every new fire was
expected to fail based on the recent eps.

**Proposed (`institutional_flow.py`):**

```python
# Suppress NEW regime fires when:
#   - last 3 closed eps all in (STALE, MISS, PARTIAL_HIT_LOW)
#   - AND each peak_favorable < 5pt
#   - AND within last 15min
# Lockout duration: 10 min idle (no fires)
# State key: `_REGIME_LOCKOUT_UNTIL_MS[alias]`
```

Visible to operator on dashboard as `flow.lockout_until_ms` and
`flow.lockout_reason`.

Pinned by `test_institutional_flow_whipsaw_lockout.py` (new).

---

## 6. `trend_filter` lag tax (MEDIUM)

**Observation:** the 15s candle TimeBucketTrendAccumulator means trend
ALIGNS 4-5 minutes after price actually flips. Trade #3 entry would
have been BLOCKED by my own rule (trend not yet aligned UP) — and the
ALIGN'd-up state arrived only after price had moved +20pt above the
absorption. Symmetric on the way down at 13:00 CT.

**Fix options:**
1. Faster bucket: 3s or 5s. Trade-off: more whipsaw flips.
2. Multi-resolution trend: blend a fast (3s) + slow (15s) trend. Fast
   triggers entry, slow gates direction. Already documented as the
   composite `trend_analyzer` model.
3. Expose trend_velocity (`d(trend) / dt`) so consumers can detect
   "about to flip" before the official flip.

`trend_signal.eligible` already exists for the eligibility gate; could
add `trend_signal.predicted_kind` based on velocity.

---

## 7. Watcher loop big-bucket fields (LOW — dial-in tool)

**Observation:** `_session_snapshots/watcher_loop.ps1` records flow
fields but NOT tape_buckets. Today added live big-bucket inspection
on each wake; should bake into watcher row for post-session analysis.

**Fix:** extend the watcher_loop JSONL row with
`big_buy_30s, big_sell_30s, big_buy_5m, big_sell_5m, big_imb_5m,
max_print_size_30s` (sum of 26-50 + 51-99 + 100+ buckets, plus single-
print scan from `trades` list).

Once bridge fix (#1) lands, these fields become the gold-standard
institutional-presence signal.

---

## 8. Java OpenRange WATCH labels disagree with Python `institutional_flow` (KNOWN)

**Observation:** screenshot at 13:38 CT showed Java chart label
`WATCH BEAR +3 EXT HIGH EXHAUST` while Python institutional_flow had
just emitted DISTRIBUTION with peak +14.375. Operator already flagged
this in earlier memory note `post_market_close_chart_label_reconcile.md`.

**Status:** punch list item carried forward. Reconcile after market
close OR over weekend.

---

## Operator-stated rule additions (no code yet, but inform tuning)

### Tape big-print rule (operator 2026-05-28)
- **NQ:** single aggressive prints of 25+ contracts are institutional
  thrust orders. Do not fade.
- **Action:** entry SIDE must match the side of any 25+ print in the
  trailing 30s window during level tests. Most relevant at OR boundary,
  +/-N extensions, session H/L.
- **Code touch-point:** `attack_response.py` should consult tape buckets
  for fade-side recommendations. If same-side big print exists in trailing
  30s, downgrade fade row to NEUTRAL.
- **Pending bridge fix (#1) before this can land** — bridge currently
  reports zero big-bucket activity even when MBO shows them clearly.

### Liquidity-void / volume-profile target (operator 2026-05-28)
- Operator reads volume profile for "where bids stop" between current
  price and next significant level cluster.
- **Code:** `volume_profile` snapshot section needs a derived
  `bid_void_below_pts` / `ask_void_above_pts` field that measures the
  distance from current mid to the next significant volume cluster.
- Today's example: 30293 (current) to 30237 (cluster) = ~56pt void below.
  That was the operator's short target. Currently the operator reads
  this visually; bringing it into `snap.volume_profile.voids[]` lets
  Pax AI and chart popups reference the same number.

---

## Priority ordering for the developer

1. **#1 bridge tape fragmentation** — unblocks #5, #7, and the tape
   big-print rule. All downstream institutional-presence logic is wrong
   until this is fixed.
2. **#2 ifl_outcomes verdict tier** — quick fix; rebalances training
   signal so we stop scoring real partial wins as MISS.
3. **#3 Stage A / Stage B model** — biggest structural win. Cuts the
   "fire at exhaustion" pattern that drove 5/5 lunchtime MISS today.
4. **#5 whipsaw lockout** — easy add once verdict tier (#2) is right.
5. **#4 micro decay window** — small tweak, eliminates the post-flip
   noise.
6. **#6 trend velocity** — exposes an early signal without breaking
   the existing trend_signal API.
7. **#7 watcher loop fields** — dial-in tool only, low priority.
8. **#8 OpenRange WATCH-label reconcile** — known, deferred.

---

## Pretend P&L snapshot (closed when this doc was written)

| Trade | Direction | Entry | Exit | Pts | $ |
|---|---|---|---|---|---|
| #1 | LONG | 30300.5 | 30300.0 | +1.0 | +$1.00 |
| #2 | SHORT | 30331.75 | 30328.50 | +6.5 | +$13.00 |
| #3 partial | LONG | 30305.50 | 30313.50 | +8.0 | +$4.00 |
| #3 runner | LONG | 30305.50 | 30320.75 | +15.25 | +$7.63 |
| #4 | SHORT | 30294.75 | OPEN | +1.25 unreal | TBD |
| **Closed total** | | | | **+30.75pt** | **+$25.63 / 3-0** |

Discipline working at small scale despite the system showing every
calibration weakness above.
