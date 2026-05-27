# Pax AI Live-Edge Audit (2026-05-26)

Owner: Will
Scope: live Bookmap copilot path only. No research framework, no
self-training, no promotion gating. Question being answered: does Pax AI,
as shipped today, give the operator a falsifiable trading read at the
chart, and if not, what is the smallest set of changes that would.

Sources: read of `pax-ai/pax_ai/prompts.py`, `chat.py`, `claude_stream.py`,
`server.py`, `feature_bus.py`, `bus_digest.py`, `static/index.html`,
`mcp-server/bookmap_mcp/dashboard.py`, `signal_engine.py`,
`indicators/OpenRange/src/main/java/com/openrange/PaxHeatwave*` and
`PaxTrendTriangle*`, `pax_bus_replay.py`, `pax_bus_tune.py`,
`pax_bus_eod.py`, `outcomes.py`, `edge_calculus.py`, `playbook.py`, and
the last 20 commits.

---

## 1. Current State

What is actually live, today:

- **Snapshot composer** (`dashboard.py::fetch_snapshot`, ~3500 lines).
  Reads Java bridge endpoints, computes OR grid + per-magnet composite,
  VWAP bias, VP bias, 17-source session conviction, trend_signal,
  flow regime, gates, micro events. Polled at 1 Hz. This part is solid.
- **Pax AI HTTP server** (`pax-ai/pax_ai/server.py`) on :18891. Endpoints:
  `/api/pax/chat/stream` (SSE), `/api/pax/level/<label>` (edge_calculus),
  `/api/pax/playbook` (scenario tree), `/api/pax/health`. Polls the
  dashboard on :18888.
- **System prompt** (`prompts.py`). ~12.3k tokens, frozen once per boot,
  written to disk for prompt-cache hits. Composed of BASE_PREAMBLE
  (hard rules + routing + output style) plus two skill bodies:
  `pax-or` (36.7k chars, 567 lines, 15 sections) and
  `hft_microstructure_quant_v1` (4.7k chars, reference-only).
- **Chat path** (`chat.py::handle_chat_stream`). Each user turn:
  router-hint string + 15-line `_digest_lines(snap)` text block + USER.
  Total user message ~300-400 tokens. STALE flag appended if
  snapshot age > 5000 ms.
- **Claude CLI invocation** (`claude_stream.py`). Hard-coded:
  `--tools ""`, `--max-turns 1`, stream-json, model swap on `/deep`
  (Haiku live default, Sonnet on /deep, Opus opt-in). 30 s / 60 s
  timeouts.
- **UI** (`static/index.html`). Pywebview shell window, NOT on chart.
  Prose chat bubbles with token streaming, cost/latency footer,
  abort + regenerate + keyboard shortcuts, optional Today's Bus drawer.
- **On-chart Pax presence** (OpenRange indicator):
  - `PaxHeatwave` quant box (top-left screen-space): 11 rows
    (DECISION + OR/FLOW/VWAP/VP/BOOK groups + level rows), tone-coded
    BULL/BEAR/AMBER/NEUTRAL.
  - `PaxTrendTriangle`: green up / red down at bucket-entered events,
    eligibility-gated.
  - Institutional thesis chart events (ICEBERG_DEFENSE, STOP_SWEEP,
    SPOOF_RISK) drawn at level prices.
- **Triggers**: mostly user-initiated. Optional auto-fire on
  level-approach / trend flip / regime change with 90 s global
  cooldown. `feature_bus.enabled` is False by default; `outcomes.enabled`
  is False by default.
- **Research stack** (offline): `pax_bus_replay.py`, `pax_bus_tune.py`,
  `pax_bus_eod.py`, `pax_bus_prune.py`, `outcomes.py`, `pax_calibration.py`,
  `pax_research_claude.py`, `pax_policy_replay.py`, preflight CLI,
  promotion gate. Live capture mostly disabled. 0 forecasts captured
  to date; today's calibration report is empty.

Last 20 commits: 0 changed live decision flow. 5+ touched research
scaffolding (preflight, audit summaries, gate enforcement). 1 touched
prompts (forecast guardrails).

---

## 2. Drift Diagnosis

What is infrastructure noise vs live edge:

### Live edge (keep)

- `dashboard.py` snapshot composer. The actual feature pipeline. This
  is the edge. The model is a UI on top of it.
- `edge_calculus.level_edge()` and `playbook.build_playbook()`.
  Deterministic math served from live endpoints, called per-level
  on demand.
- `PaxHeatwave` quant box. Operator-glanceable, on-chart.
- `PaxTrendTriangle`. On-chart entry-marker projections of conviction.
- Hard prompt rules: no numeric inference, gate on `anchorMode==LIVE`
  and `health==ok`, always tag fields with snapshot names.
- The session-anchor invariant (only the operator-saved Static OR is
  authoritative). Don't touch.
- Claude CLI guardrail: `--tools ""`, `--max-turns 1`, `--bare`
  conditionality. Don't touch.

### Drift (noise)

- **Prompt bloat in `pax-or`**:
  - Sec 1.2 "Default exchange RTH" table (15 lines, marked illustrative).
  - Sec 8 "Multi-market sanity check" (requires bonds/gold/DXY which
    the snapshot does not contain).
  - Sec 10 "Mental model - 4 fears" (philosophy / Pax's 2011 story).
  - Sec 3.3 "Rung 2+ Runners" (requires multi-hour memory; Pax AI is
    one-shot per turn).
  - Sec 4.3 "Max heat" (model cannot enforce; never wired).
  - Sec 5.4 "Hard daily stop in dollars" (model has no NLV).
  - HFT external JSON schema (the same skill's first instruction is
    "never emit JSON inside Pax AI").
  - Confirmation rules duplicated across Sec 2.2 / 4.1 / 11.5 / 12.
  - Sec 11.6 VWAP/OR gate spends 75 lines on one boolean.
- **Vague institutional language**: "institutional algos slam in their
  initial allocations" etc., not tied to a snapshot field. The
  preamble already bans speculation; the skill body reintroduces it.
- **Research stack is write-only**:
  - `pax_bus_tune.py` writes `tune-recommendations-*.json`; no code
    reads it; no human workflow applies it. Last report 2026-05-19.
  - Calibration / policy-candidates / replay reports for today are
    empty (0 forecasts captured).
  - Feature bus + outcomes are capture infrastructure with no live
    consumer; the live chat path never reads them.
  - Promotion gate forbids any active change until research_only ->
    replay_passed -> paper_candidate -> paper_passed -> human_approved.
    Preflight today fails at step 1. Net effect: scaffolding without
    a producer.
- **UI surface area not at the eye**:
  - Read is text-only in a separate pywebview window. Operator must
    alt-tab off the chart to read it, then back to act.
  - Output is prose, not a structured verdict card. Hard to scan in
    market hours.
  - Today's Bus drawer + History panel + cost footer + regenerate are
    all chat-room polish; none of them is what the trader looks at
    during entry.
- **Latency profile is wrong for live**:
  - Code-path latency ~10-50 ms; Claude inference 27-59 s end-to-end.
  - First token in <1 s, but a usable verdict requires the model to
    finish a paragraph. At the chart, the trader needs the read in
    1-3 s or not at all.
- **Triggers are passive**:
  - Auto-fire is opt-in and disabled by default. The trader has to
    ask. By the time they ask, the level has moved.
- **Signal noise**:
  - `tape_flow.deltaLabel` collapses to BALANCED in low-vol windows;
    too coarse for level-by-level reads.
  - `lt_liquidity` is a 90 s accumulation; too stale for live entry.
  - `vp_bias` VAH/VAL warm 60-120 s after OR close; until then the
    block reports zero and clutters the digest.
  - `tape_buckets` raw counts are redundant with `tape_flow`.
  - `vwap_obj.eth` overlay is zero-weighted (v20) but still rendered.
  - `trend_analyzer` independent reading is never consumed by
    `pax_decision`.
  - 17 conviction sources are exposed; the trader almost certainly
    cannot consume more than 4-5 at a glance.

### Verdict

The system is research-scaffolded but live-decoupled. The trader-facing
loop is: dashboard -> snapshot -> Claude prose -> shell window. The
research loop is: dashboard -> SQLite -> reports -> nothing. The work of
the last two weeks went almost entirely into the research loop and
none of it into the trader-facing loop. That is the drift.

---

## 3. Available Signals

Per-signal verdict (full matrix in agent report; this is the live-edge
summary):

| Signal                                | Latency  | Verdict        | Note |
|---------------------------------------|----------|----------------|------|
| `book.mid`, `spread`, `bestBid/Ask`    | <1 s    | KEEP            | Base |
| `momentum.i10/i50/i200 + flag`         | <1 s    | KEEP            | Tape pulse |
| `pull_stack` (BBO bias + rotation)     | <1 s    | KEEP            | Highest level-score weight |
| `micro_events` (SWEEP, ABSORPTION, PULL, SPOOF) | <1 s | KEEP    | High SNR at levels |
| `vwap_or_gate` (ALLOW_LONG / SHORT)    | <1 s    | KEEP            | Hard gate |
| `or_levels.levels[]` + magnet grid     | 2-3 s   | KEEP            | After OR close |
| `or_levels.composite` (9 drivers)      | 5-10 s  | SIMPLIFY        | Collapse to top 3 drivers |
| `vwap_bias.sigma_z + regime`           | 2-3 s   | KEEP            | When sigma warm |
| `vp_bias.poc_z`                        | 5 s     | KEEP            | POC only |
| `vp_bias.va_state / hvn / lvn`         | 60-120 s| SIMPLIFY        | Hide until warm |
| `conviction.score + trend`             | 5-10 s  | KEEP            | Composite |
| `conviction.sourceScores` (17 src)     | -       | DROP from prompt| Show top 3 only to trader |
| `flow.regime` (day-type)               | 30 s    | SIMPLIFY        | Coarse; drop or low-weight |
| `flow.biasTrajectory`                  | 5-10 s  | KEEP            |  |
| `trend_signal` (BULL/BEAR + cooldown)  | 3-5 s   | KEEP            | UI marker |
| `session.anchorMode`                   | minutes | KEEP            | Gates all entries |
| `news.blocked`                         | minutes | KEEP            | Gate |
| `tape_flow.deltaLabel`                 | 5-10 s  | SIMPLIFY        | Expose fast/slow components |
| `tape_buckets` raw counts              | -       | DROP            | Redundant with tape_flow |
| `lt_liquidity`                         | 90 s    | SIMPLIFY        | Drop weight from 0.20 to 0.05 |
| `vwap_obj.eth` overlay                 | -       | DROP            | Zero-weight in v20 |
| `trend_analyzer` independent reading   | -       | VERIFY/DROP     | Never used by pax_decision |
| `institutional_thesis.execution_read`  | 2-5 s   | KEEP            | STAND_DOWN / WAIT_FOR_CONFIRM / PAY_FOR_TRADE / SCRATCH_READY |

Bottom line: there is enough live signal to write a fast, falsifiable
read. The bottleneck is not data; it is presentation and latency to the
eye.

---

## 4. Decision Contract

Pax AI must produce exactly six fields per read. No prose unless asked.
Field values must come from the snapshot.

```
BIAS:        LONG | SHORT | NEUTRAL
SETUP:       <one of a small fixed vocabulary; see below>
ENTRY:       <one-line condition that must occur before action,
             written in snapshot terms>
INVALIDATION:<price level or feature condition that proves the read wrong>
NO-TRADE:    <reason if BIAS=NEUTRAL or gate is blocking, else empty>
CONFIDENCE:  LOW | MEDIUM | HIGH
EVIDENCE:    <up to 3 snapshot field=value pairs that support the read>
```

Setup vocabulary (fixed, snapshot-grounded, extend only when a new
deterministic detector exists):

- `OR_BREAK_FOLLOW` (price has accepted above OR-H or below OR-L)
- `OR_BREAK_FADE` (acceptance failed; reject + close back inside)
- `OR_REVERT` (inside OR, mean-revert from sigma stretch)
- `LEVEL_DEFEND` (iceberg/absorption defending magnet; stand down)
- `LEVEL_SWEEP_REV` (sweep through magnet then reverse)
- `VWAP_REVERT` (price >= +/-2 sigma at VWAP, regime=MEAN_REVERT/BLOWOFF_REVERT)
- `NO_TRADE_GATE` (session, news, or anchorMode blocking)
- `NO_TRADE_CHOP` (inside OR, no edge)

Hard rules:

- If `anchorMode != LIVE`, output is informational only, BIAS forced
  NEUTRAL, NO-TRADE=`stale_anchor`. No FOLLOW/FADE.
- If `news.blocked`, BIAS NEUTRAL, NO-TRADE=`news_blackout:<label>`.
- If snapshot `age_ms > 5000`, NO-TRADE=`stale_snapshot`.
- CONFIDENCE may only be HIGH when `coverage >= 0.75` of level
  composite drivers, sigma_z is warm, and conviction trajectory is
  not WARMUP.
- EVIDENCE must cite snapshot field names. No "institutional algos".

This is the ONLY output shape the live chat produces, both auto-triggered
and user-asked. Prose explanation is gated behind an explicit "explain"
ask.

---

## 5. Simplification Plan

Remove or freeze before adding anything:

### Delete from `pax-or` skill body

- Sec 1.2 default exchange RTH table.
- Sec 3.3 Rung 2+ Runners (multi-hour state; unsupported).
- Sec 4.3 Max heat (cannot enforce; not wired).
- Sec 5.4 Hard daily stop in dollars (no NLV in snapshot).
- Sec 8 Multi-market sanity check (no cross-instrument feed).
- Sec 10 Mental model / 4 fears (philosophy).
- Sec 11.6: condense from 75 lines to 6.
- Confirmation rules: keep Sec 12 decision tree; delete Sec 2.2 /
  4.1 / 11.5 duplicates.

Expected savings: ~1.5k tokens (~12 percent prompt shrink). No loss of
live signal because none of these contribute to the six-field contract.

### Delete from `hft_microstructure_quant_v1`

- The external JSON schema block. The skill's own first line says it
  is reference only inside Pax AI; the schema is dead weight here.

### Drop / hide in the snapshot digest

- `tape_buckets` raw counts.
- `vwap_obj.eth` overlay.
- `conviction.sourceScores` array; expose only `conviction.score`,
  `conviction.trend`, `conviction.trajectory`, and the top 3 drivers
  for the nearest level.
- `vp_bias.va_state / hvn_count / lvn_count` when warming (drop until
  >= 60 trades into session; keep POC always).
- `lt_liquidity` weight cut from 0.20 to 0.05; label as informational.
- `trend_analyzer` (cross-check) field unless we wire it as a real
  gate.

### Freeze (do not extend)

- `/deep` mode. Useful but expensive; keep behind explicit prefix.
- Cost / latency footer. Useful; do not promote.
- Regenerate / abort / keyboard shortcuts. Keep as is.
- Today's Bus drawer. Keep, collapsed by default.
- `feature_bus`, `outcomes`, `pax_bus_*`, `pax_calibration`,
  `pax_policy_replay`, `pax_research_claude`, preflight, promotion
  gate. Do not delete, do not extend. They are off the critical
  path and the operator has explicitly deprioritized them.

### Do not build

- New forecast schemas, calibration buckets, policy-candidate writers,
  replay verifiers, prompt-lesson generators, EOD audit polish.
- New addons, new dashboards, new UI panels.
- Any new "framework".

---

## 6. Implementation Plan (smallest viable)

Five changes. None of them is research scaffolding. All are reversible.

### IP-1. Verdict-card output contract

- Edit `pax-ai/pax_ai/prompts.py::BASE_PREAMBLE`. Replace the current
  output style block with the six-field contract (Section 4).
  Mandate a single `<<PAX_VERDICT>>...<<END_VERDICT>>` JSON block
  per turn. Prose only when the user message starts with `/explain`.
- Edit `pax-ai/pax_ai/chat.py` post-stream parser to extract the
  verdict block, validate the field set, and emit it as a structured
  SSE event `event: verdict` (in addition to the existing `token`
  stream).
- Pin with tests: missing field -> error; unknown SETUP value ->
  reject; BIAS=LONG when `anchorMode != LIVE` -> reject.

### IP-2. Verdict card on the chart, not just the shell

- Add a single small box to the OpenRange screen-space painter that
  renders the latest verdict card (BIAS, SETUP, ENTRY, INVALIDATION,
  CONFIDENCE). Reuse the `PaxHeatwave*` pattern: AtomicBoolean dirty
  flag, fetcher polls Pax AI `/api/pax/verdict/latest`, painter
  draws on the next Bookmap callback.
- Keep `PaxHeatwave` as the slow background indicator. The verdict
  card is the eye-level read.
- Operator never has to alt-tab during entry.

### IP-3. Auto-fire on level approach (high-severity only)

- Flip `trigger_engine.enabled` default to true, but narrow the
  set to only LEVEL_APPROACHING (within 2 ticks of OR-H/OR-L/+1/-1
  by default) AND `anchorMode==LIVE`. 90 s cooldown stays.
- On a trigger, run a single verdict turn (live model, not /deep)
  and update the on-chart verdict card. No prose, no chat bubble
  by default.

### IP-4. Snapshot digest trim

- Edit `chat.py::_digest_lines` to drop the noise fields listed in
  Section 5. Cut the digest from ~15 lines / 300-400 tokens to
  ~10 lines / 200-250 tokens.
- Keep STALE flag and anchorMode flag at the top so they never get
  pushed out of the small context window.

### IP-5. Latency hedge

- Add `/api/pax/verdict/instant` deterministic endpoint that runs
  `level_edge + composite + vwap_or + thesis` on the nearest level
  and returns the six-field card without calling Claude. Use for
  the on-chart card unless the operator types `/explain` or the
  triggered turn finishes. Claude reads only refine or override
  the deterministic card.
- This gives the operator a sub-second baseline; the model becomes
  a second opinion, not the only opinion.

Order of execution: IP-5 first (immediate fallback), then IP-4 +
IP-1 (prompt + digest), then IP-2 (on-chart), then IP-3 (auto-fire).
Each is a single commit, each is independently rollback-able.

### Out of scope for now

- Multi-hour state, runner trailing, cross-instrument confirmation,
  dollar-risk gating, NLV awareness, voice triggers, mobile, anything
  to do with promotion gates or self-training.

---

## 7. Live Edge Scorecard

A single sheet, filled by the operator over one full RTH session.
Manual, no UI. The goal is to answer "did Pax help me trade better".

For each Pax verdict shown that session (auto-trigger + manual):

```
Time        : HH:MM:SS local
Level       : OR-H / OR-L / +1 / -1 / mid / VWAP / VAL / VAH
BIAS        : LONG / SHORT / NEUTRAL
SETUP       : <vocab>
ENTRY       : <what condition>
INVALIDATION: <price>
CONFIDENCE  : LOW / MED / HIGH
Op acted?   : Y / N
Op direction: LONG / SHORT / FLAT
Outcome 60s : R-multiple at +60s vs invalidation distance
Outcome 5m  : R-multiple at +5m
Faithful?   : was Pax's call consistent with what the snapshot said
Useful?     : 1-5 trader subjective utility
Notes       :
```

Daily roll-up the operator fills at EOD:

- Reads shown: N
- Reads acted on: N (acted_count / shown_count)
- Hit rate at 60s (positive R): N/N
- Hit rate at 5m: N/N
- Confidence calibration: HIGH hit rate vs LOW hit rate
- Reads where Pax said NEUTRAL/NO-TRADE and operator agreed: N
- Reads where Pax said NEUTRAL and operator overrode and won/lost
- Reads that were "wrong but coherent" (faithful to snapshot, market
  did something the snapshot did not predict)
- Reads that were "wrong and unfaithful" (model invented something
  not in the snapshot)
- Frictions: did the operator need to alt-tab? Did the card disappear
  while needed?

This sheet is the source of truth. Not SQLite outcomes, not
calibration JSON. The operator's hand-filled scorecard.

---

## 8. Success / Failure Criteria (one session)

This is the gate for whether to continue investing in the live path.

### Success (continue)

After one full RTH session with the five implementation items shipped:

- >= 8 verdict cards rendered (auto + manual).
- >= 50 percent of acted-on cards positive R at +60 s, OR >= 60
  percent positive R at +5 m.
- 0 reads where Pax invented a feature not in the snapshot
  (unfaithful failures).
- Operator subjective utility average >= 3.5 / 5.
- Operator did not alt-tab to the shell window during entry on
  any acted-on card.

### Failure (stop and reassess)

- < 4 verdict cards rendered (triggers and/or digest are broken).
- < 30 percent hit rate at +60 s on acted-on cards.
- >= 1 unfaithful failure (model hallucinated a feature).
- Average utility < 2 / 5.
- Operator reports the card is in the way or the prose is still
  needed mid-trade (the contract is wrong, not the impl).

Either outcome ends with a written one-page debrief from the operator
that drives the next iteration. No automated promotion.

---

## 9. Stop Conditions

This direction is not worth continuing if any of the following hold
after the first session shipped:

- The snapshot is too noisy or too stale for a deterministic six-field
  card. (Then the problem is the dashboard, not Pax AI.)
- The operator finds the card distracting on the chart and turns it
  off. (Then Pax AI's on-chart visibility is not what was wanted.)
- The model cannot produce the verdict format reliably with the trimmed
  prompt (>5 percent format-violation rate over the session). Then
  the contract is wrong; deterministic endpoint becomes the only path.
- The deterministic verdict endpoint (IP-5) is as good as or better
  than the Claude reads in the scorecard. Then the LLM is not adding
  edge and should be cut to /explain only.
- Reads with anchorMode != LIVE leak into BIAS != NEUTRAL. That is a
  safety break, not a bug; stop until fixed.

Conversely, this direction IS worth continuing if the scorecard shows
the operator saved time, avoided at least one bad trade because of an
INVALIDATION call, and trusted the card enough to act on it without
opening the chat shell.

---

## 10. Open Questions (blockers only)

Only questions that block the five implementation items above.

1. **On-chart verdict card placement**. Top-left (under the
   `PaxHeatwave` quant box) or anchored to the active level? Top-left
   is simpler; anchored is more contextual but harder to keep readable
   when the chart scales. Recommend top-left for v1; anchored is
   a later iteration.
2. **Auto-fire signal set**. Recommend LEVEL_APPROACHING only for v1
   (proximity to OR-H/OR-L/+1/-1/-2 by default within 2 ticks). Is
   that the right narrow set, or should TREND_FLIP also be in?
3. **/explain prose contract**. When the operator types `/explain`,
   should the response be (a) plain prose, (b) prose plus the
   verdict block, or (c) per-driver score breakdown plus prose?
   Default: (b).
4. **Deterministic endpoint precedence**. Does the on-chart card show
   the deterministic verdict immediately and replace it when Claude
   responds? Or does it show the latest of either, whichever is more
   recent? Recommend: deterministic immediately; Claude replaces only
   when it raises CONFIDENCE or changes BIAS.
5. **Session anchor edge case**. If the operator changes the OR window
   mid-session, the conviction engine resets but the verdict card may
   be mid-render. Should the card hold-and-warn or blank? Recommend
   blank with an explicit `anchor_reset` reason for 30 s.

Nothing else is a blocker. Everything else is post-session refinement.

---

## Closing note

This plan deletes more than it adds. The intended shape after IP-1
through IP-5: a single on-chart card the operator reads at the eye
in <1 s, backed by a deterministic feature pipeline that already
exists, with the LLM as a slower second opinion gated on
`anchorMode == LIVE`. Everything else - feature bus, outcomes,
calibration, replay, tune, promotion gating, self-training -
remains in the tree and is not touched. It can be revisited only
after a session scorecard says the live path itself is producing
edge.
