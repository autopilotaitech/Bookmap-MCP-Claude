# PAX AI SIM Autopilot Runbook

Operational runbook for the SIM-first PAX autopilot stack. **This stack is
paper/SIM only. Live order placement is hard-blocked.** See "Live trading
blockers" at the bottom.

## TL;DR commands

```cmd
paxi.bat start        :: hidden observe-mode autopilot + overview UI (:18890)
paxi.bat armed        :: hidden ARMED SIM autopilot + overview UI
paxi.bat stop         :: stops PAX autopilot/overview/old tick cron ONLY
paxi.bat restart      :: stop then observe start
paxi.bat status       :: prints matching PAX processes + cron state
```

`paxi.bat` launches hidden `pythonw.exe` (no terminal popups) and disables the
legacy `PaxAgentCron` task. `stop` targets only PAX processes
(`pax_autopilot` / `overview_ui` / `pax_agent_tick` / `pax_daemon` / `pax_ai`)
by command-line match and window title; it does **not** touch the Bookmap
bridge, OpenRange, or Bookmap itself.

## Pre-market SIM acceptance workflow

Run this before arming SIM. It is read-only end-to-end (no broker order, no LLM,
no service auto-start). SIM-only; live stays hard-blocked.

```cmd
paxi.bat stop                                   :: 1. clean slate (writes a session report)
paxi.bat start                                  :: 2. observe mode + overview UI
curl http://127.0.0.1:18890/api/health          :: 3. freshness / kill switch / risk halt / evidence
curl http://127.0.0.1:18890/api/arming_check    :: 4. machine go/no-go
python -m bookmap_mcp.pax_acceptance            :: 5. one JSON verdict (overall pass|warn|fail)
paxi.bat armed                                  :: 6. ONLY if acceptance is not "fail"
:: ... trade SIM ...
paxi.bat stop                                   :: 7. stop writes session-report.json (+archive)
python -m bookmap_mcp.pax_evidence_report --replay   :: 8. after the session, grade the evidence
```

`pax_acceptance` verdict: **FAIL** on kill switch / stale heartbeat / stale market
/ unreadable SIM broker / `live_blocked` not true (exit code 1). **WARN** when
evidence is below `replayable`, `replay_input`/scorecard/session-report missing,
or R-denominated risk counters unavailable (exit code 0). **PASS** needs clean
ops AND evidence at least `replayable` AND no warns. Honest note: in the current
build R-denominated counters are unavailable, so a clean stack typically reports
**WARN, not PASS** -- that is expected; it means "operationally ready for SIM,
real data + tuning still needed", not a failure.

## Lifecycle

### Start observe mode (default, safe)

```cmd
paxi.bat start
```

Observe mode runs the deterministic decision loop and narration but places no
SIM orders. Verify:

```cmd
curl http://127.0.0.1:18890/api/health
```

### Arm SIM (paper execution)

Arm only after the truth/health endpoints look right.

```cmd
paxi.bat armed
```

Armed mode lets the deterministic governor place **SIM** brackets on the local
SQLite paper broker (`pax-daemon-trades.db`). The LLM never bypasses the
governor; `BOOKMAP_ALLOW_TRADING` is scrubbed in the autopilot process.

### Stop

```cmd
paxi.bat stop
```

`stop` first writes a **session report** (best-effort, read-only) BEFORE killing
processes, then stops only the PAX autopilot/overview/cron. It runs
`python -m bookmap_mcp.pax_session_report --archive` synchronously in the same
console (no new/persistent terminal), wrapped in `pushd "%SRV%"` / `popd` so it
is independent of the caller's working directory; `popd` runs even if the report
fails, and a report failure prints a notice and the stop continues regardless. The canonical report lands at
`D:\BookmapLogs\pax-agent\session-report.json` and a timestamped copy at
`D:\BookmapLogs\pax-agent\sessions\session-report-YYYYMMDD-HHMMSS.json`. It does
NOT touch Bookmap / OpenRange / the bridge and does not require Bookmap open.

### Go/no-go before arming

```cmd
curl http://127.0.0.1:18890/api/arming_check
```

`can_arm` is true only when every BLOCKING check passes: kill switch absent,
heartbeat fresh, market data fresh, SIM broker openable+readable, live
hard-blocked, required live sources fresh, no active risk halt. Missing
scorecard is a WARNING, not a blocker. `live_blocked` is always true. With
Bookmap closed/weekend the market + heartbeat sources are stale, so this
correctly returns `can_arm=false` -- that is expected, not a bug.

`/api/arming_check` is **strictly read-only**: it performs NO filesystem writes.
The session-report-path check reasons about writability without writing a probe
file -- when writability cannot be positively proven (e.g. Windows directory
`os.access` semantics) it reports `warn` / `writability_not_proven`, never
writes to find out, and never blocks arming on it.

## Dashboard / data truth

Read-only overview UI: **http://127.0.0.1:18890**

Every trading/agent/SIM endpoint carries a freshness envelope so the UI never
presents stale state as live:

- Dict endpoints add a `_meta` block.
- List endpoints return `{ "items": [...], "_meta": {...} }`.

`_meta` fields: `source`, `source_path`, `updated_at`, `age_sec`, `is_stale`,
`stale_reason`, `threshold_sec`, `mode`, `historical`.

Staleness budgets (override with env `PAX_STALE_SEC_<SOURCE>`):

| source     | default budget | meaning                                  |
|------------|----------------|------------------------------------------|
| heartbeat  | 30s            | autopilot heartbeat (writes ~15s)        |
| market     | 15s            | journal snapshot age (live data)         |
| sim_db     | 120s           | SIM order/fill DB (event-driven)         |
| journal    | 120s           | journal run/health metadata              |
| learn_file | 86400s         | scorecard/policy/calibration (historical)|

When the heartbeat is stale the header shows **AGENT STALE (age)** with an
amber dot, not a green/red "live" state.

### Key endpoints

- `GET /api/health` -- authoritative truth surface: per-source freshness,
  `up`, `git_commit`, `mode`, `kill_switch_active`, `evaluation_level`,
  `live_blocked` (always true).
- `GET /api/evaluation_state` -- autonomy ladder
  (`observe_only` / `sim_armed` / `sim_restricted` / `sim_candidate`),
  per-setup eligibility, `live_blocked` (always true), `requirements_for_next`.
- `GET /api/arming_check` -- machine-readable go/no-go before arming SIM:
  `can_arm`, `checks[]` (pass/fail/warn + code + message), `blocking_codes`,
  `warnings`, `required_actions`, `live_blocked` (always true).
- `GET /api/promotion_report` -- honest per-setup promotion view (read-only,
  freshness-enveloped); `validated` is never auto-assigned.
- `GET /api/evidence_report` -- evidence-quality report (read-only, cheap: no
  full replay on a GET): `evidence_grade`, `setup_evidence`, `blockers`,
  `next_required_data`, `live_blocked` (always true).
- `GET /api/agent_feed` -- decision feed; each item carries a `roles` block
  (observer / strategist / risk / executor / auditor) derived read-only.
- `GET /api/agent_summary`, `/api/equity`, `/api/fills`, `/api/working`,
  `/api/position`, `/api/calibration`, `/api/learning_status`,
  `/api/runtime_policy`, `/api/lessons`, `/api/signals`, `/api/errors`,
  `/api/cron_status`.

### Kill switch (enforced before SIM order placement)

Create `D:\BookmapLogs\pax-agent\KILL_SWITCH` to halt the autopilot. It is
**enforced before SIM order placement**, not merely reported:

- The autopilot risk-halts at the last safe point before any broker call
  (`pax_sim_agent._cycle_once` armed path and `decide_cycle`). An acting plan
  is vetoed with a clean audit record: `governor` = `VETO: kill_switch_active`,
  `risk_halt` = `kill_switch_active`, `order` = `null`, `executed` = `false`,
  and **no SIM broker receipt** is produced.
- Defense in depth: `pax_sim_tools.sim_place_bracket` itself raises
  `SimKillSwitchError` if reached while the switch is engaged, so no current or
  future caller can place a SIM order while halted. Helpers:
  `pax_sim_tools.kill_switch_active(learn_dir)` /
  `risk_halt_reason(learn_dir)`.
- It also forces `/api/evaluation_state` to `observe_only` / blocked and
  surfaces `kill_switch_active` in `/api/health`.

Remove the file to resume. Flatten/cancel (risk-reducing) are not blocked by
the backstop. **This does NOT enable live trading** -- live remains hard-blocked
(see "Live trading blockers"); the kill switch only stops SIM placement.

### Enforced operational risk gates (before SIM entry placement)

`pax_risk_gate.py` is the unified, pure, deterministic operational halt gate. It
sits at the LAST safe point before SIM placement in both the rule heartbeat
(`pax_sim_agent.AgentLoop._cycle_once`, armed) and the LLM path
(`pax_sim_agent.decide_cycle`). It applies to NEW ENTRY placement only --
risk-reducing flatten/cancel are never blocked (the kill switch is the sole
exception that halts an acting plan). The LLM cannot override it
(`llm_overrode_risk` is always false). It never unlocks live trading.

Precedence (first hit wins) and block codes:

1. `kill_switch_active`   -- `KILL_SWITCH` file present.
2. `stale_heartbeat`      -- prior heartbeat older than the heartbeat budget
   (30s default). First cycle bootstraps (no prior beat -> not stale).
3. `stale_market_data`    -- the snapshot's `marketDataAsOfMs` (a BRIDGE/FEED
   timestamp: `trend_analyzer.updatedAtMs` -> recent trade `nanos` ->
   `orderbook.generatedNanos`, picked by `dashboard.compute_market_freshness`)
   older than the market budget (15s default), OR no real market timestamp at
   all (fail-closed: cannot prove freshness -> block). The dashboard's own
   compose wall-clock (`composedAtMs`) is diagnostics ONLY and is never used as
   market freshness -- a frozen bridge keeps compose time advancing.
4. `sim_broker_unavailable` -- the SIM status read raised (DB unopenable), or
   `/api/health`'s read-only broker preflight (`OverviewQueries.sim_broker_preflight`:
   open read-only + `SELECT COUNT(*) FROM orders`) found the DB missing,
   corrupt, locked, or non-SIM. `sources.sim_db` carries `openable` / `readable`
   / `error`, not just file-exists. The order path also fails closed at
   `sim_place_bracket` (no fake execution).
5. session limits -- `max_trades_reached`, `max_consecutive_losses_reached`,
   `max_loss_reached`, `max_drawdown_reached`.

Every block writes a clean unified audit record (no broker receipt):
`governor` = `VETO: <code>`, `risk_halt` / `risk_halt_code` = `<code>`,
`risk_halt_message`, `risk_halt_detail` (counters + config used),
`executed` = false, `order` = null, `llm_overrode_risk` = false.

Config (env overrides; defaults are conservative-but-not-blocking for SIM):

| env var                       | default | gate                          |
|-------------------------------|---------|-------------------------------|
| `PAX_STALE_SEC_HEARTBEAT`     | 30      | stale_heartbeat budget (s)    |
| `PAX_STALE_SEC_MARKET`        | 15      | stale_market_data budget (s)  |
| `PAX_RISK_MAX_TRADES`         | 40      | max entry fills per session   |
| `PAX_RISK_MAX_CONSEC_LOSSES`  | 6       | max consecutive losses        |
| `PAX_RISK_MAX_LOSS_USD`       | 2000    | max realized session loss USD |
| `PAX_RISK_MAX_LOSS_R`         | (off)   | max realized session loss R   |
| `PAX_RISK_MAX_DD_USD`         | (off)   | max session drawdown USD      |

Set a `*_R` / `*_DD_USD` / `*_LOSS_USD` var to `off`/`none` to disable that gate.

**Data-path honesty (do not pretend these are enforced live):** the SIM status
payload (`SimEngine.snapshot`) exposes entry-fill count, `realized_today_usd`,
and now -- derived honestly from the realized-PnL close stream (the `events`
`POSITION_UPDATE` series) -- `consecutive_losses_today`, `session_peak_equity`,
and `session_drawdown_usd` (worst trough-from-peak, a latching circuit-breaker
reading). So `max_trades_reached`, USD `max_loss_reached`,
`max_consecutive_losses_reached`, and USD `max_drawdown_reached` are ALL wired to
the live heartbeat (drawdown fires only when `PAX_RISK_MAX_DD_USD` is set;
consecutive-losses uses the default cap of 6). **R-denominated counters remain
unavailable** -- the SIM close stream carries no per-trade risk (entry/stop
linkage), so `realized_today_r` / `session_drawdown_r` stay `null`
(`r_source = unavailable_*`) and the R variant of `max_loss_reached` is reported
`unavailable` (see `risk_halt_detail.unavailable`), never fabricated.

### Health / evaluation visibility

`/api/health` surfaces the enforced halt truth: `risk_halt_active`,
`risk_halt_code`, `risk_halt_message`, `last_risk_halt_record` (most recent
enforced halt from the agent feed), per-source freshness, `kill_switch_active`,
`live_blocked` (always true). `/api/evaluation_state` adds `operational_blockers`
and `risk_halt_active`; a current stale heartbeat / stale market / unavailable
SIM broker restricts an armed agent to `sim_restricted` (kill switch ->
`observe_only`). With Bookmap closed (weekend), expect `market`/`heartbeat`
stale and `risk_halt_active=true` -- that is correct, not a bug.

## Replay and research (existing, deterministic, no orders)

These already exist; this work did not duplicate them.

```bash
# Policy replay (time-split, candidate vs current, no orders)
python -m bookmap_mcp.pax_policy_replay --forecasts D:\BookmapLogs\pax-forecast.db \
    --candidates reports\policy-candidates-YYYY-MM-DD.json --report out.json

# Outcome analyzer
python -m bookmap_mcp.pax_replay [from_date] [to_date] --json

# Calibration report
python -m bookmap_mcp.pax_calibration --date YYYY-MM-DD --forecasts D:\BookmapLogs\pax-forecast.db

# Candidate-lesson research (dry-run by default, writes candidates only)
python -m bookmap_mcp.pax_research_claude --date YYYY-MM-DD --calibration reports\calibration-YYYY-MM-DD.json
```

### Decision-path replay (new, deterministic)

Re-runs the SAME deterministic policy (`pax_loop.decide`) over saved JSONL --
no orders, no LLM, no live Bookmap. Distinct from `pax_policy_replay` (lesson
candidates) and `pax_replay` (CSV outcomes): this answers "given the saved
snapshots, what would the policy decide, and does it match what was logged?".

```cmd
python -m bookmap_mcp.pax_agent_replay --input path\to\agent-loop.jsonl ^
    --output reports\agent-replay.json --limit 1000
```

**Future live logs are replay-grade.** Every heartbeat written by
`pax_sim_agent._cycle_once` now embeds a compact `replay_input` block
(`version`, a pruned `snapshot` with only the fields `pax_loop.decide` /
`pax_brain` read, a pruned `status`, `now_ms`, `market_age_sec`,
`heartbeat_age_sec`, `sim_broker_ok`, `kill_switch_active`). It excludes raw
orderbook depth, the trade tape, screenshots, and tokens, and truncates arrays,
so a heartbeat line stays small (~1 KB). `pax_agent_replay` consumes
`replay_input` first, so a live `agent-loop.jsonl` replays the decision path AND
the operational risk gate with no special fixture format:

```cmd
python -m bookmap_mcp.pax_agent_replay ^
    --input D:\BookmapLogs\pax-agent\agent-loop.jsonl ^
    --output reports\agent-replay.json
```

**Old logs without `replay_input` are summarized only** -- their recorded
actions/risk-halts are still counted, but decisions cannot be re-derived; the
report says so via `usable_snapshot_count`, `replay_input_count`, and a
`limitations` note ("pre-replay-input logs: summarized only"). Nothing is faked.
The report also carries `replay_input_count`, `replay_input_version_counts`, and
`malformed_replay_input`. The legacy fixture shape (top-level `snapshot`/`snap`)
under `mcp-server/tests/fixtures/pax_replay/` is still supported, alongside the
new `replay_input_*.jsonl` fixtures. Output is byte-stable except `generated_ms`.

Replay has **two layers**, both pure (no orders, no LLM, no live Bookmap):

1. **Decision replay** -- always runs `pax_loop.decide`. Yields
   `action_counts`, `setup_counts`, and `divergence_count` (recorded vs replay
   action). `risk_halt_counts` / `recorded_risk_halt_counts` count the enforced
   halts read from the log.
2. **Optional operational risk-gate replay** -- re-runs
   `pax_risk_gate.evaluate_entry_gate` ONLY for entry plans that carry a real
   market-freshness signal. It prefers the embedded `replay_input`
   (`market_age_sec`, `heartbeat_age_sec`, `sim_broker_ok`,
   `kill_switch_active` -- exactly what the live system saw), then legacy
   top-level record fields, then derives market age from the snapshot timestamp
   (`marketDataAsOfMs` / `marketAsOfMs` / `ageMs`). Session counters come from
   `status`. Results land in `replayed_risk_halt_counts`,
   `op_gate_replayed_count`, and `risk_halt_divergence_count` (recorded halt vs
   replayed halt). Entry records WITHOUT a real market timestamp are NOT gated
   -- they are counted in `op_gate_missing_fields_count` and a `limitations`
   note ("operational risk gate not replayed for N records due to missing
   fields"), never faked into a pass/fail.

### Promotion report (new, honest, candidate is the ceiling)

```cmd
python -m bookmap_mcp.pax_promotion_report ^
    --scorecard D:\BookmapLogs\pax-agent\scorecard.json --out reports\promotion-report.json
```

Reuses `pax_eval_state.setup_eligibility`. Per setup it reports n, avg R, net R,
win rate (max drawdown / calibration bucket are null when the SIM scorecard
lacks them -- never fabricated) and a status: `insufficient_sample` ->
`blocked` (non-positive expectancy) -> `exploratory` -> `candidate`.
**`validated` is never auto-assigned** -- promotion past candidate requires the
human + replay + paper-pass gate, and live stays hard-blocked. Also exposed
read-only at `GET /api/promotion_report` (freshness-enveloped).

### Evidence-quality report (new) -- "do I have enough usable evidence to tune?"

```cmd
python -m bookmap_mcp.pax_evidence_report ^
    --agent-log D:\BookmapLogs\pax-agent\agent-loop.jsonl ^
    --scorecard D:\BookmapLogs\pax-agent\scorecard.json ^
    --session-report D:\BookmapLogs\pax-agent\session-report.json ^
    --out D:\BookmapLogs\pax-agent\evidence-report.json
# add --replay to also run pax_agent_replay and embed a small summary (slower)
```

`pax_evidence_report` is a thin layer over `pax_session_report.compute_replay_readiness`
and `pax_promotion_report` -- it invents no profitability. It grades how much
usable evidence exists (`evidence_grade`, worst -> best):

| grade | meaning | what's still needed |
|-------|---------|---------------------|
| `no_data` | no agent records AND no scorecard | run PAX (`paxi.bat start`) |
| `logging_only` | records exist but `replay_input` coverage `< 50%` (logs not replay-grade) -- **even if a scorecard exists** | relaunch on the current build so heartbeats embed `replay_input` |
| `replayable` | `replay_input` coverage `>= 50%`, but no scorecard/outcomes | run armed SIM so `pax_trade_learning` writes `scorecard.json` |
| `outcome_linked` | **replayable AND** a scorecard/outcomes exist, no candidate | accumulate samples toward candidate gates |
| `promotion_candidate` | **replayable AND** scorecard/outcomes exist AND >= 1 candidate setup | human + replay + paper-pass review (NOT automatic) |

**Strict ladder (truth):** a scorecard alone CANNOT lift the grade past
`logging_only`. `outcome_linked` / `promotion_candidate` require BOTH replay-grade
logs (coverage `>= 50%`) AND outcomes -- otherwise the decisions behind those
outcomes cannot be audited. A scorecard with non-replayable (or zero) logs grades
`logging_only` with the explicit blocker `scorecard_present_but_logs_not_replayable`.

Report fields: `evidence_grade`, `blockers`, `warnings`, `next_required_data`,
`replay_readiness`, `replay_summary` (only with `--replay`), `session_summary`,
`scorecard_summary`, and a per-setup `setup_evidence` table
(`evidence_status` insufficient/exploratory/candidate/blocked +
`recommended_action` collect_more_data/review_manually/keep_observing/
candidate_for_paper_focus/block_or_throttle + `missing_fields`). `live_blocked`
is always true. **`promotion_candidate` is NOT `validated`** -- it means "worth a
closer human look", never "cleared to trade". A compact summary (`evidence_grade`,
`replay_input_pct`, `candidate_setup_count`, `evidence_blockers`) is in
`/api/health.evidence` and the session report's `evidence_summary`; the full
report (cheap, no replay on a GET) is at `GET /api/evidence_report`.

## Session report

```bash
python -m bookmap_mcp.pax_session_report
# writes D:\BookmapLogs\pax-agent\session-report.json
python -m bookmap_mcp.pax_session_report --replay-summary   # + embed a small replay pass
```

Contents: decisions by action, executions, blocked decisions, risk/veto
events, **enforced risk halts** (`risk_halts`: total count, `by_code`,
`kill_switch` / `stale_data` / `sim_broker` / `session_limits` sub-counts, and
the recent halt list), PnL/win-rate, setup stats, model calls, errors,
malformed-record count, stale-data block count, **`replay_readiness`**
(`total_records`, `replay_input_records`, `replay_input_pct`,
`missing_replay_input`, `malformed_replay_input`,
`latest_replay_input_version`, `note`), **`evidence_summary`** (`evidence_grade`,
`replay_input_pct`, `candidate_setup_count`, `evidence_blockers`,
`next_required_data` -- cheap, no replay run), and a snapshot of
`evaluation_state`.
`replay_readiness` is always cheap (counts only); `--replay-summary` additionally
runs `pax_agent_replay` over the log and embeds a small `replay_summary`. The
same readiness (over the recent feed) appears in `/api/health.replay_readiness`
(`replay_input_recent`, `replay_input_pct_recent`, `replay_input_version`) and
as a WARN-only `replay_input_present` check in `/api/arming_check` -- missing
`replay_input` is an auditability gap, never an arming blocker.

## Tests

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest          # full suite
python -m pytest tests/test_overview_ui.py tests/test_pax_freshness.py \
    tests/test_pax_roles.py tests/test_pax_eval_state.py \
    tests/test_pax_session_report.py -q                                  # truth-slice
python -m compileall -q bookmap_mcp                                       # syntax
```

## Where logs live

- `D:\BookmapLogs\pax-agent\agent-loop.jsonl` -- decision/heartbeat feed
- `D:\BookmapLogs\pax-agent\scorecard.json` / `runtime-policy.json` /
  `calibration.json` -- learning artifacts
- `D:\BookmapLogs\pax-agent\sim_lessons.md` -- self-written lessons
- `D:\BookmapLogs\pax-agent\session-report.json` -- session report
- `D:\BookmapLogs\pax-journal.db` -- daemon journal (snapshots/signals/events)
- `D:\BookmapLogs\pax-daemon-trades.db` -- SIM broker DB
- `D:\BookmapLogs\pax-agent\*.out.log` / `*.err.log` -- hidden process stdio

## Live trading blockers (intentional, do not remove)

1. No live order route exists in the autopilot/SIM path. Live placement lives
   only in `server.py`'s two MCP tools behind `confirm=True` +
   `BOOKMAP_ALLOW_TRADING=1`.
2. The autopilot scrubs `BOOKMAP_ALLOW_TRADING`; `ensure_sim_safe()` raises if
   it is `1`.
3. `/api/evaluation_state` hard-codes `live_blocked = true`; promotion past
   SIM requires a human + an explicit future config that does not exist.

## Known limitations

- This stack is **SIM-only production-hardening**, not live-validated. No claim
  of market edge -- the gates are about not trading on stale/unsafe state, not
  about being profitable.
- Kill switch is **enforced before SIM order placement** (blocks new SIM
  brackets); it does not auto-flatten an open SIM position -- flatten manually
  if needed. It does not affect live trading, which is independently
  hard-blocked.
- `max_consecutive_losses_reached`, the R variant of `max_loss_reached`, and
  `max_drawdown_reached` are gate-complete and unit-tested but NOT wired to a
  live counter (the SIM status payload lacks per-trade R / streak / drawdown).
  They report `unavailable` live rather than fake a block.
- List endpoints individually carry freshness via `_meta`; their stale budgets
  use the `sim_db` / `journal` source budgets.
- Market/heartbeat staleness depends on the Bookmap bridge -> dashboard
  (`:18888`) feed being active. If that feed is down, `/api/health` correctly
  reports those sources stale.
