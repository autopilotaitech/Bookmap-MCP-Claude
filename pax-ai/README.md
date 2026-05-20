# Pax AI

Floating dark-glass quant chat over the Bookmap MCP dashboard. Production-ready baseline established on 2026-05-20; see git history and CLAUDE.md "Pax AI production invariants" for the locked contracts.

A separate Python process that reads the live order-flow snapshot from the existing `mcp-server` dashboard, surfaces deterministic WHY-NOW triggers + Jane-Street edge calculus per OR level, and streams Claude CLI tokens for conversational quant chat. Never touches Bookmap's data thread. Never holds a bridge token. Localhost-only.

Design spec: `docs/superpowers/specs/2026-05-19-pax-ai-design.md`
Production invariants: `CLAUDE.md` § "Pax AI production invariants (locked 2026-05-20)"

---

## Architecture

Three processes, one direction of data flow. Pax AI is the green box.

```
+-----------------------+    +---------------------------+    +---------------------------+
|  Bookmap.exe          |    |  mcp-server (Python)      |    |  pax_ai (Python)          |
|                       |    |                           |    |                           |
|   MCP bridge addon    |--->|   dashboard.py            |--->|   poller.py    (1 Hz)     |
|   :8765 (HTTP+token)  |    |   :18888 /api/snapshot    |    |   triggers.py  (alias-    |
|                       |    |   cached_fetch_snapshot() |    |    scoped state, linger   |
|   OpenRange addon     |    |   (same source consumed   |    |    cache + state cond)    |
|   (also reads :18888) |    |    by OpenRange overlay)  |    |   edge_calculus.py        |
|                       |    |                           |    |   playbook.py             |
|   PaxAILauncher addon |--->|                           |    |   context.py  (digest)    |
|   (spawns pax_ai      |    |                           |    |                           |
|    --shell on enable, |    |                           |    |   server.py   :18891 HTTP |
|    destroys on        |    |                           |    |    REST + SSE             |
|    disable)           |    |                           |    |                           |
+-----------------------+    +---------------------------+    |   chat.py    SSE handler  |
                                                              |   claude_stream.py        |
                                                              |    subprocess streaming   |
                                                              |   journal.py  SQLite log  |
                                                              |   prompts.py  frozen sp   |
                                                              |                           |
                                                              |   shell.py    pywebview   |
                                                              |    frameless / on_top /   |
                                                              |    draggable / 360x500    |
                                                              +---------------------------+
                                                                         |
                                                                         v
                                                              +---------------------------+
                                                              |  Edge WebView2 window      |
                                                              |  static/index.html         |
                                                              |  strip + WHY-NOW chips +   |
                                                              |  EDGE/PLAYBOOK drawers +   |
                                                              |  chat transcript + mic     |
                                                              +---------------------------+
                                                                         |
                                                                         v
                                                              +---------------------------+
                                                              |  claude CLI (subprocess,   |
                                                              |   per chat turn)           |
                                                              |  Anthropic API             |
                                                              +---------------------------+
```

### Invariants (CLAUDE.md is the source of truth)

- **OR anchor:** the operator-configured Static OR in the OpenRange UI is the only active anchor source. Pax AI reads `snap.session.{anchorHHMM, anchorTimezone, anchorRangeSeconds, anchorMode}` + `snap.or_session_config` + `snap.or_levels.{orHigh, orLow}`. RTH/08:30 is historical/fallback context only. When `anchorMode != "LIVE"` Pax output is informational-only.
- **HFT skill:** `skills/hft_microstructure_quant_v1/SKILL.md` is reference context inside Pax AI. Never forces JSON output, even when router primary is HFT. Verified live against `claude-haiku-4-5`.
- **Claude read-only:** `--tools ""` + `--max-turns 1` are unconditional. No Bash / Read / Edit / MCP. `--bare` is conditional on `ANTHROPIC_API_KEY` or `PAX_AI_CLAUDE_BARE=1`.
- **Trigger state alias-scoped:** `_PER_ALIAS_STATE: Dict[alias, state]` so switching NQM6 → MNQM6 doesn't bleed chips.
- **Stale gate:** when snapshot age > `stale_snapshot_ms` or `health != "ok"` or `anchorMode != "LIVE"`, only `BRIDGE_DEGRADED` surfaces.

### Threading

- **Main thread:** `pywebview.start()` event loop in `--shell` mode (Edge WebView2 message pump). Blocks until window close.
- **HTTP server thread pool:** `ThreadingHTTPServer` worker per request.
- **Snapshot poller:** dedicated daemon thread, 1 Hz fetch of dashboard `/api/snapshot` via stdlib `urllib`. Backoff after 3 consecutive failures.
- **Claude subprocess:** `subprocess.Popen` line-buffered per chat turn. SSE handler drains stdout, abort via `proc.terminate()` then `proc.kill()`. Hard 30s timeout.

Bookmap's data thread is never touched — Pax AI is a downstream reader of the dashboard, mirrored on the same pattern OpenRange already uses for the heatwave + trend overlays.

---

## Run

The launcher defaults to **floating-window mode** (pywebview frameless on_top):

```powershell
.\pax-ai-start.bat                       # floating window on :18891 (default)
.\pax-ai-start.bat 18895                  # floating window on :18895
```

Server-only mode (no window — headless smoke tests, CI, curl-driven debugging):

```powershell
.\pax-ai-start.bat server                 # server-only on :18891
.\pax-ai-start.bat server 18895           # server-only on :18895
```

Direct invocation (skips the .bat health probe):

```powershell
$py = "C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe"
& $py -m pax_ai --shell                   # floating window
& $py -m pax_ai                           # server-only
& $py -m pax_ai --shell --port 18895
```

The launcher's health probe hits `GET /api/pax/health` — Pax AI is reported UP whenever its HTTP server is live, **independent of dashboard reachability**. Dashboard / Claude availability is surfaced separately from the health JSON.

When you enable the **Pax AI** addon in Bookmap's Add-ons panel (`indicators/PaxAILauncher/`), the addon spawns the Python process with `--shell` already set. Disabling the addon destroys it.

---

## API reference (`http://127.0.0.1:18891`)

All endpoints bound to `127.0.0.1` only. No CORS. No auth (localhost).

### GET `/`

Serves `static/index.html` — the chat UI. CSS-in-HTML, no build step.

### GET `/api/snapshot`

Transparent proxy to dashboard `:18888/api/snapshot`. Kept for back-compat + debug. Production code paths inside Pax AI read from the local `poller` cache rather than re-fetching.

**Response:** the dashboard's raw snapshot JSON (~220 KB) or the structured offline envelope (`{health: "offline", bridgeUrl, bridgeError, nextSteps, tokenConfigured, error}`) when the dashboard is unreachable.

### GET `/api/pax/context`

Quantized strip context. Used by the UI's top strip + chat digest. Numbers are bucketed (mid 0.25, distance 0.5, conf 0.05, score 0.05) so consecutive 1 Hz polls produce stable bytes when nothing material has changed → Anthropic ephemeral prompt cache stays warm.

**Response (200 when poller has data, 503 when not yet polled):**

```json
{
  "alias":     "NQM6.CME@RITHMIC",
  "mid":       21326.00,
  "spread":    0.25,
  "nearest":   {"label": "+1", "distance": 12.50, "side": "above",
                "decision": "ENTER_LONG_FOLLOW", "confidence": 0.60},
  "conviction": {"score": 0.35, "trend": "RISING", "anchorMode": "LIVE"},
  "regime":             "TRENDING_UP",
  "regimeConfidence":   0.70,
  "session":   {"code": "ACTIVE", "label": "RTH ACTIVE", "anchorMode": "LIVE"},
  "news":      {"blocked": false, "label": null},
  "asOfMs":    1747680240500,
  "ageMs":     312,
  "stale":     false,
  "health":    "ok",
  "consecutiveFails": 0,
  "lastError":       null
}
```

### GET `/api/pax/whynow`

Active WHY-NOW triggers (max 5, severity-sorted HIGH > MED > LOW). Edge triggers linger ~15 s after fire; state-condition triggers re-emit while the condition holds.

```json
{
  "triggers": [
    {
      "kind":          "LEVEL_APPROACH",
      "severity":      "HIGH",
      "label":         "+1",
      "headline":      "approaching +1 (+12.50p), ENTER_LONG_FOLLOW, conf 0.62",
      "details":       "price within 2.0 ticks of +1; composite_score=0.61",
      "firstSeenMs":   1747680228000,
      "asOfMs":        1747680240500
    }
  ],
  "asOfMs": 1747680240500,
  "ageMs":  312,
  "stale":  false
}
```

**Trigger taxonomy:**

| Kind | Fires when | Severity | Lifecycle |
|---|---|---|---|
| `LEVEL_APPROACH` | nearest level within `prox_ticks` (default 8) | HIGH if `confidence >= 0.5`, MED otherwise | state — re-emits while in proximity |
| `MIDDLE_LOCK_ENTER` / `_EXIT` | `or_levels.middleLock` edge change | MED | edge — lingers 15 s |
| `TREND_SIGNAL_FIRE` | `trend_signal.changedSinceLastTick` + new `bucketEnteredMs` | HIGH (STRONG_*) / MED (WEAK_*) | edge — lingers 15 s |
| `CONVICTION_FLIP` | conviction sign cross past ±0.10 hysteresis | HIGH | edge — lingers 15 s |
| `REGIME_CHANGE` | flow.regime → ABSORPTION_* / EXHAUSTION_* | HIGH | edge — lingers 15 s |
| `MICRO_EVENT` | new SPOOF / ICEBERG / STOP_SWEEP in last 5 s | MED | edge — lingers 15 s |
| `NEWS_T_MINUS_5` | `gates.news.blocked == true` | HIGH | state |
| `BRIDGE_DEGRADED` | `health != "ok"` OR `anchorMode != "LIVE"` | MED | state |
| `EOD_RISK` | `session.code ∈ {CLOSE_RISK, POST_MARKET}` | LOW | state |

Linger window is `pax_ai_config.json::linger_ms` (default 15 000, clamped [3 000, 60 000]).

### GET `/api/pax/level/<label>`

Per-level Jane-Street edge calculus. `<label>` ∈ {`OR-H`, `OR-L`, `+1`, `+2`, `+3`, `-1`, `-2`, `-3`}.

```json
{
  "label":           "+1",
  "price":           21391.00,
  "side":            "above",
  "distance":        12.50,
  "proximity":       true,
  "decision":        "ENTER_LONG_FOLLOW",
  "confidence":      0.62,
  "composite_score": 0.61,
  "composite_dir":   "FOLLOW_LONG",
  "edge_calculus": {
    "expected_R":            1.116,
    "prob_pay_for_trade":    0.61,
    "prob_reach_next_rung":  0.274,
    "max_heat_pts":          21.75,
    "invalidation_price":    21319.75,
    "scratch_price":         21391.00,
    "payline_price":         21401.00,
    "rung1_price":           21456.00,
    "size_tier":             "FULL",
    "composite_dir":         "FOLLOW_LONG",
    "level_kind":            "OR_LEVEL",
    "tick_size":             0.25,
    "reasons": [
      "composite_dir=FOLLOW_LONG",
      "composite_score in [-1,+1], confidence=0.62",
      "size_tier=FULL per confidence vs config thresholds",
      "flow.regime=TRENDING_UP (conf 0.71)"
    ]
  },
  "asOfMs":     1747680240500,
  "ageMs":      312,
  "stale":      false,
  "anchorMode": "LIVE"
}
```

All edge-calculus values are pure functions of the snapshot + `pax_ai_config.json` constants (size-tier thresholds, directional_R table, tick/payline/rung tables per product). No LLM math.

### GET `/api/pax/playbook`

Scenario tree for the active level.

```json
{
  "current_state": "AT_LEVEL_ENTRY",
  "active_level":  "+1",
  "branches": [
    {"name": "FOLLOW long break of +1",
     "if":   "first print >= 21391.00 with sustained bid confirmation",
     "then": "ENTER long, size per composite confidence"}
  ],
  "gates": {
    "session":    "ACTIVE",
    "news":       "CLEAR",
    "anchor":     "LIVE",
    "middleLock": false,
    "regime":     "TRENDING_UP"
  },
  "asOfMs": 1747680240500,
  "ageMs":  312,
  "stale":  false
}
```

States: `WAIT_FOR_LEVEL`, `AT_LEVEL_ENTRY`, `IN_TRADE`, `RUNNER`, `EXIT_WATCH`.

### GET `/api/pax/health`

Process health. Returns 200 whenever the Pax AI HTTP server is up — independent of dashboard or Claude reachability. The launcher uses this as its probe.

```json
{
  "dashboardReachable":          true,
  "dashboardLastAtMs":           1747680240500,
  "dashboardAgeMs":              312,
  "dashboardConsecutiveFails":   0,
  "dashboardLastError":          null,
  "pollMs":                      1000,
  "modelLive":                   "claude-haiku-4-5",
  "modelDeep":                   "claude-sonnet-4-6",
  "claudeAvailable":             true
}
```

### GET `/api/pax/skills`

Skill registry — lists `skills/*/SKILL.md` files Pax AI's frozen system prompt loads.

```json
{
  "skills": [
    {"id": "hft_microstructure_quant_v1", "path": "C:\\...\\skills\\hft_microstructure_quant_v1\\SKILL.md"},
    {"id": "pax-or",                       "path": "C:\\...\\skills\\pax-or\\SKILL.md"}
  ]
}
```

### GET `/api/pax/chat/history`

SQLite chat journal at `D:\BookmapLogs\pax-chat.db` (override via `PAX_LOG_DIR`).

Query params:

| Param | Default | Validation |
|---|---|---|
| `limit` | 50 | clamped to `[1, 500]`; invalid → default 50 (never 500's on garbage) |
| `scope` | `all` | `all` returns rows from every past run; `run` returns only the current `run_id` |

Response:

```json
{
  "rows": [
    {"id": 12, "run_id": "16da9c7c...", "ts_ms": 1747680240500,
     "role": "YOU", "text": "is +1 in play?", "meta": {...}},
    {"id": 13, "run_id": "16da9c7c...", "ts_ms": 1747680241200,
     "role": "PAX", "text": "Yes -- STRONG_BULL holding...", "meta": {...}}
  ],
  "run_id": "16da9c7c...",
  "scope":  "run",
  "limit":  50
}
```

### POST `/api/pax/chat/stream`

Server-Sent Events. Spawns the `claude` CLI per turn; streams `text_delta` events from the model.

**Request body:** `{"message": "is +1 in play?"}`

**SSE event sequence:**

```
event: start
data: {"model":"claude-haiku-4-5","router_primary":"pax-or",
       "router_secondary":[],"snapshot_stale":false,"snapshot_age_ms":312}

event: token
data: {"text":"Approaching "}

event: token
data: {"text":"+1 "}

...

event: done
data: {"exit_code":0,"elapsed_ms":6244,"tokens_emitted":12,
       "aborted":false,"error":null,"stderr_tail":null,
       "model":"claude-haiku-4-5","router_primary":"pax-or"}
```

Possible `event: error` payload if `prompts.write_frozen_prompt()` fails or the subprocess can't be spawned. The chat handler clears its abort flag on every exit path via `try / finally`.

### POST `/api/pax/chat/abort`

Aborts the chat currently in flight (if any). Returns `{"aborted": true|false}`. Internally calls `proc.terminate()` → `proc.kill()` if the process doesn't exit within 500 ms.

### POST `/api/pax/chat/forget`

Deletes chat journal rows.

**Request body:** `{}` (deletes current run only) or `{"run_id": "<id>"}` (delete specific run) or `{"run_id": "*"}` (wipe everything).

**Response:**

```json
{"deleted": 2, "run_id": "16da9c7c...", "new_run_id": "16da9c7c..."}
```

The frontend exposes this as a `/forget` slash command in the chat input.

---

## Claude CLI contract

`pax_ai/claude_stream.py` invokes the Claude CLI with a deterministic flag set:

```
claude [--bare] -p "<digest + ROUTER hint + user message>" \
       --model <models.live>                                \
       --output-format stream-json --verbose --include-partial-messages \
       --tools ""                                            \
       --max-turns 1                                         \
       --append-system-prompt-file <frozen system prompt>
```

| Flag | When | Why |
|---|---|---|
| `--bare` | only when `ANTHROPIC_API_KEY` is set in env OR `PAX_AI_CLAUDE_BARE=1` | API-key mode: skip OAuth/keychain + auto-discovery for faster, deterministic cold start. Omitted under OAuth subscription auth because `--bare` returns "Not logged in". |
| `--tools ""` | **always** | No Bash, no Read, no Edit, no MCP. Read-only conversation. |
| `--max-turns 1` | **always** | Single response then exit. No agentic loop. |
| `--output-format stream-json --verbose --include-partial-messages` | **always** | Required combination for line-by-line `text_delta` SSE events. |
| `--append-system-prompt-file <frozen>` | **always** | Byte-identical file across calls → Anthropic 5-min ephemeral cache reuse. |

**Models** are config-driven (`pax_ai_config.json::models`):

```json
{
  "models": {
    "live":           "claude-haiku-4-5",
    "deep":           "claude-sonnet-4-6",
    "opus_opt_in":    false,
    "deep_when_opus": "claude-opus-4-7"
  }
}
```

**Frozen system prompt** is rendered once at boot to `%LOCALAPPDATA%\pax-ai\system_prompt.txt` and contains, in order:
1. Pax AI base preamble (`pax_ai/prompts.py::_BASE_PREAMBLE`) — output style, hard rules, anchor-mode gate, edge calculus field map.
2. All available `skills/*/SKILL.md` bodies concatenated.

Per-turn routing is delivered as a `ROUTER:` hint in the user message, not by swapping system-prompt files — that keeps the cache key stable.

---

## Skills

Pax AI consults two skill files (markdown) loaded into the frozen system prompt at boot:

| Skill | Trigger keywords | Role inside Pax AI |
|---|---|---|
| `skills/pax-or/SKILL.md` | `or-`, `or-h`, `or-l`, `opening range`, `+1`/`+2`/`+3`/`-1`/`-2`/`-3`, `follow`, `fade`, `pay for`, `runner`, `scratch`, `extension`, `rung` | Default primary. Pax canon: trade location at the operator-configured Static OR window; FOLLOW/FADE rules; rung ladder; stops + payline; regime gates. |
| `skills/hft_microstructure_quant_v1/SKILL.md` | `tape`, `orderflow`, `aggressor`, `spoof`, `iceberg`, `stop_sweep`, `absorption`, `imbalance`, `microstructure` | Reference context for microstructure reads. Even when the router picks this skill as primary, the response is conversational prose (per the HFT skill invariant). |

Skill routing is implemented in `pax_ai/prompts.py::route()`. The ROUTER hint that appears in the user message looks like:

```
ROUTER: consult SKILL pax-or; secondary SKILL hft_microstructure_quant_v1.
```

---

## Tests

```powershell
cd pax-ai
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pytest
```

As of production cut: **193 passing in ~0.5 s**.

Coverage highlights:

- `test_snapshot_schema.py` — pins every snapshot field path Pax AI consumes; fixture-driven, will catch dashboard drift.
- `test_edge_calculus.py` — pure-function tables for size_tier, expected_R, prob_pay_for_trade, max_heat, payline/rung/invalidation per product.
- `test_triggers.py` — 36 tests: all 9 trigger kinds + alias-isolation matrix + linger window + stale-gate.
- `test_chat_stream.py` — fake-Claude SSE pipeline, abort path, missing-binary fallback.
- `test_chat_handler.py` — abort cleanup invariants. **Autouse `_isolate_journal` fixture replaces `chat.journal.record` with a no-op + installs `_connect` tripwire so tests never touch `D:\BookmapLogs\pax-chat.db`.**
- `test_prompts.py` — pins the Static-OR-source-of-truth language, the no-stale-anchor-claims invariant, and the HFT-skill-no-JSON-router invariant.
- `test_server_helpers.py` — `_clamp_history_limit` parametric coverage.
- `test_ui_escape.py` — static-grep regression that dynamic snapshot fields go through `_esc()` before innerHTML.
- `test_journal.py` — SQLite round-trip + run-scoped delete.
- `test_voice_normalizer.py` — jargon regex (`in queue` → `NQ`, `v whap` → `VWAP`, etc.).
- `test_config.py` — hot-reload + dotted-path getter.

The `mcp-server` test suite (486 passing) is independent — those exercise the dashboard, bridge, paper-sim engine, and pax_daemon paths.

---

## Capture snapshot fixtures

Dashboard must be running. Run:

```powershell
$py = "C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe"
& $py C:\Bookmap\addons\MCP\Bookmap\pax-ai\fixtures\capture_snapshot.py
```

Saves to `pax-ai/fixtures/snapshot_<ISO>.json`. The schema test in `tests/test_snapshot_schema.py` enumerates these and asserts every required field path resolves.

---

## Operational caveats

1. **Addon jar duplicates.** Bookmap scans `C:\Bookmap\addons` recursively. Standalone `indicators/OpenRange/build.ps1` and `indicators/PaxAILauncher/build.ps1` write into `build/libs/` which sits under that scanned tree. After any rebuild, clear:

   ```
   C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange\build\libs\openrange-release.jar
   C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange\build\libs\openrange-release-fixed.jar
   C:\Bookmap\addons\MCP\Bookmap\indicators\PaxAILauncher\build\libs\paxai-launcher-release.jar
   ```

   before any Bookmap restart, or use the existing `build-and-deploy.ps1` (bridge-jar version) as a model and write an equivalent quarantine for the indicator builds. Canonical jars live at `C:\Bookmap\addons\{bookmap-mcp-bridge, openrange-release, paxai-launcher-release}.jar`.

2. **Chat journal lives in `D:\BookmapLogs\pax-chat.db`.** Override path via `PAX_LOG_DIR` env. The frontend's `/forget` slash command (or `POST /api/pax/chat/forget`) deletes the current run's rows; pass `{"run_id": "*"}` to wipe everything.

3. **Stale anchor gates Pax output.** When `snap.session.anchorMode != "LIVE"` (e.g. dashboard restarted, OR not yet captured), Pax AI's chat is informational-only. The strip + drawers continue to update; only entry recommendations are gated.

4. **Pywebview transparency on Win11 + Edge WebView2 is not reliable** — the UI uses an opaque dark surface rather than true alpha for legibility. See spec §9.3 for the colour palette.

5. **Cold-start chat latency** is 5–8 s the first time per process; subsequent turns hit Anthropic's 5-min prompt cache and TTFB drops to ~1 s.

---

## Layout

```
pax-ai/
  pax_ai/                     -- package
    __init__.py               -- version + DEFAULT_PORT + DASHBOARD_URL
    __main__.py               -- entry: --shell | server-only, --port
    server.py                 -- ThreadingHTTPServer, REST + SSE handlers
    shell.py                  -- pywebview frameless / on_top / draggable
    poller.py                 -- 1 Hz background snapshot poller
    triggers.py               -- alias-scoped trigger engine + linger cache
    chat.py                   -- SSE chat handler, digest builder, abort owner
    claude_stream.py          -- subprocess wrapper for `claude` CLI
    prompts.py                -- frozen system prompt + skill router
    edge_calculus.py          -- pure-function Jane-Street edge per level
    playbook.py               -- scenario tree builder
    context.py                -- quantized strip digest
    journal.py                -- SQLite chat journal (WAL, default_db_path)
    voice.py                  -- server-side jargon normalizer
    config.py                 -- pax_ai_config.json hot-reload
    static/                   -- index.html + inline CSS + JS frontend
  spikes/
    spike_webview.py          -- throwaway visual spike (Phase 0a artifact)
  fixtures/
    capture_snapshot.py       -- writes snapshot_<ISO>.json
    snapshot_*.json           -- captured /api/snapshot bodies for tests
  tests/
    test_*.py                 -- 193 tests across 12 files
  pyproject.toml
  pax_ai_config.json          -- models, tick sizes, payline/rung tables, linger_ms
  README.md
```

`indicators/PaxAILauncher/` is the matching Bookmap addon (Java) that spawns / destroys this Python package on the addon enable / disable lifecycle.
