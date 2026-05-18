---
name: risk-check
description: Pre-trade safety gate. Reads position, working orders, balance, and current orderbook before any order. Returns GO with state summary or NO-GO with the specific blocker. Run this BEFORE every call to bookmap_place_limit_order or bookmap_place_market_order. Use phrases like "is it safe to", "risk check", "pre-trade check", or whenever the user is about to place an order.
---

# Risk Check

A defensive checklist Claude runs before any order placement. Stops at the first failure and reports the exact reason.

## Required inputs (ask the user if missing)
- `alias` — instrument alias (use `bookmap_list_instruments` if unsure)
- `side` — `buy` or `sell`
- `size` — positive integer contracts
- `price` — limit price, or the literal string `market`

## Default thresholds (edit this file to change)

| Rule | Default |
|---|---|
| Max absolute net position after this trade | 5 contracts |
| Daily unrealized PnL floor | −$500 |
| Daily total PnL floor (realized + unrealized) | −$1,500 |
| Max working orders per side already on this instrument | 3 |
| Max distance from BBO before a "limit" behaves like a market | 5 ticks |

## Procedure (stop at first NO-GO)

1. **Bridge alive?** Call `bookmap_ping`. If it errors, NO-GO with "bridge offline".
2. **Position state.** Call `bookmap_position(alias)`. Read `position`, `averagePrice`, `unrealizedPnl`.
   - NO-GO if `unrealizedPnl < -500`. *(drawdown brake)*
   - Compute `projectedPosition = position + (size if side=="buy" else -size)`.
   - NO-GO if `|projectedPosition| > 5`. *(size cap)*
3. **Working orders.** Call `bookmap_working_orders(alias)`. Count orders on the same `side` (matching buy/sell).
   - NO-GO if count >= 3 on that side.
4. **Balance / daily PnL.** Call `bookmap_balance(alias)`.
   - Read first currency entry's `realizedPnl` + `unrealizedPnl`.
   - NO-GO if that sum < −$1,500. *(day cap)*
5. **Price sanity.** Call `bookmap_orderbook(alias, depth=5)`.
   - If `price == "market"`: skip price check, note it as marketable.
   - Else for BUY: NO-GO if `price > bestAsk + 5 * pips`. *(would cross excessively)*
   - Else for SELL: NO-GO if `price < bestBid - 5 * pips`.

## Output format — ALWAYS use one of these two

**Pass:**
```
GO. {side} {size} {alias} @ {price}
  position: {position} (avg {averagePrice})
  uPnL: ${unrealizedPnl}   rPnL: ${realizedPnl}
  working orders: {workingBuys} buy / {workingSells} sell
  book: {bestBid} / {bestAsk}   spread: {spread}
You may now call the order-placement tool.
```

**Fail:**
```
NO-GO: {one-line reason}
  Triggered rule: {rule name}
  Current state: {the relevant numbers}
Do NOT call any order-placement tool. Address the blocker first.
```

## Hard rules

- Never silently rerun after a NO-GO. The user must explicitly acknowledge and ask again.
- Never tweak the thresholds in the same conversation that produced a NO-GO. The thresholds protect against in-flight rationalization.
- This skill never places an order itself — only reads.
