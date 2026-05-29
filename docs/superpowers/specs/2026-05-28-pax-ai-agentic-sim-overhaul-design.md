# Pax AI agentic sim-trader overhaul - design spec

Status: APPROVED + BUILT (2026-05-28, unattended build). MVP shipped & tested:
1057 mcp-server + 961 pax-ai tests green, plus a live agent decision smoke.
Author: dial-in session. Supersedes the read-only Pax AI chat contract for a
NEW agentic-sim code path (operator audit finding, this session).

BUILD NOTES (what shipped vs spec):
- LLM kept TOOL-LESS (--tools "" / --max-turns 1 preserved). Agency = the LLM
  emits a STRUCTURED decision; deterministic Python (governor + local SimEngine)
  executes. Safer than raw tool-calling and the invariant never moved. Full
  multi-turn tool-calling remains a future option.
- New modules: bookmap_mcp/pax_loop.py (shared decide+governor),
  pax_sim_tools.py (sim-only surface + learning store), pax_sim_agent.py
  (context/govern/execute/cycle + AgentLoop), pax_sim_calibration.py.
- Pax AI server: POST /api/pax/agent/{start,stop,arm} + GET /status; UI got an
  agent control bar (Start/Stop, Observe/Armed) + a live reasoning line.
- Loop starts in OBSERVE mode (thinks + logs, places nothing); arming flips sim
  execution. `AgentLoop.start()` scrubs BOOKMAP_ALLOW_TRADING in the pax_ai
  process (it inherited =1 from the bridge; pax_ai has no live path so this is
  safe and necessary for the sim tools to run).
- Tests: test_pax_loop, test_pax_sim_tools_safety (AST + runtime wall),
  test_pax_sim_agent (governor/cycle/loop, mock LLM), test_pax_sim_calibration.
- UI STRIPPED TO A TERMINAL (operator: "all that bloat got to go"): removed the
  EDGE CALCULUS + PLAYBOOK drawers, the Today's Bus capture panel, the whynow
  verdict block, and their pollers/dead JS. Kept the title bar + strip. The body
  is now a terminal transcript that streams each agent cycle (HH:MM:SS ACTION —
  rationale, color-coded) + the chat. Obsolete UI guard tests rewritten
  (test_ui_static_guards now asserts the bloat stays gone; test_ui_escape
  trimmed to the surviving strip fields).
- DEFERRED: full multi-turn tool agent; forward-return/realized-R calibration
  (needs outcomes labeler); P6 promotion harness.

## 1. Vision (operator directive)

Pax AI becomes an agentic paper trader that thinks for itself, makes its own
trade decisions, grades itself against the tape, and rewrites its own playbook -
and keeps learning. It runs unattended in the Pax AI process (no terminal, no
Claude-Code cron). The operator coaches it through the chat box and watches it
think. It earns its way to live; it does not start there.

Operator decisions locked this session:
- **Agent decides, governor bounds.** The LLM makes entry/exit calls and may
  override the deterministic pax_algo_v1 (decide()), which becomes advice. The
  only hard limits are the deterministic risk governor.
- **Fully autonomous self-modification on sim.** The agent rewrites its own
  lessons / playbook / prompts / params freely on sim with no approval. Every
  change is logged to a revertible ledger. The ONLY human gate is live promotion.

## 2. The hard safety wall (load-bearing - this is what makes full autonomy OK)

The agent may be arbitrarily aggressive and self-modifying because it **cannot
reach a live order by construction**, not merely by config. The decisive reason:
the sim is our OWN backend broker, not Bookmap.

0. **The sim broker is local, not Bookmap.** The agent trades the local SQLite
   `SimEngine` (`sim_engine.py`, driven via `pax_manual`). SimEngine reads live
   prices ONLY to *fill* paper orders; it NEVER routes an order to Bookmap's API.
   The single Bookmap touchpoint in the entire sim path is read-only market data.
   Live order routing (`server.py` -> bridge -> Bookmap) is a SEPARATE code path
   the agent never imports - there is no shared call site to slip through.
1. **Sim-only tool surface.** The agent runs against a dedicated SIM tool
   surface (new sim-only MCP server / explicit tool allowlist) exposing exactly:
   - read live data (dashboard snapshot, levels, tape, book - read only),
   - the local sim broker (`pax_manual` / SimEngine: long/short/flatten/status/
     cancel) - SQLite only, no Bookmap order API,
   - read/write of its OWN learning store (journal, lessons, playbook, tuning
     ledger, calibration reports).
2. **Tools it never has:** `bookmap_place_limit_order` / `bookmap_cancel_order`
   (live), any `server.py` gated live path, unrestricted shell, filesystem write
   outside its learning store. PRESERVES the existing invariant: live order tools
   exist ONLY in `server.py` behind `confirm=True` + `BOOKMAP_ALLOW_TRADING=1`.
3. **Defense in depth.** The agent process runs with `BOOKMAP_ALLOW_TRADING`
   UNSET, so even a hallucinated call to a live endpoint is refused by the bridge
   gate AND by SimEngine's own guard - but the primary wall is simply that the
   agent's only broker IS the local SimEngine.
4. **Blast radius = a SQLite paper DB** (`D:\BookmapLogs\pax-daemon-trades.db`).
   It can ruin the sim a thousand ways and learn from each; it cannot spend a
   real dollar, and no order ever leaves the machine.
5. **Live promotion is human-only.** A separate live-config file the agent cannot
   write. Promotion gate (unchanged from CLAUDE.md): research_only ->
   replay_passed -> paper_candidate -> paper_passed -> human_approved -> active.

Test contract: AST/grep guards (extend `tests/test_safety_boundaries.py`) assert
the agent tool module never imports the live order functions, and a runtime test
asserts the agent env has `BOOKMAP_ALLOW_TRADING` unset.

## 3. Architecture

New agentic loop lives in the Pax AI process (`:18891`, launched by the
PaxAILauncher Bookmap addon - already always-on).

```
PaxAILauncher (Bookmap addon) -> python -m pax_ai --shell
  pax_ai process
    poller thread            (exists) caches dashboard :18888 snapshot @1Hz
    trigger detector         (exists) decides WHEN something actionable develops
    AGENT LOOP (new)         event-driven: wake on trigger OR every N s
      cycle:
        1. read snapshot + own lessons + own calibration
        2. reason with tools on (multi-turn) -> decision + rationale
        3. act on SIM broker (governor-bounded)
        4. journal turn (decision, rationale, snapshot hash) -> feature_bus
    outcomes labeler         (exists, ACTIVATE) grades turns @60/180/300/900s
    calibration (new)        reliability by setup/conf bucket; agent reads it
    sim fill daemon          (exists: pax_daemon --no-auto-decide) fills+journals
  chat box (existing UI)     coach the agent / watch it think / override
```

### 3.1 The agent turn
- Invocation profile = NEW (distinct from the legacy single-shot read-only chat):
  tools = sim surface only, multi-turn enabled, model tiered (cheap for routine
  monitoring, escalate for decisions / nightly learning).
- Input block (reuse `bus_digest`): [STATE][ANCHOR][GATES][LEVELS]
  [MICROSTRUCTURE][RECENT_EVENTS][POSITION][CALIBRATION][LESSONS][SESSION_MEMORY].
- Output: a structured decision (act via tool) + a plain-English rationale shown
  in the chat transcript.

### 3.2 Cadence + cost (responsible default)
Event-driven, not brute every-minute. The existing trigger detector wakes the
agent only when something develops (level approach, trend flip, conviction flip,
big print, failed break). Routine monitoring uses a cheap model; the agent
escalates itself for an actual decision or the nightly learning pass. Operator
"could care less about sim cost" - but trigger-driven is also better trading
(no overtrading) and keeps token spend sane. Hard per-hour fire cap + per-kind
cooldown retained from trigger_engine.

### 3.3 The risk governor (the ONLY hard limit on the agent)
Deterministic, agent cannot disable it. Wraps every sim action:
- sim-only (BOOKMAP_ALLOW_TRADING unset), max position size, max concurrent
  entries, daily-stop (losers >= 2 -> stand down), post-trade cooldown, no-stack
  while an entry rests, midnight/session-stop. (This is the v4 governor we just
  built in `eth_loop_tick.py`, lifted into a shared module.)
- The agent decides WHAT/WHEN to trade; the governor bounds HOW MUCH it can hurt
  the sim.

### 3.4 Decision authority
- `pax_algo_v1` (decide()) runs every cycle as the **baseline policy** and is
  shown to the agent as advice ("the rule says X"). The agent may follow or
  override, and must journal WHY it deviated. The learning loop measures
  agent-vs-rule so we can prove whether the agent actually adds edge.
- "No LLM math in trading skills" is deliberately relaxed for THIS sim agent
  (operator directive). It stays in force for the deterministic skills and the
  governor.

### 3.5 The learning loop (now core, not bloat)
- `feature_bus` ACTIVATED: captures every agent turn + snapshot + outcome.
- `outcomes` ACTIVATED: labels realized R at horizons.
- `calibration` (new): reliability by setup bucket + stated-confidence bucket;
  agent-vs-rule expectancy; agent reads its own calibration before deciding.
- Self-rewrite (fully autonomous on sim): agent edits its own `sim_lessons.md`,
  `sim_playbook.json`, sim params. Every edit appended to
  `reports/agent-selfmod-ledger.csv` (revertible). Live config is separate and
  unwritable by the agent.

## 4. Keep / cut / refactor (bloat decision, reframed by "agentic + learning")

The learning scaffolding is NOT bloat under this directive - it is the engine.
Net cleanup is smaller than first scoped.

KEEP + ACTIVATE (was default-off): feature_bus, outcomes, trigger_engine (becomes
the agent's waking mechanism), bus_digest (becomes the agent input block).

KEEP as agent TOOLS / on-demand inputs (demote from always-on UI drawers): 
edge_calculus, verdict, playbook, attack_response, triggers - the agent may
consult them; they stop being forced UI. ("Follow the data" = the agent chooses
what to weigh, we don't shove derived analysis at the operator.)

CUT / QUARANTINE: dead CLI reports (`level_edge_report`, `attack_response_report`
- or repurpose as the agent's own reporting tools), duplicate logging paths, the
read-only single-turn chat CONSTRAINT (replaced by the agent profile).

REFACTOR: lift `decide()` + the v4 governor out of the session script
`_session_snapshots/eth_loop_tick.py` into a real importable module
(e.g. `mcp-server/bookmap_mcp/pax_loop.py`) shared by the agent, the loop, and
tests. The session script becomes a thin CLI wrapper.

## 5. UI (keep the look, change the logic)

- KEEP: the visual shell, follow-money strip, cost/latency footer, chat
  transcript + input, voice.
- CHANGE the chat from read-only Q&A to **bidirectional coaching**: you talk to
  the agent, it talks back, you override ("stop fading big prints").
- ADD: agent control (start/stop autonomy, running indicator) + a live
  "what I'm doing / why" reasoning view (the transcript shows the agent's
  decisions + rationale as they happen).
- DEMOTE: edge-calculus / playbook / attack-response drawers from always-on to
  on-demand (the agent consumes them, the operator doesn't need them shoved up).

## 6. Phasing (each phase independently reviewable; sim only throughout)

- **P1 - Safety wall + shared core.** Sim-only tool surface (MCP/allowlist),
  AST/runtime safety tests, lift decide()+governor into `pax_loop.py`. No agent
  yet. Proves the wall before anything can act.
- **P2 - Agent loop (governor-bounded), trigger-woken.** Agent makes sim
  decisions via the sim surface; rule shown as advice; every turn journaled.
  Model-tiered, fire-capped.
- **P3 - Learning loop on.** Activate feature_bus + outcomes; build calibration;
  agent reads its own calibration; self-mod ledger.
- **P4 - Self-rewrite.** Agent edits its own sim lessons/playbook/params
  autonomously; all logged + revertible.
- **P5 - UI.** Coaching chat + agent control + reasoning view; demote drawers.
- **P6 - Promotion harness.** Calibration-based live-readiness report (human
  reads it, human flips live). NOT auto.

## 7. Open risks / to settle

- Cost ceiling if trigger detector over-fires (mitigated by caps + tiering).
- Agent thrash / overtrading on sim (governor cooldown + the agent's own
  calibration feedback should self-correct; watch it).
- Model tool-calling reliability for the sim broker (structured-output / retries).
- "Prove itself" criteria for live: define the calibration thresholds later;
  human-judged for now.
- Token/keychain auth for an unattended agentic CLI (the `--bare` OAuth gotcha
  applies; the agent runs the same auth path as chat).
