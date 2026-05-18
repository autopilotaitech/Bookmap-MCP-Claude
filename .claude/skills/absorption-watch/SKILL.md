---
name: absorption-watch
description: Checks for absorption / iceberg behavior at the OR-Strategy high or low. Looks for the signature where price reaches the level, heavy aggressor volume hits, and price DOESN'T move — meaning a hidden iceberg is filling the aggressors. Returns STAND_DOWN / EXIT signal when detected. Use right before any OR-breakout entry or as a "should I bail?" check after entry.
---

# Absorption Watch

Documented setup that fakes out raw OR breakouts on NQ: price wicks beyond orHigh/orLow, big buy/sell aggression prints, but the level holds. That's an iceberg sweeping the breakout traders — fade or skip the trade.

## Inputs

- `alias` — instrument alias.

## Procedure

1. Run `or-bias` silently to get current `orHigh`, `orLow`, and live `mid`.
2. Determine proximity. If `mid` is more than 3 ticks from either level → return `NO_LEVEL_NEAR` and stop. (No absorption to check.)
3. Call `bookmap_recent_trades(alias, count=30)` — the last ~10–30 seconds of tape.
4. Pick the side of interest:
   - If `mid` is within 3 ticks below `orHigh` → look for buy-aggression hitting orHigh (long breakout attempt)
   - If `mid` is within 3 ticks above `orLow` → look for sell-aggression hitting orLow (short breakout attempt)
5. Sum aggressor volume in the breakout direction over the last 30 prints. Call this `aggro_vol`.
6. Compute price progress in the same window: `last_price - 30-prints-ago-price`. Call this `progress` (in points).
7. Also call `bookmap_orderbook(alias, depth=5)` and read resting size at the level.
8. Decide:
   - `ABSORPTION` if `aggro_vol >= 200` contracts AND `|progress| < 1 tick * pips` AND resting size at the level is HIGH or REFRESHING (didn't decrease).
   - `ACCEPTANCE` if `aggro_vol >= 200` AND price has broken through by ≥ 2 ticks AND resting size on the breakout side dropped sharply.
   - `INDETERMINATE` otherwise.

## Output

```
{
  "state": "ABSORPTION" | "ACCEPTANCE" | "NO_LEVEL_NEAR" | "INDETERMINATE",
  "level": <orHigh or orLow>,
  "aggro_vol": <number>,
  "progress_ticks": <number>,
  "resting_at_level": <number>,
  "recommendation": "<STAND_DOWN | OK_TO_BREAK | WAIT | NONE>",
  "reason": "<one sentence>"
}
```

## How to use

- Before entry on a fresh OR breakout: if `ABSORPTION` → STAND_DOWN, do not chase. If `ACCEPTANCE` → OK to enter (confluence with the 3-of-4 rule).
- After entry, if you're long and `ABSORPTION` detected at a new high → consider partial scale-out (it's likely a stall, not a continuation).
- Aggressor-volume threshold (200 contracts) is calibrated for NQ. For ES use ~500, for RTY use ~150. Edit this skill file to tune.

## Pairing

The output of `absorption-watch` is the highest-priority demotion in the OR-bias confidence chain. If absorption is detected, it overrides everything — OR-Strategy can say HIGH confidence ENTER_LONG, but if the breakout level is absorbing, you stand down.
