---
name: momentum-scan
description: Aggressor-imbalance analysis on recent prints. Pulls the last 200 trades, computes buy-vs-sell volume ratio over rolling 10 / 50 / 200 windows, and flags alignment or inflection patterns. Use when the user asks about "tape", "order flow", "aggressor", "buying pressure", "selling pressure", or "what's the tape doing on X". Read-only — never places trades.
---

# Momentum Scan

Read-only order-flow pulse. Tells the user whether short-term tape is aligned with or diverging from the medium-term tape.

## Inputs
- `alias` — instrument alias

## Procedure

1. Call `bookmap_recent_trades(alias, count=200)`. (Bridge ring buffer is 500, so this is well within range.)
2. From the returned `trades` array (newest first), compute for each window N in [10, 50, 200]:
   - `buy_vol_N` = sum of `size` where `side == "buy"` in the most recent N trades
   - `sell_vol_N` = sum of `size` where `side == "sell"` in the most recent N trades
   - `total_N` = buy_vol_N + sell_vol_N (guard against divide-by-zero)
   - `imbalance_N` = (buy_vol_N − sell_vol_N) / total_N. Range [−1, +1].
3. Decide flag (in order — first match wins):
   - **ALIGNED BULLISH** — all three imbalances > +0.20
   - **ALIGNED BEARISH** — all three imbalances < −0.20
   - **INFLECTION UP** — imbalance_200 < −0.10 AND imbalance_10 > +0.30 (short reversing against medium-term sell)
   - **INFLECTION DOWN** — imbalance_200 > +0.10 AND imbalance_10 < −0.30
   - **NEUTRAL** — otherwise

## Output format

```
{alias} momentum @ {timestamp}
  last 10:   imbalance {+0.42}   ({buy_vol_10} buy vs {sell_vol_10} sell)
  last 50:   imbalance {+0.18}   ({buy_vol_50} buy vs {sell_vol_50} sell)
  last 200:  imbalance {-0.05}   ({buy_vol_200} buy vs {sell_vol_200} sell)
  flag: {FLAG NAME}
  read: {one-sentence interpretation of the flag}
```

## Notes for Claude

- "Buy" in our trade record = ask-aggressor (someone lifted the offer). "Sell" = bid-aggressor.
- Don't conflate imbalance flags with a trade recommendation. This is a read on flow only — the user pairs it with `risk-check` and their own bias before acting.
- If the recent_trades response has fewer than 200 trades (instrument just attached, or quiet), compute over whatever's there and note "(thin sample, N=…)" in the output.
