# Overnight system audit + RTH readiness — 2026-05-29 morning

Run overnight 2026-05-28 by a 6-agent swarm (read-only) + fixes. Scope:
registry, wiring, field-name mismatches, safety boundary, RTH path. Constraint:
**NO logic changes** — only wiring/field/correctness/safety-hygiene bugs fixed;
everything else documented for your greenlight.

Proof: **mcp-server 1057 tests + pax-ai 945 tests green** after all fixes.

## TL;DR
- **RTH is READY.** One manual morning step (see bottom): flip the OpenRange
  anchor 17:00 CT -> 08:30 CT so `session_type` becomes RTH.
- **Live-order safety wall: intact.** Only `server.py` has live-order tools,
  two-gated; the sim/agent path cannot reach them (swarm-confirmed).
- **Live trade path news gate: works.** The dead news gates found are only in
  deprecated analysis surfaces, not the trade path.
- 5 real wiring bugs fixed; ~12 more documented (deprecated/default-off
  surfaces — left alone per "no logic changes").

## FIXED tonight (wiring / correctness / safety-hygiene — no strategy change)

1. **`pax_loop.py` resting-entry case bug (CRITICAL).** Compared working-order
   side `"buy"/"sell"` but SimEngine stores `"BUY"/"SELL"` -> the hold check
   never matched, so the rule churned resting orders (place->cancel->place)
   every cycle and corrupted the BASELINE the agent reads. Now case-insensitive.
2. **`pax_sim_agent.AgentLoop` double-thread race (CRITICAL for arming).**
   `stop()` then `start()` could run two worker threads at once -> double sim
   orders. Added a generation guard so a stale worker retires; `start()` won't
   spawn a second live thread.
3. **Agent self-poisoning lessons.** The agent wrote lessons into
   `sim_lessons.md` even from VETOED/garbage decisions, feeding them back into
   every future prompt. Now only governor-allowed decisions write lessons.
4. **`sim_flatten` missing `ok` key.** FLATTEN/CANCEL succeeded but were logged
   `executed=False` (undercounting calibration). `sim_flatten` now returns `ok`.
5. **Chat digest micro-events field.** `chat.py` read `ev["type"]`; the field is
   `kind` -> the operator chat always showed "(none recent)". Fixed.

## FOUND but NOT changed (your call — these alter behavior or are bloat)

Dead reads in **deprecated / UI-removed / default-off** surfaces (reviving them
changes behavior, so left alone):
- `verdict.py:239` + `level_edge.py:255`: news gate reads `snap.news` (doesn't
  exist; is `snap.gates.news`) -> news gate DEAD in those surfaces. NOTE: the
  **live trade path** (`pax_loop.decide` + `govern`) reads `gates.news`
  correctly, so there is **no live safety hole** — these two are UI-orphaned.
- `verdict.py:359/362`: `vwap_bias.sigma_z/.regime` should be `.components.*`
  -> the VWAP_REVERT setup never fires. (verdict endpoint is UI-orphaned.)
- `triggers.py:374/377/386`: micro-event reads `type/ts/side`; fields are
  `kind/timeMs/isBid` -> MICRO_EVENT trigger dead. (trigger_engine default-OFF.)
- `attack_response.py:160`: thin-tape floor keys `prints30s` off `tape_flow`
  (count lives on `tape_buckets`) -> floor inert. (defensive only.)

Agentic loop notes (work today, flagged):
- Agent alias/interval/model are hard-locked to `DEFAULT_ALIAS` — no API path to
  set them. Consistent (status+execute use the same default) but not configurable.
- `CANCEL_ENTRY` routes to `sim_flatten` (a superset: also flattens position).
  Safe when flat; wrong verb if a position is open with a working entry.
- `AgentLoop.start()` scrubs `BOOKMAP_ALLOW_TRADING` on the pax-ai process env
  permanently. Safe (pax-ai has no live route) but a global side effect.

Bloat / dead code (removal candidates, harmless):
- Dead pax-ai endpoints with no UI consumer: `/api/pax/whynow`, `/playbook`,
  `/verdict/instant`, `/levels/edge`, `/attack-response`, `/skills`.
- Dead JS in `index.html`: `toggleDrawer`, `askAboutTrigger`, `loadHistory`.
- Orphaned CSS for the removed drawers.
- Safety TEST-GAP: the live-order import guard is per-file; no directory-wide
  grep over `bookmap_mcp/*.py`. Not exploitable today; maintenance fragility.

## RTH readiness — READY

- `PROFILE['RTH']` = floor 0.50, width band (3, 60) -> typical wider RTH opening
  ranges are NOT width-gated out (ETH band (3,25) would have blocked them).
- Session detection via `or_day_ledger.session_type` (08:30 CT -> RTH); both
  `pax_loop.decide` and the agent context read it and pick the RTH profile.
- Anchor invariant intact: trades only when `session.anchorMode == LIVE`;
  nothing assumes 08:30 while live. Pinned tests green.

### Morning runbook (the one manual step)
The system does NOT auto-switch ETH->RTH. In the morning, **switch the
OpenRange Bookmap indicator anchor from 17:00 CT (ETH) to 08:30 CT (RTH)** in
the OpenRange UI. Until you do, `session_type` stays ETH and the loop uses the
ETH profile. After the switch, relaunch Pax AI (PaxAILauncher addon) so it loads
tonight's fixes + the ETH/RTH-aware prompt, then Start (observe) -> Arm.
