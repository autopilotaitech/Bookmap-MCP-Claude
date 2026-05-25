# Pax AI -> Chart Signal Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every actionable Pax AI signal visible on the Bookmap chart with a clear source (AI vs LOCAL vs CTX), direction, level, price, and confidence, without breaking the existing local-anchored institutional chart events.

**Architecture:** Pax AI emits a hard-fenced structured signal block at the end of every chat response. `chat.py` parses + validates the block against the snapshot it just used, writes accepted signals to a shared JSONL store at `D:\BookmapLogs\pax-ai-chart-signals.jsonl`. The dashboard reads the JSONL on every `/api/snapshot` poll, TTL-filters, and emits `snap["pax_ai_chart_events"]` as a NEW top-level field parallel to `institutional_chart_events`. Java fetcher parses both arrays; the renderer styles AI markers with bright magenta/cyan + `AI` prefix while local markers keep their existing colors with a `LOC` prefix and context markers get `CTX`.

**User constraints (locked 2026-05-21, override anything below if they conflict):**
1. **Atomic JSONL append**: the writer must produce one complete UTF-8 JSON object per line in a single `write()` syscall, then flush. No partial / interleaved lines. The reader MUST skip any partial / malformed final line without failing the snapshot composition.
2. **Single internal Java marker model**: AI + local + ctx events normalize to the same `PaxInstitutionalChartEvent` shape and feed into a SINGLE combined render list. The collision allocator operates over the union of all sources, not per-source. Source-aware styling is applied per-event after layout.
3. **No auto-commits**. After each phase: run focused tests, summarize the diff + risk, wait for the operator's explicit "continue" before moving on. The `git commit` steps in this plan are reference templates — they execute only when the operator says so.

**Tech Stack:** Python 3.x (dashboard, Pax AI, pytest), Java 17 (OpenRange addon, Gradle, self-rolled assertion harness), JSONL inter-process transport, no third-party JSON deps on the Java side.

---

## Audit Findings — Source Contract

The current pipeline has FOUR signal channels. The first three already exist; the fourth (PAX_AI) is missing entirely.

| Source        | Producer                                                    | Snapshot field                             | Java target                                 | Actionable?                          | Today |
|---------------|-------------------------------------------------------------|--------------------------------------------|---------------------------------------------|--------------------------------------|-------|
| LOCAL_ANCHORED| `compute_institutional_signals` (`dashboard.py:1548`)        | `snap["institutional_signals"]`            | `PaxInstitutionalSignalEvent` + entry marker| YES (`PAY_FOR_TRADE` LONG/SHORT)     | OK    |
| LOCAL_ANCHORED| `compute_institutional_chart_events` (`dashboard.py:1984`)   | `snap["institutional_chart_events"]`       | `PaxInstitutionalChartEvent`                | partial (ACC/REJ are ENTRY)          | OK    |
| COMPOSITE     | `pax_decision` (`dashboard.py:4327`)                         | `snap["pax"]`                              | none (intentionally not plotted)            | informational                        | OK    |
| MICRO/TAPE/PS | `compute_institutional_chart_events`                          | merged into `institutional_chart_events`   | `PaxInstitutionalChartEvent` (source=...)   | context only                         | OK    |
| **PAX_AI**    | **Pax AI chat response** (`pax-ai/pax_ai/chat.py:198`)       | **MISSING**                                | **MISSING**                                 | **AI-originated execution_read**     | **GAP** |

### Why Pax AI cannot reach the chart today

- `chat.py::handle_chat_stream` writes the assistant text to two destinations: SSE -> floating window, and `journal.record("PAX", ...)` -> SQLite. Neither is consumed by `dashboard.py`'s `fetch_snapshot`.
- Pax AI is a separate process (port 18891) from the dashboard (port 18888). There is no shared in-memory state.
- The Java fetcher only parses three keys (`trend_signal`, `institutional_signals`, `institutional_chart_events`). Even if a snapshot field existed, the parser would not see it.

### Source-contract rules (after this plan ships)

1. **PAX_AI**: emitted ONLY when Pax AI returns a validated, fenced JSON block. Renders with `AI` prefix, magenta/cyan border, severity = `ENTRY|EXIT|WARNING|WATCH|INFO`. Carries `action`, `direction`, `label`, `price`, `confidence`, `reason`, `timestamp_ms`.
2. **LOCAL_ANCHORED**: existing `institutional_signals` + `institutional_chart_events` whose `source != "pax_ai"`. Renders with existing colors + `LOC` prefix.
3. **COMPOSITE**: `pax_decision` continues NOT to plot. Pinned by `test_pax_decision_alone_does_not_emit_chart_events`.
4. **CTX**: micro_events / tape_flow / pull_stack chart events with `direction == "NONE"`. Renders with muted gray/amber + `CTX` prefix.

### Architectural invariants this plan preserves

- `--tools ""` and `--max-turns 1` stay on every Claude CLI call. The new emission is post-hoc text parsing, NOT an agentic tool path.
- No new Pax AI HTTP routes that mutate trading state. The JSONL writer is the single side effect.
- Existing negative tests (`test_trend_signal_alone_does_not_emit_chart_events`, `test_pax_decision_alone_does_not_emit_chart_events`) MUST continue to pass.
- The Java side never parses prose. Only the fenced JSON block reaches the chart.

---

## File Structure

### New files

| Path                                                                                              | Responsibility                                                       |
|---------------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| `pax-ai/pax_ai/ai_chart_signal.py`                                                                | extract + validate the fenced AI signal block from PAX_text          |
| `pax-ai/pax_ai/ai_chart_signal_store.py`                                                          | append-only JSONL writer (`D:\BookmapLogs\pax-ai-chart-signals.jsonl`) |
| `pax-ai/tests/test_ai_chart_signal.py`                                                            | pytest contract for extract + validate                                |
| `pax-ai/tests/test_ai_chart_signal_store.py`                                                      | pytest contract for the writer                                        |
| `mcp-server/bookmap_mcp/pax_ai_chart_events.py`                                                   | dashboard-side reader: JSONL -> validated dicts                       |
| `mcp-server/tests/test_pax_ai_chart_events.py`                                                    | pytest contract for the reader + snapshot wiring                      |
| `indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsParseTest.java`                 | parser tests for the new field                                        |
| `indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsRenderTest.java`                | render-style tests (badge text, color, collision lane)                |

### Modified files

| Path                                                                                              | Change                                                                |
|---------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------|
| `pax-ai/pax_ai/prompts.py`                                                                        | append AI_SIGNAL emission contract to `_BASE_PREAMBLE`                |
| `pax-ai/pax_ai/chat.py`                                                                           | post-stream: parse fenced block, validate, push to store              |
| `mcp-server/bookmap_mcp/dashboard.py`                                                             | call reader from `_compose_alias_snapshot`, set `snap["pax_ai_chart_events"]` |
| `mcp-server/bookmap_mcp/signal_engine.py`                                                         | re-export the new dashboard helper                                    |
| `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalSnapshotParser.java`              | `parsePaxAiChartEvents(json)` returning `List<PaxInstitutionalChartEvent>` with `source="pax_ai"` |
| `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalFetcher.java`                     | parse the new array each poll into `AtomicReference<List<PaxInstitutionalChartEvent>> latestPaxAiChartEvents` |
| `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalChartEvent.java`                | (no field changes — `source` already carries the AI marker)           |
| `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`                     | renderer: branch on `source` for badge text + color; extend collision lane key; diagnostics: add AI history count |
| `indicators/OpenRange/src/main/java/com/openrange/PaxInstitutionalChartEventsHistory.java`        | (no change; the same history class holds AI events, scope tag via `source`) |
| `indicators/OpenRange/src/test/java/com/openrange/PaxInstitutionalChartEventsParseTest.java`      | extend existing test to assert `source="institutional_thesis"` parse  |
| `mcp-server/tests/test_institutional_chart_events.py`                                              | add positive `test_pax_ai_emission_does_NOT_pollute_institutional_chart_events` (negative-pollution guard) |

### Java class naming

- The renderer dispatches on `evt.source` (already present on `PaxInstitutionalChartEvent`). No new model class.
- Reuse `PaxInstitutionalChartEvent` for AI events as well. The renderer reads `source` and chooses the marker text prefix (`AI` / `LOC` / `CTX`) plus the color.

---

## Phase 0 — Backup

### Task 0.1: Snapshot pre-state into `_phase_backups/`

**Files:** Create directory tree under `_phase_backups/pre_pax_ai_chart_signals_<YYYYMMDD_HHMMSS>/`.

- [ ] **Step 1: Compute timestamp + create backup dir**

```powershell
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$dst = "C:\Bookmap\addons\MCP\Bookmap\_phase_backups\pre_pax_ai_chart_signals_$ts"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
```

- [ ] **Step 2: Copy every file in the "Modified files" table preserving relative path**

```powershell
$root = 'C:\Bookmap\addons\MCP\Bookmap'
$rels = @(
  'pax-ai\pax_ai\prompts.py',
  'pax-ai\pax_ai\chat.py',
  'mcp-server\bookmap_mcp\dashboard.py',
  'mcp-server\bookmap_mcp\signal_engine.py',
  'indicators\OpenRange\src\main\java\com\openrange\PaxTrendSignalSnapshotParser.java',
  'indicators\OpenRange\src\main\java\com\openrange\PaxTrendSignalFetcher.java',
  'indicators\OpenRange\src\main\java\com\openrange\PaxOpeningRangeModule.java',
  'indicators\OpenRange\src\test\java\com\openrange\PaxInstitutionalChartEventsParseTest.java',
  'mcp-server\tests\test_institutional_chart_events.py'
)
foreach ($r in $rels) {
  $src = Join-Path $root $r
  $dstFile = Join-Path $dst $r
  New-Item -ItemType Directory -Force -Path (Split-Path $dstFile) | Out-Null
  Copy-Item -Path $src -Destination $dstFile
}
```

- [ ] **Step 3: Write BACKUP_MANIFEST.md with the file list + rollback snippet**

```powershell
$manifest = Join-Path $dst 'BACKUP_MANIFEST.md'
@"
# Backup manifest — Pax AI chart signals pipeline

Created: $ts

Files snapshotted (paths relative to repo root):
$(($rels | ForEach-Object { "- $_" }) -join "`n")

## Rollback
``````powershell
`$root = 'C:\Bookmap\addons\MCP\Bookmap'
`$src  = '$dst'
Get-ChildItem -Recurse -File `$src | ForEach-Object {
  `$rel = `$_.FullName.Substring(`$src.Length + 1)
  if (`$rel -eq 'BACKUP_MANIFEST.md') { return }
  Copy-Item -Path `$_.FullName -Destination (Join-Path `$root `$rel) -Force
}
``````
"@ | Out-File -FilePath $manifest -Encoding utf8
```

- [ ] **Step 4: Verify the backup is non-empty**

```powershell
(Get-ChildItem -Recurse -File $dst).Count
```

Expected: `>= 10` (9 source files + manifest).

---

## Phase 1 — Python: AI chart signal extract + validate

### Task 1.1: Define the AI emission contract + extractor

**Files:**
- Create: `pax-ai/pax_ai/ai_chart_signal.py`
- Test: `pax-ai/tests/test_ai_chart_signal.py`

**Emission contract (added to `_BASE_PREAMBLE` in Task 2.1):**

```
AI CHART SIGNAL (optional, at most one per response, last line block only):
- When you are confident in an actionable read, end your response with EXACTLY:
  <<PAX_AI_CHART_SIGNAL>>
  {"action":"PAY_FOR_TRADE|WAIT_FOR_CONFIRM|STAND_DOWN|SCRATCH_READY",
   "direction":"LONG|SHORT|NONE",
   "label":"OR-H|OR-L|+1|+2|+3|-1|-2|-3",
   "price":<float, must equal that level's price in the snapshot>,
   "confidence":<float in [0.0, 1.0]>,
   "reason":"<short text>"}
  <<END>>
- Emit NO block if you cannot ground every field in the current snapshot.
- The block is silent context for the chart, not a directive — the trader still sees your prose above.
```

- [ ] **Step 1: Write failing test for the extractor**

`pax-ai/tests/test_ai_chart_signal.py`:

```python
from pax_ai.ai_chart_signal import extract_block

def test_extracts_well_formed_block():
    pax_text = (
        "OR-H acceptance with WITH flow.\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"acceptance + WITH"}\n'
        "<<END>>\n"
    )
    blk = extract_block(pax_text)
    assert blk is not None
    assert blk["action"] == "PAY_FOR_TRADE"
    assert blk["direction"] == "LONG"
    assert blk["label"] == "OR-H"
    assert blk["price"] == 20000.0
    assert blk["confidence"] == 0.72
    assert blk["reason"] == "acceptance + WITH"

def test_no_block_returns_none():
    assert extract_block("plain prose with no block") is None

def test_malformed_json_returns_none():
    assert extract_block("<<PAX_AI_CHART_SIGNAL>>\n{not json\n<<END>>") is None

def test_only_last_block_wins():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"WAIT_FOR_CONFIRM","direction":"NONE","label":"OR-H",'
        '"price":20000.0,"confidence":0.40,"reason":"early"}\n'
        "<<END>>\n"
        "Update: ACCEPTED.\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"confirmed"}\n'
        "<<END>>"
    )
    blk = extract_block(txt)
    assert blk["action"] == "PAY_FOR_TRADE"
    assert blk["confidence"] == 0.72

def test_missing_required_field_returns_none():
    txt = (
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG"}\n'
        "<<END>>"
    )
    assert extract_block(txt) is None
```

- [ ] **Step 2: Run to confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal.py -v
```

Expected: ImportError / FAIL.

- [ ] **Step 3: Implement `extract_block`**

`pax-ai/pax_ai/ai_chart_signal.py`:

```python
"""Extract + validate a structured chart-signal block from a Pax AI response.

The block is hard-fenced:
    <<PAX_AI_CHART_SIGNAL>>
    {...json...}
    <<END>>

Multiple blocks: only the LAST well-formed one wins.
Missing required field, non-finite price, out-of-range confidence -> None.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

_REQUIRED = ("action", "direction", "label", "price", "confidence", "reason")
_ACTIONS = ("PAY_FOR_TRADE", "WAIT_FOR_CONFIRM", "STAND_DOWN", "SCRATCH_READY")
_DIRECTIONS = ("LONG", "SHORT", "NONE")
_PATTERN = re.compile(
    r"<<PAX_AI_CHART_SIGNAL>>\s*(\{.*?\})\s*<<END>>",
    re.DOTALL,
)


def extract_block(pax_text: str) -> Optional[Dict[str, Any]]:
    if not pax_text:
        return None
    matches = list(_PATTERN.finditer(pax_text))
    for m in reversed(matches):
        try:
            blk = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(blk, dict):
            continue
        if not all(k in blk for k in _REQUIRED):
            continue
        if blk["action"] not in _ACTIONS:
            continue
        if blk["direction"] not in _DIRECTIONS:
            continue
        try:
            price = float(blk["price"])
            conf = float(blk["confidence"])
        except (ValueError, TypeError):
            continue
        if not (price > 0.0) or not (0.0 <= conf <= 1.0):
            continue
        blk["price"] = price
        blk["confidence"] = conf
        return blk
    return None
```

- [ ] **Step 4: Run to confirm pass**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal.py -v
```

Expected: 5 passed.

### Task 1.2: Validate the extracted block against the snapshot it was emitted on

**Files:**
- Modify: `pax-ai/pax_ai/ai_chart_signal.py` — add `validate_against_snapshot(blk, snap)`.
- Modify: `pax-ai/tests/test_ai_chart_signal.py` — add validation tests.

- [ ] **Step 1: Write failing tests**

Add to `test_ai_chart_signal.py`:

```python
from pax_ai.ai_chart_signal import validate_against_snapshot

def _snap(or_h=20000.0, or_l=19950.0):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "or_levels": {
            "orHigh": or_h, "orLow": or_l,
            "levels": [
                {"label": "OR-H", "price": or_h, "side": "above"},
                {"label": "OR-L", "price": or_l, "side": "below"},
                {"label": "+1",   "price": or_h + 50.0, "side": "above"},
            ],
        },
    }

def test_validate_accepts_matching_level_and_price():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    assert validate_against_snapshot(blk, _snap()) is not None

def test_validate_rejects_unknown_label():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "+9",
           "price": 20450.0, "confidence": 0.50, "reason": "x"}
    assert validate_against_snapshot(blk, _snap()) is None

def test_validate_rejects_price_far_from_level():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20100.0, "confidence": 0.50, "reason": "x"}
    # OR-H = 20000; > 5 ticks (1.25p) away
    assert validate_against_snapshot(blk, _snap()) is None

def test_validate_returns_enriched_dict_with_id_alias_side():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    out = validate_against_snapshot(blk, _snap())
    assert out["alias"] == "NQM6.CME@RITHMIC"
    assert out["side"] == "above"
    assert out["id"]  # deterministic, non-empty
    assert "timestamp_ms" in out

def test_validate_rejects_offline_snapshot():
    blk = {"action": "PAY_FOR_TRADE", "direction": "LONG", "label": "OR-H",
           "price": 20000.0, "confidence": 0.72, "reason": "x"}
    snap = _snap(); snap["health"] = "offline"
    assert validate_against_snapshot(blk, snap) is None
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal.py -v
```

- [ ] **Step 3: Add `validate_against_snapshot`**

Append to `pax-ai/pax_ai/ai_chart_signal.py`:

```python
import hashlib
import time

_PRICE_TOLERANCE_TICKS = 5
_TICK_SIZE_NQ = 0.25


def validate_against_snapshot(blk: Dict[str, Any],
                              snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(blk, dict) or not isinstance(snap, dict):
        return None
    if snap.get("health") != "ok":
        return None
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    target_label = blk.get("label")
    match = next((L for L in levels if isinstance(L, dict)
                  and L.get("label") == target_label), None)
    if match is None:
        return None
    lvl_price = match.get("price")
    if not isinstance(lvl_price, (int, float)) or lvl_price <= 0.0:
        return None
    if abs(float(blk["price"]) - float(lvl_price)) > _PRICE_TOLERANCE_TICKS * _TICK_SIZE_NQ:
        return None
    alias = snap.get("alias") or ""
    ts_ms = int(time.time() * 1000)
    id_seed = f"{alias}|{target_label}|{blk['action']}|{blk['direction']}|{ts_ms // 1000}"
    sig_id = "pax_ai|" + hashlib.sha1(id_seed.encode("utf-8")).hexdigest()[:16]
    return {
        "id": sig_id,
        "alias": alias,
        "label": target_label,
        "price": float(lvl_price),
        "side": match.get("side") or ("above" if blk["direction"] == "LONG" else "below"),
        "action": blk["action"],
        "direction": blk["direction"],
        "confidence": float(blk["confidence"]),
        "reason": str(blk.get("reason") or "")[:240],
        "timestamp_ms": ts_ms,
        "source": "pax_ai",
    }
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal.py -v
```

Expected: 10 passed.

### Task 1.3: JSONL store

**Files:**
- Create: `pax-ai/pax_ai/ai_chart_signal_store.py`
- Test: `pax-ai/tests/test_ai_chart_signal_store.py`

- [ ] **Step 1: Write failing tests**

`pax-ai/tests/test_ai_chart_signal_store.py`:

```python
import json
import os
from pathlib import Path
from pax_ai.ai_chart_signal_store import append_signal, read_active, DEFAULT_TTL_SEC

def test_append_then_read(tmp_path):
    p = tmp_path / "store.jsonl"
    sig = {"id": "a", "alias": "X", "label": "OR-H", "price": 1.0,
           "side": "above", "action": "PAY_FOR_TRADE", "direction": "LONG",
           "confidence": 0.5, "reason": "r", "timestamp_ms": 0, "source": "pax_ai"}
    append_signal(sig, store_path=p)
    out = read_active(store_path=p, now_ms=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"

def test_ttl_drops_expired(tmp_path):
    p = tmp_path / "store.jsonl"
    sig = {"id": "a", "alias": "X", "label": "OR-H", "price": 1.0,
           "side": "above", "action": "PAY_FOR_TRADE", "direction": "LONG",
           "confidence": 0.5, "reason": "r", "timestamp_ms": 0, "source": "pax_ai"}
    append_signal(sig, store_path=p)
    too_late = (DEFAULT_TTL_SEC + 10) * 1000
    assert read_active(store_path=p, now_ms=too_late) == []

def test_malformed_line_skipped(tmp_path):
    p = tmp_path / "store.jsonl"
    p.write_text('not json\n{"id":"a","alias":"X","label":"OR-H","price":1.0,'
                 '"side":"above","action":"PAY_FOR_TRADE","direction":"LONG",'
                 '"confidence":0.5,"reason":"r","timestamp_ms":0,"source":"pax_ai"}\n',
                 encoding="utf-8")
    out = read_active(store_path=p, now_ms=1)
    assert len(out) == 1
    assert out[0]["id"] == "a"

def test_dedup_keeps_newest_by_id(tmp_path):
    p = tmp_path / "store.jsonl"
    sig_a = {"id": "a", "alias": "X", "label": "OR-H", "price": 1.0,
             "side": "above", "action": "PAY_FOR_TRADE", "direction": "LONG",
             "confidence": 0.4, "reason": "old", "timestamp_ms": 0, "source": "pax_ai"}
    sig_b = dict(sig_a, confidence=0.8, reason="new", timestamp_ms=100)
    append_signal(sig_a, store_path=p)
    append_signal(sig_b, store_path=p)
    out = read_active(store_path=p, now_ms=200)
    assert len(out) == 1
    assert out[0]["confidence"] == 0.8
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal_store.py -v
```

- [ ] **Step 3: Implement store**

`pax-ai/pax_ai/ai_chart_signal_store.py`:

```python
"""Cross-process JSONL store for validated Pax AI chart signals.

Pax AI's chat handler appends one JSON line per accepted signal.
The dashboard process reads the file every snapshot poll, TTL-filters,
dedups by id (newest wins), and emits as snap["pax_ai_chart_events"].

File is append-only; nothing else trims it. The reader applies the TTL.
A daily prune job is OUT OF SCOPE for v1 (the file grows ~1KB/signal).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STORE_PATH = Path(r"D:\BookmapLogs\pax-ai-chart-signals.jsonl")
DEFAULT_TTL_SEC = 300       # 5 min
DEFAULT_MAX_ROWS = 200       # cap returned list


def append_signal(sig: Dict[str, Any],
                   store_path: Optional[Path] = None) -> None:
    """Atomic single-syscall append. The whole JSON line + trailing newline
    must reach the file in one OS-level write so a concurrent tailing
    reader never sees a partial row."""
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(sig, separators=(",", ":"), ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    flags = os.O_APPEND | os.O_WRONLY | os.O_CREAT
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    fd = os.open(str(path), flags, 0o644)
    try:
        os.write(fd, data)
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        os.close(fd)


def read_active(store_path: Optional[Path] = None,
                 now_ms: Optional[int] = None,
                 ttl_sec: int = DEFAULT_TTL_SEC,
                 max_rows: int = DEFAULT_MAX_ROWS) -> List[Dict[str, Any]]:
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    if not path.exists():
        return []
    cutoff_ms = (now_ms or 0) - (ttl_sec * 1000)
    by_id: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("timestamp_ms")
                if not isinstance(ts, (int, float)) or ts < cutoff_ms:
                    continue
                rid = row.get("id")
                if not rid:
                    continue
                prev = by_id.get(rid)
                if prev is None or row.get("timestamp_ms", 0) >= prev.get("timestamp_ms", 0):
                    by_id[rid] = row
    except OSError:
        return []
    rows = sorted(by_id.values(), key=lambda r: r.get("timestamp_ms", 0))
    return rows[-max_rows:]
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_ai_chart_signal_store.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit Phase 1**

```bash
git add pax-ai/pax_ai/ai_chart_signal.py pax-ai/pax_ai/ai_chart_signal_store.py \
        pax-ai/tests/test_ai_chart_signal.py pax-ai/tests/test_ai_chart_signal_store.py
git commit -m "pax-ai: add structured chart-signal extract + store"
```

---

## Phase 2 — Pax AI emission contract + chat-handler hook

### Task 2.1: Append AI_SIGNAL emission contract to `_BASE_PREAMBLE`

**Files:**
- Modify: `pax-ai/pax_ai/prompts.py:49-138` (the `_BASE_PREAMBLE` raw string).
- Test: extend `pax-ai/tests/test_prompts.py` with one assertion.

- [ ] **Step 1: Add failing test**

`pax-ai/tests/test_prompts.py` (append):

```python
def test_base_preamble_includes_ai_chart_signal_contract():
    from pax_ai.prompts import render_system_prompt
    body = render_system_prompt()
    assert "<<PAX_AI_CHART_SIGNAL>>" in body
    assert "<<END>>" in body
    assert "action" in body
    assert "PAY_FOR_TRADE" in body
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_prompts.py::test_base_preamble_includes_ai_chart_signal_contract -v
```

- [ ] **Step 3: Insert the contract just before the closing `Available Skill bodies` line in `_BASE_PREAMBLE`**

Locate this line in `pax-ai/pax_ai/prompts.py`:

```
Available Skill bodies follow. Use them as authoritative reference for
their respective topics.
```

Insert ABOVE that line:

```
AI CHART SIGNAL (optional, at most ONE per response, must be the LAST block)
- When you are confident in an actionable read AND every field can be grounded
  in the current snapshot, end your response with EXACTLY:
    <<PAX_AI_CHART_SIGNAL>>
    {"action":"PAY_FOR_TRADE|WAIT_FOR_CONFIRM|STAND_DOWN|SCRATCH_READY",
     "direction":"LONG|SHORT|NONE",
     "label":"OR-H|OR-L|+1|+2|+3|-1|-2|-3",
     "price":<float, must equal that level's snapshot price>,
     "confidence":<float in [0.0, 1.0]>,
     "reason":"<<= 240 chars>"}
    <<END>>
- Emit NO block if any field cannot be grounded. Prose only is fine.
- The block is silent context for the chart, not a directive — your prose still
  drives the trader's read. Do NOT include the block inside markdown code fences.

```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_prompts.py -v
```

### Task 2.2: Wire chat.py post-stream parser + store

**Files:**
- Modify: `pax-ai/pax_ai/chat.py` — after the `feature_bus.record_ai_turn` call.
- Test: `pax-ai/tests/test_chat_handler.py` — new test with the existing journal-isolation fixture.

- [ ] **Step 1: Write failing test**

`pax-ai/tests/test_chat_handler.py` (append):

```python
def test_chat_post_stream_appends_ai_chart_signal(tmp_path, monkeypatch):
    """A Pax AI response with a well-formed block + valid snapshot must
    land in the AI-chart-signal store."""
    from pax_ai import chat as chat_mod
    from pax_ai import ai_chart_signal_store as store
    # Redirect the store path.
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(store, "DEFAULT_STORE_PATH", p)
    # Build a synthetic snapshot the validator will accept.
    snap = {
        "alias": "NQM6.CME@RITHMIC", "health": "ok",
        "or_levels": {"orHigh": 20000.0, "orLow": 19950.0,
                       "levels": [{"label": "OR-H", "price": 20000.0, "side": "above"}]},
    }
    pax_text = (
        "OR-H accepted with WITH flow.\n"
        "<<PAX_AI_CHART_SIGNAL>>\n"
        '{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",'
        '"price":20000.0,"confidence":0.72,"reason":"acceptance"}\n'
        "<<END>>"
    )
    chat_mod._capture_ai_chart_signal(pax_text=pax_text, snap=snap)
    out = store.read_active(store_path=p, now_ms=int(__import__("time").time() * 1000))
    assert len(out) == 1
    assert out[0]["action"] == "PAY_FOR_TRADE"
    assert out[0]["label"] == "OR-H"

def test_chat_post_stream_no_block_no_write(tmp_path, monkeypatch):
    from pax_ai import chat as chat_mod
    from pax_ai import ai_chart_signal_store as store
    p = tmp_path / "store.jsonl"
    monkeypatch.setattr(store, "DEFAULT_STORE_PATH", p)
    snap = {"alias": "X", "health": "ok",
             "or_levels": {"levels": [{"label": "OR-H", "price": 1.0, "side": "above"}]}}
    chat_mod._capture_ai_chart_signal(pax_text="plain prose only", snap=snap)
    assert not p.exists() or store.read_active(store_path=p, now_ms=10**14) == []
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_chat_handler.py -v
```

- [ ] **Step 3: Add `_capture_ai_chart_signal` to `chat.py` and call it after feature_bus capture**

Add at module level in `pax-ai/pax_ai/chat.py`:

```python
def _capture_ai_chart_signal(pax_text, snap):
    """Best-effort: extract the structured chart-signal block from a Pax
    AI response, validate against the snapshot it was emitted on, append
    to the cross-process JSONL store. Failure paths are silent — chart
    plumbing is not allowed to break the chat path."""
    try:
        from . import ai_chart_signal
        from . import ai_chart_signal_store
        blk = ai_chart_signal.extract_block(pax_text or "")
        if blk is None:
            return
        validated = ai_chart_signal.validate_against_snapshot(blk, snap or {})
        if validated is None:
            return
        ai_chart_signal_store.append_signal(validated)
    except Exception as exc:
        sys.stderr.write(f"[chat] ai_chart_signal capture failed: {exc}\n")
```

Then, inside `handle_chat_stream`, AFTER the `feature_bus.record_ai_turn(rec)` call (around `chat.py:413`), add:

```python
        # Pax AI -> chart marker bridge. Validates against the snapshot
        # the digest was built from (NOT a fresh poll).
        _capture_ai_chart_signal(pax_text, meta.get("_snapshot_for_capture"))
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai && python -m pytest tests/test_chat_handler.py -v
```

- [ ] **Step 5: Commit Phase 2**

```bash
git add pax-ai/pax_ai/prompts.py pax-ai/pax_ai/chat.py pax-ai/tests/test_prompts.py pax-ai/tests/test_chat_handler.py
git commit -m "pax-ai: emit + capture structured chart-signal block"
```

---

## Phase 3 — Dashboard reader + snapshot field

### Task 3.1: Reader module + snapshot wiring

**Files:**
- Create: `mcp-server/bookmap_mcp/pax_ai_chart_events.py`
- Modify: `mcp-server/bookmap_mcp/dashboard.py` — call into reader from `_compose_alias_snapshot`.
- Modify: `mcp-server/bookmap_mcp/signal_engine.py` — re-export `read_pax_ai_chart_events`.
- Test: `mcp-server/tests/test_pax_ai_chart_events.py`

- [ ] **Step 1: Write failing test**

`mcp-server/tests/test_pax_ai_chart_events.py`:

```python
"""Pin compute_pax_ai_chart_events(snap, store_path) reader contract.

The dashboard reads the cross-process JSONL store written by Pax AI,
TTL-filters, dedupes by id, and emits snap["pax_ai_chart_events"]. The
returned list shape MIRRORS PaxInstitutionalChartEvent's required fields
so the Java parser can reuse PaxTrendSignalSnapshotParser.parseChartEvents
shape after the new array key is added.
"""
import json
import time
from pathlib import Path

from bookmap_mcp.pax_ai_chart_events import (
    read_pax_ai_chart_events,
    pax_ai_event_to_chart_event,
)


def _write_signal(p, **kw):
    base = dict(
        id="pax_ai|abc", alias="NQM6.CME@RITHMIC", label="OR-H",
        price=20000.0, side="above",
        action="PAY_FOR_TRADE", direction="LONG",
        confidence=0.72, reason="acc + WITH",
        timestamp_ms=int(time.time() * 1000),
        source="pax_ai",
    )
    base.update(kw)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(base) + "\n")


def test_returns_chart_events_in_required_shape(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p)
    evs = read_pax_ai_chart_events(store_path=p,
                                    now_ms=int(time.time() * 1000))
    assert len(evs) == 1
    e = evs[0]
    for f in ("id", "alias", "label", "price", "side", "event_type",
               "direction", "execution_read", "marker_text",
               "marker_color_hint", "severity", "timestamp_ms", "source",
               "confidence", "reason_codes"):
        assert f in e, f"missing field {f}"
    assert e["source"] == "pax_ai"
    assert e["event_type"] == "AI_ACCEPTANCE"
    assert e["direction"] == "LONG"
    assert e["execution_read"] == "PAY_FOR_TRADE"
    assert e["severity"] == "ENTRY"


def test_pay_for_trade_short_maps_to_ai_rejection_short(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|s1", direction="SHORT", side="below",
                  label="OR-L", price=19950.0)
    evs = read_pax_ai_chart_events(store_path=p,
                                    now_ms=int(time.time() * 1000))
    assert evs[0]["event_type"] == "AI_ACCEPTANCE"
    assert evs[0]["direction"] == "SHORT"
    assert evs[0]["marker_text"].startswith("AI")


def test_wait_for_confirm_maps_to_ai_watch_severity_watch(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|w1", action="WAIT_FOR_CONFIRM",
                  direction="NONE", confidence=0.4)
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000))[0]
    assert e["event_type"] == "AI_WATCH"
    assert e["severity"] == "WATCH"
    assert e["execution_read"] == "WAIT_FOR_CONFIRM"


def test_stand_down_maps_to_ai_warning(tmp_path):
    p = tmp_path / "store.jsonl"
    _write_signal(p, id="pax_ai|sd1", action="STAND_DOWN", direction="NONE",
                  reason="iceberg defending")
    e = read_pax_ai_chart_events(store_path=p,
                                  now_ms=int(time.time() * 1000))[0]
    assert e["event_type"] == "AI_STAND_DOWN"
    assert e["severity"] == "WARNING"


def test_no_file_returns_empty():
    evs = read_pax_ai_chart_events(store_path=Path("/no/such/path.jsonl"),
                                    now_ms=10**14)
    assert evs == []


def test_snapshot_wiring_attaches_pax_ai_chart_events_when_file_present(tmp_path, monkeypatch):
    """compute_pax_ai_chart_events should be hooked into fetch_snapshot
    via _compose_alias_snapshot. Smoke: when the store file has a row,
    a freshly composed snapshot must include the key."""
    from bookmap_mcp import pax_ai_chart_events as mod
    p = tmp_path / "store.jsonl"
    _write_signal(p)
    monkeypatch.setattr(mod, "DEFAULT_STORE_PATH", p)
    # Direct reader call (snapshot smoke is in dashboard test below).
    evs = mod.read_pax_ai_chart_events(store_path=p,
                                        now_ms=int(time.time() * 1000))
    assert evs and evs[0]["source"] == "pax_ai"
```

- [ ] **Step 2: Run, confirm fail**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_pax_ai_chart_events.py -v
```

- [ ] **Step 3: Implement reader**

`mcp-server/bookmap_mcp/pax_ai_chart_events.py`:

```python
"""Dashboard-side reader for Pax AI's structured chart signals.

Reads the JSONL file written by `pax-ai/pax_ai/ai_chart_signal_store.py`
(default `D:\\BookmapLogs\\pax-ai-chart-signals.jsonl`), TTL-filters,
dedupes by id (newest wins), and converts each row to a chart-event dict
compatible with the Java side's `PaxInstitutionalChartEvent` shape.

Mapping action -> event_type / severity:
  PAY_FOR_TRADE     -> AI_ACCEPTANCE,    severity ENTRY
  WAIT_FOR_CONFIRM  -> AI_WATCH,         severity WATCH
  STAND_DOWN        -> AI_STAND_DOWN,    severity WARNING
  SCRATCH_READY     -> AI_SCRATCH,       severity EXIT
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_STORE_PATH = Path(r"D:\BookmapLogs\pax-ai-chart-signals.jsonl")
DEFAULT_TTL_SEC = 300
DEFAULT_MAX_ROWS = 50

_ACTION_TO_EVENT_TYPE = {
    "PAY_FOR_TRADE":    "AI_ACCEPTANCE",
    "WAIT_FOR_CONFIRM": "AI_WATCH",
    "STAND_DOWN":       "AI_STAND_DOWN",
    "SCRATCH_READY":    "AI_SCRATCH",
}
_ACTION_TO_SEVERITY = {
    "PAY_FOR_TRADE":    "ENTRY",
    "WAIT_FOR_CONFIRM": "WATCH",
    "STAND_DOWN":       "WARNING",
    "SCRATCH_READY":    "EXIT",
}
_AI_BULL_COLOR    = "#FF40D9"   # magenta
_AI_BEAR_COLOR    = "#40E0FF"   # cyan
_AI_NEUTRAL_COLOR = "#A86DEC"   # purple


def pax_ai_event_to_chart_event(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict):
        return None
    action = row.get("action")
    if action not in _ACTION_TO_EVENT_TYPE:
        return None
    direction = row.get("direction") or "NONE"
    event_type = _ACTION_TO_EVENT_TYPE[action]
    severity = _ACTION_TO_SEVERITY[action]
    if direction == "LONG":
        color = _AI_BULL_COLOR
        prefix = "AI ▲ "
    elif direction == "SHORT":
        color = _AI_BEAR_COLOR
        prefix = "AI ▼ "
    else:
        color = _AI_NEUTRAL_COLOR
        prefix = "AI ◆ "
    conf = float(row.get("confidence") or 0.0)
    label = row.get("label") or "?"
    marker_text = f"{prefix}{label} {int(round(conf * 100))}"
    return {
        "id":                row.get("id") or "",
        "alias":             row.get("alias") or "",
        "label":             label,
        "price":             float(row.get("price") or 0.0),
        "side":              row.get("side") or ("above" if direction == "LONG" else "below"),
        "event_type":        event_type,
        "direction":         direction,
        "execution_read":    action,
        "marker_text":       marker_text,
        "marker_color_hint": color,
        "severity":          severity,
        "timestamp_ms":      int(row.get("timestamp_ms") or 0),
        "source":            "pax_ai",
        "confidence":        conf,
        "reason_codes":      [str(row.get("reason") or "")[:240]],
        "invalidation_price": None,
        "payline_price":      None,
    }


def read_pax_ai_chart_events(store_path: Optional[Path] = None,
                              now_ms: Optional[int] = None,
                              ttl_sec: int = DEFAULT_TTL_SEC,
                              max_rows: int = DEFAULT_MAX_ROWS) -> List[Dict[str, Any]]:
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    if not path.exists():
        return []
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - (ttl_sec * 1000)
    by_id: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = row.get("timestamp_ms")
                if not isinstance(ts, (int, float)) or ts < cutoff:
                    continue
                rid = row.get("id")
                if not rid:
                    continue
                prev = by_id.get(rid)
                if prev is None or row.get("timestamp_ms", 0) >= prev.get("timestamp_ms", 0):
                    by_id[rid] = row
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for row in sorted(by_id.values(), key=lambda r: r.get("timestamp_ms", 0)):
        ev = pax_ai_event_to_chart_event(row)
        if ev is not None:
            out.append(ev)
    return out[-max_rows:]
```

- [ ] **Step 4: Wire into dashboard.py**

In `mcp-server/bookmap_mcp/dashboard.py`, near top with other imports, add:

```python
from .pax_ai_chart_events import read_pax_ai_chart_events
```

In `_compose_alias_snapshot`, after line 5070 (right after `institutional_chart_events` is set), add:

```python
        snap["pax_ai_chart_events"] = _safe_call(
            lambda s=snap: read_pax_ai_chart_events(now_ms=int(time.time() * 1000)),
            "pax_ai_chart_events") or []
```

(`_safe_call` signature in dashboard.py is `(fn, name)`. Adapt the wrapper call if it differs at HEAD.)

- [ ] **Step 5: Re-export from signal_engine.py**

In `mcp-server/bookmap_mcp/signal_engine.py`, in the import block (around line 173), add `read_pax_ai_chart_events`, and add it to `__all__` (around line 277).

- [ ] **Step 6: Run all reader + composer tests**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_pax_ai_chart_events.py tests/test_institutional_chart_events.py tests/test_institutional_signals_composer.py -v
```

Expected: all pass; existing negative tests still pass (no AI contamination of institutional_chart_events).

- [ ] **Step 7: Commit Phase 3**

```bash
git add mcp-server/bookmap_mcp/pax_ai_chart_events.py \
        mcp-server/bookmap_mcp/dashboard.py \
        mcp-server/bookmap_mcp/signal_engine.py \
        mcp-server/tests/test_pax_ai_chart_events.py
git commit -m "dashboard: surface snap['pax_ai_chart_events'] from JSONL store"
```

---

## Phase 4 — Java parser

### Task 4.1: Add `parsePaxAiChartEvents` to `PaxTrendSignalSnapshotParser`

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalSnapshotParser.java`
- Create: `indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsParseTest.java`

- [ ] **Step 1: Write failing test**

`PaxAiChartEventsParseTest.java`:

```java
package com.openrange;

import java.util.List;

public class PaxAiChartEventsParseTest {

    public static void main(String[] args) {
        parsesValidArray();
        emptyArrayYieldsEmpty();
        missingKeyYieldsEmpty();
        healthOfflineYieldsEmpty();
        sourceIsPaxAi();
        System.out.println("PaxAiChartEventsParseTest OK");
    }

    private static String paxAiEvent() {
        return "{"
            + "\"id\":\"pax_ai|abc\","
            + "\"alias\":\"NQM6.CME@RITHMIC\","
            + "\"label\":\"OR-H\","
            + "\"price\":20000.0,"
            + "\"side\":\"above\","
            + "\"event_type\":\"AI_ACCEPTANCE\","
            + "\"direction\":\"LONG\","
            + "\"execution_read\":\"PAY_FOR_TRADE\","
            + "\"marker_text\":\"AI \\u25B2 OR-H 72\","
            + "\"marker_color_hint\":\"#FF40D9\","
            + "\"severity\":\"ENTRY\","
            + "\"timestamp_ms\":1700000000000,"
            + "\"source\":\"pax_ai\","
            + "\"confidence\":0.72"
            + "}";
    }

    private static void parsesValidArray() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + paxAiEvent() + "]}";
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body);
        if (out.size() != 1) throw new AssertionError("size " + out.size());
        PaxInstitutionalChartEvent e = out.get(0);
        if (!"pax_ai".equals(e.source)) throw new AssertionError("source=" + e.source);
        if (!"LONG".equals(e.direction)) throw new AssertionError();
        if (Math.abs(e.confidence - 0.72) > 1e-6) throw new AssertionError();
        if (!e.isRenderable()) throw new AssertionError("not renderable");
    }

    private static void emptyArrayYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(
                    "{\"health\":\"ok\",\"pax_ai_chart_events\":[]}");
        if (!out.isEmpty()) throw new AssertionError();
    }

    private static void missingKeyYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents("{\"health\":\"ok\"}");
        if (!out.isEmpty()) throw new AssertionError();
    }

    private static void healthOfflineYieldsEmpty() {
        String body = "{\"health\":\"offline\",\"pax_ai_chart_events\":["
                + paxAiEvent() + "]}";
        if (!PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).isEmpty()) {
            throw new AssertionError();
        }
    }

    private static void sourceIsPaxAi() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + paxAiEvent() + "]}";
        PaxInstitutionalChartEvent e =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).get(0);
        if (!"pax_ai".equals(e.source)) throw new AssertionError();
    }
}
```

- [ ] **Step 2: Add the method to `PaxTrendSignalSnapshotParser` (mirror `parseChartEvents`, reading key `pax_ai_chart_events`)**

In `PaxTrendSignalSnapshotParser.java`, copy `parseChartEvents` body verbatim into a sibling `parsePaxAiChartEvents`, with two differences:
1. Read `rootMap.get("pax_ai_chart_events")` instead of `"institutional_chart_events"`.
2. Force `source = "pax_ai"` regardless of what the payload contains (defense-in-depth).

```java
static List<PaxInstitutionalChartEvent> parsePaxAiChartEvents(String json) {
    if (json == null || json.isEmpty()) {
        return java.util.Collections.emptyList();
    }
    Object root;
    try {
        root = new Tokenizer(json).parseValue(true);
    } catch (RuntimeException e) {
        return java.util.Collections.emptyList();
    }
    if (!(root instanceof Map)) {
        return java.util.Collections.emptyList();
    }
    Map<?, ?> rootMap = (Map<?, ?>) root;
    Object healthObj = rootMap.get("health");
    if (healthObj instanceof String && !"ok".equalsIgnoreCase((String) healthObj)) {
        return java.util.Collections.emptyList();
    }
    Object evsObj = rootMap.get("pax_ai_chart_events");
    if (!(evsObj instanceof List)) {
        return java.util.Collections.emptyList();
    }
    List<?> evs = (List<?>) evsObj;
    ArrayList<PaxInstitutionalChartEvent> out = new ArrayList<>(evs.size());
    for (Object o : evs) {
        if (!(o instanceof Map)) continue;
        Map<?, ?> e = (Map<?, ?>) o;
        String id = asString(e.get("id"));
        String alias = asString(e.get("alias"));
        String label = asString(e.get("label"));
        Double priceObj = asDouble(e.get("price"));
        String side = asString(e.get("side"));
        String eventType = asString(e.get("event_type"));
        String direction = asString(e.get("direction"));
        String executionRead = asString(e.get("execution_read"));
        String markerText = asString(e.get("marker_text"));
        String markerColorHint = asString(e.get("marker_color_hint"));
        String severity = asString(e.get("severity"));
        long timestampMs = asLong(e.get("timestamp_ms"), 0L);
        Double confObj = asDouble(e.get("confidence"));
        double price = priceObj == null ? Double.NaN : priceObj.doubleValue();
        double confidence = confObj == null ? Double.NaN : confObj.doubleValue();
        out.add(new PaxInstitutionalChartEvent(
                id, alias, label, price, side, eventType, direction,
                executionRead, markerText, markerColorHint, severity,
                timestampMs, "pax_ai", confidence));
    }
    return out;
}
```

- [ ] **Step 3: Wire the test into build.ps1 (or wherever Java tests run)**

OpenRange's test harness runs each `*Test.java` file's `main`. Make sure `PaxAiChartEventsParseTest` is in the test source dir; existing build/run script already enumerates the directory.

- [ ] **Step 4: Run Java tests**

```powershell
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange'
.\build.ps1 -RunTests
```

Expected: `PaxAiChartEventsParseTest OK`; all prior tests still OK.

### Task 4.2: Fetcher reads + exposes the new array

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalFetcher.java`

- [ ] **Step 1: Add a new AtomicReference + accessor**

Near the existing `latestChartEvents` field (around line 45):

```java
private final AtomicReference<List<PaxInstitutionalChartEvent>> latestPaxAiChartEvents =
        new AtomicReference<>(Collections.emptyList());
```

Add accessor:

```java
List<PaxInstitutionalChartEvent> latestPaxAiChartEvents() {
    return latestPaxAiChartEvents.get();
}
```

- [ ] **Step 2: Populate inside `tickOnce` (after the existing two parse calls, ~line 186)**

```java
List<PaxInstitutionalChartEvent> paxAiEvents =
        PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(resp.body());
...
latestPaxAiChartEvents.set(paxAiEvents);
```

- [ ] **Step 3: Run all OpenRange tests**

```powershell
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange'
.\build.ps1 -RunTests
```

- [ ] **Step 4: Commit Phase 4**

```bash
git add indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalSnapshotParser.java \
        indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalFetcher.java \
        indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsParseTest.java
git commit -m "openrange: parse snap['pax_ai_chart_events'] into PaxInstitutionalChartEvent"
```

---

## Phase 5 — Java renderer: source-aware styling + collision

### Task 5.1: Module-level AI chart events history + render branch

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsRenderTest.java`

- [ ] **Step 1: Write failing test**

`PaxAiChartEventsRenderTest.java`:

```java
package com.openrange;

public class PaxAiChartEventsRenderTest {

    public static void main(String[] args) {
        aiAndLocalHaveDistinctCollisionLanes();
        aiBadgeTextHasAiPrefix();
        localBadgeTextHasLocPrefix();
        ctxBadgeTextHasCtxPrefix();
        denseClusterCollapsesContextFirst();
        System.out.println("PaxAiChartEventsRenderTest OK");
    }

    private static PaxInstitutionalChartEvent ev(String id, String source,
            String direction, double price, long ts, String label) {
        return new PaxInstitutionalChartEvent(id, "NQM6", label, price,
            "above", "AI_ACCEPTANCE", direction, "PAY_FOR_TRADE",
            "ignored", "#FF40D9", "ENTRY", ts, source, 0.72);
    }

    private static void aiAndLocalHaveDistinctCollisionLanes() {
        PaxInstitutionalChartEvent ai = ev("idAi", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H");
        PaxInstitutionalChartEvent loc = ev("idLoc", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H");
        String aLane = PaxOpeningRangeModule.chartEventCollisionKey(ai, 0.25);
        String lLane = PaxOpeningRangeModule.chartEventCollisionKey(loc, 0.25);
        if (aLane.equals(lLane)) {
            throw new AssertionError(
                "AI and LOCAL markers at same price/time must occupy DIFFERENT lanes");
        }
    }

    private static void aiBadgeTextHasAiPrefix() {
        PaxInstitutionalChartEvent ai = ev("idAi", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H");
        String text = PaxOpeningRangeModule.chartEventRenderText(ai);
        if (!text.startsWith("AI ")) {
            throw new AssertionError("AI marker must start with 'AI '; got " + text);
        }
    }

    private static void localBadgeTextHasLocPrefix() {
        PaxInstitutionalChartEvent loc = ev("idL", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H");
        String text = PaxOpeningRangeModule.chartEventRenderText(loc);
        if (!text.startsWith("LOC ")) {
            throw new AssertionError("LOC marker must start with 'LOC '; got " + text);
        }
    }

    private static void ctxBadgeTextHasCtxPrefix() {
        PaxInstitutionalChartEvent ctx = ev("idC", "micro_events",
                "NONE", 20000.0, 5_000L, "OR-H");
        String text = PaxOpeningRangeModule.chartEventRenderText(ctx);
        if (!text.startsWith("CTX ")) {
            throw new AssertionError("CTX marker must start with 'CTX '; got " + text);
        }
    }

    private static void denseClusterCollapsesContextFirst() {
        // 6 markers at the same lane: 2 AI ENTRY, 2 LOC ENTRY, 2 CTX WATCH.
        // Cap at 4 visible. The two CTX markers must be the ones collapsed.
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(ev("a1", "pax_ai",                "LONG", 20000.0, 5_000L, "OR-H"));
        in.add(ev("a2", "pax_ai",                "LONG", 20000.0, 5_000L, "OR-H"));
        in.add(ev("l1", "institutional_thesis",  "LONG", 20000.0, 5_000L, "OR-H"));
        in.add(ev("l2", "institutional_thesis",  "LONG", 20000.0, 5_000L, "OR-H"));
        in.add(new PaxInstitutionalChartEvent("c1", "NQM6", "OR-H", 20000.0,
            "above", "WATCH_LEVEL", "NONE", "WAIT_FOR_CONFIRM", "WATCH",
            "#B0B0B0", "WATCH", 5_000L, "micro_events", 0.35));
        in.add(new PaxInstitutionalChartEvent("c2", "NQM6", "OR-H", 20000.0,
            "above", "WATCH_LEVEL", "NONE", "WAIT_FOR_CONFIRM", "WATCH",
            "#B0B0B0", "WATCH", 5_000L, "micro_events", 0.35));
        java.util.List<PaxInstitutionalChartEvent> kept =
                PaxOpeningRangeModule.collapseDenseCluster(in, 4);
        if (kept.size() != 4) throw new AssertionError("expected 4, got " + kept.size());
        boolean allEntry = true;
        for (PaxInstitutionalChartEvent e : kept) {
            if (!"ENTRY".equals(e.severity)) { allEntry = false; break; }
        }
        if (!allEntry) {
            throw new AssertionError("dense cluster must keep ENTRY markers over CTX/WATCH");
        }
    }
}
```

- [ ] **Step 2: Implement static helpers `chartEventRenderText` and `collapseDenseCluster`, and extend `chartEventCollisionKey` to include `source`**

In `PaxOpeningRangeModule.java`:

(a) Change `chartEventCollisionKey` (line 2311) to include source:

```java
static String chartEventCollisionKey(PaxInstitutionalChartEvent evt, double tickSize) {
    if (evt == null) return "NULL";
    long timeBucket = evt.timestampMs / 1000L;
    boolean placeBelow = chartEventPlaceBelow(evt);
    long priceTicks;
    if (Double.isFinite(evt.price) && evt.price > 0.0
            && Double.isFinite(tickSize) && tickSize > 0.0) {
        priceTicks = Math.round(evt.price / tickSize);
    } else {
        priceTicks = Long.MIN_VALUE;
    }
    long priceBucket = priceTicks == Long.MIN_VALUE
            ? Long.MIN_VALUE
            : Math.floorDiv(priceTicks, CHART_EVENT_COLLISION_PRICE_TICKS);
    // Source-aware lane: AI markers must not collide with LOCAL/CTX at the
    // same price/time. The collision detector groups identical (time, side,
    // price, sourceBucket) markers together; different sourceBucket = different
    // lane.
    String sourceBucket = chartEventSourceBucket(evt);
    return timeBucket + "|" + (placeBelow ? "B" : "A") + "|"
            + priceBucket + "|" + sourceBucket;
}

static String chartEventSourceBucket(PaxInstitutionalChartEvent evt) {
    if (evt == null) return "?";
    String src = evt.source == null ? "" : evt.source;
    if ("pax_ai".equals(src)) return "AI";
    if (src.startsWith("institutional")) return "LOC";
    return "CTX";
}
```

(Existing test `chartEventCollisionBucketIgnoresLabelText` expects identical AI/AI markers in the same lane and identical LOC/LOC markers in the same lane — verify those still pass.)

(b) Add `chartEventRenderText`:

```java
static String chartEventRenderText(PaxInstitutionalChartEvent evt) {
    if (evt == null) return "";
    String prefix;
    String src = chartEventSourceBucket(evt);
    switch (src) {
        case "AI":  prefix = "AI ";  break;
        case "LOC": prefix = "LOC "; break;
        default:    prefix = "CTX "; break;
    }
    String arrow;
    if ("LONG".equals(evt.direction))       arrow = "▲ "; // ▲
    else if ("SHORT".equals(evt.direction)) arrow = "▼ "; // ▼
    else                                     arrow = "◆ "; // ◆
    int conf = Double.isFinite(evt.confidence)
            ? Math.max(0, Math.min(100, (int) Math.round(evt.confidence * 100.0)))
            : 0;
    String label = evt.label == null || evt.label.isEmpty() ? "?" : evt.label;
    return prefix + arrow + label + " " + conf;
}
```

(c) Add `collapseDenseCluster`:

```java
static java.util.List<PaxInstitutionalChartEvent> collapseDenseCluster(
        java.util.List<PaxInstitutionalChartEvent> events, int maxVisible) {
    if (events == null || events.size() <= maxVisible) {
        return events == null ? java.util.Collections.emptyList()
                              : new java.util.ArrayList<>(events);
    }
    java.util.List<PaxInstitutionalChartEvent> copy = new java.util.ArrayList<>(events);
    copy.sort((a, b) -> Integer.compare(a.severityRank(), b.severityRank()));
    return new java.util.ArrayList<>(copy.subList(0, maxVisible));
}
```

(d) Update the renderer's `drawInstitutionalChartEvent` to use `chartEventRenderText(evt)` as its marker text instead of `evt.markerText`. The Python-side `marker_text` becomes the fallback for non-OR contexts; the Java prefix is authoritative for chart readability.

Find this line inside `drawInstitutionalChartEvent` (around 1916):

```java
PreparedImage image = labelImage(evt.markerText, color, TRIANGLE_FONT_WEAK);
```

Replace with:

```java
PreparedImage image = labelImage(chartEventRenderText(evt), color, TRIANGLE_FONT_WEAK);
```

- [ ] **Step 3: Make `chartEventRenderText` and `collapseDenseCluster` callable from tests**

They're already static, just ensure they're package-private (no `private` keyword) so test classes in the same package can call them.

- [ ] **Step 4: Run Java tests**

```powershell
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange'
.\build.ps1 -RunTests
```

Expected: `PaxAiChartEventsRenderTest OK` + all prior tests still OK.

Existing test `chartEventCollisionBucketIgnoresLabelText` will now require BOTH markers to share `source` for a same-lane match. Both have `source="institutional_thesis"` (set inside the test helper), so the test still passes.

### Task 5.2: Apply AI history layer + render it on every cycle

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`

- [ ] **Step 1: Add per-instrument `paxAiChartEventMarkers` history field**

Inside `InstrumentState` (around line 1170), add:

```java
final PaxInstitutionalChartEventsHistory paxAiChartEventMarkers =
        new PaxInstitutionalChartEventsHistory(MAX_LIVE_CHART_EVENT_MARKERS);
```

- [ ] **Step 2: Merge inside `updateTriangles` (around line 1629)**

Right after `int newChart = state.chartEventMarkers.merge(...)`:

```java
int newPaxAi = state.paxAiChartEventMarkers.merge(
        trendSignals.latestPaxAiChartEvents());
int paxAiSize = state.paxAiChartEventMarkers.size();
```

Extend the render-key to include AI sizes:

```java
String renderKey = triangleRenderKey(showLegacy, signal,
        state.lastEmittedKind, state.lastEmittedBucketEnteredMs,
        state.liveTriangles.size())
        + "|CE=" + showChartEvents
        + "|INS=" + instSize
        + (newInst > 0 ? "|+" + newInst : "")
        + "|CHE=" + chartSize
        + (newChart > 0 ? "|+" + newChart : "")
        + "|AI="  + paxAiSize
        + (newPaxAi > 0 ? "|+" + newPaxAi : "");
```

- [ ] **Step 3: Draw AI events inside `if (showChartEvents)` block**

Just below the call to `addInstitutionalChartEvents(state)`:

```java
addPaxAiChartEvents(state);
```

Add the method (alongside `addInstitutionalChartEvents`):

```java
private void addPaxAiChartEvents(InstrumentState state) {
    java.util.List<PaxInstitutionalChartEvent> history =
            state.paxAiChartEventMarkers.snapshot();
    java.util.List<PaxInstitutionalChartEvent> visible =
            collapseDenseCluster(history, MAX_LIVE_CHART_EVENT_MARKERS);
    java.util.HashMap<String, Integer> bucketOrdinal = new java.util.HashMap<>();
    for (PaxInstitutionalChartEvent evt : visible) {
        String bucketKey = chartEventCollisionKey(evt, state.pips);
        int ord = bucketOrdinal.getOrDefault(bucketKey, 0);
        bucketOrdinal.put(bucketKey, ord + 1);
        drawInstitutionalChartEvent(state, evt, ord);
    }
}
```

- [ ] **Step 4: Update `chartEventsDiagnostics` (around line 1054) with AI history count**

Inside `chartEventsDiagnostics`:

```java
int latestPaxAi = trendSignals.latestPaxAiChartEvents().size();
int historyPaxAi = (state == null) ? 0 : state.paxAiChartEventMarkers.size();
```

Append to the StringBuilder:

```java
sb.append("  latest pax_ai_chart count:   ").append(latestPaxAi).append('\n');
sb.append("  durable pax_ai history:      ").append(historyPaxAi).append('\n');
```

- [ ] **Step 5: Run Java tests**

```powershell
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange'
.\build.ps1 -RunTests
```

- [ ] **Step 6: Commit Phase 5**

```bash
git add indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java \
        indicators/OpenRange/src/test/java/com/openrange/PaxAiChartEventsRenderTest.java
git commit -m "openrange: render pax_ai chart events with source-aware styling + collision lanes"
```

---

## Phase 6 — Build, deploy, verify live

### Task 6.1: Bump bridge jar version (only if Java touched outside OpenRange)

This plan only touches `indicators/OpenRange/`. The MCP bridge jar (`indicators/Bookmap/`) is untouched and does NOT need a version bump.

Confirm:

```powershell
git diff --name-only HEAD~6 HEAD -- indicators/Bookmap
```

Expected: no output.

### Task 6.2: Build the OpenRange addon

- [ ] **Step 1: Close Bookmap (the jar is locked while running)**

User action.

- [ ] **Step 2: Run the OpenRange build**

```powershell
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange'
.\build.ps1
```

Expected: `build/libs/openrange-release.jar` written; tests pass; jar copied to `C:\Bookmap\addons\openrange-release.jar` (only if build.ps1 has the deploy step — verify).

- [ ] **Step 3: Hygiene — quarantine any stale OpenRange jars under `C:\Bookmap\addons\` other than `openrange-release.jar`**

Use the same pattern as `build-and-deploy.ps1`. After build:

```powershell
$canonical = 'C:\Bookmap\addons\openrange-release.jar'
$archive = 'C:\Bookmap\addons-archive\openrange'
New-Item -ItemType Directory -Force -Path $archive | Out-Null
Get-ChildItem -Recurse -Path 'C:\Bookmap\addons' -Filter 'openrange*.jar' | ForEach-Object {
    if ($_.FullName -ne $canonical) {
        Move-Item -Path $_.FullName -Destination (Join-Path $archive ($_.BaseName + '_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '.jar'))
    }
}
```

### Task 6.3: Run the full Python test suite

- [ ] **Step 1**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai      && python -m pytest
```

Expected: all green, including `test_institutional_chart_events.py` (regression guard) and the new files.

### Task 6.4: End-to-end live verification

- [ ] **Step 1: Restart dashboard + Pax AI**

```cmd
pax-stop.bat
.\dashboard-start.ps1
pax-ai-start.bat
```

- [ ] **Step 2: Open Bookmap → enable OpenRange addon → confirm in settings panel diagnostics:**

```
Chart events plumbing
  ...
  latest pax_ai_chart count:   0
  durable pax_ai history:      0
```

If those lines are missing, the rendered diagnostic text was not regenerated — restart Bookmap.

- [ ] **Step 3: Send Pax AI a chat that should produce a structured block**

In the Pax AI window: `"OR-H is accepted with WITH flow — what's the call?"`

Pax AI should respond with prose followed by:

```
<<PAX_AI_CHART_SIGNAL>>
{"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H", ...}
<<END>>
```

- [ ] **Step 4: Within ~1 s, observe an `AI ▲ OR-H 72` (or similar) badge near OR-H on the chart in magenta**

If absent:
- Check `D:\BookmapLogs\pax-ai-chart-signals.jsonl` — does the line exist? If not, `_capture_ai_chart_signal` is silently rejecting (validator). Inspect stderr.
- Check `http://127.0.0.1:18888/api/snapshot` for the `pax_ai_chart_events` key — present? Empty? If present + non-empty, the Java side is the failure point.
- Check the OpenRange diagnostics panel: `latest pax_ai_chart count` should be > 0.

- [ ] **Step 5: Send a follow-up where Pax AI should NOT plot (no actionable read)**

E.g. `"What does the conviction look like?"` — Pax AI gives a prose-only answer. No block should appear and no new AI marker should land on the chart.

### Task 6.5: Final commit + handoff

- [ ] **Step 1: Run a sanity diff**

```bash
git status
git diff --stat HEAD
```

- [ ] **Step 2: If any uncommitted scratch files, decide per file (move into backup or delete)**

- [ ] **Step 3: Confirm tests still green**

```bash
cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest
cd /c/Bookmap/addons/MCP/Bookmap/pax-ai      && python -m pytest
cd 'C:\Bookmap\addons\MCP\Bookmap\indicators\OpenRange' && .\build.ps1 -RunTests
```

- [ ] **Step 4: Report status**

State: "Pax AI chart pipeline live. Local-anchored events unchanged. AI markers render with `AI` prefix + magenta/cyan. Diagnostic counts in OpenRange settings panel."

---

## Test inventory (final)

### Python — `mcp-server/tests/`

- `test_pax_ai_chart_events.py` — reader contract (6 tests).
- `test_institutional_chart_events.py` — existing 18 tests still pass; pinned negatives `test_trend_signal_alone_does_not_emit_chart_events` and `test_pax_decision_alone_does_not_emit_chart_events` still pass.
- `test_institutional_signals_composer.py` — unchanged.

### Python — `pax-ai/tests/`

- `test_ai_chart_signal.py` — extract + validate (10 tests).
- `test_ai_chart_signal_store.py` — JSONL store (4 tests).
- `test_prompts.py` — adds `test_base_preamble_includes_ai_chart_signal_contract`.
- `test_chat_handler.py` — adds 2 tests for `_capture_ai_chart_signal`.

### Java — `indicators/OpenRange/src/test/java/com/openrange/`

- `PaxAiChartEventsParseTest.java` — parser contract.
- `PaxAiChartEventsRenderTest.java` — badge text + collision lane + dense-cluster collapse.
- `PaxInstitutionalChartEventsParseTest.java` — unchanged.
- `PaxInstitutionalChartEventsHistoryTest.java` — unchanged (helpers still pass `"institutional_thesis"` source so collision keys still match).
- `PaxChartEventsPlumbingTest.java` — unchanged.

---

## Non-goals (deferred)

- Daily prune job for the JSONL store. The file is ~1 KB/signal; even 1000 signals/day is 365 MB/yr.
- Multi-symbol routing of AI signals. Pax AI is single-symbol today; the alias on the signal already carries it.
- Auto-deletion of AI markers when their TTL expires from the JSONL. The dashboard reader is the TTL gate; the JSONL is append-only.
- AI signal -> SimEngine paper-bracket. Out of scope per the no-live-trading invariant.
- Live order routing. Hard rule, never.
