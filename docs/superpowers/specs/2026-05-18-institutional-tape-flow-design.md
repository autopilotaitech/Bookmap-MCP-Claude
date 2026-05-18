# Institutional Tape-Flow Delta — Design Note

**Date:** 2026-05-18 (revised after scope clarification)
**Owner:** will@autopilotaitech.com
**Scope:** replace the "Last 25 prints" browser-only heuristic with a time/volume-aware institutional-flow delta. **All work in Python and dashboard JS.** Java tape-bucket logic (`InstrumentState.tapeBucketsSnapshot()`, `TapeBucketsSnapshot`, `TapeBucketsHandler`) is NOT touched — the new score is computed in Python from the existing `/tape_buckets` bucket-array payload.

---

## Audit — what exists today

| Component | Current state | Problem |
|---|---|---|
| Java `InstrumentState.tapeBucketsSnapshot()` | Builds 5 per-size-bucket aggregates over 30s and 5m windows from `recentTrades`. | OK base layer. No aggregate / no delta. |
| Java `TapeBucketsSnapshot.Bucket` | Public fields per bucket: `buyVol30s, sellVol30s, prints30s, buyVol5m, sellVol5m, prints5m`, plus `imbalance30s()`, `imbalance5m()`. | Good per-bucket detail; no aggregates. |
| Java `TapeBucketsHandler` | Serializes the bucket array with all per-bucket fields including `imbalance*`. Response shape: `{alias, asOfNanos, buckets: [...]}`. | No top-level institutional metric. |
| Python `_source_tape_large_lot` | Expects `tape_buckets.buckets` to be a **dict** keyed by `100plus` / `largeLot`, or falls back to `tape.biasScore`. | **Dead code** — bridge emits `buckets` as a **list**, neither key exists, and `biasScore` is never set. Source returns reliability=0 in practice. |
| Dashboard JS "Last 25 prints" | `s.trades.slice(0, 25)` → size-weighted imbalance heatwave, rendered only. | Not a model input. Display-only count, not time-based, no large-lot weighting. |
| `pax_weights.json` | `tape_large_lot` at base weight 0.06 inside the `microstructure` cluster (cap 0.30 alongside pull_stack 0.10, lt_liquidity 0.04, micro_events 0.04). | Weight is fine; the source just doesn't fire. |

**Existing institutional / large-lot signals in `/momentum`:** OFI (Cont/Kukanov/Stoikov), CVD divergence, VPT absorption, regime classifier, bias trajectory. These are EWMA-windowed micro-flow proxies but operate over ALL trades — they do NOT differentiate 1-lot retail flow from 100+ block prints. That's the gap this work fills.

---

## What we build

**Metric: `deltaScore ∈ [-1, +1]`** plus a `deltaLabel` and `deltaReason`. Computed entirely in Python from the existing `/tape_buckets` bucket-array payload, exposed on the snapshot as `snap["tape_flow"]`. The Java `/tape_buckets` endpoint stays unchanged.

### `snap["tape_flow"]` shape (computed in Python)

```python
{
  "deltaScore": float,                   # in [-1, +1]
  "deltaLabel": "STRONG_BUY|BUY|BALANCED|SELL|STRONG_SELL|THIN",
  "deltaReason": "30s wImb=+0.34, 5m wImb=+0.18, aligned, n30=42",
  "totalBuyVol30s": int, "totalSellVol30s": int, "totalPrints30s": int,
  "totalBuyVol5m":  int, "totalSellVol5m":  int, "totalPrints5m":  int,
  "largeBuyVol30s": int, "largeSellVol30s": int, "largePrints30s": int,  # 51+ lots
  "largeBuyVol5m":  int, "largeSellVol5m":  int, "largePrints5m":  int,
  "blockBuyVol30s": int, "blockSellVol30s": int, "blockPrints30s": int,  # 100+ lots
  "blockBuyVol5m":  int, "blockSellVol5m":  int, "blockPrints5m":  int,
  "largeImbalance30s": float, "largeImbalance5m": float,
  "blockImbalance30s": float, "blockImbalance5m": float,
  "fast": float, "slow": float, "aligned": bool, "shrink": float,
}
```

A new helper `compute_tape_flow(snap)` in `dashboard.py` reads `snap["tape_buckets"]["buckets"]` (the existing list shape from Java) and produces the dict above. Called from `fetch_snapshot` via `_safe_call` like the other composer helpers.

### Scoring formula (proposed)

Weighted aggressor-imbalance over each time window, with bucket weights that emphasize size:

| Bucket | size range | weight `w_b` |
|---|---|---|
| `1-10`   | retail noise | 0.10 |
| `11-25`  |              | 0.20 |
| `26-50`  |              | 0.40 |
| `51-99`  | large lots   | 0.80 |
| `100+`   | block        | 1.00 |

```
weightedImb(W) = sum_b(w_b * (buyVol_b(W) - sellVol_b(W)))
                 / max(1, sum_b(w_b * (buyVol_b(W) + sellVol_b(W))))
              ∈ [-1, +1]

fast = tanh(2.0 * weightedImb(30s))
slow = tanh(2.0 * weightedImb(5m))
base = 0.65 * fast + 0.35 * slow

// Alignment bonus: both windows directional + agreeing
aligned = abs(fast) >= 0.25 AND abs(slow) >= 0.25 AND signum(fast) == signum(slow)
align   = aligned ? 0.10 * signum(fast) : 0.0

// Thin-sample guard
THIN_PRINTS_FLOOR  = 5     // < this in 30s → THIN, score=0
THIN_PRINTS_HEDGE  = 15    // between FLOOR..HEDGE → score shrunk linearly
if prints30s < THIN_PRINTS_FLOOR:
    return (0.0, "THIN", "thin tape: n30=<n>")
shrink = min(1.0, prints30s / THIN_PRINTS_HEDGE)  // ∈ [FLOOR/HEDGE, 1.0]
score  = clip(shrink * (base + align), -1, +1)
```

Label thresholds:

| `|deltaScore|` | label sign |
|---|---|
| < 0.15 | BALANCED |
| [0.15, 0.50) | BUY or SELL |
| ≥ 0.50 | STRONG_BUY or STRONG_SELL |

`THIN` overrides whenever the 30s print count is below the floor.

**Why these choices:**
- `tanh(2x)` saturates softly: |x|=0.5 → ~0.76, |x|=1.0 → ~0.96. Bounded, monotonic, derivative-friendly. Same shape as FlowRegime.
- 65/35 weighting between fast/slow biases toward reactivity but uses 5m to suppress 30s noise.
- Size-weighted aggregator follows institutional-flow inference doctrine: a 200-lot print is not "200×" a 1-lot, but it's not "1×" either. The 0.1→1.0 weight ramp gives 100+ lots ~10× the per-contract influence of 1-10 lots.
- Alignment bonus is small (±0.10) — directional reward, never a substitute for actual imbalance.
- Thin penalty is hard at the floor and linear in the hedge zone. NQ at 09:30 ET sees ~5-10 prints/sec, so n30<5 means a true vacuum; n30=15 means we want SOME confidence by then.
- Bookmap trade records do **not** tag accounts or counterparty. `largeImbalance` is a probabilistic institutional proxy, not a label. The reason field always cites the underlying counts to keep the reader honest.

### Python `_source_tape_large_lot` — rewrite

New logic:

1. Reads `snap["tape_flow"]` (the new helper output) first. If present and `deltaLabel != "THIN"`, returns `score = deltaScore`, reliability from `largePrints30s` / `totalPrints30s`.
2. If `snap["tape_flow"]` is absent (shouldn't happen in normal operation), recomputes from `snap["tape_buckets"]["buckets"]` directly — same formula, max reliability 0.7.
3. If both are missing/garbled: `score=0, reliability=0`, reason explains.

Reliability scale:
- `largePrints30s ≥ 4`: `reliability = clip(largePrints30s / 12, 0.0, 1.0)` — saturates at 12+ large lots in the last 30s.
- `largePrints30s < 4` but `totalPrints30s ≥ 10`: `reliability = clip(totalPrints30s / 40, 0.0, 0.6)` — falls back to broad sample.
- THIN payload: `reliability ≤ 0.1`.

### Dashboard UI changes

- Panel title: **"Tape — institutional flow"** (was "Last 25 prints").
- Top of the panel: heat bar driven by `s.tape_flow.deltaScore` + `.deltaLabel`. Subtitle line shows `large 30s ▲<buy>/▼<sell>` and `block 30s ▲<buy>/▼<sell>` from the same source.
- The existing 25-row print table stays underneath, **with a small caption "Recent prints (display only — not the model signal)"**. Visible row count stays at 25 via a constant `VISIBLE_RECENT_PRINTS = 25`.
- If `s.tape_flow` is missing (early-cold-start, error path), the panel falls back to the existing in-browser size-weighted heatwave with a small "(fallback)" hint.

### Tests

**Python only** — new file `mcp-server/tests/test_tape_flow.py`:
- `test_compute_tape_flow_positive_on_large_buy_pressure` — build a synthetic `tape_buckets.buckets` list where the 100+ bucket is 5×100 buys, 0 sells; assert `deltaScore > 0.4` and `deltaLabel ∈ {BUY, STRONG_BUY}`.
- `test_compute_tape_flow_negative_on_large_sell_pressure` — symmetric.
- `test_compute_tape_flow_is_thin_when_too_few_prints` — `totalPrints30s = 3` → `deltaLabel == "THIN"`, `deltaScore == 0.0`.
- `test_compute_tape_flow_size_buckets_weighted_correctly` — 100 lots of 1-10 buys + 1 lot of 100+ sell → score is negative (size-weighted overrides print count).
- `test_compute_tape_flow_alignment_bonus_applied` — fast and slow both > 0.25 in same direction → assert score includes the +0.10 bump.
- `test_source_tape_large_lot_reads_tape_flow` — `snap["tape_flow"] = {"deltaScore": 0.42, "deltaLabel": "BUY", "largePrints30s": 10, ...}` → `_source_tape_large_lot(snap)["score"] == 0.42`, reliability ≥ 0.8.
- `test_source_tape_large_lot_thin_low_reliability` — thin tape_flow → reliability ≤ 0.1.
- `test_source_tape_large_lot_missing_tape_flow_falls_back_to_bucket_array` — only `tape_buckets` (no `tape_flow`) → recomputes; score within ±0.05 of `compute_tape_flow`.
- `test_source_tape_large_lot_unknown_shape_returns_zero` — empty snap → reliability 0, no exception.
- `test_compute_session_conviction_includes_tape_when_valid` — full bull snapshot with positive `tape_flow.deltaScore` → conviction output's `sourceScores["tape_large_lot"]` is positive.

---

## Files changed

| File | Change |
|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Add `compute_tape_flow(snap)` helper. Wire it into `fetch_snapshot` via `_safe_call`. Rewrite `_source_tape_large_lot` to consume `snap["tape_flow"]` (with bucket-array fallback). Rename panel + label the recent-prints table; backend-driven heat bar at top of the panel. |
| `mcp-server/tests/test_tape_flow.py` | New file, ~10 tests (5 for `compute_tape_flow`, 4 for `_source_tape_large_lot`, 1 for conviction integration). |
| `mcp-server/bookmap_mcp/pax_weights.json` | **No change** — base weight stays at 0.06; cluster cap stays at 0.30. The source is just becoming real instead of dead. |

**Untouched:**
- `src/main/java/com/bookmapmcp/state/InstrumentState.java`
- `src/main/java/com/bookmapmcp/state/TapeBucketsSnapshot.java`
- `src/main/java/com/bookmapmcp/handlers/TapeBucketsHandler.java`
- `src/main/java/com/bookmapmcp/handlers/RecentTradesHandler.java`
- All other Java code
- `mcp-server/bookmap_mcp/server.py`
- `mcp-server/bookmap_mcp/bridge_client.py`

No jar rebuild needed.

## Out of scope

- Per-tick volume-synchronized buckets (VPIN-style). The 30s/5m time windows are coarser but match the snapshot poll cadence (~1-2 Hz).
- Account-level institutional attribution. Bookmap doesn't expose counterparty IDs.
- Magnet-level interaction. Untouched.
- Trading/order paths. Untouched.

## Residual caveats

- A single 1000-lot "fat finger" print can swing the 30s score significantly. The `tanh(2x)` shape softens this but does not fully suppress it. Acceptable — if a 1000-lot just printed, you DO want to know.
- During the 09:30 ET RTH open, the first 30s usually has < THIN_PRINTS_FLOOR prints. The first minute of session may show THIN until the tape ramps. This is correct behavior.
- The bucket count edges (1-10, 11-25, 26-50, 51-99, 100+) are NQ-tuned defaults from existing code. Other instruments (ES, RTY, micros) may need recalibrated cutoffs. Out of scope here.
