---
name: trade-decision
description: Composite decision skill. Fuses session-clock, news-blackout, risk-check, or-bias, vwap-or-gate, absorption-watch, momentum, and the current shadow position into ONE structured verdict — ENTER_LONG, ENTER_SHORT, WAIT, STAND_DOWN, or EXIT — with explicit reason chain. Deterministic decision tree, no LLM math. Use whenever the trader asks "should I take this trade", "what's the call right now", "am I OK to enter", "should I exit", or before any new entry on NQ in playback or live.
---

# Trade Decision

A single skill that runs every gate in the right order and returns one structured verdict. Designed to be the final call before clicking buy/sell. Reads-only — never places orders.

The order matters: cheap deterministic gates first, expensive ones last. If a cheap gate vetoes, expensive gates don't even run.

## Inputs

- `alias` — instrument alias (default `NQM6.CME@RITHMIC`).

## Procedure (run in order, short-circuit on veto)

### Phase 1 — kill switches (cheap, deterministic)

1. **session-clock** — get current window code (`PRE_MARKET`, `OR_FORMING`, `ACTIVE`, `LATE_MORNING`, `CHOP`, `AFTERNOON`, `CLOSE_RISK`, `POST_MARKET`).
   - If code in {`PRE_MARKET`, `OR_FORMING`, `CHOP`, `CLOSE_RISK`, `POST_MARKET`} → **return `WAIT`** with reason "session: {label}". Stop here.

2. **news-blackout** — read `news-calendar.json`.
   - If `blocked` → **return `STAND_DOWN`** with reason "news: {label}". Stop here.

3. **risk-check** — check current shadow position, daily PnL, max-loss cap, max-position cap.
   - If any cap breached → **return `EXIT`** with reason "risk: {which cap}". Stop here.

### Phase 2 — directional thesis

4. **or-bias** — call the OR-Strategy CSV reader.
   - Capture `bias` (LONG_BIAS/SHORT_BIAS/NEUTRAL), `action`, `confidence`, `score`/`maxScore`, `location`.
   - If `bias` is `NEUTRAL` and `action` is `STAND_DOWN`/`WAIT` → **return `WAIT`** with reason "OR-Strategy neutral, score {score}/{maxScore}". Stop here.
   - Note the proposed direction: LONG or SHORT.

5. **vwap-or-gate** — run the VWAP/OR alignment check with σ-stretch.
   - Capture `state` (ALLOW_LONG/ALLOW_SHORT/BLOCKED/UNKNOWN), `stretch` (FAIR_VALUE/STRETCHED/EXTREME/BLOWOFF), `dev_sigma`.
   - **Disagreement rule**: if or-bias says LONG_BIAS but vwap-or-gate says ALLOW_SHORT (or vice versa) → **return `STAND_DOWN`** with reason "OR/VWAP disagree". Stop here.
   - **Blowoff rule**: if `stretch` is `BLOWOFF` → **return `STAND_DOWN`** with reason "price >3σ from VWAP — mean-reversion risk". Stop here.
   - **Extension exhausted rule**: if `stretch` is `EXTREME` on the same side as proposed direction → **return `STAND_DOWN`** with reason "2-3σ extension already exhausted that side".

### Phase 3 — microstructure check

6. **absorption-watch** — check for absorption / iceberg at the proposed entry level (OR breakout level).
   - If `state` is `ABSORPTION` → **return `STAND_DOWN`** with reason "absorption at {orHigh|orLow}". Stop here.
   - If `state` is `ACCEPTANCE` → strong confirmation (boost confidence in Phase 5).
   - If `INDETERMINATE` or `NO_LEVEL_NEAR` → continue, no boost.

7. **momentum** — call `bookmap_recent_trades(alias, count=200)` and compute 10/50/200-print imbalance (or the new time-windowed momentum once shipped). Compare flag with proposed direction.
   - If flag is `ALIGNED BULL` and proposed is LONG → confirmation.
   - If flag is `ALIGNED BEAR` and proposed is SHORT → confirmation.
   - If flag is `INFLECTION` against proposed direction → demote confidence one tier (HIGH→MEDIUM, MEDIUM→LOW).
   - If flag is opposite of proposed → **return `STAND_DOWN`** with reason "tape against thesis".

### Phase 4 — already in a position?

8. Call `bookmap_position(alias)`. Read the shadow position.
   - If `position != 0` and proposed direction is **same** as current position sign → **return `WAIT`** with reason "already long/short, do not pyramid this skill build". (Pyramiding is a separate decision.)
   - If `position != 0` and proposed direction is **opposite** → this is a flip. Treat as a normal entry but call it out: reason includes "reversing existing position".

### Phase 5 — final rollup

9. Compute confidence:
   - Start with OR-Strategy's `confidence` tier (HIGH/MEDIUM/LOW).
   - +1 tier if `absorption-watch` returned `ACCEPTANCE`.
   - +1 tier if `vwap-or-gate.state` is `ALLOW_*` AND `stretch` is `FAIR_VALUE` (clean entry from the mean).
   - −1 tier for each soft demotion from Phases 2–3.
   - Cap at HIGH; floor at LOW.

10. Compute size suggestion: pull from `risk-check`'s `max_size_per_trade` cap.

11. Suggested stop / target (advisory — strategy still owns its own):
    - If LONG: stop = `orLow - 2 ticks`, target1 = `vwap + 1σ` (or first resistance), target2 = `vwap + 2σ`.
    - If SHORT: stop = `orHigh + 2 ticks`, target1 = `vwap - 1σ`, target2 = `vwap - 2σ`.

## Output (always this exact shape)

```
{
  "decision": "ENTER_LONG" | "ENTER_SHORT" | "WAIT" | "STAND_DOWN" | "EXIT",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "size": <int contracts>,
  "entry": <suggested entry price | null>,
  "stop":  <suggested stop price | null>,
  "target1": <suggested target1 | null>,
  "target2": <suggested target2 | null>,
  "reasons": [ "<phase1 result>", "<phase2 result>", ... ],
  "gates": {
    "session":     "<code>",
    "news":        "<clear|blocked>",
    "risk":        "<go|caution|halt>",
    "or_bias":     "<LONG_BIAS|SHORT_BIAS|NEUTRAL>",
    "vwap_or":     "<ALLOW_LONG|ALLOW_SHORT|BLOCKED|UNKNOWN>",
    "stretch":     "<FAIR_VALUE|STRETCHED|EXTREME|BLOWOFF>",
    "absorption":  "<ABSORPTION|ACCEPTANCE|INDETERMINATE|NO_LEVEL_NEAR>",
    "momentum":    "<flag>",
    "position":    <signed int>
  },
  "timestamp_ct": "<HH:MM:SS CT>"
}
```

## Hard rules

- **Never call any order-placement tool.** This skill is read-only.
- **Phase 1 vetoes are absolute.** If session is CHOP, the answer is WAIT — no exceptions, no overrides.
- If any gate skill returns an error or missing data, the decision must be `WAIT` with reason "missing data: {which gate}". Better to skip than guess.
- The `reasons` array must list every gate that ran, not just the deciding one. The user wants to see the full chain.
- Times must be in CT (the user's local zone). Get from `session-clock`.

## Examples of expected outputs

**Good HIGH-confidence long, RTH active, OR break above VWAP, fair-value entry, tape aligned:**
```
decision: ENTER_LONG, confidence: HIGH, size: 2
reasons: ["session ACTIVE", "news clear", "risk go", "OR LONG_BIAS HIGH 4/4", "vwap_or ALLOW_LONG FAIR_VALUE", "no absorption", "tape aligned bull"]
```

**Late morning, OR bias is short, but tape flipped bull mid-session:**
```
decision: STAND_DOWN, confidence: LOW
reasons: ["session LATE_MORNING", "news clear", "OR SHORT_BIAS MED", "vwap_or ALLOW_SHORT", "tape against thesis (INFLECTION UP)"]
```

**CPI window:**
```
decision: STAND_DOWN
reasons: ["news: CPI blackout 08:30-10:00 ET"]
```

## Pairing

This is the top of the food chain — it composes everything else. Other skills should not call this one (no recursion). The dashboard should eventually surface this verdict in a banner above all other cards.
