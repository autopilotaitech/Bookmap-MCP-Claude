# Pax AI — Design Spec

Date: 2026-05-19
Owner: Will (autopilotaitech)
Status: Draft for review. Phase 0a spike PASSED (aesthetic locked; on_top + drag verified after `easy_drag=True`). No product code scaffolded yet.

---

## 1. Goal

A separate, floating, modern dark-glass chat box ("Pax AI") that helps a live NQ/MNQ trader make informed decisions over the order flow already computed by the Bookmap MCP dashboard, OpenRange addon, and TrendAnalyzer port. It must be fast, low-latency, never block Bookmap's data thread, and never invent data.

Pax AI is a thin projection + chat layer over the existing quant stack. It does not recompute signals. It exposes a Jane-Street-style API surface that reads the dashboard snapshot, quantizes it for prompt-cache stability, runs deterministic edge calculus per level, and streams Claude CLI tokens for natural-language conversation.

---

## 2. Constraints (non-negotiable)

- **Separate process**, separate package, separate port (`:18891`). Lives at repo root in `pax-ai/`. Does NOT modify `indicators/OpenRange/`, `indicators/trendanalyzer-mvp/`, the Java bridge addon, or any code under `mcp-server/bookmap_mcp/` (except adding a new file or two — never editing the production `claude_cli.py` blocking path).
- **Read-only over Bookmap.** Pax AI never holds a bridge token, never calls `/place_limit_order`, never imports `bookmap_place_limit_order` or `bookmap_cancel_order`. AST test pins this.
- **Read-only over the dashboard.** Polls `127.0.0.1:18888/api/snapshot` (no auth needed; localhost). Never writes to dashboard state.
- **No LLM math.** All numeric outputs (scores, EVs, probabilities) come from deterministic code. Claude handles natural-language explanation only.
- **Never blocks Bookmap.** Pax AI runs entirely outside Bookmap's JVM. Polling the dashboard piggybacks on the existing 5s in-memory cache (`cached_fetch_snapshot`) — no additional bridge load.
- **ASCII-only source.** Per CLAUDE.md.
- **MVP (Phases 0–4) news scope is `news-calendar.json` only.** External feeds (RSS, Finnhub, Benzinga, Truthbrush) gated behind explicit settings toggles in post-MVP phases, default off.
- **All HTTP bound to `127.0.0.1`.** No CORS allowed. No remote access.
- **Existing tests must still pass:** `cd mcp-server && python -m pytest`; `indicators/OpenRange/build.ps1` (Java tests); `indicators/trendanalyzer-mvp/...` if touched (it won't be).

---

## 3. Architecture

Three processes, all already-running pattern from the repo. Pax AI is the new third process.

```
Bookmap.exe                       Python dashboard                 Python pax_ai (NEW)
  MCP bridge addon  :8765   ->    fetch_snapshot()  :18888   ->    HTTP + SSE  :18891
  OpenRange addon         polls   /api/snapshot                     - context poller
  (also polls dashboard)          cached 5s in-memory                - signal projection layer
                                                                    - trigger engine
                                                                    - chat (claude CLI)
                                                                    - news-calendar reader
                                                                    - SQLite journal
                                                                            |
                                                                            v
                                                                    pywebview shell
                                                                    (Edge WebView2, frameless,
                                                                     on_top, easy_drag)
```

### 3.1 Dependencies

- Python 3.11+, same venv as `mcp-server` (`mcp-server/.venv`), one extra package: `pywebview==6.2.1` (installed; verified).
- Edge WebView2 Runtime (already installed on this Win11 box: 148.0.3967.70).
- `claude` CLI on PATH (already used by existing `claude_cli.py`).
- Standard library only for HTTP server (`http.server.ThreadingHTTPServer`) — matches `overview_ui.py` precedent.

### 3.2 Threading

- **Main thread:** pywebview event loop (Edge WebView2 message pump). Must remain unblocked.
- **HTTP server thread pool:** ThreadingHTTPServer worker per request. Each request returns within 2s budget.
- **Context poller thread:** 1s loop, fetches `/api/snapshot` over HTTP, parses, updates in-memory `_LATEST_SNAPSHOT` + `_LATEST_SNAPSHOT_AT_MS`. Single thread, no fan-out.
- **Claude streaming subprocess:** spawned per chat turn via `asyncio.subprocess.create_subprocess_exec`. SSE handler drains stdout. Cancel via `proc.terminate()` then `proc.kill()`.

No Bookmap thread is touched. No bridge call is made. The dashboard remains the single tenant of the Java bridge.

### 3.3 On-top quirk (Phase 0a finding)

`pywebview.create_window(on_top=True)` applies `WS_EX_TOPMOST` once on creation. Some Bookmap fullscreen modes can still cover the Pax window. **Mitigation:** a 5s ctypes ticker calls `SetWindowPos(hwnd, HWND_TOPMOST=-1, 0,0,0,0, SWP_NOMOVE|SWP_NOSIZE|SWP_NOACTIVATE)` to reassert topmost without stealing focus. Documented in `pax_ai/shell.py`.

---

## 4. Data sources (read-only inputs)

### 4.1 Dashboard snapshot — `GET http://127.0.0.1:18888/api/snapshot`

Already produced by `mcp-server/bookmap_mcp/dashboard.py::fetch_snapshot()`. Cached 5s in-memory by `cached_fetch_snapshot()`. Pax AI's poller hits this every 1s; reads will mostly hit the cache.

**Fields Pax AI consumes** (exact paths — pinned by Phase 0 schema test against captured fixtures):

| Pax AI surface | Snapshot path | Notes |
| --- | --- | --- |
| Active alias / mid / spread | `alias`, `book.mid`, `book.spread` | mid is points; `null` while bridge offline |
| OR levels | `or_levels.levels[]` | list of {label, price, side, distance, proximity, decision, confidence, composite_score, composite_dir, components} |
| Middle lock / proximity flags | `or_levels.middleLock`, `or_levels.inProximity` | bool gates |
| Composite conviction | `conviction.score`, `conviction.trend`, `conviction.anchorMode`, `conviction.sourceScores`, `conviction.sourceReliability`, `conviction.effectiveWeights` | score in [-1,+1] |
| Trend signal | `trend_signal.kind`, `.eligible`, `.eventMs`, `.eventMsSource`, `.blockedReason`, `.mid` | parsing rules in trend_signal_policy memory |
| Flow regime + bias | `flow.regime`, `flow.regimeConfidence`, `flow.biasScore`, `flow.biasTrajectory`, `flow.ofi`, `flow.cvdDelta`, `flow.vpt` | quant features |
| VWAP / VP biases | `vwap_bias.score`, `.label`, `.components.{sigma_z,regime}`; `vp_bias.score`, `.label`, `.components.{va_state,hvn_count,lvn_count}` | bias context |
| Microstructure | `micro_events.events[]` (type, side, ts, price) | SPOOF / ICEBERG / STOP_SWEEP |
| Tape | `tape_flow.deltaScore`, `.deltaLabel`; `tape_buckets` | aggressor imbalance |
| Session + news gates | `gates.session.code`, `.label`, `.anchorMode`, `.anchorAgeMs`; `gates.news.blocked`, `.label` | regime gates |
| Pax verdict | `pax.decision`, `pax.size_tier`, `pax.confidence`, `pax.reason` | upstream Pax engine call |
| Position / working / fills | `position`, `working`, `fills` | NOT sent to Claude; used for local gating only |
| Health | `health`, `bridgeUrl`, `bridgeError`, `nextSteps`, `tokenConfigured` | offline payload shape |

**Phase 0 must capture fixtures and assert these field names exist BEFORE any consumer code is written.** Fixture file: `pax-ai/fixtures/snapshot_*.json`. Schema test: `pax-ai/tests/test_snapshot_schema.py`.

### 4.2 News calendar — `news-calendar.json` (MVP-only news source)

Already at `C:\Bookmap\addons\MCP\Bookmap\news-calendar.json`. Shape pinned by `dashboard.py::news_blackout()`. Pax AI reads it directly; never writes.

---

## 5. Pax AI signal layer — Jane-Street API (`/api/pax/*`)

Pax AI's API on `:18891` is a thin projection on top of the dashboard snapshot. Every endpoint:

- Is **deterministic**: same snapshot in -> same response out. No LLM math.
- Refuses to act on stale data: returns `{"stale": true, "reason": "..."}` when `_LATEST_SNAPSHOT_AT_MS` is more than 5000 ms old OR `gates.session.anchorMode != "LIVE"` and the endpoint requires live anchor.
- Returns quantized fields where used for chat context — quantization is the prompt-cache key driver.
- Never exposes bridge token, balance, fill prices, or PII.

### 5.1 `GET /api/pax/context`

Compact strip data. ~150 bytes JSON. Polled by the UI every 1s.

```
{
  "alias": "NQM6.CME@RITHMIC",
  "mid": 21326.00,
  "spread": 0.25,
  "nearest": {"label": "+1", "distance": 12.50, "side": "above", "decision": "ENTER_LONG_FOLLOW", "confidence": 0.62},
  "conviction": {"score": 0.34, "trend": "RISING", "anchorMode": "LIVE"},
  "regime": "TRENDING_UP",
  "regimeConfidence": 0.71,
  "session": {"code": "ACTIVE", "label": "RTH ACTIVE"},
  "news": {"blocked": false, "label": null},
  "asOfMs": 1747680240500,
  "ageMs": 312,
  "stale": false
}
```

### 5.2 `GET /api/pax/whynow`

Current trigger set, deduplicated, severity-ranked. Returns at most 5. See section 6 for the trigger taxonomy.

```
{
  "triggers": [
    {
      "kind": "LEVEL_APPROACH",
      "severity": "HIGH",
      "label": "+1",
      "headline": "approaching +1, FOLLOW long, conf 0.62",
      "details": "STRONG_BULL trend just fired; VWAP_BIAS bull aligned; no SPOOF on ask",
      "bucketMs": 60000,
      "firstSeenMs": 1747680228000,
      "asOfMs": 1747680240500
    },
    ...
  ],
  "asOfMs": 1747680240500,
  "stale": false
}
```

### 5.3 `GET /api/pax/level/{label}`

Per-level edge calculus. `label in {OR-H, OR-L, +1, +2, +3, -1, -2, -3}`. Used by the UI for "tap the chip" detail and by Claude for level-specific reasoning. Deterministic. Pure functions only.

```
{
  "label": "+1",
  "price": 21391.00,
  "side": "above",
  "distance": 12.50,
  "proximity": true,
  "decision": "ENTER_LONG_FOLLOW",
  "confidence": 0.62,
  "composite_score": 0.61,
  "composite_dir": "FOLLOW_LONG",

  "edge_calculus": {
    "expected_R":          1.85,   // EV in R-multiples, capped by max_heat
    "prob_pay_for_trade":  0.58,   // P(at least 10 pts in our favor before stop)
    "prob_reach_next_rung":0.34,   // P(reach +2 before stop / before scratch)
    "max_heat_pts":        18.0,   // worst expected adverse excursion before payline
    "invalidation_price":  21306.25, // 1 tick below OR-Low (initial stop) for long
    "scratch_price":       21391.00, // entry price (scratch stop)
    "payline_price":       21401.00, // entry + 10 NQ pts
    "rung1_price":         21456.00, // +65 from entry
    "size_tier":           "FULL",   // FULL / HALF / NONE — from composite confidence
    "reasons": [
      "composite_score >= 0.5 -> FULL",
      "STRONG_BULL trend signal eligible",
      "VWAP_BIAS bull aligned with FOLLOW_LONG",
      "no ICEBERG defending the ask in last 10s"
    ]
  },

  "scenario": {
    "active_branch":  "ENTRY",
    "entry":   {"trigger": "first print >= +1 with confirmation", "size": "FULL"},
    "manage":  {"payline": 21401.00, "stop_after_payline": "BE", "rung1": 21456.00},
    "exit":    {"max_runner_target": 21521.00, "invalidation": "STOP_SWEEP at OR-H rotation"}
  },

  "asOfMs": 1747680240500,
  "stale": false,
  "anchorMode": "LIVE"
}
```

#### 5.3.1 Edge calculus — exact definitions

All inputs are dashboard-provided. All outputs are pure functions.

- `expected_R` = composite_confidence * directional_R(regime, level) + (1 - composite_confidence) * 0. Capped at `max_runner_R` from settings.
- `directional_R(regime, level)` = lookup table calibrated to NQ extension rungs:
  - FOLLOW_LONG + TRENDING_UP at OR-H or +1: 1.8 R median
  - FOLLOW_LONG + ABSORPTION_BID at OR-L: 2.4 R median (highest)
  - FADE + EXHAUSTION at +2 / +3: 1.6 R median
  - All other: 1.0 R median
- `prob_pay_for_trade` = `0.30 + 0.50 * confidence` (linear in level composite confidence), bounded [0.30, 0.80].
- `prob_reach_next_rung` = `0.10 + 0.40 * confidence * regimeConfidence`, bounded [0.10, 0.50].
- `max_heat_pts` = `OR_width + 1 tick` for initial-stop trade; `entry - scratch + slippage_buffer (1 tick)` for scratch-stop.
- `invalidation_price` = opposite-side OR boundary minus/plus 1 tick.
- `scratch_price` = entry price (the level itself for FOLLOW; OR-H/-L for FADE inside OR).
- `payline_price` = entry + 10 NQ pts (long) / entry - 10 NQ pts (short). For ES: 2.5 pts. Pulled from `pax-or` per-product table.
- `rung1_price` = entry +/- 65 NQ pts (15 ES pts).
- `size_tier`:
  - `confidence >= 0.50` -> `FULL`
  - `0.35 <= confidence < 0.50` -> `HALF`
  - `confidence < 0.35` -> `NONE`

All thresholds and table values live in `pax-ai/pax_ai_config.json` (hot-reloaded by mtime, same pattern as `pax_weights.json`). Defaults pinned by `tests/test_edge_calculus.py`.

### 5.4 `GET /api/pax/playbook`

Current scenario tree for the active level. Text-shaped for both UI and Claude consumption.

```
{
  "current_state": "WAIT_FOR_LEVEL",   // WAIT_FOR_LEVEL | AT_LEVEL_ENTRY | IN_TRADE | RUNNER | EXIT_WATCH
  "active_level": "+1",
  "branches": [
    {"name": "FOLLOW long break", "if": "first print >= 21391.00 + confirmation", "then": "ENTER FULL"},
    {"name": "FADE long rotation", "if": "STRONG_BEAR fires at +1 within 8s of approach", "then": "ENTER SHORT HALF"},
    {"name": "STAND DOWN", "if": "VWAP regime flips BLOWOFF_REVERT", "then": "no entry"}
  ],
  "gates": {
    "session":   "ACTIVE",
    "news":      "CLEAR",
    "anchor":    "LIVE",
    "middleLock": false,
    "vwap_or":   "ALLOW_LONG"
  },
  "asOfMs": 1747680240500,
  "stale": false
}
```

### 5.5 `POST /api/pax/chat/stream`

Server-Sent Events. Spawns `claude` CLI per turn. Frames the snapshot digest into the user message; the system prompt is byte-identical across turns to maximize Anthropic ephemeral cache hits.

**Exact CLI invocation (the `--bare` flag is conditional, see "Auth modes" below):**

```
claude [--bare] -p "<user message + compact digest>" \
  --model <models.live>                    \   # default claude-haiku-4-5
  --output-format stream-json --verbose --include-partial-messages \
  --tools ""                               \   # no tools; chat-only
  --max-turns 1                            \
  --append-system-prompt-file <frozen system prompt file>
```

**Why each flag:**
- `--bare` (conditional, see Auth modes) — skip auto-discovery of hooks/skills/plugins/MCP/CLAUDE.md/auto-memory. Deterministic across machines.
- `--output-format stream-json --verbose --include-partial-messages` — required combination for line-by-line partial-message events (per Claude CLI docs).
- `--tools ""` — no built-in tools. Pax AI live chat is conversation over a digest, not agentic coding. Active in **both** auth modes.
- `--max-turns 1` — single response then exit. No agent loop. Active in **both** auth modes.
- `--append-system-prompt-file` — points at the frozen system prompt file (router rules + concatenated skill bodies; see section 8). Byte-identical text across calls => Anthropic ephemeral prompt cache hit (5-min TTL).

**Auth modes** (`pax_ai/claude_stream.py::_use_bare()`):

The Claude CLI's `--bare` flag explicitly skips OAuth + keychain reads (per the official Claude Code docs). That makes it inappropriate for subscription-authenticated users by default, because the CLI would return "Not logged in".

| Condition                                                                | `--bare` passed? | Notes                                                                                 |
|---------------------------------------------------------------------------|------------------|---------------------------------------------------------------------------------------|
| `ANTHROPIC_API_KEY` set in env (API-key mode)                             | YES              | Fastest cold start; recommended for scripted / CI / automation use.                  |
| `PAX_AI_CLAUDE_BARE=1` set in env (manual opt-in)                         | YES              | Bypass auto-detection; assumes operator has set up an API key separately.            |
| Neither env var set (subscription OAuth mode — typical individual user)   | NO               | CLI uses the operator's `claude auth login` OAuth keychain. Cold start ~1 s slower because the CLI now reads CLAUDE.md / hooks / skills from `~/.claude` and the cwd. `--tools ""` + `--max-turns 1` keep the run read-only and single-turn even in this mode. |

The previous (pre-audit) version of this spec asserted `--bare` was always used. That contradicted reality and broke for OAuth subscribers — fixed.

**SSE event shape sent to UI:**

```
event: token
data: {"text": "Approaching "}

event: token
data: {"text": "+1"}

...

event: done
data: {"cost_usd": 0.0012, "input_tokens": 824, "output_tokens": 47, "cache_read": 720}
```

**Cancel:** `POST /api/pax/chat/abort` -> `proc.terminate()` then `proc.kill()` if still alive after 500 ms.

### 5.6 `GET /api/pax/skills`

List of skills available + which fire by query keyword routing rules (see section 8).

### 5.7 `GET /api/pax/health`

Process health:
- `dashboard_reachable`, `dashboard_age_ms`
- `claude_cli_present` (boolean; checks `where claude` once at boot, cached)
- `cache_hit_rate` (running window)
- `triggers_per_minute` (running window)
- `last_chat_at_ms`

---

## 6. Trigger engine policy

Pure delta-detector. Runs on every successful snapshot poll. Emits `Trigger{kind, severity, label, headline, details, bucketMs, firstSeenMs, asOfMs}`. Dedup by `(kind, label, bucketMs=60000)`.

| Kind | Fire condition | Severity |
| --- | --- | --- |
| `LEVEL_APPROACH` | `min(level.proximity_ticks for level in or_levels.levels) <= prox_ticks` (default 8 ticks = 2 NQ pts) | HIGH if level.confidence >= 0.5, else MED |
| `MIDDLE_LOCK_ENTER` / `MIDDLE_LOCK_EXIT` | `or_levels.middleLock` edge change | MED |
| `TREND_SIGNAL_FIRE` | `trend_signal.kind` changes to STRONG_BULL/STRONG_BEAR with `eligible == true` | HIGH |
| `CONVICTION_FLIP` | sign change in `conviction.score` with +/- 0.10 hysteresis | HIGH |
| `REGIME_CHANGE` | `flow.regime` transitions into ABSORPTION_BID / ABSORPTION_ASK / EXHAUSTION_UP / EXHAUSTION_DOWN | HIGH |
| `MICRO_EVENT` | new SPOOF / ICEBERG / STOP_SWEEP within last 5s | MED |
| `NEWS_T_MINUS_5` | within 5 min of any blackout window in `news-calendar.json` | HIGH |
| `BRIDGE_DEGRADED` | `health == "offline"` OR `anchorMode != "LIVE"` | MED |
| `EOD_RISK` | `session.code in {CLOSE_RISK, POST_MARKET}` | LOW |

**Stale gate:** triggers are SUPPRESSED entirely when snapshot age > 5s OR (kind requires live anchor AND `anchorMode != "LIVE"`).

**Dedup:** same `(kind, label)` within `bucketMs` ignored.

---

## 7. Claude CLI contract

### 7.1 Models

Hot-reloaded from `pax-ai/pax_ai_config.json`:

```
{
  "models": {
    "live":             "claude-haiku-4-5",
    "deep":             "claude-sonnet-4-6",
    "opus_opt_in":      false,
    "deep_when_opus":   "claude-opus-4-7"
  }
}
```

- Live mode (default for all chat + WHY-NOW): `claude-haiku-4-5`.
- Deep mode (slash-command `/deep` or button): `claude-sonnet-4-6` by default; `claude-opus-4-7` when `models.opus_opt_in == true`.

### 7.2 Frozen system prompt file

Rendered ONCE at process boot to a single temp file (`%LOCALAPPDATA%/pax-ai/system_prompt.txt`). Contents (in order):

1. Router rules (which Skill section is authoritative for a given keyword set).
2. Output style guide (terse, quant analyst voice, no emojis, no markdown headers).
3. Hard rules (no LLM math, never invent fields, refuse to recommend live orders).
4. ALL available Skill file contents concatenated, each prefixed by an unambiguous header (e.g. `## SKILL: pax-or`).

This single file is byte-identical for the whole process lifetime, so every chat call passes the same `--append-system-prompt-file` -> Anthropic's 5-min ephemeral cache reuses it. Size is bounded (~15-25 KB after all skills concat).

Per-turn routing is delivered as a short hint INTO the user message digest, not by swapping system-prompt files. Example user message header:

```
ROUTER: consult SKILL pax-or; secondary SKILL hft_microstructure_quant_v1.
SNAPSHOT DIGEST
  alias        : NQM6.CME@RITHMIC
  ...
USER:
  is +1 still in play?
```

This keeps the cache key stable across turns (system prompt file unchanged) and pushes the dynamic part into the user-message slot, which is short and inexpensive to re-tokenize.

### 7.3 User message digest (per turn)

```
SNAPSHOT DIGEST
  alias        : NQM6.CME@RITHMIC
  mid          : 21326.00
  nearest      : +1 (+12.50p, FOLLOW long, conf 0.62)
  conviction   : 0.34 RISING LIVE
  regime       : TRENDING_UP (conf 0.71)
  vwap_bias    : BULLISH
  vp_bias      : INSIDE_VA
  micro_events : []
  session      : ACTIVE
  news         : CLEAR

USER:
  is +1 still in play?
```

Numbers quantized: mid to 0.25, conf to 0.05, distance to 0.5, score to 0.05. Same quantization as `claude_cli.py::_decision_cache_key`. This is what makes the user-message cache key stable across consecutive 1s polls when nothing material has changed.

### 7.4 Excluded from prompts

Bridge token, position size, position avg price, balance, raw fills, raw working orders.

---

## 8. Skill mapping

Pax AI loads Skill files (markdown system-prompt text) from `skills/` to give Claude domain-specific reasoning.

### 8.1 Existing skills reused (no modification)

- `skills/pax-or/SKILL.md` — autoload for keyword set `{or, opening range, +1, -1, +2, -2, level, follow, fade, pay for the trade, runner}`.
- `skills/hft_microstructure_quant_v1/SKILL.md` — autoload for `{tape, orderflow, absorption, spoof, iceberg, stop_sweep, aggressor}`.
- Existing MCP-tool skills (`momentum-scan`, `risk-check`, `or-bias`, `vwap-or-gate`, `session-clock`, `news-blackout`, `absorption-watch`, `trade-decision`, `trade-journal`) — Pax AI's TRIGGER ENGINE invokes the deterministic logic these describe; Claude does NOT call them as tools (we run `--tools ""`). The skills' decision-tree text is read by the router only when a relevant keyword fires.

### 8.2 New skills (created in Phase 4, gated on spec approval)

- `skills/pax-ai-router/SKILL.md` — single source of truth for query routing. Maps query keywords to the sub-skill(s) to load via `--append-system-prompt-file`. Tiny, deterministic.
- `skills/pax-ai-edge-calculus/SKILL.md` — explains the field names + units in the `/api/pax/level/{label}.edge_calculus` payload so Claude speaks them naturally without inventing them.
- `skills/pax-ai-playbook/SKILL.md` — explains the scenario tree shape from `/api/pax/playbook` so Claude reads "active_branch" and walks the user through the next decision.

### 8.3 Loading rule

System prompt file is BYTE-IDENTICAL across all turns (see section 7.2) and already contains ALL Skill bodies. The router's only job per turn is to tell Claude which sub-skill to weigh as authoritative, delivered as a one-line ROUTER hint in the user message.

For each chat turn:

1. Tokenize the user message.
2. Apply router rules to pick 0..N sub-skill IDs (pure function, deterministic).
3. Render the ROUTER hint string: `"ROUTER: consult SKILL <primary>; secondary SKILL <a>, <b>."` (sub-skill list is comma-joined; absent if no match).
4. Prepend the ROUTER hint to the digest block in the user message.

If no sub-skill matches, default to `pax-or` (the most-common NQ chat case). The system-prompt file is never re-written or re-flagged per turn — that is what preserves the prompt cache hit rate.

---

## 9. UI spec

Locked aesthetic from Phase 0a spike v2 (file: `pax-ai/spikes/spike_webview.py`).

### 9.1 Window

- Size: 360 x 500 default. Resizable corner.
- Frameless. Custom title bar with brand + two traffic-light-style dots (minimize, close). Close calls `js_api.close()` -> `window.destroy()`.
- Always-on-top via `pywebview.create_window(on_top=True)` + ctypes ticker reassert every 5s (mitigation for fullscreen Bookmap covering it).
- Drag: `easy_drag=True` (pywebview-managed; the CSS `-webkit-app-region` approach does NOT work in pywebview's WebView2 surface).
- Resizable: yes, snap-to-edge tolerance handled by pywebview.

### 9.2 Layout

```
+----------------------------------+
| PAX AI                       o O |   title bar (drag, custom controls)
+----------------------------------+
| NQM6  21326.00   +12.50p +1 FOLLOW   conv +0.34 LIVE   |   strip (mono)
+----------------------------------+
|                                  |
|  +-- WHY NOW ------------------+ |
|  | approaching +1, FOLLOW...   | |
|  +------------------------------+ |
|                                  |
|  YOU 14:32:08                    |   transcript
|  is +1 still in play?            |
|                                  |
|  PAX 14:32:09                    |
|  Yes. STRONG_BULL holding...     |
|                                  |
|  +-- WHY NOW ------------------+ |
|  | STOP_SWEEP printed at OR-L  | |
|  +------------------------------+ |
+----------------------------------+
| ask about flow ...  [mic] [send] |   input
+----------------------------------+
```

### 9.3 Aesthetic specifics (from locked spike)

- Background: radial gradient `#131a25 -> #0a0e15 -> #07090d`. NOT transparent (Edge WebView2 + DWM compositing on Win11 doesn't reliably show desktop behind; legibility wins).
- Accent: `#36d6ff` cyan (matches OpenRange marker bull color). `#ff9b3c` orange (matches bear marker).
- Typography: `Inter` for prose, `JetBrains Mono` / `Consolas` for the strip + price.
- Borders: 1 px `rgba(255,255,255,0.045)` interior lines; 1 px `rgba(54,214,255,0.10)` outer rim.
- Shadow: `0 14px 50px rgba(0,0,0,0.55)` outer; 1 px inner highlight `rgba(255,255,255,0.04)`.
- WHY-NOW chip: cyan-tinted card, badge label, single-line headline + italic-toned details.
- Clear-on-send: input value emptied immediately on Enter; user message renders in transcript on next SSE `done`.

### 9.4 Voice button

Web Speech API in the WebView. `webkitSpeechRecognition` for continuous=false, interim=true. Final transcript runs through a server-side jargon normalizer before being sent to chat:

```
in queue -> NQ        v whap -> VWAP        or low -> OR-Low      or high -> OR-High
plus one -> +1        minus one -> -1       plus two -> +2        minus two -> -2
buying pressure -> bid-side pressure         selling pressure -> ask-side pressure
```

Same regex list lives in `pax_ai/voice.py` (pure function, unit-tested) and `pax-ai/pax_ai/static/app.js` (in case server unreachable). Server is the source of truth.

---

## 10. Failure modes

| Failure | UI surface | Behavior |
| --- | --- | --- |
| Dashboard offline (`/api/snapshot` -> `health=offline`) | Header red "BRIDGE OFFLINE"; context strip greyed | Triggers paused. Chat keeps working but tells user "live context unavailable". |
| Snapshot age > 5s | Yellow STALE pill | New triggers suppressed. Last good context still shown. |
| `anchorMode != LIVE` | Yellow "STALE OR" pill | Live-anchor-required triggers suppressed (LEVEL_APPROACH, TREND_SIGNAL_FIRE). |
| `claude` not on PATH | Toast: "AI offline -- claude CLI not found" | Context strip + triggers keep working. Chat input disabled. |
| Chat call > 20s | "request timed out, /retry to try again" | `proc.terminate()` -> `kill()`. SSE closed with `event: error`. |
| Voice perm denied | Mic icon greyed | Falls back to text only. |
| WebView2 missing | pywebview raises -> falls back to `webbrowser.open(http://127.0.0.1:18891)` | Loses always-on-top + frameless; chat still works. |
| News calendar parse error | Silent stderr log | News blackout triggers paused; everything else continues. |

---

## 11. Latency budget (warm, MVP Phase 4)

| Stage | ms |
| --- | --- |
| Mic -> final text (Web Speech, on-device path) | 300 |
| `POST /api/pax/chat/stream` localhost | 5 |
| Build digest + L0 cache lookup | 10 |
| `claude` CLI spawn (Windows overhead) | 250 |
| First token (Anthropic ephemeral cache hit) | 700 |
| SSE -> DOM render | 5 |
| **Total TTFB** | **~1.27 s warm** |

Cold (no cache): ~3.5 s. Latency benchmark in `pax-ai/tests/test_latency_bench.py` (marked `slow`), asserts warm < 2 s.

---

## 12. Security / privacy

- All HTTP bound to `127.0.0.1`. No CORS. No remote access.
- Bridge token NEVER read by Pax AI (only the dashboard handles it).
- No PII, balance, position-size, fill-price, working-order text in prompts.
- Voice: Web Speech API; UI badge shows "on-device" vs "cloud" per browser capability.
- Chat journal `pax-chat.db` opened WAL, never shared. `/forget` slash command erases the run rows.
- AST test pins: no `bookmap_place_limit_order` / `bookmap_cancel_order` references in `pax-ai/`; no `BOOKMAP_ALLOW_TRADING` references.

---

## 13. Tests

| Layer | What | Location |
| --- | --- | --- |
| Schema | Captured fixtures + exact-field-name assertions for every snapshot path Pax AI uses | `pax-ai/tests/test_snapshot_schema.py`, fixtures in `pax-ai/fixtures/` |
| Edge calculus | Pure-function table tests: directional_R, prob_pay_for_trade, prob_reach_next_rung, size_tier thresholds | `pax-ai/tests/test_edge_calculus.py` |
| Trigger engine | Parametric fixture-replay -> expected trigger sequence; dedup matrix | `pax-ai/tests/test_triggers.py` |
| Voice normalizer | Regex round-trip on a curated phrase set | `pax-ai/tests/test_voice_normalizer.py` |
| Context digest | Quantization stability + cache-key determinism | `pax-ai/tests/test_context_digest.py` |
| Chat stream | Mocked `claude` binary (stub script) -> SSE event sequence | `pax-ai/tests/test_chat_stream.py`, `pax-ai/tests/fake_claude.py` |
| News calendar | T-5m logic, blackout window edges | `pax-ai/tests/test_news_calendar.py` |
| Safety AST | No bridge / trading imports anywhere under `pax-ai/` | `pax-ai/tests/test_safety_ast.py` |
| Latency bench | Real `claude` CLI cold/warm; asserts warm < 2 s | `pax-ai/tests/test_latency_bench.py` (marked `slow`) |
| UI smoke | `python -m pax_ai` starts; `/`, `/api/pax/context`, SSE end-to-end against fake_claude | `pax-ai/tests/test_smoke.py` |

No Java changes => no Java tests required.

---

## 14. Phase plan

| Phase | Scope | Verification |
| --- | --- | --- |
| **0a (done)** | pywebview spike, aesthetic locked, drag/on_top resolution captured | PASS |
| **0** | `pax-ai/` scaffold (`pyproject.toml`, package), `fixtures/capture_snapshot.py`, captured fixtures, `tests/test_snapshot_schema.py`. ThreadingHTTPServer at `:18891` serving placeholder index + `/api/snapshot` proxy. `pax-ai-start.bat`. **No trigger code, no claude CLI yet.** | `pytest pax-ai/tests/test_snapshot_schema.py` green; `curl :18891/api/snapshot` returns dashboard JSON. |
| **1** | Context strip endpoint + JS poller. Window opens, strip updates 1 Hz. No chat. | Eyeball updates live; AST test added; pytest green. |
| **2** | Signal layer: `/api/pax/level/{label}`, `/api/pax/playbook`, `/api/pax/whynow`, edge calculus pure functions. `pax_ai_config.json` hot reload. | All section-5 fields present; `test_edge_calculus.py` + `test_triggers.py` green. |
| **3** | Claude streaming wrapper + SSE `/api/pax/chat/stream` + abort. Frontend chat (input + send + clear-on-send + transcript). | `test_chat_stream.py` green against fake_claude; manual SSE token stream verified. |
| **4** | Frontend WHY-NOW chips + click-to-explain; voice button (Web Speech API); jargon normalizer; news-calendar.json reader for `NEWS_T_MINUS_5`. New skills (`pax-ai-router`, `pax-ai-edge-calculus`, `pax-ai-playbook`) authored. | Full E2E demo against live dashboard. |
| **5 (post-MVP)** | pywebview shell promotion (frameless on_top window with ctypes topmost ticker). Settings panel. | Manual visual. |
| **6 (post-MVP)** | News feeds gated behind toggles: RSS poller + Finnhub calendar populator. | RSS items show; Finnhub populates `news-calendar.json` weekly. |
| **7 (post-MVP)** | Journal (`pax-chat.db`), `/deep` mode (Sonnet/Opus per opt-in), whisper.cpp toggle. | Latency bench + safety AST green. |

---

## 15. Locked defaults (approved 2026-05-19)

1. **Voice:** mic button visible but **inert until clicked**. No always-listening behavior.
2. **Chat journal path:** `D:\BookmapLogs\pax-chat.db` (matches existing `pax-journal.db`). Override via `PAX_LOG_DIR` env var, same convention as `dashboard.py`.
3. **Opus opt-in:** one-time settings toggle (`models.opus_opt_in` in `pax_ai_config.json`). No per-message `--opus` override flag. Keeps the surface small.
4. **Dashboard poll rate:** 1 Hz. Configurable via `pax_ai_config.json::poll_ms` (default 1000, clamped [500, 3000]).

---

## 16. Phase 0a artifacts (this commit cycle)

- `pax-ai/spikes/spike_webview.py` — spike script, aesthetic reference. Stays in tree (in `pax-ai/spikes/` so it's clearly throwaway). Not packaged.
- This spec.

No production code is written until this spec is approved.
