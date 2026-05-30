# Bookmap MCP - Claude Working Instructions

Loaded automatically at session start. Short and operational. Detailed
operations and history live in `docs/PAX_RUNBOOK.md`.

## What This Repo Is

- Java Bookmap addon under `addons/` plus Python MCP/server code under
  `mcp-server/bookmap_mcp/`.
- PAX AI has two surfaces:
  - Pax AI chat/copilot: read-only Claude reasoning over Bookmap snapshots.
  - PAX SIM autopilot: deterministic SIM/paper execution stack run by
    `paxi.bat`.
- Trading code must be deterministic, audit-friendly, replayable, and tested.
  Claude/LLM may explain and research; it must not bypass risk or place orders.

## Current State (SIM-only)

SIM/paper only. Live trading is hard-blocked. Production-ready PLUMBING
(operationally hardened, test-covered): runtime safety gates, kill switch,
stale-data enforcement, SIM broker preflight, honest USD drawdown +
consecutive-loss counters, replay-grade logging, deterministic replay,
evidence grading, promotion/eval reporting, fail-closed acceptance doctor,
post-session data-quality verdict, report-only tuning, session reports. ~1340
tests pass.

Not yet done: no real-market validation or tuning; R-denominated risk counters
unavailable (no per-trade risk in the SIM close stream); strategy thresholds
untuned on live tape. Explicitly NOT claimed: profitability, market edge, live
readiness.

Remaining work is data quality, calibration, and tuning. Keep it narrow. Do not
rebuild replay, research, learning, or strategy logic. If a module exists, wire
or extend it - do not build a parallel risk brain, replay engine, or promotion
system.

## No-Drift / Safety Rules

- No model output overrides kill switch, stale-data blocks, session risk limits,
  or SIM/live guards. Safety gates must not depend on Claude output.
- PAX SIM/autopilot is paper/SIM only. Live order routing exists only in
  `server.py` MCP tools behind `confirm=True` and `BOOKMAP_ALLOW_TRADING=1`.
- Do not import or call `bookmap_place_limit_order` / `bookmap_cancel_order`
  outside `server.py`. The autopilot scrubs `BOOKMAP_ALLOW_TRADING`;
  `ensure_sim_safe()` raises if it is `1`.
- `/api/evaluation_state` must keep `live_blocked=true`.
- Kill switch blocks new SIM placement but does not auto-flatten an existing SIM
  position. Flatten/cancel exits must remain possible.
- Do not add framework unless it improves execution safety, observability,
  replayability, or measured edge. Before changing PAX architecture, policy,
  replay, or prompts, explain how it improves review or SIM safety.

## Runtime Commands

Preferred PAX SIM manager (`paxi.bat`, not legacy launchers; it launches hidden
`pythonw.exe` and disables the old `PaxAgentCron` task):

```cmd
paxi.bat start      :: hidden observe-mode autopilot + overview UI (:18890)
paxi.bat armed      :: hidden ARMED SIM autopilot + overview UI
paxi.bat stop       :: stops PAX stack only
paxi.bat restart    :: stop then observe start
paxi.bat status     :: matching PAX processes + cron state
```

Do not kill Bookmap, OpenRange, or the Java bridge unless explicitly asked.

Health (if Bookmap is closed/weekend/no feed, stale market/heartbeat is expected
- report it honestly, never as live):

```cmd
curl http://127.0.0.1:18890/api/health
curl http://127.0.0.1:18890/api/evaluation_state
```

Operator CLIs:
`python -m bookmap_mcp.pax_acceptance [--out F] [--bundle-out F] [--replay]`;
`python -m bookmap_mcp.pax_session_report [--archive|--replay-summary]`;
`python -m bookmap_mcp.pax_evidence_report --replay`;
`python -m bookmap_mcp.pax_data_quality`; `python -m bookmap_mcp.pax_tuning_report`.

## Test Commands

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest -q       # full
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

## PAX SIM Module Map

- `pax_autopilot.py` - preferred always-awake runner.
- `pax_sim_agent.py` - orchestrates observe/armed cycles; embeds `replay_input`.
- `pax_sim_tools.py` - SIM broker helper boundary and safety guards.
- `pax_loop.py` - deterministic decision/governor path.
- `pax_brain.py` - pure setup/thesis selection, no I/O/model/broker.
- `pax_risk_gate.py` - pure operational HALT gate (kill switch / stale data /
  session limits) at the final pre-SIM-placement point. Not a strategy brain.
- `pax_agent_replay.py` - deterministic decision-path replay over saved JSONL;
  no orders/LLM/live. Reuses the policy, not a new engine.
- `pax_promotion_report.py` - honest per-setup promotion view; `validated`
  never auto-assigned.
- `pax_evidence_report.py` - evidence grading + per-setup table; `candidate`
  != `validated`.
- `pax_acceptance.py` - read-only fail-closed doctor CLI (pass|warn|fail). No
  service start, no broker order, no LLM.
- `pax_data_quality.py` - read-only post-session data-quality verdict.
- `pax_tuning_report.py` - REPORT-ONLY tuning suggestions; writes no policy,
  never auto-promotes.
- `pax_expectancy.py`, `pax_trade_learning.py` - learned expectancy and SIM
  scorecards.
- `pax_runtime_policy.py` - guarded runtime policy lookup/adjustment.
- `pax_freshness.py`, `pax_eval_state.py`, `pax_roles.py` - read-side truth,
  evaluation, role projection.
- `pax_session_report.py` - writes session report JSON.
- `overview_ui.py` - read-only PAX overview UI/API at `:18890`.

Reuse existing engines: `pax_replay`, `pax_policy_replay`, `pax_calibration`,
`pax_research_claude`, `pax_bus_replay`, `pax_bus_tune`. Promotion principle:
`research_only -> replay_passed -> paper_candidate -> paper_passed ->
human_approved -> active`. Never auto-edit active config/prompts without
explicit user approval.

## Bridge / Other Notes

- Java bridge port is in `~/.bookmap-mcp/bridge.properties` (default `8765` on
  this machine; do not assume `18888`). Token must never be printed/committed.
- Python dashboard `:18888`; PAX overview UI `:18890`.
- Bookmap aliases include route, e.g. `NQM6.CME@RITHMIC`.
- `dashboard.py` is the large live HUD/signal hub - back up before refactors.
  `pax_daemon.py` is legacy background paper daemon; refuses live env.
- Pax AI chat is read-only: Claude CLI uses `--tools ""` and `--max-turns 1`;
  `/deep` is a model swap only. Output is prose unless an external tool requests
  a schema. When `snap.session.anchorMode != "LIVE"`, output is informational
  only (no FOLLOW/FADE, no new entries). Do not weaken `pax-ai/tests/`.

## Backup / Editing / Git

- Before substantial refactors that replace existing functions/models/config:
  create `_phase_backups/pre_<phase>_<YYYYMMDD_HHMMSS>/`, copy touched files
  preserving paths, add `BACKUP_MANIFEST.md`. `_phase_backups/` is gitignored.
- ASCII unless the file already uses Unicode. Be terse. Do not reformat large
  files as cleanup. Preserve user changes in a dirty tree. One reversible commit
  per slice.
- Never commit `bridge.properties`, `_phase_backups/`, `__pycache__/`, `.venv/`,
  `*.pyc`. Run focused + full `python -m pytest -q` before push on Python
  changes. If tests fail, stop and report the exact failure.

## What Not To Do

- Do not enable live trading from PAX SIM/autopilot.
- Do not let LLM output bypass deterministic safety.
- Do not add a second risk governor or rebuild replay/research if existing
  modules can be wired.
- Do not use stale data as current, or fake market validation when the feed is
  closed.
- Do not call the system institutional/live-ready until replay, risk, ops, and
  validation gates are implemented and tested.
