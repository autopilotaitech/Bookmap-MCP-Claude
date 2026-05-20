# Pax Feature Bus — Phase 4B Plan (EOD + Retention + UTC Cleanup)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. **Do NOT implement this plan yet — it is a draft awaiting operator approval.**

**Goal:** Ship the end-of-day rollup CLI gated on the now-stable replay foundation, add a lightweight retention pruner, and clean up the `datetime.utcfromtimestamp()` deprecation surface. Defer Phase 5 tuning, skill edits, news ingestion, and any auto-apply.

**Architecture:** Three additive, read-mostly modules. (1) `pax_bus_eod.py` — batch CLI in `mcp-server/bookmap_mcp/`, reads `ai_turns` + `trade_outcomes` per UTC day, emits markdown. Optionally invokes `pax_bus_replay.py` for SHA-equality checks on the same day. (2) `pax_bus_prune.py` — operator-run CLI that deletes bus rows older than `feature_bus.retention_days` and `VACUUM`s the DB. (3) A single 4-line refactor swapping `datetime.utcfromtimestamp(t).strftime(...)` -> `datetime.fromtimestamp(t, timezone.utc).strftime(...)` in two functions.

**Tech Stack:** Python 3.11+, stdlib only. pytest for tests. No new dependencies.

**Parent commits:**
- Phase 4A router-hint fix: `102c2f2 pax-ai: preserve router hints in bus digest replay`
- Phase 3A + 4A foundation: `c49fce5`
- Phase 2 UI:               `6021aa9`
- Phase 1 capture:          `35e8c22`

---

## Smoke findings that inform this plan

The live Phase 3A + 4A smoke (2026-05-20) demonstrated:

1. **Phase 3A shadow path works as designed**: stderr `[bus-digest]` log fires, `ai_turns.digest_sha256` always matches the `live_sha` (legacy digest), Claude receives the legacy digest. SSE bytes byte-identical to Phase 1+2 when `feature_bus.enabled=false`.
2. **Phase 3A live-swap works**: with `chat.use_feature_bus_digest=true`, `ai_turns.digest_sha256` matches `bus_sha`. Replay 1/1 matched.
3. **Phase 4A outcomes works**: daemon labels old ai_turn rows within one wake interval, NULL mid tolerance verified (skipped T+180s snapshot -> `mid_at_t180s=NULL`, no crash), no advisory-lock contention with the Phase 1 writer thread, idempotent (no re-labeling on subsequent wake).
4. **Replay correctness has a flag-dependent semantic**: shadow-mode `ai_turns` rows are NOT replay-able (they store legacy digest hashes; replay reconstructs bus-digest hashes). Only `chat.use_feature_bus_digest=true` captures are replay-able. Phase 4B's EOD CLI must handle this gracefully (skip non-bus-format rows or report them as "not bus-format" rather than counting them as mismatches).
5. **Operator-facing PYTHONPATH gotcha**: `python -m bookmap_mcp.pax_bus_replay` requires both `mcp-server` and `pax-ai` on `PYTHONPATH` if invoked from arbitrary CWD. Pax AI's smoke launcher set this implicitly via `cd pax-ai`. The EOD CLI must document the same invocation contract (or ship a thin launcher in `mcp-server/`).

---

## Findings / risks (read before any task)

| # | Risk | Likelihood | Severity | Mitigation |
|---|---|---|---|---|
| 1 | EOD CLI mis-counts shadow-format rows as mismatches | High if naive | Medium | EOD inspects digest blob CONTENT (not blob presence) to classify each ai_turns row as `format=bus`, `format=legacy`, `format=missing_blob`, or `format=unknown`. Only `format=bus` rows are SHA-verified via replay. The other three buckets are counted separately and never treated as replay mismatches. The Phase 1 writer saves a blob for every ai_turn regardless of digest path, so "blob exists" is NOT a format signal — content is. |
| 2 | Prune deletes blob files but leaves orphan `ai_turns` rows pointing at them (or vice versa) | Medium | Medium | Prune is delete-by-day-bucket. For one UTC day, delete (a) rows in all 5 tables AND (b) blob files in that day's partition. Document the order; both must succeed before logging "pruned day X". Single transaction for the DB; blob deletion is best-effort with logged failures. |
| 3 | `utcfromtimestamp` cleanup changes blob path partitions for borderline timestamps | Low | High | `fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")` produces the SAME date string as `utcfromtimestamp(t).strftime("%Y-%m-%d")` for all `t` in the supported range. Pin with a parametrized test on 1000 random ts_ms values — both functions return identical strings. Only then swap. |
| 4 | EOD CLI traverses `snapshot_features` (high-volume) and hangs on a big day | Medium | Low | EOD only COUNTS `snapshot_features` (cheap `SELECT COUNT(*) WHERE ts_ms>=? AND ts_ms<?`). It does NOT iterate row-by-row. Rollups use `GROUP BY model / verdict / router_primary` SQL. |
| 5 | Prune defaults too aggressive and deletes recent data | Low | High | Default uses the existing `feature_bus.retention_days` config (defaults to 30 days). Prune must REFUSE to run with `--days < 1` and require `--yes` flag to actually delete (otherwise dry-run mode prints what would be deleted). |
| 6 | Prune triggered while writer thread is mid-batch -> SQLite lock contention | Low | Medium | Prune acquires its own short-lived sqlite3 connection with 5s busy_timeout. It uses `DELETE FROM ... WHERE ts_ms < ?` (no scan past the keep window). WAL mode handles concurrent reads/writes; prune yields to the writer if locked. |
| 7 | DB growth from snapshot_features over weeks | Confirmed | Low | Phase 1 production smoke measured ~16 snapshot_features rows in 6s of live capture (~2.7 rows/s). At 1Hz live (8h/day session) = ~28800 rows/day × ~3KB/row = ~86 MB/day. Over 30 days = ~2.6 GB. Manageable but worth retention; Phase 4B ships the prune CLI for operator-run weekly. |
| 8 | EOD report leaks PII / large blob text into the markdown file | Low | Low | EOD reads ONLY allowlisted columns (mirror Phase 2 `_RECENT_PROJECTION` shape): no `pax_text`, no blob text. Pinned by a test. |
| 9 | EOD report path collision (overwrites existing report) | Low | Low | Default report path `reports/bus-eod-YYYY-MM-DD.md`. `--report` flag overrides. If file exists, append `_N` suffix to avoid clobber. |

---

## Off-limits (Phase 4B)

| Path / scope | Reason |
|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Snapshot composer |
| `mcp-server/bookmap_mcp/or_session.py` | OR anchor invariant |
| `mcp-server/bookmap_mcp/pax_weights.json` | Conviction weights |
| `mcp-server/bookmap_mcp/pax_replay.py` | CSV-era replay (untouched) |
| `mcp-server/bookmap_mcp/pax_outcomes.py` | CSV-era outcomes (untouched) |
| `mcp-server/bookmap_mcp/pax_bus_replay.py` | Phase 4A replay — frozen |
| `pax-ai/pax_ai/prompts.py` | System prompt frozen |
| `pax-ai/pax_ai/claude_stream.py` | CLI args locked |
| `pax-ai/pax_ai/edge_calculus.py` | Math frozen |
| `pax-ai/pax_ai/journal.py` | Chat journal as-is |
| `pax-ai/pax_ai/triggers.py` | Phase 1 hook frozen |
| `pax-ai/pax_ai/chat.py` | Phase 3A wiring frozen — no Phase 4B changes |
| `pax-ai/pax_ai/poller.py` | Snapshot poller |
| `pax-ai/pax_ai/__main__.py` | Phase 4A wiring frozen — Phase 4B does NOT auto-start any new daemon |
| `pax-ai/pax_ai/outcomes.py` | Phase 4A daemon frozen |
| `pax-ai/pax_ai/bus_digest.py` | Phase 3A renderer frozen (only the `_format_ts` utcfromtimestamp swap is touched) |
| `pax-ai/pax_ai/static/index.html` | UI frozen |
| `pax-ai/pax_ai/server.py` | API endpoints frozen |
| `indicators/**` | Bookmap addons |
| `skills/**` | Skill bodies frozen |
| `pax-ai/pax_ai/feature_bus.py` writer paths | Same hard list (start/stop/record_*/_accepting_events/_writer_loop/_drain_to_db/_detect_snapshot_deltas/_insert_*). Phase 4B touches ONLY `_date_partition` (the utcfromtimestamp swap) and adds NO new public API. |
| `pax-ai/pax_ai/config.py` | NO new config keys in Phase 4B (we reuse `feature_bus.retention_days` for prune). |

**Allowed in Phase 4B:**
- `mcp-server/bookmap_mcp/pax_bus_eod.py` — NEW
- `mcp-server/bookmap_mcp/pax_bus_prune.py` — NEW
- `pax-ai/pax_ai/feature_bus.py` — ONE function body edit (`_date_partition`)
- `pax-ai/pax_ai/bus_digest.py` — ONE function body edit (`_format_ts`)
- `pax-ai/tests/test_pax_bus_eod.py` — NEW
- `pax-ai/tests/test_pax_bus_prune.py` — NEW
- `pax-ai/tests/test_utc_cleanup.py` — NEW (the equivalence test)

---

## Task batches

### Batch 4B-1 — `utcfromtimestamp` cleanup

Smallest, lowest risk, foundational. Do this first so subsequent batches inherit a clean date-partition API.

**Files:**
- Modify: `pax-ai/pax_ai/feature_bus.py` (function body of `_date_partition` only)
- Modify: `pax-ai/pax_ai/bus_digest.py` (function body of `_format_ts` only)
- Create: `pax-ai/tests/test_utc_cleanup.py`

- [ ] **Step 1: Write failing equivalence test**

```python
"""Confirms the new tz-aware datetime.fromtimestamp(t, timezone.utc) returns
the SAME string as the deprecated datetime.utcfromtimestamp(t) across a wide
range of timestamps. Required before the swap so we know blob-path
partitions don't shift for any historical ts_ms."""
from __future__ import annotations

import datetime as _dt
import random

import pytest

from pax_ai import feature_bus, bus_digest


@pytest.mark.parametrize("ts_ms", [
    0, 1, 86_400_000, 1_704_067_200_000,  # 2024-01-01 UTC
    1_715_000_000_000,                     # 2024-05-06 UTC (Phase 1 fixture)
    1_768_478_400_000,                     # 2026-01-15 UTC (Phase 4A fixture)
    2_524_608_000_000,                     # 2050-01-01 UTC
])
def test_date_partition_tz_aware_equivalence(ts_ms):
    """Bus blob path partition must not change after the utcfromtimestamp -> tz-aware swap."""
    new_str = feature_bus._date_partition(ts_ms)
    expected = _dt.datetime.fromtimestamp(ts_ms / 1000.0, _dt.timezone.utc).strftime("%Y-%m-%d")
    assert new_str == expected


def test_format_ts_tz_aware_equivalence():
    """bus_digest._format_ts must produce the same HH:MM:SS as the prior implementation."""
    ts_ms = 1_715_000_000_000
    new = bus_digest._format_ts(ts_ms)
    expected = _dt.datetime.fromtimestamp(ts_ms / 1000.0, _dt.timezone.utc).strftime("%H:%M:%S")
    assert new == expected


def test_no_utcfromtimestamp_in_production_code():
    """AST scan: production modules must not call datetime.utcfromtimestamp."""
    import ast
    from pathlib import Path
    for fn in (feature_bus.__file__, bus_digest.__file__):
        tree = ast.parse(Path(fn).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "utcfromtimestamp":
                pytest.fail(f"{fn} still references utcfromtimestamp")
```

Step 1 may PASS for the parametrize cases without code change (because both APIs produce the same string for these inputs); the AST scan will FAIL because the source still has `utcfromtimestamp`. That's the failing test.

- [ ] **Step 2: Implement the swap**

In `pax-ai/pax_ai/feature_bus.py::_date_partition`:

```python
def _date_partition(ts_ms: int) -> str:
    """UTC date partition for blob paths. UTC so the partition matches across timezones."""
    return _dt.datetime.fromtimestamp(ts_ms / 1000.0,
                                       _dt.timezone.utc).strftime("%Y-%m-%d")
```

In `pax-ai/pax_ai/bus_digest.py::_format_ts`:

```python
def _format_ts(ts_ms: Optional[int]) -> str:
    if not ts_ms:
        return "??:??:??"
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(int(ts_ms) / 1000.0,
                                            _dt.timezone.utc).strftime("%H:%M:%S")
    except (OSError, ValueError, OverflowError):
        return "??:??:??"
```

- [ ] **Step 3: Run + Phase 1 invariants + full suite**

```
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai
python -m pytest tests/test_utc_cleanup.py -v
python -m pytest -v
python -m pytest -v <Phase 1 invariant set>
```

Expected: 3 new tests pass, full suite green (~431), Phase 1 invariants all pass.

### Batch 4B-2 — `pax_bus_prune.py`

Retention pruner. Operator-run (no daemon, no auto-schedule). Lives next to `pax_bus_replay.py` in `mcp-server/bookmap_mcp/`.

**Files:**
- Create: `mcp-server/bookmap_mcp/pax_bus_prune.py`
- Create: `pax-ai/tests/test_pax_bus_prune.py`

**Contract:**
- `python -m bookmap_mcp.pax_bus_prune --days 30 --yes [--db ... --snap ... --dig ...]`
- Default `--days` from `feature_bus.retention_days` config (30).
- Dry-run mode (no `--yes`): prints what would be deleted; exit 0.
- With `--yes`: deletes rows older than the cutoff in all 5 tables (`snapshot_features`, `level_events`, `microstructure_events`, `trigger_events`, `ai_turns`); does NOT delete `trade_outcomes` rows (those are referenced by `ai_turn_id` — if the ai_turn is deleted, the outcome is orphaned but kept for forensics).
- Deletes blob files in `pax-snapshots/YYYY-MM-DD/` and `pax-digests/YYYY-MM-DD/` partitions where the date is older than cutoff (best-effort; partial failure logs and continues).
- Runs `PRAGMA optimize` + `VACUUM` after deletes.
- Refuses to run with `--days < 1` (sanity gate).
- Reports `{table: rows_deleted, blob_files_deleted, vacuum_bytes_freed}` to stdout + writes `reports/bus-prune-YYYY-MM-DD.md`.

**Tests:**
- `test_prune_dry_run_no_writes`
- `test_prune_yes_deletes_rows_older_than_cutoff`
- `test_prune_keeps_rows_within_cutoff`
- `test_prune_keeps_trade_outcomes` (orphan after ai_turn delete is intentional)
- `test_prune_deletes_blob_partitions_for_old_dates`
- `test_prune_refuses_days_zero_or_negative`
- `test_prune_default_days_reads_config`
- `test_prune_idempotent` (second run with same args is a no-op)
- `test_prune_no_off_limits_imports` (AST scan: no imports from prompts/claude_stream/etc.)

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Implement CLI**
- [ ] **Step 3: Full suite green; Phase 1 invariants stay green**

Test count after Batch 4B-2: ~440.

### Batch 4B-3 — `pax_bus_eod.py` (gated)

End-of-day rollup CLI. Reads `ai_turns` and `trade_outcomes` per UTC day, produces a markdown report. Optionally invokes `pax_bus_replay` for SHA-equality on bus-format rows.

**Gate (per smoke finding 4 — content-based detection, no schema migration):** EOD classifies each ai_turns row by inspecting the digest blob CONTENT, never by blob presence alone. The Phase 1 writer saves a blob for every ai_turn regardless of whether chat used the legacy or bus digest, so "blob exists" is NOT a format signal. Format detection rules:

- `format=bus`          — digest blob exists AND contains ALL nine bus block markers: `[STATE]`, `[ANCHOR]`, `[GATES]`, `[LEVELS]`, `[MICROSTRUCTURE]`, `[RECENT_EVENTS]`, `[POSITION]`, `[SESSION_MEMORY]`, `[USER]`.
- `format=legacy`       — digest blob exists, contains the literal `SNAPSHOT DIGEST` substring, and does NOT contain the full bus marker set.
- `format=missing_blob` — no digest blob exists at the expected `{snapshot_blob_dir}/YYYY-MM-DD/{digest_sha256}.txt` path.
- `format=unknown`      — blob exists but matches neither the bus nor legacy shape.

Only `format=bus` rows are SHA-verified via replay. The other three buckets are counted separately in the EOD report and are NEVER treated as replay mismatches. No schema migration: there is no `ai_turns.format` column; classification happens at EOD time from blob content alone.

**Files:**
- Create: `mcp-server/bookmap_mcp/pax_bus_eod.py`
- Create: `pax-ai/tests/test_pax_bus_eod.py`

**Contract:**
- `python -m bookmap_mcp.pax_bus_eod --date YYYY-MM-DD [--alias ALIAS] [--report ...] [--verify-replay]`
- Default `--date` is the current UTC day.
- `--verify-replay`: ALSO invoke `pax_bus_replay.main([...])` on the same date and embed its report.
- Output: markdown report with these sections:
  - **Turns** — total ai_turns, by model, by router_primary, total cost (USD), mean / median / p95 latency (`elapsed_ms`).
  - **Verdicts** — count of `trade_outcomes` by `verdict`. (Phase 4A heuristic; documented as placeholder.)
  - **Mid drift** — for ENTER_LONG and ENTER_SHORT rows, mean `mid_at_t60s - mid_at_t0`, t180, t300, t900 (or "n/a" if NULL columns).
  - **Format breakdown** — counts of `format=bus`, `format=legacy`, `format=missing_blob`, `format=unknown` ai_turns rows (content-based; see gate above).
  - **Replay verification** (if `--verify-replay`): only `format=bus` rows are submitted to `pax_bus_replay`. Report shows matched / mismatched counts ONLY across the `format=bus` subset. legacy / missing_blob / unknown counts come from the Format breakdown section, NOT the replay-mismatch counter.
  - **Counts** — same per-table counts as Phase 2 `summary_today()`.
- No raw blob text. No `pax_text`. No `digest_text`.

**Tests:**
- `test_eod_empty_day_returns_empty_report`
- `test_eod_counts_turns_by_model`
- `test_eod_groups_verdicts`
- `test_eod_mid_drift_handles_null_mids`
- `test_eod_format_legacy_blob_with_snapshot_digest_marker` — seeds an ai_turn whose digest blob contains the literal `SNAPSHOT DIGEST` text and does NOT contain bus markers. Asserts the EOD report counts it as `format=legacy` and does NOT report it as a replay mismatch.
- `test_eod_format_bus_blob_with_all_markers` — seeds an ai_turn whose digest blob contains all 9 bus markers (`[STATE]`...`[USER]`) AND the stored `digest_sha256` matches its content. With `--verify-replay`, the row is submitted to `pax_bus_replay` and the report shows it under matched.
- `test_eod_format_missing_blob_counted_separately` — seeds an ai_turn whose digest blob file does not exist. Asserts the report counts it as `format=missing_blob` and never tries to replay it.
- `test_eod_format_unknown_blob_counted_separately` — seeds an ai_turn whose digest blob exists but contains neither `SNAPSHOT DIGEST` nor the full bus marker set (e.g. empty file or random text). Asserts `format=unknown` and no replay attempt.
- `test_eod_verify_replay_invokes_replay_for_bus_format_only` — seeds one bus, one legacy, one missing, one unknown row. `--verify-replay` submits ONLY the bus row to the replay code path; legacy/missing/unknown counts come from the format-breakdown section, NOT the replay-mismatch counter.
- `test_eod_excludes_blob_text_columns_from_report` (PII / size guard) — no `pax_text`, `digest_text`, or `snapshot_json` substring in the rendered markdown.
- `test_eod_default_date_is_utc_today`
- `test_eod_no_off_limits_imports`

- [ ] **Step 1: Write failing tests**
- [ ] **Step 2: Implement CLI**
- [ ] **Step 3: Full suite green; Phase 1 invariants stay green**

Test count after Batch 4B-3: ~450.

### Batch 4B-4 — Operator launcher script (small QoL)

The smoke finding 5 showed the PYTHONPATH gotcha. Phase 4B ships a thin Windows batch launcher next to `pax-ai-start.bat`:

**File:** `pax-bus-tools.bat` (new at repo root, similar to `pax-ai-start.bat`)

Contract: `pax-bus-tools.bat replay --date YYYY-MM-DD` and `pax-bus-tools.bat eod --date YYYY-MM-DD` and `pax-bus-tools.bat prune --days 30 --yes` set `PYTHONPATH=pax-ai:mcp-server` and invoke the right module.

**Tests:**
- (none — `.bat` files are not covered by pytest; verified by operator smoke)

- [ ] **Step 1: Write `pax-bus-tools.bat`**
- [ ] **Step 2: Operator smoke** (manual; document expected output in plan only)

---

## Parallel work

| Batch | Dependency |
|---|---|
| 4B-1 (UTC cleanup) | None — foundational |
| 4B-2 (prune CLI) | After 4B-1 (uses `_date_partition`) |
| 4B-3 (EOD CLI) | After 4B-1, can run in parallel with 4B-2 |
| 4B-4 (launcher .bat) | After 4B-2 and 4B-3 |

---

## Rollback per flag

Phase 4B introduces NO new config flags. Rollback paths:

| Component | Rollback action |
|---|---|
| UTC cleanup | `git revert <4B-1 commit>` — pure code change, no state |
| Prune CLI   | Don't run it. If accidentally run, the deleted rows are gone — restoration is out-of-band (operator-managed off-site backup). Plan emphasizes operator runs prune EXPLICITLY with `--yes`. |
| EOD CLI     | Don't run it. Pure read-only; running has no side effects beyond a markdown report. |
| Launcher .bat | `git revert <4B-4 commit>` or delete the file. |

---

## Final verification commands

After every batch:

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m compileall -q pax_ai
```

After Batch 4B-3 (full Phase 4B):

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m compileall -q pax_ai

# Off-limits diff guard (Phase 4B scope).
git -C /c/Bookmap/addons/MCP/Bookmap diff --name-only HEAD~4..HEAD | grep -E "(dashboard\.py|or_session\.py|pax_weights\.json|pax_replay\.py|pax_outcomes\.py|prompts\.py|claude_stream\.py|edge_calculus\.py|journal\.py|triggers\.py|poller\.py|chat\.py|static/index\.html|server\.py|outcomes\.py|pax_bus_replay\.py|indicators/|skills/)" || echo "OK: zero off-limits"

# Phase 1 writer-path additive-only guard.
git -C /c/Bookmap/addons/MCP/Bookmap diff HEAD~4..HEAD -- pax-ai/pax_ai/feature_bus.py | grep -E "^[-+].*def (start|stop|record_trigger|record_ai_turn|_accepting_events|_writer_loop|_drain_to_db|_detect_snapshot_deltas|_insert_)" || echo "OK: zero writer-path defs touched"

# Phase 1 invariant regression.
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest -v \
  tests/test_chat_handler.py::test_chat_path_unchanged_when_bus_disabled \
  tests/test_chat_handler.py::test_aiturn_uses_snapshot_captured_at_prompt_build_not_post_done \
  tests/test_feature_bus_integration.py::test_whynow_byte_identical_with_and_without_bus \
  tests/test_feature_bus_triggers.py::test_compute_triggers_never_called_by_feature_bus \
  tests/test_feature_bus_ai_turn.py::test_record_methods_noop_when_unhealthy_or_not_running
```

All must pass.

---

## Out-of-scope (explicit deferrals)

- **Phase 5 tuning recommendations** — `pax_bus_tune.py` and threshold-drift analysis.
- **Auto-apply of anything** — no automated config / skill / prompt edits.
- **Skill edits** — `skills/pax-or/SKILL.md` and `skills/hft_microstructure_quant_v1/SKILL.md` stay frozen.
- **News calendar ingestion** — `news-calendar.json` stays operator-curated.
- **Schema migrations** — no new columns on `ai_turns` / `trade_outcomes` / etc. (specifically: no `format` column on `ai_turns`; format is inferred from blob presence at EOD time).
- **Outcomes verdict v2** — Phase 4A heuristic stays. Phase 5+ will replace with a real verdict extractor.
- **Phase 4A daemon CPU/disk profiling** — out of scope until operator runs `outcomes.enabled=true` for ≥1 trading session and reports load.

---

## Stop here

Plan complete. Not implemented. Awaiting operator approval.
