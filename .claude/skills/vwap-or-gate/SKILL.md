---
name: vwap-or-gate
description: Combines true session VWAP (anchored at 09:30 ET via the bridge's bookmap_vwap tool) with OR-Strategy's current orHigh / orLow to return a directional bias gate — ALLOW_LONG, ALLOW_SHORT, or BLOCKED with reason. Also reports σ-band stretch (price vs ±1σ / ±2σ / ±3σ extensions). Documented to lift NQ ORB win rate from ~40% (raw) to ~55% (filtered). Pure deterministic, no LLM math.
---

# VWAP / OR Gate

Long-only bias if Opening Range formed above session VWAP and price is at/above VWAP. Short-only bias if OR formed below VWAP and price is at/below. Conflict → BLOCKED. Also flags the σ-stretch — if price is beyond ±2σ on a band-extension, mean-reversion risk dominates and confidence is downgraded.

## Inputs

- `alias` — instrument alias.

## Procedure

1. Call `bookmap_vwap(alias)`. This returns the session-anchored VWAP (resets daily at 09:30 ET / 08:30 CT) plus `stddev` and the ±1σ/±2σ/±3σ bands (`upper1/lower1`, `upper2/lower2`, `upper3/lower3`). If `samples` is 0 → return `UNKNOWN` with reason "no trades this session yet".
2. Run the `or-bias` skill silently to get the latest OR-Strategy row. Extract `orHigh` and `orLow`. Compute `or_center = (orHigh + orLow) / 2`.
3. Call `bookmap_orderbook(alias, depth=3)` to get the current mid price.
4. Compute σ-stretch: `dev_sigma = (mid - vwap) / stddev`. Label it:
   - `|dev| <= 1` → FAIR_VALUE
   - `1 < |dev| <= 2` → STRETCHED
   - `2 < |dev| <= 3` → EXTREME
   - `|dev| > 3` → BLOWOFF (very high mean-reversion risk)
5. Decide direction:
   - If `or_center > vwap` AND `mid >= vwap` → `ALLOW_LONG`
   - If `or_center < vwap` AND `mid <= vwap` → `ALLOW_SHORT`
   - Otherwise → `BLOCKED` (directional conflict)
6. Apply σ-stretch downgrade:
   - `BLOWOFF` → force `BLOCKED` regardless of direction (chasing here historically gives back the move).
   - `EXTREME` on the same side as the proposed direction (e.g. ALLOW_LONG + price already +2σ over VWAP) → demote to `BLOCKED` with reason "extension exhausted".

## Output

```
{
  "state": "ALLOW_LONG" | "ALLOW_SHORT" | "BLOCKED" | "UNKNOWN",
  "vwap": <number>,
  "stddev": <number>,
  "bands": {"u1": ..., "u2": ..., "u3": ..., "l1": ..., "l2": ..., "l3": ...},
  "or_center": <number>,
  "mid": <number>,
  "dev_sigma": <number>,
  "stretch": "FAIR_VALUE" | "STRETCHED" | "EXTREME" | "BLOWOFF",
  "samples": <int>,
  "sessionStartCt": "<ISO timestamp>",
  "reason": "<one-sentence explanation>"
}
```

## How to use

This skill is a directional **filter** layered on top of OR-Strategy's own bias:

- If OR-Strategy says `LONG_BIAS` but vwap-or-gate returns `BLOCKED` → DOWNGRADE confidence one tier (HIGH → MEDIUM → LOW).
- If both agree → keep confidence as-is.
- If they disagree completely (OR says LONG_BIAS, gate says ALLOW_SHORT) → force STAND_DOWN.
- `BLOWOFF` / `EXTREME` always force STAND_DOWN on chasers — wait for a mean-reversion back to ±1σ or a clean retest of VWAP.

## Notes

- The dashboard renders the same VWAP + bands server-side every 1.5s in the "Session VWAP & bands" card with zero LLM cost.
- The VWAP accumulator lives in the Java add-on and persists across MCP-process restarts as long as Bookmap stays attached. It resets cleanly at 09:30 ET each day.
- If `bookmap_vwap` returns `samples=0`, the strategy attached after the cash open or replay started mid-session — wait for trades to accumulate before trusting the gate.
