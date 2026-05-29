---
name: pax-trade-loop
description: Use when the operator asks to run, resume, continue, or check the live pax_algo_v1 sim trading loop on NQ (ETH or RTH session), or when picking a trading session back up after a reboot / fresh Claude session. Triggers include "trade ETH", "run the loop", "start trading the sim", "what's the loop doing", "pick up where we left off", "you trade this".
---

# pax-trade-loop — run/resume the pax_algo_v1 sim trading loop

## Core principle

**Read the documented rules, then REACT — never reinvent.** A reboot wipes
the live session context; do not guess to fill the gap. The Pax OR strategy is
already specified and the dashboard already computes the decision. You execute
it; you do not re-derive thresholds. "We get paid to react, not analyze." Sim
(paper) only.

## STEP 0 — On a fresh/rebooted session, READ THESE FIRST (do not skip)

1. `skills/pax-or/SKILL.md` — the authoritative Pax OR ruleset (entries at
   OR-H/OR-L only, confirmation, stretch gate, rungs, stops, scale-out).
2. Memory: `eth_sim_loop_operating_model`, `feedback_read_records_dont_reinvent`.
3. `_session_snapshots/eth_trade_loop_spec.md` — the active loop spec.
4. `_session_snapshots/eth_loop_tick.py` — the pure-function tick executor.

If the rule's exact logic is unclear, recover it from the prior session
transcript (`C:\Users\autop\.claude\projects\C--Bookmap-addons-MCP-Bookmap\*.jsonl`)
— do NOT fabricate a substitute.

## The rule (pax_algo_v1, v3 — documented)

REACT to `snap.or_levels` (the dashboard already resolved FOLLOW/FADE +
confirmation into a per-level decision):

| Field | Use |
|---|---|
| `middleLock` | true -> SIT (inside OR, no position) |
| `inProximity` | false -> SIT (not within ~50 ticks of a level = stretch gate s2.4) |
| `levels[].decision` | ENTER_LONG_* / ENTER_SHORT_* / WAIT |
| `levels[].confidence` | must be >= floor |
| `levels[].proximity` | only the proximate level is actionable |

- **Entry** only at OR-H / OR-L boundary, FOLLOW only, `confidence >= floor`,
  rotation not against (ps_rot). Place a RESTING order AT the level and let
  price come to it — never chase (operator rule 2026-05-28):
  - LONG (OR-H break): buy STOP-LIMIT, trigger = orHigh, limit = orHigh+2tk.
  - SHORT (OR-L break): sell STOP-LIMIT, trigger = orLow, limit = orLow-2tk.
  - FADE + rung (+N/-N) entries are DEFERRED (v4 marks ARMED).
- **Stop** = OR midpoint +/- breathing room (3pt testing value): a break that
  fails back through the OR middle is stopped ("middle forbidden"). **TP1** =
  level +/- 10pt, **TP2** = level +/- 65pt rung. Order = 2 units via
  `pax_manual ... --entry-stop <trigger>`.
- **Governor (v4)**: verify the place subprocess succeeded before claiming a
  fill; never stack a 2nd entry while one is WORKING (HOLD it, cancel only on
  invalidation); 5-min post-trade cooldown; daily stop from sim `losers_today`;
  in-position management runs before the entry gates.
- **In position**: resting entry triggers -> brackets manage TP/stop; flatten if
  price falls back inside the OR (failed break, s2.3 "middle forbidden").
- **Daily stop**: 2 losers -> stand down (s5.4), wired to sim `losers_today`.
- **Testing**: `python eth_loop_tick.py --dry-run` reads live data + logs the
  decision but places NO order. `decide()` is a pure function pinned by
  `_session_snapshots/test_eth_loop_tick.py`.

## ETH vs RTH (they differ — do not forget)

Same OR mechanics; tuning is keyed to `or_day_ledger.session_type`. Confidence
floor: **ETH 0.35, RTH 0.50**. The full ETH threshold/weight profile is still
being calibrated — treat ETH floor as tunable and confirm with operator before
changing it (do not invent thresholds).

## Running stack (all local; sim/paper only)

- Dashboard `:18888` (snapshot source), overview `:18890`, tick-only daemon
  (`pax_daemon --no-auto-decide` — fills orders + journals, does NOT auto-trade),
  watcher loop 60s -> `D:\BookmapLogs\watcher_ps_log.jsonl`.
- Sim CLI: `python -m bookmap_mcp.pax_manual {long|short|flatten|status}` with
  `BOOKMAP_ALLOW_TRADING=` cleared. Sim DB `D:\BookmapLogs\pax-daemon-trades.db`.

## Start / run / stop the loop

- **Loop = `CronCreate` `*/1 * * * *`** firing a prompt that runs
  `python _session_snapshots\eth_loop_tick.py` each minute and logs one JSON
  line to `D:\BookmapLogs\eth_claude_loop.jsonl`. Session-local: dies if the
  Claude session closes; the daemon/dashboard/watcher keep running.
- Each tick reply = ONE line: `state | action | mid/levels | reason`.
- **Stop**: `CronDelete <id>`, then `pax_manual flatten` if a position is open.
- Auto session-stop is OFF by default (`STOP_CT_HOUR=None`) — operator stops
  manually. The old `ct_h >= 24.0` branch was dead code (hour never reaches 24).
  OPEN RULE: does ETH trade right up to the RTH 08:30 open, or stop at midnight?
  Set `STOP_CT_HOUR` once decided. Until then, operator "stop trading" is the cut.

## Resume-after-reboot checklist

1. Read STEP 0 files. 2. Verify processes: dashboard owns `:18888`
(`/api/snapshot` returns `health:ok`), one tick-only daemon, watcher running.
3. `pax_manual status` -> note position / realized / losers. 4. `CronList` ->
is the loop already scheduled? 5. Resume or re-create the cron. Do not flip the
daemon between auto-decide/tick-only repeatedly — tick-only is correct when
Claude drives.

## Known issues (carry forward, fix deliberately)

- **Sim fills at NQ $20/pt, not MNQ** — `qty 2` = 2 NQ (~10x intended MNQ risk).
  Confirm multiplier before sizing.
- Thin ETH tape -> low confidence + DEAD lt; eligibility-style gates over-block
  (v3 bypasses lt/eligibility, gates on or_levels decision instead).
- Tiny ETH ORs (~14pt) are fakeout-prone; the break may not hold (live-observed
  2026-05-28: OR-L FOLLOW short conf 0.48 was a fakeout, scratched -1 unit).

## Communication

Terse one-line ticks. Talk like a person, own mistakes plainly, surface real
findings instead of burying them. Don't over-explain routine SIT/ARMED ticks.

## What NOT to do

- Don't invent entry rules or thresholds — read `pax-or` + `or_levels`.
- Don't chase a break already past proximity (stretch gate).
- Don't trade inside the OR. Don't flip mid-trade without a fresh confirmed break.
- Don't place live orders — sim only; live routing stays gated in `server.py`.
- Don't reinvent the setup each session — this skill + the memory ARE the setup.
