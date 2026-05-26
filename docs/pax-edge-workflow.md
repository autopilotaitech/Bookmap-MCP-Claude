# Pax AI self-training edge workflow

Offline research pipeline that turns captured Pax AI forecasts into
candidate lessons, replays them deterministically against historical
outcomes, and stops short of any automatic config / weights / prompt
change.

## Hard rules

- **No live trading anywhere on this path.** No broker, no order tools.
- **No auto-edit of active config.** `pax_ai_config.json`,
  `pax_weights.json`, production prompts, and the active playbook are
  never written by the research / replay / promotion modules.
  Lessons land in `reports/policy-candidates-YYYY-MM-DD.json` and
  `reports/prompt-lessons-YYYY-MM-DD.md` only.
- **Time-ordered splits, never random shuffle.** Train / validation /
  test are 60 / 20 / 20 of the `ts_ms`-sorted forecast list.
- **No silent join invention.** When a forecast cannot be paired with an
  outcome (no `source_turn_id`, no linkage metadata, or an ambiguous
  fallback match), the row is counted as unpaired -- never imputed.

## Promotion ladder

`research_only -> replay_passed -> paper_candidate -> paper_passed ->
human_approved -> active`

The replay module assigns at most `replay_passed` or `paper_candidate`
automatically. `paper_passed`, `human_approved`, and `active` are
manual transitions.

## Forecast capture

Pax AI's system prompt instructs Claude to optionally append a
structured block to its response:

```
<<PAX_FORECAST>>
{"alias":"...", "level":"OR-H", "thesis":"ACCEPTANCE_LONG",
 "execution_read":"PAY_FOR_TRADE", "direction":"LONG",
 "horizon_sec":300, "prob_success":0.62, "expected_r":0.74,
 "invalidation":"...", "features_used":["or_levels", "..."]}
<<END_FORECAST>>
```

When `forecast.enabled` is set to `true` in `pax_ai_config.json` the
chat handler:

1. extracts the block (`pax_ai/forecast_signal.py`),
2. validates it against the SAME snapshot the digest was built from,
3. persists the validated record to the SQLite store at
   `forecast.store_path` (default `D:/BookmapLogs/pax-forecast.db`)
   together with linkage metadata (`chat_run_id`, `digest_sha256`,
   `snapshot_sha256`).

Persistence failures are silent. The chat path is never broken by a
forecast-side failure.

## Linkage to bus outcomes

Forecasts captured at chat time do **not** have `source_turn_id`: the
feature_bus writer thread assigns `ai_turns.id` asynchronously. The
calibration / replay paths therefore use a two-step lookup:

1. `source_turn_id` direct join to `trade_outcomes.ai_turn_id`,
2. fallback: `ai_turns.id WHERE digest_sha256=? AND chat_run_id=?` (must
   match exactly one row); then the same `trade_outcomes` join.

An ambiguous fallback match (more than one ai_turn row sharing
`(digest_sha, chat_run_id)`) returns `None`. We never invent the join.

## Operator workflow

The PowerShell helper runs all five steps with sane defaults:

```powershell
.\scripts\pax-edge-workflow.ps1 -Date 2026-05-25
```

Equivalent manual sequence:

```powershell
$DATE = "2026-05-25"
$FCST = "D:\BookmapLogs\pax-forecast.db"
$BUS  = "D:\BookmapLogs\pax-bus.db"
$REP  = ".\reports"

# 1. bus replay (digest byte-equivalence check)
python -m bookmap_mcp.pax_bus_replay --date $DATE

# 2. outcomes backfill (journal-side, off the pax-bus DB)
python -m bookmap_mcp.journal_outcomes

# 3. calibration: probability buckets, setup buckets, horizon stats
python -m bookmap_mcp.pax_calibration `
    --date $DATE --forecasts $FCST --bus-db $BUS `
    --report "$REP\calibration-$DATE.json" --min-samples 5

# 4. candidate lessons (dry-run by default; never invokes Claude)
python -m bookmap_mcp.pax_research_claude `
    --date $DATE `
    --calibration "$REP\calibration-$DATE.json" `
    --out-dir $REP --min-samples 5 --dry-run

# 5. policy replay (deterministic 60/20/20 time split)
python -m bookmap_mcp.pax_policy_replay `
    --forecasts $FCST `
    --candidates "$REP\policy-candidates-$DATE.json" `
    --bus-db $BUS `
    --report "$REP\replay-$DATE.json" --min-samples 30 --date $DATE
```

## Artifacts

| Path                                              | Producer                | Contains                                                  |
|---------------------------------------------------|--------------------------|------------------------------------------------------------|
| `reports/calibration-YYYY-MM-DD.json`             | `pax_calibration`        | probability + setup + horizon buckets with calibration_error |
| `reports/policy-candidates-YYYY-MM-DD.json`       | `pax_research_claude`    | structured candidate lessons, all `promotion_status=research_only` |
| `reports/prompt-lessons-YYYY-MM-DD.md`            | `pax_research_claude`    | human / Claude prompt summarizing calibration + lessons    |
| `reports/replay-YYYY-MM-DD.json`                  | `pax_policy_replay`      | per-candidate current vs. candidate metrics on train/val/test + promotion_status + reason |

## Tests

```powershell
cd .\mcp-server
python -m pytest -q `
    tests\test_pax_forecast_schema.py `
    tests\test_pax_forecast_store.py `
    tests\test_pax_calibration.py `
    tests\test_pax_outcome_linkage.py `
    tests\test_pax_research_claude.py `
    tests\test_pax_policy_replay.py `
    tests\test_pax_edge_workflow_e2e.py

cd ..\pax-ai
python -m pytest -q `
    tests\test_prompts.py `
    tests\test_forecast_signal.py `
    tests\test_forecast_store_writer.py `
    tests\test_chat_forecast_capture.py `
    tests\test_chat_handler.py
```
