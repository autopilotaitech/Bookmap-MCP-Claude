# Pax AI Full Project Audit (2026-05-26)

Owner: Will
Scope: Bookmap MCP + Pax AI + OpenRange chart overlay + Claude/research loop.
Question: how to turn the current codebase into one usable live edge loop:

`Bookmap features -> chart signal -> reason -> outcome -> Claude review -> offline tuning proposal -> approved live update`

External benchmark note: Jane Street publicly describes ML trading work as
market data, training/inference infrastructure, studying model trades in
production, and risk controls. That maps here to: clean event data, a fast
deterministic live path, outcome logging, replay/validation, and strict
deployment control. It does not map to "Claude says long" or unscored chart
widgets.

Sources checked:
- Local code: `pax-ai/pax_ai/*`, `mcp-server/bookmap_mcp/*`,
  `indicators/OpenRange/src/main/java/com/openrange/*`, tests, runtime config.
- Runtime endpoints: `:18891/api/pax/health`, `:18891/api/pax/levels/edge`.
- External references:
  - Jane Street ML overview: https://www.janestreet.com/machine-learning/
  - Jane Street performance engineering overview: https://www.janestreet.com/performance-engineering/
  - Jane Street real-world ML blog: https://blog.janestreet.com/real-world-machine-learning-part-1/

---

## 1. Bottom Line

The project has useful pieces, but they are not yet one working edge loop.

What works:
- Bookmap bridge + dashboard snapshot are live.
- Pax AI server is live on `18891`.
- Feature bus / forecast / trigger engine are enabled and capturing/firing.
- Deterministic `/api/pax/levels/edge` exists.
- OpenRange level-edge chart overlay code exists and compiles.
- Claude path exists for chat, chart-signal blocks, forecast blocks, and
  offline research prompts.

What does not work yet:
- Current `/api/pax/levels/edge` returns all `WAIT`. No plotted entry signal.
- The level-edge endpoint derives direction from `level.decision`, while the
  richer upstream signal may live in `level.composite.direction`.
- `expected_R` is not measured EV. It is `confidence * hand table`; correctly
  hidden as `score_R`, but still unproven.
- The self-training stack trains/replays Claude forecast blocks, not the new
  chart-plotted level signals.
- The chart overlay has no outcome log. A plotted signal is not automatically
  scored.
- Claude is present, but not correctly positioned in the loop. It should
  explain/review/propose, not be the blocking millisecond signal.
- Deployment is fragile because this repo lives under `C:\Bookmap\addons`; any
  build-output jar under the repo can become a Bookmap load candidate.

Conclusion: the next work must connect live chart signals to logged outcomes.
Without that, "training" is disconnected and "edge" is unproven.

---

## 2. Current Runtime State

Observed during audit:

- Bookmap bridge: `127.0.0.1:8765`
- Dashboard: `127.0.0.1:18888`
- Pax AI: `127.0.0.1:18891`
- Pax AI health:
  - dashboard reachable
  - feature_bus enabled/running
  - forecast enabled
  - trigger_engine enabled/running
  - trigger fires observed

Current `/api/pax/levels/edge`:
- `anchorMode=LIVE`
- `stale=false`
- health/news/session gates clear
- all levels `direction=WAIT`
- all levels `actionable=false`
- confidence low (`~0.06-0.09` at audit time)
- reasons include `execution_read=WAIT_FOR_CONFIRM -> thesis_gated_size_tier=NONE`

Practical meaning: chart overlay can only draw actionable rows, but the live
endpoint currently emits no actionable rows. The chart can be technically
correct and still show nothing all day.

---

## 3. Live Signal Path

Path:

`dashboard.py snapshot -> pax-ai/poller.py -> /api/pax/levels/edge -> OpenRange PaxLevelEdgeFetcher -> PaxLevelEdgePainter`

Good:
- Fast deterministic path exists.
- No Claude dependency for the chart signal.
- Output is honest: `score_R`, not EV.
- Java overlay draws only `actionable=true`, no WAIT clutter.
- Fetcher polls Pax AI on port `18891`, correct surface.

Critical flaw:
- `pax-ai/pax_ai/level_edge.py` calls `edge_calculus.level_edge`, and
  `edge_calculus.composite_dir_from_decision()` maps only
  `or_levels.levels[].decision`.
- Live snapshots often show `decision=WAIT` while `composite.direction` may
  contain richer upstream information.
- Result: the signal can be crushed to `WAIT` before charting.

Needed fix:
- Use the best available directional source in priority order:
  1. `level.decision` when it is an ENTER_* code.
  2. `level.composite.direction` when it is one of
     `FOLLOW_LONG/FOLLOW_SHORT/FADE_LONG/FADE_SHORT`.
  3. no direction -> WAIT.
- Keep thesis gate and confidence gate. Do not plot weak composite noise.

Do not loosen everything just to make the chart light up. Make the signal source
correct first, then see whether it naturally produces rare actionable rows.

---

## 4. Chart Overlay

Current implementation shape:
- New Java files:
  - `PaxLevelEdgeModel`
  - `PaxLevelEdgeSnapshotParser`
  - `PaxLevelEdgeFetcher`
  - `PaxLevelEdgePainter`
- `PaxOpeningRangeModule` wires the fetcher and painter.
- Painter renders:
  - `LONG/SHORT confidence`
  - `R~score size_tier`
  - `STOP price`
- It skips null / WAIT / non-actionable rows.

This is the right display policy.

Problems:
- No reason tags yet. The operator asked to see why it fired on chart.
- It cannot display anything until Python endpoint produces actionable rows.
- Deployment failed once due duplicate jar/classloader risk.

Required chart contract:

```text
LONG 0.72 R~1.15
OR-L FADE
Pull + VWAP + Absorb
STOP 30061.25
```

Only actionable rows. No prose. No WAIT rows.

Next chart work should wait until Python endpoint includes `setup` and
`top_drivers`, otherwise the chart signal has no "why."

---

## 5. Claude / LLM Role

Current Claude path:
- `prompts.py` builds a large frozen prompt from BASE_PREAMBLE + skills.
- `chat.py` builds a digest and routes user messages.
- `claude_stream.py` calls Claude CLI with:
  - stream-json
  - tools disabled
  - max-turns 1
  - 30s/60s timeouts
- Claude can emit:
  - `PAX_AI_CHART_SIGNAL`
  - `PAX_FORECAST`
- `trigger_engine.py` can auto-fire Claude calls on triggers and persist
  chart signal / forecast outputs.

Good:
- Claude is useful for explanation, critique, after-action review, and
  candidate lesson generation.
- It is already wired to prompt lineage and forecast capture.
- It is constrained to no tools / one turn.

Bad:
- Prompt still claims `expected_R` is EV/probability-like, while the audit
  proved it is not. That is a trust bug.
- Claude-generated forecast blocks are not the same object as deterministic
  chart signals. The training stack can learn from forecasts while the chart
  signal remains untrained.
- Trigger engine is enabled and firing Claude calls, but those are not the
  same as the new deterministic chart overlay.

Correct role:
- Live deterministic engine plots fast signal.
- Claude asynchronously explains why the signal fired, grounded in snapshot
  fields.
- Claude reviews logged outcomes after the session.
- Claude proposes changes offline.
- Claude does not mutate live weights/config mid-session.

Needed prompt fix:
- Replace "expected_R is EV" language with "score_R / expected_R is a
  confidence-scaled heuristic unless a measured report says otherwise."
- Teach Claude the exact chart-signal object and outcome log object so its
  reviews align with what the chart actually plotted.

---

## 6. Training / Research Stack

Existing stack:
- `feature_bus.py`: captures snapshots, triggers, AI turns.
- `forecast_signal.py` / forecast store: captures Claude `PAX_FORECAST`.
- `pax_calibration.py`: pairs forecasts to outcomes.
- `pax_research_claude.py`: derives candidate lessons.
- `pax_policy_replay.py`: time-split replay of candidate lessons.
- `policy_promotion_gate.py` / `pax_research_preflight.py`: blocks active
  policy changes without evidence.

Good:
- Time-ordered split exists.
- Read-only research discipline exists.
- Promotion gate prevents silent live policy mutation.
- Prompt lineage and snapshot hash lineage exist.

Bad:
- This stack is centered on Claude forecasts and ai_turn outcomes.
- The new chart overlay signal is not the primary logged/trained object.
- No per-plotted-signal JSONL exists yet.
- No +15s/+60s/+300s realized-R backfill exists for chart signals.
- No daily report groups by actual chart setup/top driver bundle.

Required pivot:
- The object of training must be the plotted chart signal, not only Claude
  forecast text.

Minimum training object:

```json
{
  "signal_id": "...",
  "ts_ms": 0,
  "alias": "NQM6.CME@RITHMIC",
  "level_label": "OR-L",
  "level_price": 30063.5,
  "mid_at_signal": 30062.75,
  "direction": "LONG",
  "setup": "OR_L_FADE",
  "score_R": 0.92,
  "confidence": 0.51,
  "size_tier": "HALF",
  "stop_price": 30061.25,
  "top_drivers": ["pull_stack", "vwap_or", "absorption"],
  "blocked_reason": null,
  "mid_at_15s": null,
  "mid_at_60s": null,
  "mid_at_300s": null,
  "realized_R_15s": null,
  "realized_R_60s": null,
  "realized_R_300s": null,
  "invalidated": null
}
```

This can be JSONL first. It does not need SQLite until it proves value.

---

## 7. Deployment / Runtime Risks

High risk:
- The repo is inside `C:\Bookmap\addons`.
- Bookmap scans recursively.
- Any built jar under repo subdirectories can become loadable.
- This caused duplicate OpenRange jar risk and likely the classloader error.

Current state after cleanup:
- Only one OpenRange jar matched: `C:\Bookmap\addons\openrange-release.jar`

But broader risk remains:
- There are many jars under `C:\Bookmap\addons` from other projects/build dirs.
- Build scripts can recreate loadable jars inside the repo under addons.

Required invariant:
- For Bookmap addons that should load, exactly one canonical jar should exist
  under `C:\Bookmap\addons`.
- Build-output jars should not remain under the recursive addon scan tree, or
  build scripts should quarantine/delete them after deploy.

This is not a nice-to-have. Classloader errors make every signal unreliable.

---

## 8. Main Failure Modes

1. No signals ever plot
   - Cause: endpoint uses `level.decision`, confidence too low, or thesis gate
     always `WAIT_FOR_CONFIRM`.
   - Fix: audit direction source and thesis gate; do not loosen randomly.

2. Signals plot but are not measured
   - Cause: no chart-signal outcome log.
   - Fix: JSONL rising-edge log + backfill.

3. Claude learns from the wrong object
   - Cause: training stack sees forecasts, not plotted signals.
   - Fix: make plotted signal log the primary training set; Claude reviews it.

4. Chart shows "edge" that is fake EV
   - Cause: `expected_R` label or probability labels leak back.
   - Fix: keep `score_R`, omit fake probabilities.

5. Bookmap loads wrong jar
   - Cause: recursive addons scan.
   - Fix: enforce one canonical jar policy.

6. Trigger engine burns Claude calls without improving chart edge
   - Cause: auto-fire enabled before chart signal/outcome loop is closed.
   - Fix: either disable temporarily or route triggered turns to review/log
     only, not live actionability.

---

## 9. Correct Build Plan

### Slice 0 - Stabilize Runtime

Goal: reliable load, no duplicate jars.

Actions:
- Enforce one OpenRange jar under `C:\Bookmap\addons`.
- Build script should not leave loadable OpenRange jars under repo build dirs
  when repo lives under addons.
- Confirm Bookmap loads without `NoClassDefFoundError`.

No strategy changes.

### Slice 1 - Fix Live Signal Source

Goal: endpoint should detect real directional composites.

Actions:
- In `/api/pax/levels/edge`, derive direction from:
  - `level.decision` ENTER_* first,
  - then `level.composite.direction`.
- Add `setup`, `blocked_reason`, `top_drivers`, `snapshot_ts_ms`,
  `mid_at_signal`.
- Top drivers come from `level.composite.drivers`, capped at 3, field-grounded.
- Tests for:
  - composite.direction produces LONG/SHORT when decision is WAIT.
  - thesis gate still blocks.
  - confidence gate still blocks.
  - reasons are compact and field-grounded.

### Slice 2 - Chart Reason Tags

Goal: chart shows why.

Actions:
- Java parser reads `setup` and `top_drivers`.
- Painter adds one compact line:
  `OR-L FADE` and `Pull + VWAP + Absorb`.
- Still draw only actionable rows.

### Slice 3 - Plotted Signal Log

Goal: every chart signal becomes a training example.

Actions:
- JSONL log on false -> true actionable transition.
- Backfill +15s/+60s/+300s from later snapshots.
- Compute realized R using stop distance.
- Append-only; no DB.

### Slice 4 - Daily Score Report

Goal: prove/disprove edge.

Actions:
- CLI groups by setup, drivers, confidence bucket, level, direction.
- Report mean/median R, hit rate, invalidation rate, sample count.
- Claude may summarize this report and identify hypotheses.

### Slice 5 - Offline Training Proposal

Goal: improve signal without live mutation.

Actions:
- Use logged plotted signals as training set.
- Candidate changes: thresholds, driver weights, thesis-gate rules.
- Time-ordered holdout comparison.
- Write proposal only.
- Human approval before config/weight change.

### Slice 6 - Promotion Into Live

Goal: controlled update.

Actions:
- Apply approved changes in one reversible commit.
- Compare old vs new for next session.

---

## 10. What To Stop Doing

- Stop treating tests passed as edge proven.
- Stop building more audit/preflight surfaces before chart signal/outcome log.
- Stop calling `expected_R` expected value.
- Stop relying on Claude chat as the trader-facing product.
- Stop adding UI panels before the chart overlay has a signal and outcome log.
- Stop letting build artifacts remain under the recursive addon scan tree.

---

## 11. Immediate Next Prompt For Claude

Use this exact slice prompt:

```text
Proceed with Slice 1: fix live signal source and add chart-signal reasons.

Objective:
Make `/api/pax/levels/edge` produce actionable rows from the real upstream
direction source, and include enough compact reason fields for the chart and
future training log.

Hard constraints:
- No Claude prompt changes.
- No Java changes in this slice.
- No logging yet.
- No feature_bus/research/preflight changes.
- No config/weight changes.
- Do not loosen gates just to make signals appear.

Allowed files:
- `pax-ai/pax_ai/level_edge.py`
- `pax-ai/tests/test_level_edge.py`

Changes:
1. Direction source priority:
   - If `level.decision` maps to FOLLOW/FADE, use it.
   - Else if `level.composite.direction` is FOLLOW_LONG/FOLLOW_SHORT/FADE_LONG/FADE_SHORT, use it.
   - Else WAIT.
2. Add fields per level:
   - `setup`
   - `top_drivers`
   - `blocked_reason`
   - `snapshot_ts_ms`
   - `mid_at_signal`
3. `top_drivers` must be max 3 compact labels from `level.composite.drivers`.
4. `blocked_reason` must explain the first blocking gate:
   stale, anchor, news, session, health, low_confidence, thesis_gate, no_direction.
5. Keep actionability strict:
   health ok, not stale, anchor LIVE, no news block, session active,
   confidence >= 0.35, size tier HALF/FULL, thesis_gated_size_tier != NONE,
   direction LONG/SHORT.
6. Do not expose expected_R/probability fields.

Tests:
- decision WAIT + composite.direction FOLLOW_LONG can produce LONG when all gates pass.
- thesis gate still blocks even with composite.direction.
- low confidence blocks.
- top_drivers capped at 3 and compact.
- blocked_reason populated for non-actionable rows.
- no fake EV/prob fields leak.

Run:
cd C:\Bookmap\addons\MCP\Bookmap\pax-ai
python -m pytest -q tests\test_level_edge.py

Stop after this slice and report sample `/api/pax/levels/edge` output.
```

---

## 12. Final Verdict

The system can become useful, but only if the plotted chart signal becomes the
center of the project. Claude belongs in the loop, but as explanation/review/
training assistant around the deterministic signal, not as the blocking live
trigger. The next decisive test is whether fixing the direction source makes
`/api/pax/levels/edge` produce rare, sensible actionable rows. If yes, log and
score them. If no, the upstream composite/thesis engine is the real target.
