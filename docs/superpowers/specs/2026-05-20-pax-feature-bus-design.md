# Pax Feature Bus - Audit + Design (Phase 0)

Status: DRAFT, awaiting operator approval before Phase 1 plan.
Date: 2026-05-20
Author: Claude (Opus 4.7), audit dispatched via 5 parallel Explore agents.

## EXECUTIVE VERDICT

Pax AI today is a read-only chat client over a live digest - strong skeleton, wrong
stage for what comes next. The dashboard already emits the data; Pax AI already
digests it; Claude CLI already returns metered cost/latency. What is missing is
persistence, replay, and outcome attribution. Every edge-bearing field is already
on `/api/snapshot` - none of it survives the process. `triggers.py` detects
regime/conviction/trend transitions in memory and discards them. `chat.py` writes
a 7-column chat log (`pax-chat.db`) that captures what was said but not what state
the model saw, what it cost, or whether it was right.

Recommendation: do NOT refactor; add a parallel writer. Pax AI already runs a 1 Hz
background poller (`poller.py`). Bolt a `feature_bus` daemon next to it that does
append-only structured capture into a new SQLite DB (cohabiting `D:\BookmapLogs\`)
plus an opaque digest blob store. Then build the replay/tuning tooling on top of
that DB. Five well-bounded phases, each independently shippable, each rollback-able
by toggling one config key.

Treat Claude as feature engineering + retrieval + replay, not online learning. The
Claude CLI stays `--tools ""` and `--max-turns 1` forever. The model improves
because the digest improves, not because the model trains.

## CURRENT DATA MAP

### What is captured today (and where)

| Stream                | Writer                                | Destination                                                | Cadence                       | Replayable | In Claude prompt          |
| --------------------- | ------------------------------------- | ---------------------------------------------------------- | ----------------------------- | ---------- | ------------------------- |
| OR signal CSV (27c)   | `PaxOpeningRangeSignalCsvLogger.java` | `D:\BookmapLogs\openrange-signals-{sym}.csv`               | on-change, 250 ms flush       | yes        | no (only summarized)      |
| OR session config     | `PaxOpeningRangeSessionConfigWriter`  | `D:\BookmapLogs\or-session-config.json`                    | atomic on settings change     | yes        | no (anchor only)          |
| Pax agent signals     | `dashboard.py::pax_record()`          | `D:\BookmapLogs\pax-agent-signals-{YYYYMMDD}.csv`          | on state-change               | yes        | no                        |
| Proximity recordings  | `pax_collector.py`                    | `D:\BookmapLogs\pax-recordings\{ts}_{level}.csv`           | snapshot while `inProximity`  | yes        | no                        |
| Paper-trade journal   | `pax_daemon.py` -> `journal.py`       | `D:\BookmapLogs\pax-journal.db` (9 tables, WAL)            | per signal/fill               | yes        | no                        |
| SIM trades            | `sim_engine.py`                       | `pax-trades.db`, `pax-daemon-trades.db`                    | per fill                      | yes        | no                        |
| Pax-AI chat           | `pax_ai/journal.py`                   | `D:\BookmapLogs\pax-chat.db` (1 table)                     | per turn                      | partial    | yes (history endpoint)    |
| Snapshot (live)       | dashboard.py (in-memory only)         | RAM                                                        | 1 Hz pull from bridge         | NO         | yes (digested)            |
| Triggers/transitions  | `triggers.py` (in-memory only)        | RAM                                                        | per snapshot                  | NO         | yes (rendered to /whynow) |
| Live Claude turn meta | `claude_stream.py`                    | None (cost/usage emitted, only in `meta_json` text blob)   | per chat                      | partial    | n/a                       |

### What `/api/snapshot` already exposes (edge-bearing fields)

All present today, sourced via `dashboard.py` composing bridge endpoints:

- OR levels and extensions:
  `or_levels.{orHigh, orLow, orWidthPts, levels[].{label, price, distance, decision, confidence, composite_score, proximity}, middleLock, inProximity}`
- Distance / proximity: `or_levels.levels[].distance`, `proxTicks`, `proxPts`
- Aggressor / delta proxy: `tape_flow.{deltaScore, fast30, slow5m, alignment}`, `flow.{biasScore, biasTrajectory, regime}` (no raw signed CVD because Bookmap API does not surface counterparty; the bridge proxies via aggressor side)
- Tape velocity / momentum: `momentum.{i10, i50, i200, flag}`, `flow.biasScore`
- Iceberg / spoof / absorption / stop-sweep: `pull_stack.recent_events`, `micro_events.events[].{type, price, side, ts/tsMs}`
- Liquidity pull / stack: `lt_liquidity.{survivalRate, ageDistribution}`, `pull_stack.windows[].{bias, rotation, aggregateBias}`
- Volume profile: `volume_profile.{poc, vah, val, levels[]}`, `vp_bias.{label, components.{va_state, hvn_count, lvn_count, regime}}`
- VWAP sigma / anchored: `vwap_obj.{vwap, stddev, upper1/2/3, lower1/2/3}`, `vwap_bias.{label, components.{sigma_z, regime, rth_eth}}`
- Trend signal: `trend_signal.{kind, renderableKind, eligible, bucketEnteredMs, eventMsSource}`
- Middle-lock / OR-lock: `or_levels.{middleLock, inProximity}`
- Session anchor mode: `session.{anchorHHMM, anchorTimezone, anchorRangeSeconds, anchorMode, anchorSource, code}`, `or_session_config`
- News blackout: `gates.news.{blocked, label}`
- Position / fills: `position.{position, entryPrice, pnl}`, `fills[]`, `working[]`, `balance.{equity, available}`
- Decision layer: `decision.{verdict, entry, stop, target1, target2, gates}`, `pax.{decision, size, size_tier, confidence, level_label, entry, overrides}`

### What today's Pax AI prompt path actually reads

`context.py::build_context` + `chat.py::_digest_lines` consume only:
`alias`, `book.{mid,spread}`, `conviction.{score,trend,anchorMode}`,
`flow.{regime,regimeConfidence,biasScore,biasTrajectory}`, top-K
`or_levels.levels[]`, `or_levels.{middleLock,inProximity}`,
`trend_signal.{kind,eligible}`, `vwap_bias.{label,components.sigma_z}`,
`vp_bias.{label,components.va_state}`, `micro_events.events[]`,
`gates.{session,news}`, `pax.{decision,size_tier}`, `health`, `bridgeError`.
Everything else in the snapshot is in RAM and dropped.

## EDGE DATA GAPS

Ordered by expected lift on Pax AI advice quality.

1. No event journal. `triggers.py` detects trend fires, conviction sign flips,
   regime shifts, middle-lock transitions, level approaches - and writes nothing.
   Today's "what changed in the last 60s" is reconstructable only from RAM, only
   for the current process. Lift: highest. Without it, the model cannot reason
   about velocity of conditions; replay cannot reconstruct the why.
2. No snapshot persistence. Snapshot is digested in flight and discarded. Replay
   is structurally impossible. Even today's chat turns cannot be re-rendered
   because the source snapshot is gone.
3. No outcome labels. When Pax says ENTER_LONG with confidence 0.62, there is no
   T+1m / T+3m / T+5m / T+15m mark-to-market that scores whether the call was
   right. `pax-journal.db.outcomes` exists but is unused at the AI-turn grain.
4. AI turn metadata is unindexed. `pax-chat.db` stores `meta_json` as a text blob
   - model, cost, tokens, router decision, snapshot age. None is queryable as
   columns. Cost drift, model drift, latency regression are all hidden.
5. Prompt digest is not content-hashed. Cache-hit rate on Anthropic's 5-minute
   prompt cache is opaque. We pin system prompt (good - `prompts.py` only
   rewrites on content change) but the user-message + digest changes every turn.
   Cache savings come from the system-prompt prefix; no measurement of reuse.
6. No schema versioning. Any persisted artifact (CSVs, JSON, DB rows) has no
   `schema_version` field. A change to `pax_record()` columns silently breaks
   downstream parsers.
7. No retention/rotation on growing files. `pax-journal.db` (4.7 MB and growing),
   `openrange-signals-*.csv` (14 MB+ per symbol), `pax-recordings/` (17 MB and
   unbounded). Operationally this will bite within months. Code comment in
   `journal.py` already says pruning lives in a tool that does not exist yet.
8. Microstructure raw is windowed away. `/microstructure_events?max=30` returns
   the last 30 events. Anything older than that window is lost forever. For the
   live moment this is fine; for replay it is fatal.
9. News calendar is a stub. `news-calendar.json` has 2 example entries from
   2026-05-18. No ingestion path keeps it current. The news-blackout gate is
   therefore mostly inactive in production.
10. Two skills overlap and conflict. `pax-or` is authoritative (5.2K words),
    `hft_microstructure_quant_v1` (660 words) is reference-only inside Pax AI,
    but its trigger words (tape, iceberg, absorption) route it as primary with
    pax-or as secondary - the larger authoritative skill demoted. Verified by
    the production invariant test that confirms prose output, but the routing
    inversion is a smell.
11. Operator settings are versioned only at the file level. `pax_settings.json`
    has `pax_settings.last_good.json` as a one-deep backup. No append-only audit
    row per change with diff and timestamp.

## PROPOSED FEATURE BUS ARCHITECTURE

Design rule: one writer, many readers, no live coupling.

```
                 +---------------------+
   Bridge -----> | dashboard /snapshot |
                 +----------+----------+
                            |
                            | HTTP poll 1 Hz
                            v
                 +---------------------+        +-----------------+
                 | pax_ai poller.py    | -----> | latest snapshot |  (RAM)
                 +----------+----------+        +-------+---------+
                            |                           |
                            |                           +--> /api/pax/context (UI)
              feature_bus.py (new daemon)               +--> chat digest -> Claude
              started from __main__.py::main                          |
              AFTER poller.start(),                                   | on every
              BEFORE journal.init();                                  | turn
              guarded by config.feature_bus.enabled                   v
                            |                           +-------------------------+
                            v                           | chat.py builds          |
              +-----------------------------+           |   AiTurnRecord          |
              | D:\BookmapLogs\pax-bus.db   |           | (with snapshot_sha256   |
              |   (WAL, append-only)        |  <--------+  and digest_sha256)     |
              +-----------------------------+           +-------------------------+
                            +                                       |
              +-----------------------------+                       |
              | D:\BookmapLogs\pax-digests\ |  <--------------------+
              |   YYYY-MM-DD\<sha>.txt      |   on chat turn
              +-----------------------------+                       |
              | D:\BookmapLogs\pax-snapshots| <--------------------+
              |   YYYY-MM-DD\<sha>.json     |   on chat turn (lossless source)
              +-----------------------------+
                            ^
                            |
              +-------------+--------------+--------------+
              |             |              |              |
        pax_bus_replay  pax_bus_eod   outcomes.py     ad-hoc SQL
        (offline)       (EOD batch)   (deferred,      (operator)
                                       in-process)

   triggers._emit_edge() -- new hook ------> feature_bus.record_trigger()
   (transition emit point)                   (queued, never blocks)
```

### Components

FeatureBus writer (`pax-ai/pax_ai/feature_bus.py`, new) - single daemon thread
started from `pax-ai/pax_ai/__main__.py::main` AFTER `poller.start()` and
BEFORE `journal.init()`, guarded by `config.feature_bus.enabled` (default
`false`). The order matters: poller must already have a tick available before
the bus tries to read it, and journal.init must NOT happen before the bus
because we want the bus's own DB-open path to fail loudly without taking the
chat journal down with it. Reads `poller.latest()` once per second, computes
deltas against the previous tick, writes:

- A `snapshot_features` row (quantized, ~100 cols, ~3 KB) every tick.
- Zero or more `level_events` rows when a level decision/confidence/proximity
  flips - detected by the bus's own snapshot delta logic, comparing the new
  `or_levels.levels[]` against the previous tick's stored copy.
- Zero or more `microstructure_events` rows for any `micro_events.events[]` not
  seen in the previous tick (dedup by `(type, price, tsMs)`).
- Zero or more `trigger_events` rows for EDGE-EVENT triggers (trend fires,
  conviction sign flips, regime changes, middle-lock transitions, micro_events)
  via a new outbound hook from `triggers._emit_edge()` (see Trigger Capture
  below). The bus NEVER calls `triggers.compute_triggers()` itself, because
  that function mutates the per-alias UI linger cache used by /api/pax/whynow.
- Zero or more `trigger_events` rows for STATE-CONDITION triggers
  (LEVEL_APPROACH, BRIDGE_DEGRADED, EOD_RISK, NEWS_BLACKOUT) captured by the
  bus's snapshot delta logic, independent of `triggers.py`. The shape is the
  same; the source path is different.

Threading: one writer thread + WAL = no blocking on read path. If the writer
queue fills (back-pressure), drop snapshots-not-events and log to stderr. Never
block the snapshot poller. `_emit_edge` writes synchronously to the bus's
bounded queue and returns immediately; the bus drains the queue on its own
thread.

Trigger Capture (load-bearing detail). `triggers.py` has two distinct
populations today:

(a) EDGE-EVENT triggers are emitted by `triggers._emit_edge(state, trig, now)`
    at line 121 - a single function called from every transition site
    (`_check_trend`, `_check_conviction_flip`, `_check_regime_shift`,
    `_check_middle_lock`, `_check_micro_events`). It is the natural,
    already-deduplicated "a new edge fired" choke point. Phase 1 adds ONE
    line at the bottom of `_emit_edge`: `feature_bus.record_trigger(trig)`,
    fire-and-forget. The write is queued; failure logs but does not raise.
    The existing linger-cache write and /api/pax/whynow rendering path are
    unchanged.

(b) STATE-CONDITION triggers (LEVEL_APPROACH, BRIDGE_DEGRADED, EOD_RISK,
    NEWS_BLACKOUT) are NOT produced by `_emit_edge` - they are appended
    fresh on every call to `compute_triggers()` based on the current snapshot.
    The bus MUST NOT call `compute_triggers()` to get them, because that
    function mutates the per-alias linger cache and is owned by the UI/whynow
    request path. Instead, the bus computes state-condition triggers itself
    from its own snapshot delta logic: it already inspects `or_levels`,
    `health`, `session`, and `gates.news` on every tick, so detecting "level
    approach crossed proximity threshold" or "news blackout opened" is one
    additional comparison against the previous tick's stored copy.

Net effect: every trigger that fires (edge-event or state-condition) gets a
`trigger_events` row. `compute_triggers()` is never invoked by the bus.
/api/pax/whynow byte-for-byte unchanged.

Digest blob store (`D:\BookmapLogs\pax-digests\YYYY-MM-DD\<sha256>.txt`) - on
every Claude turn, write the exact user message + digest to a content-addressed
file. Store the SHA-256 in the `ai_turns` row. This (a) measures digest entropy,
(b) enables byte-exact prompt replay, (c) gives an easy cache-reuse audit.

Raw snapshot blob store (`D:\BookmapLogs\pax-snapshots\YYYY-MM-DD\<sha256>.json`) -
on every Claude turn (NOT on every poll - too much volume), write the canonical
JSON of the snapshot dict that fed `build_user_message()` into a
content-addressed file. SHA-256 stored in `ai_turns.snapshot_sha256` (NOT NULL).
Without this, Phase 4 replay cannot byte-exactly reconstruct the digest because
`snapshot_features` is quantized + columnar - lossy by design. The raw blob is
the lossless source of truth. Canonicalization: `json.dumps(snap, sort_keys=True,
separators=(",", ":"), default=str)` - identical bytes for identical content,
no whitespace drift. Dedup is automatic (content-addressed): two turns one
second apart with an unchanged snapshot share one blob.

Outcome labeler (`pax-ai/pax_ai/outcomes.py`, new) - deferred-execution loop.
For each `ai_turn` with a directional verdict, schedule mark-to-market reads at
T+60s, T+180s, T+300s, T+900s by querying `snapshot_features` for the same
alias. Writes `trade_outcomes` rows. Runs as its own daemon thread, lagging by
15 minutes.

AI turn capture - the assembly point and shape. `chat.py::handle_chat_stream`
already builds `final_info` via the `on_done(info)` callback (lines 267-270 in
current source) - but `on_done` only carries Claude CLI metadata (exit_code,
elapsed_ms, cost, tokens). It does NOT carry the digest text, the normalized
user text, the assembled `pax_text`, the router fields, the snapshot meta, or
the run_id. Phase 1 introduces an explicit `AiTurnRecord` dataclass assembled
at the END of `handle_chat_stream` (after the SSE `done` event is sent), built
from variables already in scope:

```python
@dataclass
class AiTurnRecord:
    schema_version: int = 1
    ts_ms:          int                  # time.time_ns() // 1_000_000 at done
    chat_run_id:    str                  # journal.current_run_id()
    deep:           bool                 # explicit bool from request
    model:          str                  # _select_model(deep)
    router_primary: Optional[str]        # meta['router_primary']
    router_secondary: Optional[List[str]]
    user_text_raw:  str                  # user_text param verbatim
    user_text_normalized: str            # voice.normalize() result, from meta
    digest_text:    str                  # full_msg from build_user_message()
    digest_sha256:  str                  # sha256(digest_text.encode("utf-8")) - computed
                                          #   before insert, NEVER NULL
    snapshot_alias: Optional[str]        # from poller.latest()[0].get("alias")
    snapshot_ts_ms: Optional[int]        # poller as_of_ms
    snapshot_age_ms: int                 # poller age_ms
    snapshot_sha256: str                 # sha256 of canonical snapshot JSON -
                                          #   computed before insert, NEVER NULL
    pax_text:       str                  # accumulated streamed tokens
    exit_code:      Optional[int]        # final_info['exit_code']
    elapsed_ms:     Optional[int]        # final_info['elapsed_ms']
    api_duration_ms: Optional[int]       # final_info['duration_api_ms']
    total_cost_usd: Optional[float]
    input_tokens:   Optional[int]
    output_tokens:  Optional[int]
    cache_creation_tokens: Optional[int] # final_info['cache_creation_input_tokens']
    cache_read_tokens:     Optional[int] # final_info['cache_read_input_tokens']
    aborted:        bool
    error:          Optional[str]
```

`feature_bus.record_ai_turn(record)` inserts one row into `ai_turns`. Both
`digest_sha256` and `snapshot_sha256` are computed in `chat.py` BEFORE the
record is built and BEFORE `feature_bus.record_ai_turn` is called - they are
required fields, never NULL. The corresponding `pax-digests/.../<sha>.txt` and
`pax-snapshots/.../<sha>.json` files are written by `feature_bus` itself
(idempotent: `if exists: skip`), keeping chat.py free of filesystem layout
concerns.

Why this is safer than the original "on_done(meta)" sketch: `on_done` runs
inside the watchdog's exit path inside `claude_stream.py`. It does NOT have
direct access to `full_msg`, `meta`, or `user_text_normalized` - those are
chat.py's local variables. An on_done-only design either leaks chat scope into
claude_stream (bad coupling) or builds an incomplete record. Assembling the
record at the END of handle_chat_stream, in chat.py, with all variables in
scope, is the clean choice.

Schema versioning - every table has `schema_version INTEGER NOT NULL`. Bump on
any column add/remove. Replay tool refuses to read across versions without an
explicit `--allow-version-mismatch` flag.

Retention - config-driven (`feature_bus.retention_days`, default 30). A nightly
`tools/feature_bus_prune.py` deletes rows older than the window and `VACUUM`s
the DB. Default OFF in Phase 1 - manual operator runs only - until storage
growth is observed.

### Failure modes (designed for)

| Failure                                  | Impact                          | Mitigation                                                                |
| ---------------------------------------- | ------------------------------- | ------------------------------------------------------------------------- |
| Bridge down                              | snapshot=offline                | bus writes `health='offline'` rows; preserves the gap explicitly          |
| Pax AI server crash                      | bus stops, last good rows survive | next start resumes; no recovery needed because append-only              |
| Disk full                                | bus writer fails                | log to stderr, set `bus_writer_healthy=false` in /api/pax/health          |
| Schema mismatch on read                  | replay tool errors              | explicit `--allow-version-mismatch` + migration table                     |
| Concurrent writer (second Pax AI proc)   | SQLite WAL serializes           | second writer's BEGIN IMMEDIATE blocks max ~50 ms; advisory file lock     |

## DATA SCHEMA

All times are epoch ms (INTEGER) unless noted. All tables have
`id INTEGER PRIMARY KEY AUTOINCREMENT` and `schema_version INTEGER NOT NULL`.

```sql
-- One row per snapshot poll (~1 Hz)
CREATE TABLE snapshot_features (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  alias TEXT NOT NULL,
  health TEXT NOT NULL,
  bridge_error TEXT,
  session_code TEXT, session_anchor_mode TEXT, session_anchor_source TEXT,
  session_anchor_hhmm TEXT, session_anchor_tz TEXT, session_anchor_range_s INTEGER,
  mid REAL, spread REAL, best_bid REAL, best_ask REAL,
  or_high REAL, or_low REAL, or_width_pts REAL,
  middle_lock INTEGER, in_proximity INTEGER,
  flow_regime TEXT, flow_regime_conf REAL,
  flow_bias_score REAL, flow_bias_traj TEXT,
  tape_flow_delta REAL, tape_flow_fast30 REAL, tape_flow_slow5m REAL,
  momentum_i10 REAL, momentum_i50 REAL, momentum_i200 REAL, momentum_flag TEXT,
  vwap REAL, vwap_sigma REAL, vwap_sigma_z REAL, vwap_regime TEXT,
  vp_poc REAL, vp_vah REAL, vp_val REAL, va_state TEXT, hvn_count INTEGER, lvn_count INTEGER,
  conviction_score REAL, conviction_trend TEXT, conviction_trajectory TEXT,
  trend_kind TEXT, trend_renderable_kind TEXT, trend_eligible INTEGER,
  pax_decision TEXT, pax_size INTEGER, pax_size_tier TEXT, pax_confidence REAL,
  decision_verdict TEXT,
  position_size INTEGER, position_entry REAL, position_pnl REAL,
  news_blocked INTEGER, news_label TEXT,
  raw_json_sha256 TEXT
);
CREATE INDEX idx_snap_alias_ts ON snapshot_features (alias, ts_ms);

CREATE TABLE level_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  alias TEXT NOT NULL,
  level_label TEXT NOT NULL,
  level_price REAL,
  prev_decision TEXT, new_decision TEXT,
  prev_confidence REAL, new_confidence REAL,
  prev_proximity INTEGER, new_proximity INTEGER,
  trigger_reason TEXT
);
CREATE INDEX idx_lvl_alias_ts ON level_events (alias, ts_ms);

CREATE TABLE microstructure_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  alias TEXT NOT NULL,
  event_type TEXT NOT NULL,
  price REAL, side TEXT, size REAL,
  raw_json TEXT
);
CREATE INDEX idx_micro_alias_ts ON microstructure_events (alias, ts_ms);

CREATE TABLE trigger_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  alias TEXT NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  label TEXT, headline TEXT, details TEXT,
  snapshot_ts_ms INTEGER NOT NULL
);
CREATE INDEX idx_trig_alias_ts ON trigger_events (alias, ts_ms);

CREATE TABLE ai_turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  chat_run_id TEXT NOT NULL,
  deep INTEGER NOT NULL,
  model TEXT NOT NULL,
  router_primary TEXT, router_secondary TEXT,
  user_text_raw TEXT NOT NULL,
  user_text_normalized TEXT NOT NULL,
  pax_text TEXT,
  snapshot_alias TEXT, snapshot_ts_ms INTEGER, snapshot_age_ms INTEGER,
  snapshot_sha256 TEXT NOT NULL,    -- FK-ish to pax-snapshots/.../<sha>.json
  digest_sha256 TEXT NOT NULL,      -- FK-ish to pax-digests/.../<sha>.txt
  exit_code INTEGER,
  elapsed_ms INTEGER, api_duration_ms INTEGER,
  total_cost_usd REAL,
  input_tokens INTEGER, output_tokens INTEGER,
  cache_creation_tokens INTEGER, cache_read_tokens INTEGER,
  aborted INTEGER NOT NULL, error TEXT
);
CREATE INDEX idx_aiturn_ts ON ai_turns (ts_ms);
CREATE INDEX idx_aiturn_snapshot_sha ON ai_turns (snapshot_sha256);
CREATE INDEX idx_aiturn_digest_sha ON ai_turns (digest_sha256);

CREATE TABLE trade_outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ai_turn_id INTEGER NOT NULL,
  alias TEXT NOT NULL,
  verdict TEXT NOT NULL,
  entry_price REAL,
  mid_at_t0 REAL,
  mid_at_t60s REAL, mid_at_t180s REAL, mid_at_t300s REAL, mid_at_t900s REAL,
  realized_r_at_t60s REAL, realized_r_at_t180s REAL,
  realized_r_at_t300s REAL, realized_r_at_t900s REAL,
  expected_r REAL, prob_pay REAL,
  invalidated INTEGER, invalidation_reason TEXT,
  label_method TEXT, labeled_at_ms INTEGER
);
CREATE INDEX idx_outc_aiturn ON trade_outcomes (ai_turn_id);

CREATE TABLE settings_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  ts_ms INTEGER NOT NULL,
  file_path TEXT NOT NULL,
  full_sha256 TEXT NOT NULL,
  diff_summary TEXT,
  actor TEXT
);

CREATE TABLE replay_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  created_ms INTEGER NOT NULL,
  alias TEXT NOT NULL,
  start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
  label TEXT, notes TEXT,
  ai_turn_ids_json TEXT
);
```

JSONL alternative: each table can be mirrored as gzipped JSONL daily files
(`feature-bus-{table}-{YYYYMMDD}.jsonl.gz`) for cheap pandas/polars ingestion.
Phase 1 ships SQLite only; JSONL is a tuning-tool-side concern.

## CLAUDE LIVE DIGEST SPEC

### Hard budget

- System prompt: stable bytes, identical across the process lifetime,
  ~7.5K tokens (pax-or 6.8K + hft 0.9K + preamble). Already pinned. Do not touch.
  This is what gets the 5-minute Anthropic prompt-cache hit.
- User message + digest (the dynamic part): target 1200 tokens, hard cap 1500.
  Above 1500, drop the lowest-priority block and re-render. Above 1800, refuse
  to send and log a structured error.
- Per-turn raw cost on Haiku 4.5 at this size: ~$0.001 input + $0.001-0.003 output.
  Cache hits on the system prefix drop input by ~10x. Deep (Sonnet 4.6) ~3-5x
  more per turn; Opus 4.7 ~15x more (~$0.02-0.05/turn). Cost gate: budget alarm
  in EOD report when daily Opus spend exceeds operator-configured cap.

### Stable ordering (cache-aware)

Block order is FROZEN. Cache hits require byte-identical prefix; the first turn
of a session warms the cache, subsequent turns get reads on at least the early
blocks. Never reorder; never inject conditional blocks at the top.

```
[STATE]            (always present, ~120 tokens)
[ANCHOR]           (always present, ~60 tokens)
[GATES]            (always present, ~80 tokens)
[LEVELS]           (always present, top-3 nearest, ~250 tokens)
[MICROSTRUCTURE]   (always present, ~180 tokens)
[RECENT_EVENTS]    (last 5 micro_events + last 3 triggers, ~180 tokens)
[POSITION]         (always present, even if flat - ~60 tokens)
[SESSION_MEMORY]   (today's last 3 Pax verdicts + outcomes if labeled, ~180 tokens)
[USER]             (the user message verbatim, no transform, ~variable)
```

Quantize like `context.py` already does: mid in 0.25 increments, distance in 0.5,
scores/confidence in 0.05. Stable quantization is critical - without it, every
prompt is bytewise unique, killing cache reuse.

### Block content

- [STATE] alias, mid, spread, ts age, health. If `health=offline`, replace all
  subsequent blocks with one `[BRIDGE_OFFLINE] reason=... nextSteps=[...]` block.
- [ANCHOR] anchorHHMM, anchorTz, anchorRangeSeconds, anchorMode, anchorSource.
  If `anchorMode != LIVE`, prepend `INFORMATIONAL_ONLY: anchor is
  LAST_KNOWN_STALE/FALLBACK`. Pinned to invariant.
- [GATES] session code, session label, news.blocked, news.label, vwap_or state,
  stretch state. One line each.
- [LEVELS] top-3 by `|distance|`, formatted as
  `label price distance(pts) decision confidence composite`. Skip levels with
  `decision=WAIT` and `confidence<0.3`.
- [MICROSTRUCTURE] one line each: tape_flow.delta + alignment, momentum.flag
  (10/50/200 imbalances), vwap_bias.regime + sigma_z, vp_bias.label + va_state,
  conviction.score + trend + trajectory.
- [RECENT_EVENTS] last 5 micro_events (type, price, side, age) + last 3 triggers
  from `trigger_events` table (kind, severity, age). Triggers persisted means we
  can show "30 seconds ago: trend_fire STRONG_BULL" deterministically.
- [POSITION] size, entry, pnl, working orders count. Flat state is one line:
  `position: FLAT`.
- [SESSION_MEMORY] today's last 3 directional Pax verdicts from `ai_turns` +
  their labeled `trade_outcomes`. Format: `09:32:14 ENTER_LONG @23450.5 conf=0.62
  -> +0.8R at T+5m`. This is the model's only feedback loop.
- [USER] verbatim.

### What must NOT be sent

- Raw orderbook ladder (use derived: spread, mid only).
- Full trade tape (use momentum + tape_flow aggregates).
- `volume_profile.levels[]` (use poc/vah/val only).
- Snapshot raw JSON.
- Today's full chat history (only last 3 directional turns).
- Anything from yesterday's session.
- `pax-chat.db` row counts or any DB internals.
- Stack traces or bridge debug output.

### Failure modes

| Condition                                  | Behavior                                                                                                |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------- |
| Digest > 1800 tokens                       | Do not call Claude; emit `done` SSE with `error='digest_overflow'`; log full digest to digest_blobs    |
| Snapshot age > stale_threshold_ms (5s)     | Render `[STATE]` with `STALE: age=<ms>` tag; rest of digest unchanged; let Claude decide               |
| `health=offline`                           | Replace blocks 2-9 with bridge-offline block; user message still appended                              |
| `anchorMode != LIVE`                       | Render INFORMATIONAL_ONLY line; rest unchanged                                                          |

## AFTER-HOURS TUNING SPEC

Pipeline (each step independent, each rollback-able).

1. Outcome labeling (cron, runs continuously with a 15-min lag): for every
   `ai_turns` with a directional verdict, look up `snapshot_features` at
   T+60/180/300/900 for the same alias and write `trade_outcomes`. Idempotent:
   re-runs OK.
2. Daily replay (`python -m bookmap_mcp.pax_bus_replay --date YYYY-MM-DD [--alias ALIAS]`):
   - Naming note: the existing `mcp-server/bookmap_mcp/pax_replay.py` is the
     CSV-era replay tool (reads `pax-agent-outcomes-*.csv`). It is NOT removed
     and NOT renamed. The bus-era replay ships as a separate module
     `pax_bus_replay.py` so both coexist.
   - Streams `snapshot_features` rows for the date in time order to verify the
     event timeline.
   - For each historical `ai_turns` row, loads the lossless source from
     `D:\BookmapLogs\pax-snapshots\YYYY-MM-DD\<snapshot_sha256>.json`, rebuilds
     the digest by re-running the Phase-3 digest builder against it, and
     verifies the rebuilt digest's SHA-256 matches `ai_turns.digest_sha256`.
     Mismatch = digest-builder regression. This is why both shas are NOT NULL
     and required: `snapshot_features` is quantized and cannot reproduce the
     exact byte input that fed Claude.
   - Optionally re-routes the user message and reports the routing diff.
   - Optionally re-runs `edge_calculus.level_edge()` and compares expected_R vs
     actual realized_R from `trade_outcomes`.
   - Output: a markdown report at `reports/bus-replay-YYYY-MM-DD.md`.
3. EOD report (`python -m bookmap_mcp.pax_bus_eod --date YYYY-MM-DD`):
   - Total turns, total cost, model split, mean latency, cache-hit ratio.
   - Hit rate by verdict (ENTER_LONG/SHORT) at T+5m, T+15m.
   - Mean realized_R per verdict bucket; mean expected_R vs realized delta.
   - Threshold-drift scan: for each numeric threshold in `pax_weights.json` and
     `pax_settings.json`, compute the realized hit rate above/below the threshold
     over the last N days. Flag thresholds where the implied optimal differs from
     the configured value by >15%.
   - Top 5 wins and losses: turns with the largest |realized_R - expected_R|.
   - Stale/bad prompts: turns where the digest fired the same primary skill as
     3+ consecutive prior turns AND realized_R was below median.
   - Skill coverage: which skill files were never primary-routed today.
4. Tuning recommendations (`python -m bookmap_mcp.pax_bus_tune --window-days 14`):
   - Reads `trade_outcomes` + `settings_versions`.
   - Produces a machine-readable recommendation JSON at
     `reports/tune-recommendations-YYYY-MM-DD.json`. Schema:
     `[{file, key, current, recommended, rationale, confidence}]`.
   - Never auto-applies. Human approves and runs a separate
     `pax_apply_recommendations.py` (out of scope here).
   - Recommendation engine should at minimum:
     - Detect drift in `confidence_floor` / `confidence_full`.
     - Re-fit `vwap_stretch_penalties` against realized R.
     - Detect dead `conviction_source_weights` entries (source whose contribution
       to composite stayed |w|<0.01 for the window).
   - Confidence intervals on every recommendation (bootstrap N=1000); reject any
     recommendation where CI crosses zero.
5. Code-change candidates (`python -m bookmap_mcp.pax_bus_review --window-days 14`):
   - Scans `ai_turns` for systematic prompt failures.
   - Scans `triggers.py` linger windows vs actual transition density - recommends
     `LINGER_MS_DEFAULT` changes.
   - Scans `pax-or` SKILL.md numeric thresholds vs realized data - produces a
     markdown diff at `reports/skill-edits-YYYY-MM-DD.md`.
   - Suggestions only. Code edits go through the normal repo workflow.

### Overfitting controls

- Minimum sample-size gate: no recommendation unless N >= 30 turns in the
  relevant bucket.
- Hold-out: tuning window must end at least 24h before today (no in-sample
  fitting).
- Direction-balance check: reject if recommendation is driven entirely by one
  side (long or short).
- Settings audit: every applied recommendation writes a `settings_versions` row
  with `actor='tuning_recommendation'` so the audit trail is complete.

## PHASED IMPLEMENTATION PLAN

Each phase ships behind a config flag so rollback is one JSON edit + a service
restart.

### Phase 0 - Audit + schema doc only (THIS DOC)

- Output: this design doc, committed to `docs/superpowers/specs/`.
- No code change. No DB. No file writes outside `docs/`.
- Approval gate: operator reads and signs off.
- Rollback: discard the doc.

### Phase 1 - Passive capture, no behavior change

- New `pax-ai/pax_ai/feature_bus.py` with all 8 tables defined + blob-store
  writer helpers (snapshot + digest content-addressed files, idempotent).
- New daemon thread started from `pax-ai/pax_ai/__main__.py::main` AFTER
  `poller.start()` and BEFORE `journal.init()` - guarded by
  `config.feature_bus.enabled` (default `false`; flip to `true` on the dev
  machine first). One line of code in `__main__.py`:
  `if config.get("feature_bus.enabled", False): feature_bus.start()`.
- DB at `D:\BookmapLogs\pax-bus.db` (WAL). Blob stores at
  `D:\BookmapLogs\pax-digests\YYYY-MM-DD\<sha>.txt` and
  `D:\BookmapLogs\pax-snapshots\YYYY-MM-DD\<sha>.json`.
- Snapshot + level + micro events flow from the bus's own snapshot delta logic.
- Trigger events flow from a ONE-LINE hook added to `triggers._emit_edge()`
  (the existing edge transition emit point at triggers.py:121). The hook is
  fire-and-forget into the bus queue. `compute_triggers()` is NEVER called by
  the bus. `/api/pax/whynow` is byte-for-byte unchanged.
- AI turns rows are written by an explicit `AiTurnRecord` assembled at the end
  of `chat.py::handle_chat_stream` (after the SSE `done` event has been sent).
  `digest_sha256` and `snapshot_sha256` are both computed in chat.py BEFORE
  `feature_bus.record_ai_turn(record)` is called - both are NOT NULL. The
  existing `pax-chat.db.meta_json` text-blob write is unchanged.
- `/api/pax/health` extended with
  `feature_bus: {healthy, last_write_ms, queue_depth, rows_today, blob_writes_today}`.
- No change to digest building, routing, prompts, Claude CLI invocation, UI, or
  trading. Pure shadow.
- Tests: writer round-trip, schema version check, back-pressure drop behavior,
  concurrent-writer file-lock, offline-bridge graceful path, `_emit_edge` hook
  fires exactly once per transition + does not perturb the linger cache,
  whynow output byte-identical with and without the hook, AiTurnRecord shape
  pinned + both shas required, snapshot blob round-trip byte-exact, digest
  blob round-trip byte-exact.
- Rollback: `feature_bus.enabled=false` + restart. The DB file and blob trees
  stay for forensics; nothing else.

### Phase 2 - Feature digest in Pax AI UI

- Add a collapsible UI section "Today's Bus" in `static/index.html` reading
  `/api/pax/bus/recent?table=trigger_events&limit=10` (new endpoint).
- Show: last 10 triggers + last 10 level events.
- No change to Claude prompt path.
- Operator can sanity-check: are the right transitions being captured.
- Rollback: hide the UI section + remove the endpoint. Bus keeps writing.

### Phase 3 - Claude digest uses the bus

- Extend `[RECENT_EVENTS]` block to read from `trigger_events` instead of
  in-memory linger cache (still write to both for one release cycle).
- Extend `[SESSION_MEMORY]` block to read from `ai_turns` + `trade_outcomes`
  joined.
- A/B switch: `chat.use_feature_bus_digest` (default false). Run both digests in
  parallel for N days, log byte-diff. Flip when diffs are explained.
- Tests: digest builder pinned by snapshot fixtures + bus rows; cache reuse
  measured (digest_sha256 unique-count over N turns).
- Rollback: `chat.use_feature_bus_digest=false`. Linger-cache path still alive.

### Phase 4 - Replay + outcome labeling

- `pax-ai/pax_ai/outcomes.py` daemon thread (lagging 15 min) writing
  `trade_outcomes`. Lives inside the Pax AI process because it needs the same
  `pax-bus.db` writer connection pool and shares the config/retention path.
- `mcp-server/bookmap_mcp/pax_bus_replay.py` +
  `mcp-server/bookmap_mcp/pax_bus_eod.py` scripts. Live in `bookmap_mcp`
  because they are batch tools invoked as `python -m bookmap_mcp.X` (matches
  existing `pax_daemon` / `pax_collector` pattern). Read-only consumers of
  `pax-bus.db` + the snapshot/digest blob stores; no Pax AI process imports.
  Naming: the `pax_bus_` prefix is required because
  `mcp-server/bookmap_mcp/pax_replay.py` and `pax_outcomes.py` already exist
  with CSV-era semantics (they read `pax-agent-signals-*.csv` and
  `pax-agent-outcomes-*.csv`). Those files are kept untouched - they serve
  the CSV/Phase-B/Phase-C pipeline. The bus tools are a parallel pipeline.
- EOD report scheduled as a Windows Task Scheduler entry (manual install).
- No change to live chat path.
- Tests: replay golden run on a known day reproduces the SHA-256 of every logged
  digest; outcome labeler correctly handles offline gaps in snapshot history.
- Rollback: stop the cron / outcomes daemon. Live path unaffected.

### Phase 5 - Tuning recommendations

- `mcp-server/bookmap_mcp/pax_bus_tune.py` +
  `mcp-server/bookmap_mcp/pax_bus_review.py` scripts produce JSON + markdown
  reports. Same package + invocation rationale + same `pax_bus_` naming
  collision avoidance as Phase 4.
- Never auto-apply. Operator reviews, optionally runs a separate apply script.
- `settings_versions` table records every change as audit.
- Tests: hold-out validation passes; recommendation engine refuses with N<30;
  recommendation diff format stable.
- Rollback: stop running the tuning scripts. Nothing else.

Each phase plan is its own writing-plans invocation.

## TEST PLAN (Phase 1, representative)

Unit (`pax-ai/tests/test_feature_bus.py`):
- test_schema_version_present_on_every_row
- test_snapshot_round_trip_quantized_values
- test_level_events_dedup_by_decision_change
- test_micro_events_dedup_by_type_price_ts
- test_back_pressure_drops_snapshots_not_events
- test_writer_survives_disk_full_simulated
- test_concurrent_writer_blocks_via_advisory_lock
- test_health_endpoint_exposes_writer_state
- test_writer_disabled_means_zero_db_writes
- test_startup_order_main_poller_bus_journal (verify __main__ initializes in
  the documented order; bus refuses to read poller before poller has a tick)
- test_emit_edge_hook_fires_exactly_once_per_transition
- test_emit_edge_hook_does_not_perturb_linger_cache
- test_whynow_byte_identical_with_and_without_bus_enabled
- test_compute_triggers_never_called_by_bus (AST scan of feature_bus.py)
- test_snapshot_blob_round_trip_byte_exact
- test_digest_blob_round_trip_byte_exact
- test_canonical_json_is_stable_across_dict_iteration
- test_aiturn_record_shape_pinned (dataclass field set + types)
- test_aiturn_record_rejects_null_snapshot_sha256
- test_aiturn_record_rejects_null_digest_sha256

Integration (`pax-ai/tests/test_feature_bus_integration.py`):
- test_chat_turn_emits_ai_turn_row_with_both_shas_and_cost_columns
- test_chat_turn_writes_both_blob_files_with_content_addressed_names
- test_offline_bridge_writes_health_offline_row
- test_chat_path_unchanged_when_writer_enabled_or_disabled (golden SSE byte stream)
- test_replay_can_reload_snapshot_and_rebuild_digest_byte_exact
  (the Phase 4 invariant, tested in Phase 1 as a positive contract)

Pinned invariants (carry forward to every phase):
- test_claude_cli_flags_unchanged - --tools "" --max-turns 1 stay locked.
- test_pax_ai_never_imports_place_order (AST scan).
- test_or_anchor_invariant_holds_under_bus_path.
- test_no_emoji_in_ascii_only_files (lint).

Performance (`pax-ai/tests/test_feature_bus_perf.py`):
- test_writer_keeps_up_at_2hz_for_60s.
- test_db_size_after_8h_simulated_under_100mb.

## RISKS / CAVEATS

| Risk                                                       | Likelihood | Severity | Mitigation                                                          |
| ---------------------------------------------------------- | ---------- | -------- | ------------------------------------------------------------------- |
| Latency - bus writer blocks the poll thread                | Low        | Medium   | Separate writer thread + bounded queue + back-pressure drop         |
| Data volume - bus DB grows unbounded                       | High       | Low      | Default retention 30 days + prune tool + alerts at 1 GB             |
| Prompt bloat - digest creeps above 1500 tokens             | Medium     | Medium   | Hard cap + refuse-and-log; per-block budget enforced in tests       |
| Hallucination                                              | Low        | Medium   | Continue prose-only style; explicit "no data" labels per block      |
| Stale snapshot masquerading as fresh                       | Low        | High     | Existing stale flag wired into digest; bus rows include bridge_error |
| Overfitting in tuning                                      | High       | High     | Min-sample gates, hold-out windows, bootstrap CIs, no auto-apply    |
| Survivorship bias - only logged turns get scored           | Medium     | Medium   | EOD report includes WAIT verdicts; not silent                        |
| False edge from in-sample tuning                           | Medium     | High     | Walk-forward windows; reject if CI crosses zero                      |
| Operator trust erodes if Pax watches without acting        | Low        | Low      | Make the bus visible - UI section showing what was captured today    |
| Schema drift                                               | Medium     | High     | schema_version gate on every read; explicit migration table          |
| Concurrent Pax AI processes corrupting bus DB              | Low        | High     | Advisory file lock + WAL serialization + start-up refuse-if-locked   |
| Subprocess latency on Windows spikes Claude turn times     | Medium     | Low      | Already running; baseline ~200-500ms cold + ~100-200ms warm          |
| Cache-hit invisibility                                     | Medium     | Low      | cache_creation_tokens + cache_read_tokens already in result event    |
| News calendar staleness limits gate value                  | High       | Medium   | Out of scope here; flag for separate news-ingestion design           |
| Skill routing inversion - hft_microstructure shadows pax-or | Known     | Low      | Out of scope for the bus; logged for future routing review           |

## EXACT FILES TO CHANGE IN PHASE 1

New files:

| Path                                                                            | Purpose                                                         | LOC  |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------- | ---- |
| `pax-ai/pax_ai/feature_bus.py`                                                  | Writer daemon, schema DDL, advisory lock, back-pressure queue   | ~400 |
| `pax-ai/tests/test_feature_bus.py`                                              | Unit tests (10 cases)                                           | ~300 |
| `pax-ai/tests/test_feature_bus_integration.py`                                  | End-to-end SSE + writer interaction                             | ~200 |
| `pax-ai/tests/test_feature_bus_perf.py`                                         | Throughput sanity                                               | ~80  |
| `tools/feature_bus_prune.py`                                                    | Retention pruner (operator-run, not scheduled in Phase 1)       | ~100 |
| `docs/superpowers/specs/2026-05-20-pax-feature-bus-design.md`                   | This document                                                   | done |

Files modified (small, surgical edits):

| Path                                  | Change                                                                                                  |
| ------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `pax-ai/pax_ai/__main__.py`           | After `poller.start()` and BEFORE `journal.init()`, add: `if config.get("feature_bus.enabled", False): from . import feature_bus; feature_bus.start()`. One line, guarded |
| `pax-ai/pax_ai/server.py`             | `_handle_health()` attaches `feature_bus.status()` if the bus is running; no-op when disabled           |
| `pax-ai/pax_ai/chat.py`               | At end of `handle_chat_stream` (after SSE done emit): compute `digest_sha256` and `snapshot_sha256`, assemble `AiTurnRecord` from in-scope vars, call `feature_bus.record_ai_turn(record)`. Existing `pax-chat.db` write unchanged |
| `pax-ai/pax_ai/triggers.py`           | One line appended at the bottom of `_emit_edge()` (line ~121): `try: feature_bus.record_trigger(trig) except Exception: pass`. Linger cache + whynow path otherwise unchanged. NO call to `feature_bus` from `compute_triggers` |
| `pax-ai/pax_ai/config.py`             | New keys: `feature_bus.{enabled, db_path, snapshot_blob_dir, digest_blob_dir, queue_max, retention_days}`. Default `enabled=false`. Hot-reload-safe |
| `pax-ai/pax_ai/__init__.py`           | Bump `__version__`                                                                                      |
| `pax-ai/pax_ai/static/index.html`     | None in Phase 1 (UI changes are Phase 2)                                                                |
| `pax-ai-config.example.json` if exists | Add feature_bus block with defaults                                                                    |

Files NOT touched in Phase 1 (explicit, to prevent scope creep):

- `mcp-server/bookmap_mcp/dashboard.py`
- `mcp-server/bookmap_mcp/or_session.py`
- `mcp-server/bookmap_mcp/pax_replay.py` (existing CSV-era; bus-era is the
  separate `pax_bus_replay.py` in Phase 4)
- `mcp-server/bookmap_mcp/pax_outcomes.py` (existing CSV-era; bus-era outcomes
  live in `pax-ai/pax_ai/outcomes.py` and run in-process in Phase 4)
- `indicators/OpenRange/**`
- `mcp-server/bookmap_mcp/pax_weights.json`
- `pax-ai/pax_ai/prompts.py`
- `pax-ai/pax_ai/claude_stream.py` (CLI flags pinned; on_done callback shape
  unchanged)
- `pax-ai/pax_ai/edge_calculus.py`
- `pax-ai/pax_ai/journal.py` (chat journal is independent of the bus)
- `pax-ai/pax_ai/triggers.py::compute_triggers` (only `_emit_edge` gets a
  one-line outbound hook; `compute_triggers` itself untouched; /api/pax/whynow
  byte-for-byte identical)

## Live-bug report

None found. The audit surfaced design gaps (no event journal, no replay, no
outcome attribution, no retention) but no broken-today regressions. Prior audit
fixes (strict-deep parsing, cost-footer no client-side pricing, chat-handler
test isolation, OR-anchor invariant tests) all still hold per the explore
evidence.
