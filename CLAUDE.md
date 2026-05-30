# Bookmap MCP - Claude Working Instructions

This file is loaded automatically by Claude Code at session start. Keep it
short and operational. Detailed PAX SIM runbook lives in
`docs/PAX_RUNBOOK.md`.

## What This Repo Is

- Java Bookmap addon under `addons/` plus Python MCP/server code under
  `mcp-server/bookmap_mcp/`.
- PAX AI has two related surfaces:
  - Pax AI chat/copilot: read-only Claude reasoning over Bookmap snapshots.
  - PAX SIM autopilot: deterministic SIM/paper execution stack managed by
    `paxi.bat`.
- Trading code must be deterministic, audit-friendly, replayable, and tested.
  Claude/LLM may explain and research; it must not be the authority that
  bypasses risk or places orders.

## Current Agentic Phase

Objective: make PAX SIM autopilot production-ready for unattended SIM operation,
where remaining work is data quality, calibration, and tuning.

Meaning of "production ready" here:

- SIM/paper only.
- Refuses unsafe trades.
- Logs every decision and every block reason.
- Health/evaluation endpoints tell the truth.
- Decisions can be replayed and compared against outcomes.
- No live trading route is enabled by the autopilot.

Do not call this live-production-ready. Live trading needs separate broker,
legal/risk, capital, latency, exchange-failure, and manual approval controls.

### Completed Checkpoints

- `52ba5d3` - PAX truth and health slice:
  freshness envelopes, `/api/health`, `/api/evaluation_state`, role labels,
  session report writer, runbook.
- `d0ca133` - enforced kill switch before SIM execution:
  `D:\BookmapLogs\pax-agent\KILL_SWITCH` blocks SIM order placement before any
  broker call; also guarded inside `pax_sim_tools.sim_place_bracket`.
- stale-data + session-risk gates (`pax_risk_gate.py`): pure deterministic
  operational HALT facade at the last pre-execution gate. ENTRIES only;
  flatten/cancel never gated (kill switch excepted). Precedence
  `kill_switch_active` -> `stale_heartbeat` -> `stale_market_data` ->
  `sim_broker_unavailable` -> session limits. Health/eval/session report expose
  the same halt truth. LLM cannot override.
- `d9f5c1c` - market-freshness vs compose-time split (`marketDataAsOfMs` from a
  real bridge/feed timestamp, `composedAtMs` diagnostics only) + real read-only
  SIM broker preflight.
- replay/validation/arming slice (this checkpoint): `pax_agent_replay.py`
  (deterministic decision-path replay over saved JSONL; no orders/LLM/live),
  regression fixtures under `tests/fixtures/pax_replay/`,
  `pax_promotion_report.py` (honest per-setup status; `validated` never
  auto-assigned), auto session-report-on-`paxi.bat stop` (+timestamped archive),
  and `GET /api/arming_check` go/no-go + `GET /api/promotion_report`. 1232 tests
  pass.
- audit fixes: `/api/arming_check` is strictly read-only (no probe-file write;
  non-mutating writability check). `paxi.bat stop` runs the report under
  `pushd "%SRV%"`/`popd`. `pax_agent_replay` adds an OPTIONAL second layer
  (`pax_risk_gate.evaluate_entry_gate` for entry plans with a real market
  timestamp) -> `replayed_risk_halt_counts` / `op_gate_replayed_count` /
  `op_gate_missing_fields_count` / `risk_halt_divergence_count`; missing fields
  are a limitation, never faked.
- replay-grade live logs: `pax_sim_agent._cycle_once` embeds a compact
  `replay_input` (version 1) in every heartbeat -- pruned snapshot (only fields
  pax_loop/pax_brain read) + pruned status + now_ms + market/heartbeat ages +
  sim_broker_ok + kill_switch_active; no depth/tape/screenshots/tokens, arrays
  truncated (~1 KB/line). `pax_agent_replay` consumes `replay_input` first
  (legacy fixture shape still supported) -> `replay_input_count` /
  `replay_input_version_counts` / `malformed_replay_input`. Session report +
  `/api/health` expose `replay_readiness`; `/api/arming_check` warns (never
  blocks) on missing `replay_input`.

### Active Next Phase

Replay-grade logging + readiness surfacing is DONE -- see the checkpoint above
and `docs/PAX_RUNBOOK.md`. Remaining prototype-grade items:

1. Wire consecutive-loss / R / drawdown counters to the live SIM path (the
   status payload lacks them; gates are unit-tested but report `unavailable`).
2. Old `agent-loop.jsonl` lines (pre-`replay_input`) are summarized only -- a
   one-time backfill is not provided (honest limitation, not faked).
3. This is SIM-only; live remains hard-blocked. No market-edge validation yet.

Keep this narrow. Do not rebuild replay, research, learning, or strategy logic.

## No-Drift Rules

- Do not add more framework unless it directly improves execution safety,
  observability, replayability, or measured trading edge.
- Before changing PAX architecture, policy, replay, or prompts, explain how the
  work improves live/manual review or SIM safety.
- If a module already exists, wire it or extend it. Do not build a parallel
  risk brain, replay engine, research harness, or promotion system.
- Strategy logic and execution safety are separate. Safety gates must not depend
  on Claude output.
- No model output can override kill switch, stale-data blocks, session risk
  limits, or SIM/live guards.

## Safety Boundaries

- PAX SIM/autopilot is paper/SIM only.
- Live order routing exists only in `server.py` MCP tools behind
  `confirm=True` and `BOOKMAP_ALLOW_TRADING=1`.
- Do not import or call `bookmap_place_limit_order` or
  `bookmap_cancel_order` outside `server.py`.
- The autopilot scrubs `BOOKMAP_ALLOW_TRADING`; `ensure_sim_safe()` raises if
  it is `1`.
- `/api/evaluation_state` must keep `live_blocked=true`.
- Kill switch blocks new SIM placement but does not auto-flatten an existing
  SIM position. Flatten/cancel exits should remain possible.

## Runtime Commands

Preferred PAX SIM manager:

```cmd
paxi.bat start      :: hidden observe-mode autopilot + overview UI (:18890)
paxi.bat armed      :: hidden ARMED SIM autopilot + overview UI
paxi.bat stop       :: stops PAX stack only
paxi.bat restart    :: stop then observe start
paxi.bat status     :: matching PAX processes + cron state
```

Use `paxi.bat`, not legacy launchers, unless explicitly debugging legacy code.
It launches hidden `pythonw.exe` processes and disables the old `PaxAgentCron`
tick task.

Do not kill Bookmap, OpenRange, or the Java bridge unless explicitly asked.

Dashboard and health:

```cmd
curl http://127.0.0.1:18890/api/health
curl http://127.0.0.1:18890/api/evaluation_state
```

If Bookmap is closed/weekend/no feed, stale market/heartbeat is expected and
should be reported honestly.

## Test Commands

Python full suite:

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest -q
```

Fast syntax:

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp
```

Java build/tests:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Bookmap\jre'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat test
.\gradlew.bat build
```

If changing Java addon source, bump `version` in `build.gradle` and use the
versioned jar policy. Bookmap can hold old jars open.

## PAX SIM Modules

- `pax_autopilot.py` - preferred always-awake runner.
- `pax_sim_agent.py` - orchestrates observe/armed cycles and execution.
- `pax_sim_tools.py` - SIM broker helper boundary and safety guards.
- `pax_loop.py` - deterministic decision/governor path.
- `pax_risk_gate.py` - pure operational HALT gate (kill switch / stale data /
  session limits) at the final pre-SIM-placement point. Not a strategy brain.
- `pax_agent_replay.py` - deterministic decision-path replay (`pax_loop.decide`)
  over saved JSONL; no orders/LLM/live. Reuses the policy, not a new engine.
- `pax_promotion_report.py` - honest per-setup promotion view over the SIM
  scorecard; reuses `pax_eval_state.setup_eligibility`. `validated` never auto.
- `pax_brain.py` - pure setup/thesis selection, no I/O/model/broker.
- `pax_expectancy.py`, `pax_trade_learning.py` - learned expectancy and SIM
  outcome scorecards.
- `pax_runtime_policy.py` - guarded runtime policy lookup/adjustment.
- `pax_freshness.py`, `pax_eval_state.py`, `pax_roles.py` - read-side truth,
  evaluation, and role projection.
- `pax_session_report.py` - writes session report JSON.
- `overview_ui.py` - read-only UI/API at `:18890`.

Detailed operations: `docs/PAX_RUNBOOK.md`.

## Replay, Research, Promotion

Existing engines should be reused:

- `pax_replay`
- `pax_policy_replay`
- `pax_calibration`
- `pax_research_claude`
- `pax_trade_learning`
- `pax_bus_replay`
- `pax_bus_tune`

Promotion principle:

`research_only -> replay_passed -> paper_candidate -> paper_passed ->
human_approved -> active`

Any candidate lesson/config/prompt change must include setup bucket, sample
count, before/after metrics, time split, calibration impact, replay result,
and promotion status. Never auto-edit active production config/prompts without
explicit user approval.

## Dashboard Truth Contract

Read-only `:18890` endpoints use freshness envelopes:

- Dict endpoints add `_meta`.
- List endpoints return `{ "items": [...], "_meta": {...} }`.
- `_meta` includes `source`, `source_path`, `updated_at`, `age_sec`,
  `is_stale`, `stale_reason`, `threshold_sec`, `mode`, `historical`.

Default stale budgets:

- heartbeat: 30s
- market: 15s
- sim_db: 120s
- journal: 120s
- learn_file: 86400s and historical

Header must show stale state as stale, never as live armed/observing.

## Bookmap / Bridge Notes

- Java bridge port is configured in `~/.bookmap-mcp/bridge.properties`.
  Default on this machine has been `8765`; do not assume `18888`.
- Python dashboard runs at `:18888`; PAX overview UI runs at `:18890`.
- Bookmap aliases include route, e.g. `NQM6.CME@RITHMIC`.
- Bridge token must never be printed or committed.
- `fetch_snapshot` offline responses must remain structured and token-safe.

## Code Layout Pointers

- `dashboard.py` - main live HUD snapshot composer and many signal helpers.
  Large production hub; back up before substantial refactors.
- `signal_engine.py` - pure facade over dashboard signal helpers.
- `institutional_flow.py` - deterministic regime/flow aggregator.
- `or_day_ledger.py` - OR/session audit ledger.
- `sim_engine.py` - local SQLite paper broker.
- `pax_daemon.py` - legacy/background paper daemon; refuses live env.
- `journal.py` - SQLite journal, WAL mode.
- `adapters/` - data adapters.
- `overview_ui.py` - read-only PAX SIM overview API/UI.

## Pax AI Chat Invariants

Pax AI chat is read-only:

- Claude CLI uses `--tools ""`.
- Claude CLI uses `--max-turns 1`.
- `/deep` is a model swap only, not an agentic loop.
- Chat output is prose unless a standalone external tool explicitly requests a
  schema. The HFT skill must not force Pax AI chat into JSON.
- The operator-configured Static OR is the active OR anchor unless the live
  snapshot says otherwise. Do not claim RTH/08:30 is active OR without live
  config.
- When `snap.session.anchorMode != "LIVE"`, output is informational only: no
  FOLLOW/FADE recommendations and no new entries.

Relevant tests live under `pax-ai/tests/`; do not weaken them.

## Backup Rule

Before substantial refactors that replace existing functions/models/config:

1. Create `_phase_backups/pre_<phase_name>_<YYYYMMDD_HHMMSS>/`.
2. Copy touched files preserving relative paths.
3. Add `BACKUP_MANIFEST.md` with files and rollback snippet.

`_phase_backups/` is ignored. Do not commit it.

## Editing Style

- ASCII unless the file already uses Unicode.
- Be terse in comments and docs.
- Prefer named functions and tests over narration.
- Do not reformat large files as cleanup.
- Preserve user changes in a dirty tree.
- Use one reversible commit per completed slice.

## Git Etiquette

- Never commit `bridge.properties`, `_phase_backups/`, `__pycache__/`,
  `.venv/`, or `*.pyc`.
- Run focused tests and full `python -m pytest -q` before push when Python code
  changes.
- If tests fail, stop and report exact failure. Do not claim success.

## What Not To Do

- Do not enable live trading from PAX SIM/autopilot.
- Do not let LLM output bypass deterministic safety.
- Do not add a second risk governor.
- Do not rebuild replay/research if existing modules can be wired.
- Do not spawn visible terminal loops from dashboard refresh logic.
- Do not use stale data as current.
- Do not fake market validation when Bookmap/feed is closed.
- Do not call the system institutional/live-ready until replay, risk, ops, and
  validation gates are actually implemented and tested.
