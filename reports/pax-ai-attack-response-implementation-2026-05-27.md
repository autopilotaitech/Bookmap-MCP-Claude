# Pax AI Attack-Response Implementation Report (2026-05-27)

Stages 1-7 implementation + Stage 8 final verification.

Plan reference: `reports/pax-ai-attack-response-plan-2026-05-27.md`.

ASCII only. WATCH only - no proven EDGE is claimed by this build.

## Hard warning up-front

**WATCH is not proven EDGE.** Every state row this build emits carries
`proven_edge=false`, `sample_n=null`, `edge_R_60s=null`. The closed
attack-response vocabulary is evidence the operator can read on the
chart; whether each (state, bias, level, OR width, etc.) bucket has
positive expectancy is a STATISTICAL question that requires accumulated
JSONL outcomes (Stage 5 + Stage 6). EDGE promotion is intentionally NOT
automated here - that would just rebrand a hand-tuned rules system as
"edge."

## Live verification

**Live verification was NOT possible in this session.** The bridge at
`127.0.0.1:8765` returned `WinError 10061 (connection refused)` and the
Pax AI server at `127.0.0.1:18891` was offline. The dashboard at
`127.0.0.1:18888/api/snapshot` returned its structured offline envelope
(health=offline). All work was done against the source + the test
fixtures.

Operator must run an end-to-end smoke once Bookmap, the MCP Bridge
addon, the dashboard process, and the Pax AI server are all online to
validate the chart-side glyph vocabulary and (optionally) the new
endpoint payload.

## Files changed / added

### Stage 1 (audit / plan; no code)
- ADD `reports/pax-ai-attack-response-plan-2026-05-27.md`

### Stage 2 (Java glyph vocabulary)
- MOD `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
  - Replaced `compactChartEventCode` body (~30 lines -> ~75 lines).
    Dispatches on `event_type` first; unknown types fall through to
    the legacy marker-text scan so prior test contracts hold.
  - Added `isAboveContext(evt)` static helper - the bid/ask classifier
    used by the new glyph table.
  - Added `attackResponseAccentColor(evt)` static helper - closed-
    palette accent (BULL / BEAR / SEVERITY_OUTLINE amber / STAND_DOWN
    gray; returns null for unknown event_types so the painter falls
    back to `evt.colorFromHint()`).
  - `drawInstitutionalChartEvent` now reads accent via the new helper
    with hint-fallback - preserves legacy event-type behavior.
- ADD `indicators/OpenRange/src/test/java/com/openrange/PaxAttackResponseGlyphTest.java`
  (28 assertions over the closed table + palette + sweep-is-amber rule).
- MOD `indicators/OpenRange/build.ps1` - registers the new test.

### Stage 3 (Python attack-response classifier)
- ADD `pax-ai/pax_ai/attack_response.py` (~420 lines, pure function).
- ADD `pax-ai/tests/test_attack_response.py` (32 tests).

### Stage 4 (HTTP endpoint)
- MOD `pax-ai/pax_ai/server.py`:
  - Imports `attack_response`, `attack_response_log`.
  - New `_api_pax_attack_response()` -> 503 cold, 200 with payload.
  - New route `/api/pax/attack-response` wired into `do_GET`.
- ADD `pax-ai/tests/test_attack_response_endpoint.py` (9 tests).

### Stage 5 (Append-only JSONL logger)
- ADD `pax-ai/pax_ai/attack_response_log.py` (~270 lines).
- ADD `pax-ai/tests/test_attack_response_log.py` (13 tests).
- MOD `pax-ai/pax_ai/server.py` - endpoint now calls
  `attack_response_log.record_payload_safe(body, snap)` after compute.
  Safe wrapper guarantees disk hiccups never 500 the endpoint.

### Stage 6 (Daily report CLI)
- ADD `pax-ai/pax_ai/attack_response_report.py` (~250 lines).
- ADD `pax-ai/tests/test_attack_response_report.py` (15 tests).

### Stage 7 (Optional Java label painter)
- ADD `indicators/OpenRange/src/main/java/com/openrange/PaxAttackResponseModel.java`
- ADD `indicators/OpenRange/src/main/java/com/openrange/PaxAttackResponseSnapshotParser.java`
- ADD `indicators/OpenRange/src/main/java/com/openrange/PaxAttackResponseLabelPainter.java`
- ADD `indicators/OpenRange/src/test/java/com/openrange/PaxAttackResponseParserAndPainterTest.java`
  (20 assertions including parser safety defaults + label vocabulary).
- MOD `indicators/OpenRange/build.ps1` - registers the new test.

### Pre-existing changes (left in place from earlier session)
- DEL `indicators/OpenRange/src/main/java/com/openrange/PaxChartLayerRegistry.java`
- DEL `indicators/OpenRange/src/test/java/com/openrange/PaxChartLayerRegistryTest.java`
- Earlier `build.ps1` change dropped its `Invoke-JavaTest` call.
- ADD (earlier session) `reports/pax-ai-edge-and-bloat-audit-2026-05-27.md`.

## What was NOT changed

- `C:\Bookmap\addons\openrange-release.jar` (canonical install). Size
  stays 209,602 bytes, mtime 05/27/2026 13:36:47. Untouched.
- `mcp-server/bookmap_mcp/dashboard.py` - the Python composition that
  emits `institutional_chart_events` is intact. The new attack-response
  layer is a CONSUMER of that array, not a competitor.
- The MCP bridge Java addon. No live trading code. No order execution.
- No new UI toggle for showInstitutionalChartEvents - the Stage 2
  vocabulary change rides the existing toggle.
- Stage 7's Java pieces (model + parser + painter) are NOT wired into
  the live `PaxOpeningRangeModule` chart pipeline in this build. Wiring
  would require a `PaxAttackResponseFetcher` (HTTP daemon analogous to
  `PaxLevelEdgeFetcher`) + a repaint-key term + an `addAttackResponseLabels`
  method on the painter. That coupling needs visual verification against
  a running Pax AI server; I could not do that in this session.

  Follow-up work for the next session (with the bridge online):
  1. Add `PaxAttackResponseFetcher` mirroring `PaxLevelEdgeFetcher`.
  2. Add `boolean showAttackResponseLabels` to
     `PaxOpeningRangeUiSettings` (default true once the operator has
     visually approved the label size).
  3. Add `addAttackResponseLabels(state)` called from
     `updateTriangles` when the toggle is on; staggers labels above /
     below each row's `level_price`.
  4. Extend `triangleRenderKey` to include attack-response model
     signature so the painter redraws when the active set changes.

## Tests run

### Python (`pax-ai`)
```
cd C:\Bookmap\addons\MCP\Bookmap\pax-ai
python -m pytest -q
953 passed, 4 warnings in 9.61s
```

Of which the new tests are:
- `tests/test_attack_response.py` - 32 tests
- `tests/test_attack_response_endpoint.py` - 9 tests
- `tests/test_attack_response_log.py` - 13 tests
- `tests/test_attack_response_report.py` - 15 tests

Total new Python tests: 69. All green.

### Byte-compile sanity
```
python -m compileall -q bookmap_mcp  (mcp-server) - clean
python -m compileall -q pax_ai       (pax-ai)     - clean
```

### Java (OpenRange)
```
cd C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange
.\build.ps1
```
All tests OK. Two new test classes:
- `PaxAttackResponseGlyphTest` (Stage 2) - 28 sub-asserts.
- `PaxAttackResponseParserAndPainterTest` (Stage 7) - 20 sub-asserts.

Staged jar produced + hygiene invariant passed.

### Endpoint smoke
Blocked - Pax AI server offline this session. To smoke-test once the
operator restarts the chain:
```
curl http://127.0.0.1:18891/api/pax/attack-response
```
Expected shape: `{alias, asOfMs, health, states: [...], blocked: {...}}`.
With a known SWEEP_LOW + bid iceberg snapshot, exactly one
`OR_L_SWEEP_RECLAIM / BULL_WATCH` state should appear.

## Staged jar

- Path: `C:\Bookmap\addons-staging\openrange\openrange-release.jar`
- Size: 205,669 bytes (was 190,170 before Stage 7; was 188,937 before
  Stage 2).
- SHA-256: `1080e08a29a15e63aab4089dac74051247fe3596a46f906ecbed788191ccdcd9`
- Hygiene: `C:\Bookmap\addons` contains exactly one
  `openrange*.jar` - the canonical 209,602 byte install. No strays.

The operator deploys MANUALLY by closing Bookmap and running:
```
Copy-Item -LiteralPath 'C:\Bookmap\addons-staging\openrange\openrange-release.jar' `
          -Destination 'C:\Bookmap\addons\openrange-release.jar' -Force
```
Re-open Bookmap. Verify the chart-event vocabulary now uses the
short-form glyphs (e.g. `L^AL68`, `Lv RS62`, sweeps render with amber
outline color rather than green/red, ICE-B / ABS-B paint green,
ICE-A / ABS-A paint red).

## Inspector checklist for the operator (post-restart)

1. Confirm Stage 2 glyphs:
   - Sweep marker (`SL` / `SH`) is amber.
   - Iceberg on the bid side (`BI`) is green; ask side (`AI`) is red.
   - ABSORPTION green for `BA`, red for `AA`.
   - STACKING green below price (`BS`), red above (`AS`).
   - ACCEPTANCE long `AL` (green up-arrow), short `AC-S` (red down).
2. Confirm `showInstitutionalChartEvents` still gates the whole layer
   (toggle off -> all evidence glyphs vanish).
3. Restart Pax AI server (`pax-ai-start.bat server`) and hit
   `/api/pax/attack-response`. Verify 503 cold, 200 with a snapshot.
4. Once live evidence accumulates in
   `%LOCALAPPDATA%\pax-ai\attack-response-log\YYYY-MM-DD.closed.jsonl`,
   run the daily report:
   ```
   python -m pax_ai.attack_response_report --date 2026-05-28
   ```
   The report text starts with `WATCH evidence only - NOT measured EDGE`.

## Rollback plan

Each stage is independent. Rollback by file:

- Stage 2: `git checkout HEAD --
  indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
  and delete `PaxAttackResponseGlyphTest.java`. Rebuild.
- Stage 3-6 (Python): delete `pax-ai/pax_ai/attack_response*.py` + the
  matching test files; revert the `server.py` route block + imports.
- Stage 7: delete `PaxAttackResponseModel.java`,
  `PaxAttackResponseSnapshotParser.java`,
  `PaxAttackResponseLabelPainter.java`, and the test. Rebuild.

The staged jar is reproducible by re-running `build.ps1`. The canonical
install at `C:\Bookmap\addons\openrange-release.jar` was never touched
in this session, so a "do nothing" rollback (delete the staging jar)
returns the system to the pre-session runtime state.

## Hard constraints honored

- [x] No commits made (operator policy: explicit commits only).
- [x] Canonical addon jar untouched.
- [x] Build to staging only.
- [x] No live trading code edited; no Pax AI Claude tools enabled.
- [x] No new HUD / box.
- [x] No gates lowered.
- [x] ASCII only in new source files.
- [x] Tests at every stage.
- [x] Every WATCH state explicit; no auto-promotion to EDGE.

End.
