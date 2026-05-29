# ETH auction strategy — audit + plan (2026-05-29)

Status: PLAN for operator review (per CLAUDE.md no-drift: plan before build).
ETH and RTH must be DIFFERENT strategies that use the OR levels differently.

## 1. Audit — what ETH does today (the problem)

`pax_loop.decide()` is ONE strategy with two tuning knobs: `PROFILE` floor
(ETH 0.35 / RTH 0.50) and width band. The actual entry logic is identical in
both sessions: **FOLLOW breakout at OR-H/OR-L only** (rungs + fades deferred).
So "ETH" today = RTH breakout-follow with a looser floor. The agent prompt now
says "ETH = rotation, EXT-to-EXT" but there is NO mechanical rule behind it —
it's LLM judgment, which is why it stands down and misses the overnight
rotations. **There is no real ETH strategy.** That is the gap this plan fills.

What we DO already have (verified, real snapshot fields — build on these, don't
reinvent): `volume_profile` {vpoc/poc, vah, val, hvn/lvn}; `vp_bias`
{poc_z, va_state = ABOVE_VAH/BELOW_VAL/INSIDE, components}; per-session VP
(vpEth/vpRth in InstrumentState); `or_levels` (OR-H/OR-L + 65pt rungs);
`or_day_ledger.session_type` (ETH/RTH/EU).

## 2. Research-grounded ETH thesis (auction market theory)

Overnight (ETH) is the BALANCE / book-balancing session, not a trend session:
- Markets spend ~70-80% of time INSIDE the value area (balance), 20-30% in
  trend. ETH is mostly balance -> mean-reversion edge, not breakout edge.
- **80% rule**: price opening/pushing OUTSIDE the value area and then trading
  back INSIDE has ~80% odds of traversing to the OPPOSITE value-area boundary.
- **Naked / prior-day POC = magnet**: prior-session POC not yet revisited gets
  revisited ~85% within 10 sessions. Operator: "usually retraces to previous
  day POC." This is the primary ETH target.
- **Failed auction reversal**: a break that can't hold (no acceptance outside
  balance) snaps back to the POC inside balance. Operator: "fail auction then
  bounce."
- ~70% of opens outside value trade back into the prior-day value area.
- High-probability setup = THREE references agree: a STRUCTURAL level (OR-H/L,
  rung), a VOLUME reference (POC / VA boundary / naked POC), and an ORDER-FLOW
  confirmation (stall / rejection / delta). 
Sources in the chat message.

## 3. The strategy — both follow the money; ETH adds a second leg

Core for BOTH sessions: FOLLOW THE MONEY (orderflow: pull_stack rotation, LT
resting liquidity, big aggressive prints, CVD/OFI). The difference is what plays
are live, not a different philosophy.

**RTH = the Pax OR strategy + orderflow "flare".** The documented Pax OR
breakout/follow (entries at OR-H/OR-L, rungs as continuation) with the operator's
orderflow signals layered behind it to confirm/enhance. (This is the "website"
strategy + the flare.) Unchanged — it works.

**ETH = TWO legs (it is NOT fade-only):**
- **Leg 1 — OR breakout (same as RTH, esp. at the ETH open).** The overnight OR
  forms and breaks; trade the break/follow exactly like RTH, orderflow-confirmed.
  Do NOT drop breakouts in ETH.
- **Leg 2 — reversal back to POC before RTH open (the balance play).** Most
  nights balance out: after a push, price rotates back to value before RTH.
  Catch that rotation to POC / prior-day POC. This is the EXTRA edge ETH offers
  over RTH. Setups for leg 2:
  - **Failed-auction reversal:** break of OR/rung with NO acceptance (stall /
    reject / no follow-through) -> reverse back to balance.
  - **Value-area rotation:** price outside VAH/VAL (va_state ABOVE_VAH/BELOW_VAL)
    -> rotate back to POC; runner to the opposite VA boundary (80% rule).
  - **Prior-day / naked POC retrace:** target the unfilled prior-session POC
    magnet ("usually retraces to previous-day POC").

Management (operator rule, both sessions): 2 contracts — one PAYS at the first
target (payline / POC), one RIDES (next rung for breakouts; opposite VA boundary
/ prior POC for the reversal leg). Resting buy/sell stop-limit or limit at the
level; ~5-10pt off is fine (react, don't analyze); never chase mid.

Confluence gate (the edge filter, both legs): STRUCTURAL level (OR/rung) + a
VOLUME reference (POC / VA boundary / naked POC) + ORDER-FLOW (break thrust for
leg 1; stall/rejection for leg 2). Three references agree = take it.

## 3b. Risk / re-entry model (both sessions) — and a governor it contradicts

Operator model: tight stops + RE-ENTER to catch the move (may miss 2-3 tries;
re-entry is the edge, not revenge). Get to break-even, ride the runner.
Asymmetry: 3 scratch losses (~16-25pt total) are covered by ONE rotation (65pt)
and still net ahead. The OPEN is harder to judge -> wider stop at the OR. At
extensions expect a bounce or two before price decides; usually the day's
direction is clear by the 1st ext / 1st hour.

**Governor retune required (P2) — current settings sabotage this model:**
- The 5-min post-trade COOLDOWN blocks re-entry. Re-entry IS the edge -> allow
  same-move re-entry (cap spam a different way, e.g. max attempts per level).
- The 2-LOSS daily stop halts right before the winner. Measure stand-down in NET
  points/R (e.g. drawdown > ~1 rotation), NOT a raw loss count.
- Wider stop at the open (session/phase-aware stop width).
Exact numbers (re-entry cap, stop width, net-R stop) are LEARNED from data, not
guessed. These are the only governor changes; the safety wall is untouched.

## 4. Data + the LEARN-ETH-separately requirement

- Tag every decision/outcome with `session_type`. Calibrate ETH-fade-to-balance
  setups SEPARATELY from RTH-breakout-follow (separate buckets in
  `pax_sim_calibration`), so the agent finds ETH edge from ETH outcomes only —
  RTH stats must not contaminate ETH and vice-versa.
- The agent reads its ETH calibration before deciding in ETH; lessons are
  session-scoped.
- DATA TO ADD (we don't store it yet): **prior-day / naked POC** (cross-session
  POC carried forward until revisited) and a confirmed **ETH-session value area**
  in the snapshot. `or_day_ledger` already persists session boundaries — extend
  it to snapshot the prior session's POC/VA.

## 5. What's removed / simplified (not just more infra)

- Delete the "ETH = RTH-with-lower-floor" hack. ETH gets its own ruleset; the
  floor knob stops pretending to be a strategy.
- The agent stops being asked to "trade rotations" with no definition — it gets
  the mechanical ETH setups above as its baseline, and only exercises judgment
  within them.
- Net: one new deterministic ETH branch + one new data field (prior POC) +
  session-split calibration. No new framework, no new process.

## 6. Edge / success measure (falsifiable)

EOD ETH review shows: fades-to-POC have positive expectancy and hit the balance
target; "failed-auction" entries reverse as expected; reads-vs-tape shows it
caught the overnight rotations back to POC/prior-POC that it currently misses.
If ETH calibration expectancy is not positive after N sessions, the ETH ruleset
is wrong and we cut/retune it — judged from stored outcomes, not opinion.

## 7. Phases (each reviewable; sim only; promotion-gated)

- **P1 (data):** surface POC/VAH/VAL/va_state + prior-day(naked) POC into the
  snapshot + agent context (build on existing vp_bias / volume_profile).
- **P2 (rule):** add the deterministic ETH fade-to-balance branch in `decide()`,
  session-keyed (RTH branch unchanged). OR levels = FADE in ETH.
- **P3 (learning):** session-split calibration buckets; agent reads ETH stats.
- **P4 (agent):** align the agent prompt to the mechanical ETH setups.
Promotion gate unchanged: research_only -> replay_passed -> paper_candidate ->
paper_passed -> human_approved -> active.
