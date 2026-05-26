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

# 2. calibration: probability buckets, setup buckets, horizon stats
#    Reads outcomes from the feature-bus DB trade_outcomes table.
python -m bookmap_mcp.pax_calibration `
    --date $DATE --forecasts $FCST --bus-db $BUS `
    --report "$REP\calibration-$DATE.json" --min-samples 5

# 3. candidate lessons (dry-run by default; never invokes Claude)
python -m bookmap_mcp.pax_research_claude `
    --date $DATE `
    --calibration "$REP\calibration-$DATE.json" `
    --out-dir $REP --min-samples 5 --dry-run

# 4. policy replay (deterministic 60/20/20 time split)
python -m bookmap_mcp.pax_policy_replay `
    --forecasts $FCST `
    --candidates "$REP\policy-candidates-$DATE.json" `
    --bus-db $BUS `
    --report "$REP\replay-$DATE.json" --min-samples 30 --date $DATE
```

`journal_outcomes` is intentionally not part of this helper. It writes
daemon-journal outcomes, while `pax_calibration` consumes feature-bus
`trade_outcomes` through `--bus-db`.

## Artifacts

| Path                                              | Producer                | Contains                                                  |
|---------------------------------------------------|--------------------------|------------------------------------------------------------|
| `reports/calibration-YYYY-MM-DD.json`             | `pax_calibration`        | probability + setup + horizon buckets with calibration_error |
| `reports/policy-candidates-YYYY-MM-DD.json`       | `pax_research_claude`    | structured candidate lessons, all `promotion_status=research_only` |
| `reports/prompt-lessons-YYYY-MM-DD.md`            | `pax_research_claude`    | human / Claude prompt summarizing calibration + lessons    |
| `reports/replay-YYYY-MM-DD.json`                  | `pax_policy_replay`      | per-candidate current vs. candidate metrics on train/val/test + promotion_status + reason |

## Promotion gate

`policy_promotion_gate.py` turns the documented promotion ladder into a
mechanical check. Given a list of changed files and a list of replay
report paths, it answers one question: *is there enough structural
evidence to allow this active-policy change?* It never runs the replay,
never scores edge — that's the replay module's job. It only refuses to
wave a change through when the discipline has not been followed.

### Active-policy surfaces

A change is "active-policy" when it touches one of:

- `mcp-server/bookmap_mcp/pax_weights.json`
- `pax-ai/pax_ai/prompts.py`
- any file under `pax-ai/skills/`
- any file under `skills/`

If the diff touches nothing on this list, the gate passes with
`reason=no_active_policy_change` (no report needed).

### What the gate requires for an active-policy change

1. At least one structurally valid replay report JSON, with all of:
   `generated_ms`, `n_forecasts`, `n_paired`, `split_sizes`
   (`train`/`validation`/`test`), and a `candidates` list.
2. Each candidate must carry `promotion_status`, `reason`, and `splits`
   with `current` + `candidate` metrics on every split (test-split
   `candidate.n_samples >= --min-samples`, default 30).
3. At least one candidate with `promotion_status` past `research_only`.
4. Every non-research-only candidate must carry a `promoted_by` actor.
5. `active` is rejected by default. It passes only when both
   `allow_active=True` is set AND the candidate carries a
   `human_approved_by` actor field.

### Run from Python (preferred — used by CI / tests)

```python
from bookmap_mcp.policy_promotion_gate import check_promotion_gate

result = check_promotion_gate(
    changed_files=[
        "mcp-server/bookmap_mcp/pax_weights.json",
    ],
    report_paths=[Path("reports/replay-2026-05-25.json")],
    allow_active=False,    # default; only promote past `human_approved`
                            # in an explicit operator-driven branch
    min_samples=30,
)
assert result["passed"], result
```

### Run from the CLI

```powershell
python -m bookmap_mcp.policy_promotion_gate `
    --changed-file mcp-server/bookmap_mcp/pax_weights.json `
    --report reports\replay-2026-05-25.json `
    --min-samples 30
```

Exit code 0 = passed, non-zero = blocked. The full result dict is
emitted to stdout for log capture.

`--git-base <ref>` resolves additional changed files via
`git diff --name-only <ref> HEAD`. **Discovery failures fail closed.**
If `git` is not on PATH, the ref does not exist, or the diff returns
a non-zero exit code for any other reason, the CLI exits non-zero with
`reason=git_diff_failed:<detail>` and empty result lists. The gate
will never silently report `no_active_policy_change` on a failed
discovery — a gate that cannot see the diff must refuse, not wave it
through.

CI may still prefer the explicit `--changed-file` form (one flag per
file, repeated) for fully deterministic input — it does not depend on
the working tree, the git index, or the availability of the git
binary in the test image.

### What the gate does NOT do

- Does not rerun the replay or recompute calibration. The replay report
  is the authoritative artifact.
- Does not mutate `pax_weights.json`, `pax_ai_config.json`, prompts,
  reports, or any database — pinned by
  `tests/test_policy_promotion_gate.py::test_gate_is_read_only_against_sentinel_files`.
- Does not promote a candidate from `replay_passed` to `paper_passed`
  or onward. Those transitions are still manual.

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
    tests\test_pax_edge_workflow_e2e.py `
    tests\test_policy_promotion_gate.py

cd ..\pax-ai
python -m pytest -q `
    tests\test_prompts.py `
    tests\test_forecast_signal.py `
    tests\test_forecast_store_writer.py `
    tests\test_chat_forecast_capture.py `
    tests\test_chat_handler.py
```
