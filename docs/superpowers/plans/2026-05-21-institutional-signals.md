# Institutional Signal Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or subagent-driven-development) to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace generic trend-driven buy/sell chart markers with a level-anchored institutional-signal engine. Produce `snap["institutional_signals"]` (a top-level array of signal-event objects per proximate OR/extension level) and wire the Java chart to plot ONLY from this source. Disable legacy trend triangles by default. Gate the native-engine markers on institutional alignment.

**Architecture:** Per-level `institutional_thesis` (already exists) is a state. Per state-change, the new composer `compute_institutional_signals` emits one event with a deterministic id (`{alias}|{label}|{side}|{state_since_ms}`). Only `execution_read == "PAY_FOR_TRADE"` produces `direction in (LONG, SHORT)`; everything else is `NONE` (watch / warning / scratch). The composer also enforces the trading rules: middle-of-OR yields no signals; spoof risk → SPOOF_STAND_DOWN only; iceberg defense → ICEBERG_DEFENSE only; stop sweep → WAIT_FOR_CONFIRM until acceptance/failure transition. Java side adds parser + model + fetcher + dedup + painter mirroring the existing trend-triangle plumbing.

**Tech stack:** Python 3, pytest, Java 17 (Bookmap addon, JUnit 5). No live-order code touched.

---

## File Structure

**Modified (Python):**
- `mcp-server/bookmap_mcp/dashboard.py` — add `compute_institutional_signals`, signal-type mapping helpers, insertion into `_compose_alias_snapshot`.
- `mcp-server/bookmap_mcp/signal_engine.py` — re-export the new composer + signal-type constants.

**Modified (Java):**
- `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java` — register new fetcher; conditionally suppress legacy trend triangles + native marker publishing per new flags.
- `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeUiSettings.java` — three new boolean settings: `showTrendTriangles` (default **false**), `showInstitutionalSignals` (default **true**), `gateNativeMarkersOnInstitutional` (default **true**).

**Created (Java):**
- `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalSignalModel.java` — immutable carrier.
- `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalSignalsSnapshotParser.java` — reads `snap["institutional_signals"]`.
- `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalSignalsFetcher.java` — polls `/api/snapshot`, sets `instSignalsDirty`.
- `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalSignalsDedup.java` — dedup by signal id; bounded LRU.
- `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalSignalsPainter.java` — renders 5 marker types (PAY LONG green, PAY SHORT red, WAIT yellow, STAND_DOWN orange, SCRATCH gray X) anchored at level price + signal timestamp.

**Created (tests):**
- `mcp-server/tests/test_institutional_signals_composer.py` — 13 tests covering every hard trading rule from the spec.
- `mcp-server/tests/test_institutional_signals_dedup.py` — dedup by `state_since_ms`; same state across polls emits exactly one event.
- `mcp-server/tests/test_institutional_signals_backcompat.py` — legacy snapshot keys preserved; `trend_signal` still emitted as context.
- `mcp-server/tests/test_signal_engine_exports_institutional_signals.py` — re-export contract.
- `indicators/OpenRange/src/test/java/com/openrange/PaxInstitutionalSignalsSnapshotParserTest.java` — parser shape + safety defaults.
- `indicators/OpenRange/src/test/java/com/openrange/PaxInstitutionalSignalsDedupTest.java` — dedup by id; LRU eviction.

**Created (backup):**
- `_phase_backups/institutional_signals_<YYYYMMDD_HHMMSS>/BACKUP_MANIFEST.md`
- copies of every modified file under the same relative tree.

---

## Signal-type mapping (authoritative table)

| thesis.state | thesis.thesis | execution_read | signal_type | direction |
|---|---|---|---|---|
| ACCEPTED_ABOVE | ACCEPTANCE_LONG | PAY_FOR_TRADE | ACCEPTANCE_LONG | LONG |
| ACCEPTED_BELOW | ACCEPTANCE_SHORT | PAY_FOR_TRADE | ACCEPTANCE_SHORT | SHORT |
| REJECTED (side=above) | REJECTION_SHORT | PAY_FOR_TRADE | REJECTION_SHORT | SHORT |
| REJECTED (side=below) | REJECTION_LONG | PAY_FOR_TRADE | REJECTION_LONG | LONG |
| TOUCHED + sweep (side=above) | STOP_SWEEP_CONTINUATION | WAIT_FOR_CONFIRM | STOP_SWEEP_LONG | NONE |
| TOUCHED + sweep (side=below) | STOP_SWEEP_CONTINUATION | WAIT_FOR_CONFIRM | STOP_SWEEP_SHORT | NONE |
| FAILED_BREAK + sweep (side=above) | STOP_SWEEP_FAILURE | PAY_FOR_TRADE | STOP_SWEEP_SHORT | SHORT |
| FAILED_BREAK + sweep (side=below) | STOP_SWEEP_FAILURE | PAY_FOR_TRADE | STOP_SWEEP_LONG | LONG |
| TOUCHED | ICEBERG_DEFENSE | STAND_DOWN | ICEBERG_DEFENSE | NONE |
| any | NONE (lq=SPOOF_RISK) | STAND_DOWN | SPOOF_STAND_DOWN | NONE |
| RETEST_FAIL / INVALIDATED | any | SCRATCH_READY | SCRATCH | NONE |
| (anything not matching) | — | — | (suppressed) | — |

**Aggressor-flow alignment veto:**
- ACCEPTANCE_LONG / ACCEPTANCE_SHORT require `aggressor_flow == "WITH"`. If `AGAINST`, downgrade to WAIT_FOR_CONFIRM and direction → NONE.
- REJECTION_LONG / REJECTION_SHORT require `aggressor_flow == "AGAINST"` (counter-flow at the rejected level). If WITH, downgrade similarly.

**Proximity / mid-of-OR rule:** signals only emit for levels with `proximity == true`. Inside-OR levels (the +1/-1 etc. embedded within the range when the OR is wide) and far extensions outside proximity yield no events.

---

## Phase 0: Backup

### Task 0.1: Timestamped backup of files to be modified

- [ ] **Step 1: Create backup tree**

```powershell
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$bdir = "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\institutional_signals_$ts"
New-Item -ItemType Directory -Force $bdir | Out-Null
foreach ($p in @(
  'mcp-server\bookmap_mcp\dashboard.py',
  'mcp-server\bookmap_mcp\signal_engine.py',
  'indicators\OpenRange\src\main\java\com\openrange\PaxOpeningRangeModule.java',
  'indicators\OpenRange\src\main\java\com\openrange\PaxOpeningRangeUiSettings.java'
)) {
  $src = Join-Path 'C:\Bookmap\addons\MCP\Bookmap' $p
  $dst = Join-Path $bdir $p
  New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
  Copy-Item $src $dst
}
$bdir
```

- [ ] **Step 2: Write BACKUP_MANIFEST.md** with rollback PowerShell snippet.

---

## Phase 1: Python composer + signal-type mapping

### Task 1.1: Write failing composer tests

**File:** `mcp-server/tests/test_institutional_signals_composer.py`

13 tests covering every hard trading rule:

1. `test_no_signals_when_or_levels_missing` — `snap` without `or_levels` yields `compute_institutional_signals(snap) == []`.
2. `test_no_signal_in_middle_of_or` — mid inside OR with no level in proximity → empty list.
3. `test_spoof_risk_blocks_entry` — SPOOF event at OR-H produces only `SPOOF_STAND_DOWN`, direction=NONE, execution_read=STAND_DOWN.
4. `test_iceberg_defense_blocks_breakout_entry` — ASK iceberg at OR-H → `ICEBERG_DEFENSE`, NONE, STAND_DOWN. No `ACCEPTANCE_LONG` emitted.
5. `test_or_h_acceptance_creates_long_pay_for_trade` — drive APPROACHING → TOUCHED → ACCEPTED_ABOVE over 4 polls with `tape_flow.deltaScore=+0.45` → signal: `ACCEPTANCE_LONG`, LONG, PAY_FOR_TRADE.
6. `test_or_l_acceptance_creates_short_pay_for_trade` — symmetric.
7. `test_or_h_rejection_creates_short_pay_for_trade` — TOUCHED OR-H then reverse > REJECT_BACKOFF_PTS with `deltaScore=-0.30` (AGAINST = correct counter-flow for rejection short) → signal: `REJECTION_SHORT`, SHORT, PAY_FOR_TRADE.
8. `test_or_l_rejection_creates_long_pay_for_trade` — symmetric.
9. `test_stop_sweep_creates_wait_first_then_pay_on_failure` — STOP_SWEEP event at TOUCHED → first signal `STOP_SWEEP_*`, direction=NONE, WAIT_FOR_CONFIRM. After failed-break transition → `STOP_SWEEP_*`, direction LONG or SHORT (opposite to sweep side), PAY_FOR_TRADE.
10. `test_aggressor_flow_mismatch_blocks_entry` — ACCEPTED_ABOVE but tape `deltaScore=-0.45` (AGAINST) → signal `ACCEPTANCE_LONG`, direction=NONE, execution_read=WAIT_FOR_CONFIRM (downgraded from PAY_FOR_TRADE).
11. `test_trend_signal_alone_does_not_create_institutional_entry` — snap with `trend_signal.kind="STRONG_BULL"` but mid in middle of OR + no level touched → empty list.
12. `test_retest_fail_produces_scratch_signal` — drive ACCEPTED_ABOVE then reverse hard → state=RETEST_FAIL → signal `SCRATCH`, NONE, SCRATCH_READY.
13. `test_signal_id_deterministic_from_state_since_ms` — repeating the same state across polls produces the same id; state transition changes the id.

Each test asserts the FULL signal-event payload shape: `id`, `alias`, `label`, `price`, `side`, `direction`, `signal_type`, `execution_read`, `confidence`, `size_tier`, `reason_codes`, `invalidation_price`, `payline_price`, `timestamp_ms`, `source_level_state`, `liquidity_quality`, `aggressor_flow`, `book_state`.

- [ ] **Step 1: Write the test file.**

- [ ] **Step 2: Run to confirm failure.**

```
cd mcp-server && python -m pytest tests/test_institutional_signals_composer.py -v
```

Expected: ImportError — `compute_institutional_signals` not defined.

### Task 1.2: Implement composer + signal-type mapper

**File:** `mcp-server/bookmap_mcp/dashboard.py` (insert after `compute_institutional_thesis`)

- [ ] **Step 1: Add the signal-type constants and mapper helpers**

```python
_SIGNAL_TYPE_CODES = (
    "ACCEPTANCE_LONG", "ACCEPTANCE_SHORT",
    "REJECTION_LONG", "REJECTION_SHORT",
    "STOP_SWEEP_LONG", "STOP_SWEEP_SHORT",
    "ICEBERG_DEFENSE", "SPOOF_STAND_DOWN",
    "SCRATCH",
)
_SIGNAL_DIRECTION_CODES = ("LONG", "SHORT", "NONE")


def _signal_type_from_thesis(side: str, state: str, thesis: str,
                             liquidity: str, execution_read: str
                             ) -> Tuple[Optional[str], str]:
    """Map (state, thesis, liquidity, execution_read) -> (signal_type, default_direction).

    Returns (None, "NONE") when no signal should be emitted at this level.
    """
    if liquidity == "SPOOF_RISK":
        return "SPOOF_STAND_DOWN", "NONE"
    if thesis == "ICEBERG_DEFENSE":
        return "ICEBERG_DEFENSE", "NONE"
    if execution_read == "SCRATCH_READY" or state in ("RETEST_FAIL", "INVALIDATED"):
        return "SCRATCH", "NONE"
    if thesis == "STOP_SWEEP_CONTINUATION":
        return (("STOP_SWEEP_LONG" if side == "above"
                 else "STOP_SWEEP_SHORT"), "NONE")
    if thesis == "STOP_SWEEP_FAILURE":
        # Failure above OR-H -> SHORT; failure below OR-L -> LONG.
        if side == "above":
            return "STOP_SWEEP_SHORT", "SHORT"
        return "STOP_SWEEP_LONG", "LONG"
    if thesis == "ACCEPTANCE_LONG":
        return "ACCEPTANCE_LONG", "LONG"
    if thesis == "ACCEPTANCE_SHORT":
        return "ACCEPTANCE_SHORT", "SHORT"
    if thesis == "REJECTION_LONG":
        return "REJECTION_LONG", "LONG"
    if thesis == "REJECTION_SHORT":
        return "REJECTION_SHORT", "SHORT"
    return None, "NONE"


def _signal_aggressor_align_required(signal_type: str) -> Optional[str]:
    """Return the required aggressor_flow value for this signal_type, or None
    if no alignment veto applies."""
    if signal_type in ("ACCEPTANCE_LONG", "ACCEPTANCE_SHORT"):
        return "WITH"
    if signal_type in ("REJECTION_LONG", "REJECTION_SHORT"):
        return "AGAINST"
    if signal_type in ("STOP_SWEEP_LONG", "STOP_SWEEP_SHORT"):
        # Failure variants: opposite side counter-flow is consistent with the
        # failure direction. Acceptable: WITH (the new direction) or MIXED.
        return None
    return None  # ICEBERG_DEFENSE / SPOOF_STAND_DOWN / SCRATCH: no veto


def _signal_size_tier_from_confidence(confidence: float) -> str:
    """FULL >= 0.50; HALF >= 0.35; else NONE — same thresholds as
    pax-ai/pax_ai/edge_calculus.size_tier()."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return "NONE"
    if c >= 0.50:
        return "FULL"
    if c >= 0.35:
        return "HALF"
    return "NONE"
```

- [ ] **Step 2: Implement `compute_institutional_signals`**

```python
def compute_institutional_signals(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the institutional signal-event array from per-level thesis state.

    Pure: reads snap, produces a list. Empty list when there are no proximate
    levels, no or_levels block, or all levels suppressed by thesis rules.
    """
    out: List[Dict[str, Any]] = []
    ol = snap.get("or_levels") or {}
    if not isinstance(ol, dict):
        return out
    levels = ol.get("levels") or []
    if not isinstance(levels, list):
        return out
    or_high = ol.get("orHigh")
    or_low = ol.get("orLow")
    alias = snap.get("alias") or ""
    try:
        tick = NQ_TICK
        or_high_f = float(or_high) if or_high is not None else None
        or_low_f = float(or_low) if or_low is not None else None
    except (TypeError, ValueError):
        return out

    for lvl in levels:
        if not isinstance(lvl, dict):
            continue
        if not lvl.get("proximity"):
            continue  # rule #8: never plot buy/sell unless at/near a level
        ith = lvl.get("institutional_thesis") or {}
        state = ith.get("state") or ""
        thesis = ith.get("thesis") or "NONE"
        liquidity = ith.get("liquidity_quality") or ""
        aggressor = ith.get("aggressor_flow") or ""
        book_st = ith.get("book_state") or ""
        execution_read = ith.get("execution_read") or "WAIT_FOR_CONFIRM"
        confidence = float(ith.get("confidence") or 0.0)

        signal_type, default_direction = _signal_type_from_thesis(
            side=lvl.get("side") or "above",
            state=state, thesis=thesis,
            liquidity=liquidity, execution_read=execution_read,
        )
        if signal_type is None:
            continue

        # Aggressor-flow alignment veto: downgrade direction to NONE +
        # execution_read to WAIT_FOR_CONFIRM if the required alignment is not met.
        direction = default_direction
        required = _signal_aggressor_align_required(signal_type)
        effective_exec = execution_read
        reason_codes: List[str] = list(ith.get("reasons") or [])[:6]
        if required and aggressor != required and direction != "NONE":
            direction = "NONE"
            effective_exec = "WAIT_FOR_CONFIRM"
            reason_codes.append(f"aggressor_flow={aggressor} != required {required}")

        # rule #1: only PAY_FOR_TRADE produces LONG/SHORT entry direction.
        if effective_exec != "PAY_FOR_TRADE":
            direction = "NONE"

        # rule #15: book pulling against the signal is a downgrade.
        if direction in ("LONG", "SHORT") and book_st == "PULLING":
            direction = "NONE"
            effective_exec = "WAIT_FOR_CONFIRM"
            reason_codes.append("book_state=PULLING contradicts signal")

        size_tier = _signal_size_tier_from_confidence(confidence) if direction != "NONE" else "NONE"

        # Invalidation + payline derive from the level price and OR boundary.
        price = float(lvl.get("price") or 0.0)
        side = lvl.get("side") or "above"
        invalidation_price = None
        payline_price = None
        if direction == "LONG":
            invalidation_price = (or_low_f - tick) if or_low_f is not None else None
            payline_price = round(price + 10.0, 2)  # NQ payline = 10 pts
        elif direction == "SHORT":
            invalidation_price = (or_high_f + tick) if or_high_f is not None else None
            payline_price = round(price - 10.0, 2)

        state_since_ms = int(ith.get("last_state_change_ms") or 0)
        touched_at_ms = ith.get("touched_at_ms")
        timestamp_ms = touched_at_ms if touched_at_ms is not None else state_since_ms

        signal_id = f"{alias}|{lvl.get('label')}|{side}|{state_since_ms}"

        out.append({
            "id":                  signal_id,
            "alias":               alias,
            "label":               lvl.get("label"),
            "price":               round(price, 2),
            "side":                side,
            "direction":           direction,
            "signal_type":         signal_type,
            "execution_read":      effective_exec,
            "confidence":          round(confidence, 3),
            "size_tier":           size_tier,
            "reason_codes":        reason_codes,
            "invalidation_price":  (round(invalidation_price, 2)
                                     if invalidation_price is not None else None),
            "payline_price":       payline_price,
            "timestamp_ms":        timestamp_ms,
            "source_level_state":  state,
            "liquidity_quality":   liquidity,
            "aggressor_flow":      aggressor,
            "book_state":          book_st,
        })
    return out
```

- [ ] **Step 3: Re-run tests until green.**

```
cd mcp-server && python -m pytest tests/test_institutional_signals_composer.py -v
```

Expected: 13 passed.

### Task 1.3: Wire into `_compose_alias_snapshot`

- [ ] **Step 1: Locate the assembly site.** `dashboard.py` `_compose_alias_snapshot` constructs the snap dict in `fetch_snapshot`. The `pax` key is the last decision-layer key before `sim`. Insert `institutional_signals` immediately after `pax`.

- [ ] **Step 2: Add the assignment.**

```python
snap["pax"] = pax_decision(snap)
snap["institutional_signals"] = compute_institutional_signals(snap)
# sim follows as before
```

- [ ] **Step 3: Re-export from signal_engine.**

In `mcp-server/bookmap_mcp/signal_engine.py`, add to imports + `__all__`:
- `compute_institutional_signals`
- `_signal_type_from_thesis`
- `_SIGNAL_TYPE_CODES`
- `_SIGNAL_DIRECTION_CODES`

- [ ] **Step 4: Run full mcp-server suite.**

```
cd mcp-server && python -m pytest -q
```

Expected: all green (prior 569 + ~17 new).

- [ ] **Step 5: Commit (after user approval).**

---

## Phase 2: Backcompat + dedup tests

### Task 2.1: Backcompat tests

**File:** `mcp-server/tests/test_institutional_signals_backcompat.py`

- [ ] Tests:
  - `test_legacy_top_level_keys_still_present` — `snap` after `_compose_alias_snapshot` still contains `or_levels`, `pax`, `trend_signal`, `conviction`, `flow`, `vwap_bias`, `vp_bias`.
  - `test_trend_signal_still_emitted_as_context` — `trend_signal.kind` field shape unchanged.
  - `test_per_level_legacy_fields_preserved` — every level retains `decision`, `confidence`, `composite`, `components`, `institutional_thesis`.
  - `test_institutional_signals_is_top_level_array` — `snap["institutional_signals"]` is a list (possibly empty).

### Task 2.2: Dedup tests

**File:** `mcp-server/tests/test_institutional_signals_dedup.py`

- [ ] Tests:
  - `test_same_state_across_polls_yields_same_signal_id` — drive ACCEPTED_ABOVE, then run composer twice. id is identical.
  - `test_state_transition_changes_signal_id` — ACCEPTED_ABOVE then RETEST_FAIL → different ids.
  - `test_multiple_proximate_levels_produce_independent_signals` — when both OR-H and +1 are in proximity, both emit signals with distinct ids.

---

## Phase 3: Java parser + model + fetcher + dedup + painter

### Task 3.1: Model + parser

**Files:**
- `PaxInstitutionalSignalModel.java` — immutable record holding the 17 fields from the spec.
- `PaxInstitutionalSignalsSnapshotParser.java` — tokenizer/parser (no JSON lib dep, matches the existing parser style). Reads `snap["institutional_signals"]` array. Defaults: `direction = NONE`, `execution_read = WAIT_FOR_CONFIRM`, `signal_type = SCRATCH`, all numeric fields safe defaults.

**Test:** `PaxInstitutionalSignalsSnapshotParserTest.java`
- parses a full signal array
- empty array yields empty model list
- missing `institutional_signals` key returns empty list (no crash)
- partial / malformed fields default to safe values
- never crashes on unexpected JSON shapes

### Task 3.2: Fetcher + dedup

- `PaxInstitutionalSignalsFetcher.java` — daemon thread, polls `/api/snapshot`, sets `AtomicBoolean instSignalsDirty`. Mirrors `PaxTrendSignalFetcher`.
- `PaxInstitutionalSignalsDedup.java` — `shouldEmit(signal, nowMs, staleAgeMs)`: keyed by `signal.id`. Maintains LinkedHashMap LRU bounded at 64 entries. Reject if id already seen within `dedupWindowMs` (default 60_000 ms).

**Test:** `PaxInstitutionalSignalsDedupTest.java`
- same id within window blocked
- different ids both pass
- LRU evicts oldest after 64 entries
- stale signals (older than staleAgeMs) blocked

### Task 3.3: Painter

`PaxInstitutionalSignalsPainter.java` renders 5 marker types anchored at level price + signal timestamp:

| direction / signal_type | marker | colour |
|---|---|---|
| direction == LONG | triangle-up (filled) | green (0xFF2BD25B) |
| direction == SHORT | triangle-down (filled) | red (0xFFFF4D4D) |
| execution_read == WAIT_FOR_CONFIRM | hollow circle | yellow (0xFFE5C100) |
| execution_read == STAND_DOWN | square + diagonal | orange (0xFFFF9900) |
| signal_type == SCRATCH | X | gray (0xFFB0B0B0) |

Anchored via `PaxChartTimeCoords.epochMsToChartNanos(signal.timestamp_ms)` and `price` directly. NO mid-of-chart x.

### Task 3.4: Module wiring

`PaxOpeningRangeModule.java`:
- Spin up `PaxInstitutionalSignalsFetcher` alongside the existing fetchers.
- New `AtomicBoolean instSignalsDirty`.
- In `InstrumentState.shouldRepaint`, also consume `instSignalsDirty`.
- Painter call in `PaxPainter.update()`.
- Gate the existing `PaxTrendTrianglePainter.update()` on `showTrendTriangles` (default false).
- Gate the existing `publishNativeSignalMarkerIfNeeded` on `gateNativeMarkersOnInstitutional`: when true, native marker only fires if a matching `institutional_signal` is currently PAY_FOR_TRADE at the same level.

---

## Phase 4: Settings defaults + UI wiring

`PaxOpeningRangeUiSettings.java`:
- `showTrendTriangles` (default `false`) — legacy context-only.
- `showInstitutionalSignals` (default `true`).
- `gateNativeMarkersOnInstitutional` (default `true`).

No layout changes — checkbox rows mirror existing settings.

---

## Phase 5: Full verification

- [ ] `cd mcp-server && python -m pytest -q` — expect prior 569 + ~25 new = ~594.
- [ ] `cd pax-ai && python -m pytest -q` — expect 509 (no changes to pax-ai).
- [ ] `cd indicators\OpenRange; .\build.ps1 -SkipTests:$false` — JUnit 5 tests for new parser + dedup must all pass.
- [ ] **Manual smoke (snap inspection):**
  ```python
  python -c "
  from bookmap_mcp.dashboard import compute_institutional_signals
  snap = { ... a TOUCHED ACCEPTANCE setup ... }
  print(compute_institutional_signals(snap))
  "
  ```
  Verify the expected event object appears with correct id, direction, timestamp.
- [ ] Final report:
  - Files changed (~9 Java new/modified, 2 Python modified)
  - Backup path
  - Tests run (mcp-server, OpenRange Java JUnit)
  - Marker rules + colours
  - Remaining limitations (poll-cadence confirmation, no MBO queue position, etc.)
  - Confirmation: no live-order code touched

---

## Risk callouts

1. **PaxOpeningRangeSignalEngine native markers.** Gating native publishing on institutional alignment is a behavior change. The flag defaults ON, so without this change the native markers will continue firing. If you'd rather **NOT** touch the native engine at all (and accept dual marker layers — native + institutional — during transition), set `gateNativeMarkersOnInstitutional` default to `false` and tune by hand.

2. **`PaxTrendSignalSnapshotParser`'s "NEW PAX decision path"** (lines 84–117) re-encodes `pax.decision` into trend-triangle kinds. If `showTrendTriangles` defaults to `false` the painter won't render — but the parser still runs and consumes bandwidth. Cost is trivial; leaving it alone for now.

3. **Aggressor-flow alignment veto thresholds.** I'm using the existing `_thesis_aggressor_flow` output (WITH / AGAINST / MIXED / THIN). MIXED does NOT block direction by default — it passes through with reduced confidence. If you want MIXED to also block, say so before Phase 1 starts.

4. **Stop-sweep flip-direction semantics.** The spec says "STOP_SWEEP_LONG = sweep below OR-L failed → buy back" and "STOP_SWEEP_SHORT = sweep above OR-H failed → sell back". My mapper implements exactly that. The CONTINUATION-phase event uses the level side directly (sweep above OR-H during continuation → STOP_SWEEP_LONG, direction=NONE) — i.e., the signal_type matches the continuation direction, then on FAILURE the direction flips to opposite. If you prefer the signal_type to also flip on failure (e.g., continuation-phase ↑ above-OR-H → STOP_SWEEP_SHORT throughout), say so before Phase 1.

5. **Native-engine gating tests.** Java-side test coverage for the gated native-marker path requires mock state injection that the existing test infrastructure may not have. I'll write the gate logic + a unit test for the gate condition in isolation; an end-to-end native-marker integration test is out of scope for this plan.
