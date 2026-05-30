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
- `GET /api/agent_feed` -- decision feed; each item carries a `roles` block
  (observer / strategist / risk / executor / auditor) derived read-only.
- `GET /api/agent_summary`, `/api/equity`, `/api/fills`, `/api/working`,
  `/api/position`, `/api/calibration`, `/api/learning_status`,
  `/api/runtime_policy`, `/api/lessons`, `/api/signals`, `/api/errors`,
  `/api/cron_status`.

### Kill switch (read-only status today)

Create `D:\BookmapLogs\pax-agent\KILL_SWITCH` to flag the system: it forces
`/api/evaluation_state` to `observe_only` / blocked and surfaces in
`/api/health`. **Enforcement note:** the file is currently REPORTED only; it
is not yet wired into the governor to physically halt SIM order placement.
See "Known limitations".

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

## Session report

```bash
python -m bookmap_mcp.pax_session_report
# writes D:\BookmapLogs\pax-agent\session-report.json
```

Contents: decisions by action, executions, blocked decisions, risk/veto
events, PnL/win-rate, setup stats, model calls, errors, malformed-record
count, and a snapshot of `evaluation_state`.

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

- Kill switch is **reported, not enforced** in the governor yet (wiring it
  into `pax_loop`/`pax_sim_agent` is a decision-path change, out of scope for
  the read-only truth slice).
- List endpoints individually carry freshness via `_meta`; their stale budgets
  use the `sim_db` / `journal` source budgets.
- Market/heartbeat staleness depends on the Bookmap bridge -> dashboard
  (`:18888`) feed being active. If that feed is down, `/api/health` correctly
  reports those sources stale.
