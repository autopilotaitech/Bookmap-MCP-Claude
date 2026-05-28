# Bookmap MCP — Live System Report

**Audience:** Developer hand-off.
**Author:** Claude (Pax AI dial-in session).
**Date:** 2026-05-28.

---

## 1. What "MCP" means in this repo

Two separate things share the name:

| Component | Process | Port | Role |
|---|---|---|---|
| **Java Bookmap addon bridge** (`addons/bookmap-mcp-bridge-v<N>.jar`) | Inside Bookmap | **8765** | Exposes raw market data over HTTP (orderbook, trades, fills, VWAP, momentum, microstructure events, lt_liquidity, etc.) behind a bearer token |
| **Python MCP server** (`mcp-server/bookmap_mcp/`) | Standalone Python | **18888** | Polls the Java bridge, computes derived signals (conviction, OR, IFL, trend, etc.), exposes a unified `/api/snapshot` JSON |
| **Pax AI** | Standalone Python + pywebview shell | **18891** | LLM (Claude Haiku/Sonnet) reasoning layer over the dashboard snapshot |
| **OpenRange addon** | Inside Bookmap | n/a | ScreenSpace HUD overlay reading both `/api/snapshot` and JSONL signal logs |

The Anthropic "MCP" protocol is exposed only by `mcp-server/bookmap_mcp/server.py` (two live-trade tools). All in-process trading logic is **paper-only** behind `pax_daemon.py`.

---

## 2. Bridge HTTP API (Java → Python)

All endpoints token-guarded (`Authorization: Bearer <token>` from `~/.bookmap-mcp/bridge.properties`).

**Read (GET):**

```
/ping                       liveness
/instruments                list aliases (e.g. "NQM6.CME@RITHMIC")
/orderbook?alias=...        L2 ladder
/recent_trades              tape (side = buy/sell from isBidAggressor)
/recent_fills               own fills (paper or live)
/working_orders             open orders
/position                   own position
/balance                    account
/vwap                       session VWAP + sigma bands
/momentum                   MomentumSnapshot (regime, z-scores)
/volume_profile             POC/VAH/VAL, HVN/LVN
/tape_buckets               binned tape flow
/lt_liquidity               LT bid/ask sizes + ratio
/book_dynamics              size churn / depth deltas
/pull_stack                 stacked queues / pulls
/microstructure_events      ICEBERG / ABSORPTION / STOP_SWEEP / SPOOF
/trend_analyzer             ported trendanalyzer-mvp (fast/slow/score)
/screenshot                 PNG of canvas
```

**Write (POST, token-guarded, some additionally gated by `BOOKMAP_ALLOW_TRADING=1`):**

```
/magnet_levels              configures stop-sweep magnets
/place_limit_order          LIVE - two-gate (confirm + env)
/cancel_order               LIVE - two-gate
```

**Trade sign convention (load-bearing):**

- `isBidAggressor=true`  -> buy aggressor  -> `"side":"buy"`  -> CVD +=
- `isBidAggressor=false` -> sell aggressor -> `"side":"sell"` -> CVD -=

---

## 3. Dashboard `/api/snapshot` (Python composite)

Single JSON blob, recomposed on every poll. Key sections:

```jsonc
{
  "ts": "...",
  "book": { "mid": 30322.0, "bid": ..., "ask": ... },
  "or_levels": {
    "orHigh": 30105.75, "orLow": 30040.25,
    "levels": [ { "label":"+3","price":30300.75,"side":"above",
                  "distance":21.25,"proximity":false,
                  "composite":{ "score":0.31,"direction":"FADE_SHORT",
                                "confidence":0.42,"drivers":[...],
                                "warnings":[] } }, ... ]
  },
  "or_session_config": { "anchorHHMM":"08:30", "anchorIso":"...",
                         "anchorTimezone":"America/Chicago",
                         "anchorRangeSeconds":30, "anchorMode":"LIVE" },
  "session": { "anchorHHMM":"08:30", "anchorMode":"LIVE", ... },

  "vwap_bias":   { "components":{ "sigma_z":..., "regime":..., "rth_eth":... }},
  "vp_bias":     { "components":{ "va_state":..., "hvn_count":..., "lvn_count":... }},
  "flow":        { "regime":..., "biasTrajectory":..., "regimeConfidence":... },
  "conviction":  { "score":..., "trend":..., "trajectory":...,
                   "anchorMode":..., "anchorAgeMs":... },
  "trend_signal":{ "kind":"STRONG_BULL"|"WEAK_BULL"|"STRONG_BEAR"|"WEAK_BEAR"|"NONE",
                   "eligible":true, "blockedReason":null,
                   "eventMs":..., "eventMsSource":"trend_analyzer" },

  "institutional_flow": {
    "regime": "ACCUMULATION"|"DISTRIBUTION"|"BALANCED"|"TRANSITION",
    "weighted_vote": -0.125, "raw_vote_pre_trend_filter": -0.25,
    "conviction": 0.25, "trend_filter":"ALIGNED|OPPOSED|NEUTRAL",
    "trend_sign": +1|-1|0,
    "drivers": [ { "name":"micro_events","signed":-1.0 }, ... ],
    "rotation_state": { "commit_level":"OR-H","rotations_completed":3,
                        "current_extreme_price":30326.25,
                        "next_rotation_target":30365.75 },
    "chop_window": null
  },
  "institutional_chart_events": [
    { "kind":"IFL","label":"IFL","direction":"LONG"|"SHORT","price":...,"ts":... }
  ],
  "ifl_outcomes": {
    "active": { "regime":..., "peak_favorable_pts":..., "max_adverse_pts":...,
                "rungs_advanced_so_far":... },
    "last_closed":{ "regime":..., "verdict":
                    "STALE"|"MISS"|"PARTIAL_HIT"|"HIT"|"SUSTAINED_HIT",
                    "rungs_advanced":... }
  },
  "ifl_rich_signals": { "emitted": 0|1 },
  "or_day_ledger": {
    "session_high_price": 30326.25, "session_low_price": 30282.75,
    "session_range": 43.5, "final_status":"HELD|BROKE_UP|BROKE_DOWN|..."
  },

  "pax": { "decision":"WAIT"|"LONG"|"SHORT", "components":..., ... },
  "pax_ai_chart_events": [ ... ]
}
```

---

## 4. Snapshot consumers

| Consumer | What it reads |
|---|---|
| **OpenRange addon (Java)** | Polls `/api/snapshot` every 1s. Renders Heatwave Quant Box, trend triangles, IFL triangles. NEVER mutates canvas off-thread - fetcher sets dirty flag, Bookmap callbacks repaint. |
| **Pax AI** | Polls `/api/snapshot`, builds a structured digest, hands to Claude CLI with `--tools "" --max-turns 1` (read-only). |
| **pax_daemon** | Polls, runs `decide_and_act()`, places **paper** brackets on local SimEngine. |
| **JSONL signal logs** | `D:\BookmapLogs\pax-ai-chart-signals.jsonl` (deterministic IFL-rich + LLM-emitted) -> OpenRange consumes for chart popups. |

---

## 5. How I'm learning the system

Today's session is the **dial-in window** before the Sunday-night Globex reopen and full-month June autonomous build-out. Approach:

### 5a. Live read loop

- Detached PowerShell watcher (`watcher_loop.ps1`, PID 19628) snapshots key fields every 30s into `D:\BookmapLogs\watcher_ps_log.jsonl`:
  `mid, regime, weighted_vote, raw_vote, conviction, trend_filter, trend_sign, micro_signed, top_drivers, rotation_state, lt_ratio, session_h/l/range, ifl_active, ifl_last_closed, rich_emit`
- Claude wakes every 60s via `ScheduleWakeup`, reads the last 3 rows, evaluates against operator's trade rules, and either pretend-enters or stays flat.
- Pretend trade ledger: `D:\BookmapLogs\my_pretend_trades.jsonl` (INIT/ENTRY/HOLD/EXIT/LESSON with reasoning).

### 5b. Calibration changes shipped today (Python-only, no Bookmap restart)

Tuned `institutional_flow.py` from morning-session retrospective (24 episodes, all MISS/STALE, 09:40-09:49 whipsaw cluster):

```python
_REGIME_VOTE_THRESHOLD          = 0.20    # base entry threshold
_REGIME_VOTE_THRESHOLD_ALIGNED  = 0.15    # lower when trend agrees
_TRANSITION_VOTE_THRESHOLD      = 0.20    # was 0.10, was firing on noise
_REGIME_OPPOSITE_HOLD_SEC       = 60.0    # block opposite regime 60s (anti-whipsaw)
_MICRO_EVENT_WINDOW_SEC         = 180.0   # widened from 90s
_TEXTBOOK_MICRO_THRESHOLD       = 0.7     # textbook-override gate
_TREND_OPPOSED_DAMPEN           = 0.5     # dampen, never amplify
```

Other live changes:

- **SPOOF added to signed map** (bid spoof -> bearish, ask spoof -> bullish).
- **chop_window made informational** (was hard-blocking real moves during lunch).
- **5-tier verdict** in `ifl_outcomes.py`: STALE / MISS / PARTIAL_HIT (peak_fav >= 32.5pt) / HIT (1 rung = 65pt) / SUSTAINED_HIT (>=2 rungs).
- **Proximity-relaxed rich-popup fallback** (`_NEAREST_LEVEL_MAX_DIST_PTS=65`): when regime fires but no level is in strict proximity, emit at nearest level within 1 rotation.
- **Extension state guard** in `attack_response.py`: OR-boundary state names (OR_H_BREAK_ACCEPT, etc.) replaced with EXT_HIGH/LOW_EXHAUST or NO_EDGE when level is `+N`/`-N`. Java drops NO_EDGE invisibly.
- **Regime veto** in `attack_response.py`: high-conviction (>=0.30) IFL contradicting row bias collapses to NEUTRAL.

### 5c. Deterministic rich-signal emitter

New `ifl_rich_signals.py` replaces LLM hallucination on chart popups:

- One row per (alias, level, regime, direction) per 30s bucket.
- `setup_type`: FOLLOW/FADE x LONG/SHORT from regime x level side.
- Stop placement: FADE = 1 rung past level, FOLLOW = scratch at level.
- Reason string packs: setup, top 3 signed drivers, stop price, `conv/vote/trend/rot`, label@price + mid.
- Writes to `D:\BookmapLogs\pax-ai-chart-signals.jsonl` with `source="institutional_flow_rich"`.

### 5d. Verdict signal

Every regime episode auto-closes with a verdict tier and the episode goes into `D:\BookmapLogs\ifl_outcomes.csv`. That's the closed-loop training signal: HIT/SUSTAINED_HIT vs MISS/STALE per setup, enabling re-tuning.

---

## 6. Key invariants the developer needs to respect

1. **Bridge port** comes from `~/.bookmap-mcp/bridge.properties`. The CLAUDE.md example "18888" is the **dashboard** port; the **bridge** is 8765 on this machine.
2. **Alias format includes the broker route**: `NQM6.CME@RITHMIC`, not `NQM6`.
3. **No live trade paths outside `server.py`.** AST tests pin this.
4. **No LLM math in decision skills.** Skills are deterministic decision trees over the snapshot. Pax AI chat is conversational over a digest, never JSON-forced.
5. **Pax AI Claude CLI calls are read-only**: `--tools "" --max-turns 1`, always. `--bare` only when `ANTHROPIC_API_KEY` env is set (OAuth subscribers omit it).
6. **Threading rule for OpenRange**: fetcher worker thread NEVER touches canvas; signals dirty flag, Bookmap callback paints.
7. **OR anchor**: `or_session.effective_session_anchor()` is the SOLE production read point. No more hard-coded 08:30 fallbacks in active code paths.
8. **Bookmap addon scan is recursive over `C:\Bookmap\addons`** - repo lives under that tree, so build artifacts race the canonical install. `build-and-deploy.ps1` quarantines duplicates.
9. **`pax_weights.json` hot-reloads** via mtime; cache dict is mutated in place (never reassigned) so `signal_engine`'s re-export stays valid.
10. **Bridge sync seconds**: `_sync_bridge_config` always posts `rth_open` as `HH:MM:SS`. Java `ConfigHandler` accepts `HH:MM[:SS]` via `LocalTime.parse`. `anchorHHMM` is legacy/truncated; `anchorIso` is canonical.

---

## 7. Things I'd flag for the developer

- **Bridge micro-event detector is sparser than operator's chart detectors** (FV Absorption Alert at trigger=125). Currently compensated by lowering ALIGNED vote threshold to 0.15. Java-side detector tuning needed.
- **`or_day_ledger` in-memory state resets on dashboard restart.** Needs disk persist or startup replay from `_session_snapshots/`.
- **Trend lag** (15s candle aggregator) means trend filter often blocks entries at the actual reversal point. Operator's proprietary "TTW cyan" line is faster - possible future integration as a 2nd trend opinion.
- **PARTIAL_HIT threshold** currently 32.5pt (half-rotation NQ). Operator flagged but didn't decide whether to lower to 20pt.
- **Feature Bus Phase 5 deferred**: auto-apply tuning, news, schema migrations. Phases 1-4B shipped (469 tests, all default-off behind flags).
- **OpenRange jar lock** while Bookmap runs - rebuilds must wait for close, or use the `addons-staging` workflow.

---

## 8. Operator's trade structure (for context)

The system plots signals; the operator executes manually in Bookmap DOM. The algo must produce signals that fit this structure:

- **Entry**: 2 MNQ contracts at OR boundary or extension when setup signal fires (trend-flip + institutional flow).
- **TP1**: close contract #1 at 1st extension (65pt NQ, 15pt ES).
- **Runner**: 2nd contract trails behind extension levels; stop ratchets up each completed rotation.
- **Add-on (optional)**: continued trend AND not in chop AND not at ATH without confirmation.
- **Discipline**: catch ONE directional move per day, then DONE. Skip add-on at ATH without confirmation.

Rotation unit (Pax-canonical): **NQ = 65pt, ES = 15pt** between OR boundary and 1st extension, and between each subsequent extension.

---

## 9. Test surface

```
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest
```

Currently green. Key pinning suites:

- `test_institutional_flow.py` - regime, vote weights, opposite-hold, chop informational, SPOOF sign map
- `test_ifl_outcomes.py` - 5-tier verdict
- `test_ifl_rich_signals.py` - setup type matrix, stop math, dedup
- `test_or_levels_dynamic_extensions.py` - `floor(reach/rung)+1` math
- `test_offline_snapshot_shape.py` - bridge-offline diagnostic + token leak guard
- `test_source_share_caps.py` - TrendAnalyzer can't dominate composite
- `test_safety_boundaries.py` - no live-order imports outside `server.py`
- `test_trend_signal_policy.py` - debounce + eligibility gate
- `test_crash_recovery_across_process_restart` - journal `_event_seq` reseed

---

## 10. Runtime operator scripts

Three PowerShell entry points (repo root + `scripts/`):

- **`.\build-and-deploy.ps1`** - runs `gradlew clean test jar`, installs newest `bookmap-mcp-bridge-v<N>.jar` as canonical `C:\Bookmap\addons\bookmap-mcp-bridge.jar`, quarantines every other matching jar under `C:\Bookmap\addons` (recursive scan) to `C:\Bookmap\addons-archive\`. Waits for Bookmap close.
- **`.\dashboard-start.ps1`** - clears stale `__pycache__`, launches `python -B -u -m bookmap_mcp.dashboard --port 18888` in a new window using `mcp-server\.venv\Scripts\python.exe`. Probes `/api/snapshot` to verify port owner.
- **`.\scripts\verify-runtime.ps1`** - 4-layer diagnostic. Layer -1: addon jar hygiene (exactly one canonical jar). Layer 0: direct bridge probe with `Authorization` header (token never printed). Layer 1: dashboard reachable. Layer 2: dashboard `health=ok`. Layer 3: OpenRange poll URL matches.

Plus paper-trading daemon: `pax-start.bat` / `pax-stop.bat`.
Plus Pax AI: `pax-ai-start.bat` (defaults to floating pywebview window; `server` arg for server-only mode).

---

## 11. Code layout pointers

- `mcp-server/bookmap_mcp/dashboard.py` - live HUD snapshot composer (~3500 lines, audited heavily). `fetch_snapshot`, `compute_or_levels`, `_level_composite`, `compute_tape_flow`, `compute_vwap_bias`, `compute_vp_bias`, `trade_decision`, `compute_session_conviction`, `_sync_magnet_levels`.
- `mcp-server/bookmap_mcp/signal_engine.py` - pure-Python facade re-exporting every signal helper from dashboard.py without bridge dependency. Non-dashboard consumers (pax_daemon, replay tools, research notebooks) import from here.
- `mcp-server/bookmap_mcp/institutional_flow.py` - continuous regime aggregator (ACCUMULATION / DISTRIBUTION / BALANCED / TRANSITION) over pull_stack / lt_liquidity / flow.* / micro_events with research-grounded weights (Cont/Kukanov OFI). Emits chart triangles into `snap["institutional_chart_events"]` (label `IFL`, deduped per 60s bucket).
- `mcp-server/bookmap_mcp/or_day_ledger.py` - per-session OR + extension audit log. Writes append-only `D:/BookmapLogs/or-day-ledger.csv` on session boundary.
- `mcp-server/bookmap_mcp/or_level_crossings.py` - tracks every OR/ext level crossing with prior context, speed, regime state, event counts. CSV: `D:\BookmapLogs\or-level-crossings.csv`.
- `mcp-server/bookmap_mcp/ifl_rich_signals.py` - deterministic rich chart signal emitter (replaces LLM hallucinations).
- `mcp-server/bookmap_mcp/sim_engine.py` - local SQLite-backed paper broker. EOD auto-flatten at 15:00 CT; pass `eod_close_hour_ct=None` in tests.
- `mcp-server/bookmap_mcp/pax_daemon.py` - background paper-trading daemon. Refuses to start if `BOOKMAP_ALLOW_TRADING=1`.
- `mcp-server/bookmap_mcp/journal.py` - SQLite journal (runs, snapshots, signals, orders, fills, positions, daily_stats, adapter_health, events, outcomes). WAL mode; daemon writes, UI reads.
- `mcp-server/bookmap_mcp/overview_ui.py` - read-only HTTP dashboard at `:18890` with collapsible sections.
- `indicators/OpenRange/src/main/java/com/openrange/` - Heatwave Quant Box (5 Java classes), trend triangle painter, IFL triangle painter.

---

## 12. Bridge-offline diagnostic

When `/api/snapshot` cannot reach the bridge, `fetch_snapshot` returns a structured payload (NOT a bare `{"health":"offline"}`):

```jsonc
{
  "health": "offline",
  "bridgeUrl": "http://127.0.0.1:8765",
  "bridgeReachable": false,
  "bridgeError": "...",
  "dashboardPort": 18888,
  "expectedBridgeConfigPath": "<path to bridge.properties>",
  "tokenConfigured": true,
  "nextSteps": [ "...failure-class-specific..." ],
  "error": "<legacy mirror of bridgeError>"
}
```

Token is NEVER included. Pinned by `test_offline_snapshot_shape.py` (5 tests including token-leak guard). OpenRange's `PaxHeatwaveSnapshotParser` detects `health=offline` and renders `BRIDGE OFFLINE` + reason on the canvas instead of stale data.

---

## Summary for the developer

Stack: Java bridge (in Bookmap) -> Python dashboard composer (18888) -> consumers (OpenRange overlay, Pax AI LLM, pax_daemon paper broker). Everything is read-only except the two AST-pinned live-trade endpoints. Pax AI's LLM access is `--tools "" --max-turns 1` (no agentic loop, no tools, no JSON-forced output). The institutional_flow engine + 5-tier verdict + rich-signal emitter is the deterministic core; LLM output is shape-validated decorator. All today's tuning changes are Python-only and hot-loadable without a Bookmap restart.
