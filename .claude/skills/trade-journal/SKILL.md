---
name: trade-journal
description: Append every recent fill to a daily CSV log with position, PnL, and the OR-Strategy bias at fill time. Also produces an end-of-day summary on request. Use when the user says "journal my fills", "log this trade", "summarize today", "EOD review", or "show today's trades".
---

# Trade Journal

Two modes: **append** (after fills happen) and **summarize** (on demand).

## File layout

One CSV per trading day:

```
C:\Bookmap\trade-journal\<YYYY-MM-DD>.csv
```

If the file doesn't exist, create it with this header:

```
timestamp,alias,orderId,executionId,side,size,price,simulated,
position_after,avgPrice_after,unrealizedPnl_after,realizedPnl_after,
or_bias,or_action,or_confidence,or_score,or_reason
```

## Append mode

Trigger phrases: *"journal my fills"*, *"log this trade"*, or right after a successful `bookmap_place_limit_order` / `bookmap_place_market_order` call.

1. Call `bookmap_recent_fills(alias, count=20)`.
2. Load today's CSV (if it exists) and collect the set of already-recorded `executionId`s.
3. For each fill where `executionId` is NOT already in the file:
   - Call `bookmap_position(alias)` to capture position state at this moment.
   - Run the `or-bias` skill silently (don't print its output) to grab the latest OR-Strategy snapshot for the same alias. Extract `bias`, `action`, `confidence`, `score`, `reason`.
   - Append a row:
     ```
     {fill.timeMillis ISO},{alias},{orderId},{executionId},{side},{size},{price},{simulated},
     {pos.position},{pos.averagePrice},{pos.unrealizedPnl},{pos.realizedPnl},
     {or.bias},{or.action},{or.confidence},{or.score},"{or.reason escaped}"
     ```
   - Quote `reason` (may contain commas).
4. Print a one-line confirmation: `"Logged {N} new fill(s) to <path>."`

## Summarize mode

Trigger phrases: *"summarize today"*, *"EOD"*, *"end-of-day review"*, *"show today's trades"*.

1. Read today's CSV. If missing or empty, output `"No fills logged today."`
2. Compute aggregate stats:
   - **Trades** — count of rows.
   - **Buys / Sells** — split.
   - **Net realized PnL** — last row's `realizedPnl_after`.
   - **Max unrealized drawdown intraday** — minimum value of `unrealizedPnl_after` seen across all rows.
   - **Max position held** — max of `|position_after|`.
   - **Win/loss count** — pair sequential opposite-side fills (FIFO), tag each closing fill as win or loss based on whether realizedPnl increased.
   - **Average win / average loss** — mean of those.
   - **OR-Strategy follow rate** — count of fills where `side` matched the prevailing `or_bias` direction. `LONG_BIAS` + `side=buy` = follow. `SHORT_BIAS` + `side=sell` = follow.

## Output format for summary

```
Trade journal — {YYYY-MM-DD}

Activity
  trades: {N}      buys: {B}   sells: {S}
  max position: {maxAbsPosition} contracts
  net realized PnL: ${realizedPnl}
  worst intraday uPnL: ${minUnrealizedPnl}

P&L
  winners: {W}   avg win: ${avgWin}
  losers:  {L}   avg loss: ${avgLoss}
  win rate: {pct}%

OR-Strategy alignment
  fills aligned with OR bias: {alignedCount} / {N}  ({pct}%)
  fills against OR bias: {againstCount}

Trade-by-trade (chronological):
{one line per row: HH:MM:SS side size @ price | uPnL ${u} rPnL ${r} | OR={bias}/{action}/{confidence}}
```

## Hard rules

- Never modify a row once it's written. If a fill is amended, append a new row with a `_REPLAY` suffix on the executionId.
- Always quote text fields (`reason`) since they may contain commas, newlines, or quotes (escape inner quotes as `""`).
- Never roll over the file mid-day or rename it; one calendar date = one file.
- Daily file path uses local time (the user's timezone) — Bookmap reports timestamps in epoch millis, convert before writing.
- This skill never places, modifies, or cancels orders. Read-only on Bookmap; write-only on the journal.
