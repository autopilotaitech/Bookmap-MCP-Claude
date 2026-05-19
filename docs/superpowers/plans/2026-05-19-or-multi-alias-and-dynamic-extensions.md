# OR Multi-Alias + Dynamic Extensions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make OR row selection per-alias (NQ and ES attached together must each see their own OR grid), let the OR ladder grow beyond ±3 to cover whichever rotation price has reached, and make the dashboard's OR levels area scroll without changing its look.

**Architecture:**
- Build a `{symbol -> latest_row}` index from every OpenRange CSV under the search globs, then a `{alias -> row}` map using `/instruments` metadata. `fetch_snapshot` passes the alias-specific row into `_compose_alias_snapshot`. Top-level legacy `or_row` remains via the shallow copy of the default alias.
- `compute_or_levels` reads `mid` and grows extensions on each side independently until they cover the current mid plus one buffer rung, with `OR_MIN_EXT=3` and `OR_MAX_EXT=12`. Labels generated dynamically (`"+4"`, `"-6"`, ...).
- Frontend: add `max-height` + `overflow-y:auto` to `.lvl-grid`. No new card, no typography change.

**Tech Stack:** Python 3.11 (`mcp-server/bookmap_mcp/dashboard.py`), pytest, embedded HTML/JS/CSS in `dashboard.py`. Java side untouched.

---

## File Structure

**Modify:**
- `mcp-server/bookmap_mcp/dashboard.py`
  - Add `_or_rows_by_symbol()` helper (per-symbol latest row from all CSVs).
  - Add `_resolve_alias_symbol(alias, instruments)` helper.
  - Add `_build_or_rows_by_alias(alias_list, instruments)` helper.
  - Modify `fetch_snapshot()` to call the builder and pass per-alias row.
  - Modify `compute_or_levels()` to generate extensions dynamically.
  - Add module-level constants `OR_MIN_EXT = 3`, `OR_MAX_EXT = 12`.
  - Inline CSS: add `max-height` / `overflow-y` on `.lvl-grid`.

**Create:**
- `mcp-server/tests/test_or_csv_multi_symbol.py` (per-symbol index + alias resolution).
- `mcp-server/tests/test_or_levels_dynamic_extensions.py` (dynamic extension math).
- `mcp-server/tests/test_or_multi_alias_snapshot.py` (NQ + ES attached → per-alias rows + per-alias magnet posts).

**Modify (tests):**
- `mcp-server/tests/test_level_composite.py` — relax `len(out["levels"]) == 8` to `>= 8` and pin labels for the default-mid fixture.

`or_latest_row()` keeps its current shape (delegates to the new index, returning the newest-by-mtime row). That preserves legacy single-CSV callers and `or_bias` skill.

---

### Task 1: Per-symbol OR row index

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py:145-161` (`or_latest_row` + add `_or_rows_by_symbol` above it)
- Test: `mcp-server/tests/test_or_csv_multi_symbol.py` (create)

- [ ] **Step 1: Write the failing test for per-symbol indexing**

Create `mcp-server/tests/test_or_csv_multi_symbol.py`:

```python
"""Per-symbol OR CSV indexing. Multiple OpenRange CSVs (one per symbol)
must each be read and the latest row mapped to its symbol."""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402


_HEADER = ("time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
           "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,"
           "rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,"
           "evidence,reason")


def _row(symbol: str, or_high: float, or_low: float) -> str:
    return (f"2026-05-19T09:00:00,{symbol},{(or_high+or_low)/2:.2f},"
            f"{or_high:.2f},{or_low:.2f},0,0,0,0,0,0,0,0,IN,0,0,GOOD,30,"
            f"NEUTRAL,WAIT,LOW,0,1,\"\",\"\"")


def _write_csv(path: Path, symbol: str, or_high: float, or_low: float) -> None:
    path.write_text(_HEADER + "\n" + _row(symbol, or_high, or_low) + "\n",
                    encoding="utf-8")


def test_or_rows_by_symbol_indexes_each_csv(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    es_csv = tmp_path / "openrange-signals-ES.csv"
    _write_csv(nq_csv, "NQ", 21500.00, 21400.00)
    _write_csv(es_csv, "ES",  5900.00,  5870.00)

    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    out = d._or_rows_by_symbol()
    assert set(out.keys()) == {"NQ", "ES"}
    assert float(out["NQ"]["orHigh"]) == pytest.approx(21500.00)
    assert float(out["NQ"]["orLow"]) == pytest.approx(21400.00)
    assert float(out["ES"]["orHigh"]) == pytest.approx(5900.00)
    assert float(out["ES"]["orLow"]) == pytest.approx(5870.00)
    # Each row must carry its own _csv_path / _csv_mtime.
    assert out["NQ"]["_csv_path"].endswith("openrange-signals-NQ.csv")
    assert out["ES"]["_csv_path"].endswith("openrange-signals-ES.csv")


def test_or_rows_by_symbol_returns_empty_when_no_files(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    assert d._or_rows_by_symbol() == {}


def test_or_rows_by_symbol_picks_latest_row_per_symbol(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    body = _HEADER + "\n"
    body += _row("NQ", 21500.0, 21400.0) + "\n"
    body += _row("NQ", 21525.0, 21425.0) + "\n"   # later row
    nq_csv.write_text(body, encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._or_rows_by_symbol()
    assert float(out["NQ"]["orHigh"]) == pytest.approx(21525.0)


def test_or_rows_by_symbol_skips_unreadable_csv(tmp_path, monkeypatch):
    good = tmp_path / "openrange-signals-NQ.csv"
    bad  = tmp_path / "openrange-signals-CORRUPT.csv"
    _write_csv(good, "NQ", 21500.0, 21400.0)
    # Header-only file → no row.
    bad.write_text(_HEADER + "\n", encoding="utf-8")
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._or_rows_by_symbol()
    assert set(out.keys()) == {"NQ"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py -v`
Expected: FAIL with `AttributeError: module 'bookmap_mcp.dashboard' has no attribute '_or_rows_by_symbol'`.

- [ ] **Step 3: Implement `_or_rows_by_symbol`**

Edit `mcp-server/bookmap_mcp/dashboard.py:145-161`. Replace `or_latest_row` with this block:

```python
def _or_rows_by_symbol() -> Dict[str, Dict[str, Any]]:
    """Index every OpenRange CSV under OR_SIGNAL_GLOBS by its `symbol` column.
    For each symbol, return the latest row across all matching files. The
    OpenRange addon writes one CSV per symbol (filename
    `openrange-signals-<symbol>.csv`); the `symbol` column inside the row is
    the authoritative key — filename normalization (safeSymbol) is more
    aggressive than the row's, so matching on the column avoids drift."""
    candidates: List[str] = []
    for pat in OR_SIGNAL_GLOBS:
        candidates.extend(glob.glob(pat))
    out: Dict[str, Dict[str, Any]] = {}
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh))
        except Exception:
            continue
        if not rows:
            continue
        last = rows[-1]
        sym = (last.get("symbol") or "").strip()
        if not sym:
            continue
        last["_csv_path"] = path
        try:
            last["_csv_mtime"] = dt.datetime.fromtimestamp(
                os.path.getmtime(path), ET).isoformat()
        except OSError:
            last["_csv_mtime"] = None
        # If two CSVs claim the same symbol (rare; rotated files), keep the
        # one with the newer file mtime.
        existing = out.get(sym)
        if existing is None:
            out[sym] = last
        else:
            try:
                if os.path.getmtime(path) > os.path.getmtime(existing["_csv_path"]):
                    out[sym] = last
            except OSError:
                pass
    return out


def or_latest_row() -> Optional[Dict[str, Any]]:
    """Back-compat: return the newest row across every OR CSV. Used by the
    `or_bias` skill and any single-instrument caller. Multi-alias callers
    should use `_or_rows_by_symbol()` directly."""
    rows = _or_rows_by_symbol()
    if not rows:
        return None
    return max(rows.values(),
               key=lambda r: r.get("_csv_mtime") or "")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Run existing OR-related tests to check back-compat**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_level_composite.py tests/test_csv_replay.py tests/test_magnet_sync.py -v`
Expected: PASS (all existing tests still pass).

- [ ] **Step 6: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_csv_multi_symbol.py
git commit -m "dashboard: index OR CSV rows by symbol; or_latest_row delegates"
```

---

### Task 2: Alias → symbol resolution

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (add `_resolve_alias_symbol` near the new index helper)
- Test: `mcp-server/tests/test_or_csv_multi_symbol.py` (extend)

- [ ] **Step 1: Write failing tests for alias resolution**

Append to `mcp-server/tests/test_or_csv_multi_symbol.py`:

```python
def test_resolve_alias_symbol_returns_symbol_from_instruments():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ", "fullName": "Nasdaq E-mini"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ES", "fullName": "S&P 500 E-mini"},
    ]}
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", instruments) == "NQ"
    assert d._resolve_alias_symbol("ESM6.CME@RITHMIC", instruments) == "ES"


def test_resolve_alias_symbol_missing_alias_returns_none():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ"},
    ]}
    assert d._resolve_alias_symbol("BTCUSDT@COINBASE", instruments) is None


def test_resolve_alias_symbol_handles_missing_payload():
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", None) is None
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", {}) is None
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", {"instruments": []}) is None


def test_resolve_alias_symbol_handles_missing_symbol_field():
    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},  # no symbol field
    ]}
    assert d._resolve_alias_symbol("NQM6.CME@RITHMIC", instruments) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py::test_resolve_alias_symbol_returns_symbol_from_instruments -v`
Expected: FAIL with `AttributeError: ... has no attribute '_resolve_alias_symbol'`.

- [ ] **Step 3: Implement `_resolve_alias_symbol`**

In `mcp-server/bookmap_mcp/dashboard.py`, directly after `_or_rows_by_symbol`, add:

```python
def _resolve_alias_symbol(alias: Optional[str],
                          instruments: Optional[Dict[str, Any]]) -> Optional[str]:
    """Look up the `symbol` for a given Bookmap alias from the /instruments
    payload. Returns None on any mismatch (missing alias, missing payload,
    missing symbol field). The OR CSV is keyed by symbol, so this is the
    bridge between Bookmap's alias namespace and the OpenRange CSV namespace."""
    if not alias or not isinstance(instruments, dict):
        return None
    for entry in instruments.get("instruments", []) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("alias") == alias:
            sym = entry.get("symbol")
            if isinstance(sym, str) and sym.strip():
                return sym.strip()
            return None
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py -v`
Expected: PASS (8 tests total in this file now).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_csv_multi_symbol.py
git commit -m "dashboard: add _resolve_alias_symbol from /instruments"
```

---

### Task 3: Per-alias OR row builder

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (add `_build_or_rows_by_alias` near the other OR helpers)
- Test: `mcp-server/tests/test_or_csv_multi_symbol.py` (extend)

- [ ] **Step 1: Write the failing test**

Append to `mcp-server/tests/test_or_csv_multi_symbol.py`:

```python
def test_build_or_rows_by_alias_maps_each_alias_to_its_symbol_row(tmp_path, monkeypatch):
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    es_csv = tmp_path / "openrange-signals-ES.csv"
    _write_csv(nq_csv, "NQ", 21500.0, 21400.0)
    _write_csv(es_csv, "ES",  5900.0,  5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ES"},
    ]}
    out = d._build_or_rows_by_alias(
        ["NQM6.CME@RITHMIC", "ESM6.CME@RITHMIC"], instruments)

    assert set(out.keys()) == {"NQM6.CME@RITHMIC", "ESM6.CME@RITHMIC"}
    assert float(out["NQM6.CME@RITHMIC"]["orHigh"]) == pytest.approx(21500.0)
    assert float(out["ESM6.CME@RITHMIC"]["orHigh"]) == pytest.approx(5900.0)


def test_build_or_rows_by_alias_missing_csv_yields_none(tmp_path, monkeypatch):
    """An alias whose symbol has no OR CSV must map to None (no row), not crash."""
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    _write_csv(nq_csv, "NQ", 21500.0, 21400.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    instruments = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ"},
        {"alias": "MESM6.CME@RITHMIC", "symbol": "MES"},  # no CSV
    ]}
    out = d._build_or_rows_by_alias(
        ["NQM6.CME@RITHMIC", "MESM6.CME@RITHMIC"], instruments)

    assert out["NQM6.CME@RITHMIC"] is not None
    assert out["MESM6.CME@RITHMIC"] is None


def test_build_or_rows_by_alias_missing_instruments_returns_none_for_each(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])
    out = d._build_or_rows_by_alias(["NQM6.CME@RITHMIC"], None)
    assert out == {"NQM6.CME@RITHMIC": None}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py -k build_or_rows_by_alias -v`
Expected: FAIL with `AttributeError: ... has no attribute '_build_or_rows_by_alias'`.

- [ ] **Step 3: Implement `_build_or_rows_by_alias`**

Add directly after `_resolve_alias_symbol`:

```python
def _build_or_rows_by_alias(alias_list: List[str],
                            instruments: Optional[Dict[str, Any]]
                            ) -> Dict[str, Optional[Dict[str, Any]]]:
    """For each alias, find the OR CSV row matching its instrument symbol.
    Returns {alias: row_or_None}. Reads CSVs once and reuses the index."""
    by_symbol = _or_rows_by_symbol()
    out: Dict[str, Optional[Dict[str, Any]]] = {}
    for alias in alias_list:
        sym = _resolve_alias_symbol(alias, instruments)
        out[alias] = by_symbol.get(sym) if sym else None
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_csv_multi_symbol.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_csv_multi_symbol.py
git commit -m "dashboard: build per-alias OR row map via instruments symbol"
```

---

### Task 4: Wire per-alias `or_row` into `fetch_snapshot`

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py:3307` (`or_row = or_latest_row()` line and downstream loop)
- Test: `mcp-server/tests/test_or_multi_alias_snapshot.py` (create)

- [ ] **Step 1: Write the failing test for per-alias OR rows in fetch_snapshot**

Create `mcp-server/tests/test_or_multi_alias_snapshot.py`:

```python
"""Per-alias OR row selection in fetch_snapshot. NQ and ES attached
together must each see their own orHigh / orLow / magnet grid."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402
from bookmap_mcp.config import BridgeConfig  # noqa: E402

_HEADER = ("time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,"
           "netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,"
           "rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,"
           "evidence,reason")


def _row(symbol: str, or_high: float, or_low: float) -> str:
    return (f"2026-05-19T09:00:00,{symbol},{(or_high+or_low)/2:.2f},"
            f"{or_high:.2f},{or_low:.2f},0,0,0,0,0,0,0,0,IN,0,0,GOOD,30,"
            f"NEUTRAL,WAIT,LOW,0,1,\"\",\"\"")


def _write_csv(path, symbol, or_high, or_low):
    path.write_text(_HEADER + "\n" + _row(symbol, or_high, or_low) + "\n",
                    encoding="utf-8")


def _per_alias_payload(alias: str):
    base = {"NQM6.CME@RITHMIC": 21450.0, "ESM6.CME@RITHMIC": 5885.0}
    mid = base.get(alias, 100.0)
    return {
        "/orderbook":      {"alias": alias, "bestBid": mid - 0.25, "bestAsk": mid + 0.25,
                            "mid": mid, "spread": 0.5, "bids": [], "asks": []},
        "/recent_trades":  {"alias": alias, "trades": []},
        "/position":       {"alias": alias, "size": 0, "avgPrice": 0.0},
        "/working_orders": {"alias": alias, "orders": []},
        "/balance":        {"alias": alias, "balance": 100000.0},
        "/recent_fills":   {"alias": alias, "fills": []},
        "/vwap":           {"alias": alias, "vwap": mid + 0.10, "samples": 100},
        "/momentum":       {"alias": alias, "regime": "BALANCED", "biasScore": 0.0,
                            "ofi": 0.0, "ofiZ": 0.0,
                            "ib": {"ibHigh": mid + 5.0, "ibLow": mid - 5.0,
                                   "ibComplete": True, "ibRange": 10.0}},
        "/volume_profile": {"alias": alias},
        "/tape_buckets":   {"alias": alias, "buckets": []},
        "/lt_liquidity":   {"alias": alias},
        "/pull_stack":     {"alias": alias, "stack": []},
        "/microstructure_events": {"alias": alias, "events": []},
        "/trend_analyzer": {"alias": alias, "warmedUp": False},
    }


class _FakeClient:
    def __init__(self, *_a, **_kw):
        self.calls = []
        self.posts = []
        self._instruments = None
    def __enter__(self): return self
    def __exit__(self, *exc): return None
    def get_json(self, path, params=None):
        params = params or {}
        alias = params.get("alias", "")
        self.calls.append((path, alias))
        if path == "/ping": return {"ok": True}
        if path == "/instruments": return self._instruments
        return _per_alias_payload(alias).get(path, {})
    def post_json(self, path, payload):
        self.posts.append((path, dict(payload)))
        return {}


@pytest.fixture
def fake_bridge(monkeypatch):
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="t")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))
    holder = {}
    def _factory(*a, **kw):
        c = _FakeClient(*a, **kw)
        c._instruments = holder["instruments"]
        holder["clients"] = holder.get("clients", []) + [c]
        return c
    monkeypatch.setattr(d, "BridgeClient", _factory)
    monkeypatch.setattr(d, "_get_pax_collector", lambda: None)
    monkeypatch.setattr(d, "pax_record", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_bridge_config", lambda *a, **kw: None)
    return holder


def test_each_alias_gets_its_own_or_row(fake_bridge, monkeypatch, tmp_path):
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    es_csv = tmp_path / "openrange-signals-ES.csv"
    _write_csv(nq_csv, "NQ", 21500.0, 21400.0)
    _write_csv(es_csv, "ES",  5900.0,  5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ", "fullName": "Nasdaq"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ES", "fullName": "S&P"},
    ]}

    snap = d.fetch_snapshot()
    nq = snap["aliases"]["NQM6.CME@RITHMIC"]
    es = snap["aliases"]["ESM6.CME@RITHMIC"]

    assert float(nq["or_row"]["orHigh"]) == pytest.approx(21500.0)
    assert float(nq["or_row"]["orLow"])  == pytest.approx(21400.0)
    assert float(es["or_row"]["orHigh"]) == pytest.approx(5900.0)
    assert float(es["or_row"]["orLow"])  == pytest.approx(5870.0)


def test_per_alias_magnet_post_uses_per_alias_grid(fake_bridge, monkeypatch, tmp_path):
    """Magnet POSTs for NQ and ES must contain prices anchored at each
    alias's OR — proves _sync_magnet_levels receives the right or_levels."""
    # Reset module cache so the fresh symbol values don't get TTL-suppressed.
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()

    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    es_csv = tmp_path / "openrange-signals-ES.csv"
    _write_csv(nq_csv, "NQ", 21500.0, 21400.0)
    _write_csv(es_csv, "ES",  5900.0,  5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ES"},
    ]}

    d.fetch_snapshot()

    all_posts = [p for c in fake_bridge["clients"]
                 for p in c.posts if p[0] == "/magnet_levels"]
    by_alias = {payload["alias"]: payload["levels"] for _, payload in all_posts}

    assert "NQM6.CME@RITHMIC" in by_alias
    assert "ESM6.CME@RITHMIC" in by_alias
    # NQ grid sits near 21,400-21,500 → no level can be below 5,000.
    nq_prices = [float(p) for p in by_alias["NQM6.CME@RITHMIC"].split(",")]
    assert min(nq_prices) > 10000.0
    # ES grid sits near 5,870-5,900 → no level can exceed 10,000.
    es_prices = [float(p) for p in by_alias["ESM6.CME@RITHMIC"].split(",")]
    assert max(es_prices) < 10000.0


def test_top_level_or_row_is_default_alias_row(fake_bridge, monkeypatch, tmp_path):
    """The legacy top-level `or_row` field must match the default (first) alias."""
    nq_csv = tmp_path / "openrange-signals-NQ.csv"
    es_csv = tmp_path / "openrange-signals-ES.csv"
    _write_csv(nq_csv, "NQ", 21500.0, 21400.0)
    _write_csv(es_csv, "ES",  5900.0,  5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQ"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ES"},
    ]}

    snap = d.fetch_snapshot()
    assert snap["alias"] == "NQM6.CME@RITHMIC"
    assert float(snap["or_row"]["orHigh"]) == pytest.approx(21500.0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_multi_alias_snapshot.py -v`
Expected: FAIL — the first test fails because both aliases currently see the *newest* CSV's row (NQ both, or ES both), not their own.

- [ ] **Step 3: Modify `fetch_snapshot` to pass per-alias OR rows**

Edit `mcp-server/bookmap_mcp/dashboard.py:3307` (replace the `or_row = or_latest_row()` line plus the per-alias loop).

Find this block (around lines 3307-3325):

```python
    or_row = or_latest_row()
    state, label = session_state(now_et)
    blocked, news_label = news_blackout(now_et)
    gates_shared = {
        "session": {
            "code": state, "label": label,
            "now_local": dt.datetime.now(DISPLAY_TZ).strftime("%H:%M:%S ") + DISPLAY_TZ_LABEL,
            "now_et":    now_et.strftime("%H:%M:%S ET"),
        },
        "news":    {"blocked": blocked, "label": news_label},
    }

    alias_list = [i["alias"] for i in insts if isinstance(i, dict) and i.get("alias")]
    per_alias_snaps: Dict[str, Dict[str, Any]] = {}
    with BridgeClient(cfg, timeout_s=3.0) as c:
        for alias in alias_list:
            per_alias_snaps[alias] = _compose_alias_snapshot(
                c, cfg, alias, ping, instruments, now_et, or_row, gates_shared)
```

Replace with:

```python
    state, label = session_state(now_et)
    blocked, news_label = news_blackout(now_et)
    gates_shared = {
        "session": {
            "code": state, "label": label,
            "now_local": dt.datetime.now(DISPLAY_TZ).strftime("%H:%M:%S ") + DISPLAY_TZ_LABEL,
            "now_et":    now_et.strftime("%H:%M:%S ET"),
        },
        "news":    {"blocked": blocked, "label": news_label},
    }

    alias_list = [i["alias"] for i in insts if isinstance(i, dict) and i.get("alias")]
    or_rows = _build_or_rows_by_alias(alias_list, instruments)
    per_alias_snaps: Dict[str, Dict[str, Any]] = {}
    with BridgeClient(cfg, timeout_s=3.0) as c:
        for alias in alias_list:
            per_alias_snaps[alias] = _compose_alias_snapshot(
                c, cfg, alias, ping, instruments, now_et,
                or_rows.get(alias), gates_shared)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_multi_alias_snapshot.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Run the full multi-alias regression set**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_multi_alias_snapshot.py tests/test_or_multi_alias_snapshot.py tests/test_magnet_sync.py tests/test_level_composite.py -v`
Expected: PASS — including the existing `test_two_aliases_are_both_fetched` etc.

- [ ] **Step 6: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_multi_alias_snapshot.py
git commit -m "dashboard: per-alias OR row selection in fetch_snapshot"
```

---

### Task 5: Dynamic OR extensions in `compute_or_levels`

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py:201` (constants) and `:874-883` (raw_levels generation)
- Test: `mcp-server/tests/test_or_levels_dynamic_extensions.py` (create)

- [ ] **Step 1: Add the constants**

Insert directly under `NQ_RUNG_PTS = 65.0` at `mcp-server/bookmap_mcp/dashboard.py:201`:

```python
NQ_RUNG_PTS = 65.0          # Pax canon NQ extension rung
NQ_TICK     = 0.25          # NQ tick size
PROX_TICKS  = 50            # 50 ticks = 12.5 pts proximity zone
OR_MIN_EXT  = 3             # always render at least ±3 extensions
OR_MAX_EXT  = 12            # hard cap so a runaway mid can't fill the page
```

(The `NQ_TICK` and `PROX_TICKS` lines already exist; just add `OR_MIN_EXT` / `OR_MAX_EXT` below them. Adjust the edit to keep the existing lines verbatim.)

- [ ] **Step 2: Write the failing test for dynamic extensions**

Create `mcp-server/tests/test_or_levels_dynamic_extensions.py`:

```python
"""Dynamic OR extension generation.

`compute_or_levels` must grow the magnet ladder as price travels further from
the open range. Minimum ±3 rungs (back-compat). When mid is past the topmost
existing extension, add as many rungs as needed to cover mid + 1 buffer rung.
Cap at ±OR_MAX_EXT to bound DOM growth in the frontend."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402

RUNG = d.NQ_RUNG_PTS  # 65.0


def _snap(or_high: float, or_low: float, mid: float):
    return {
        "or_row": {"orHigh": or_high, "orLow": or_low, "symbol": "NQ"},
        "book":   {"mid": mid, "bestBid": mid - 0.25, "bestAsk": mid + 0.25},
        "vwap_obj": None,
        "pull_stack": None,
        "lt_liquidity": None,
        "tape_flow":  {"deltaScore": 0.0},
        "micro_events": None,
        "volume_profile": None,
        "alias": "NQM6.CME@RITHMIC",
    }


def _labels(out):
    return [l["label"] for l in out["levels"]]


def test_mid_inside_or_yields_min_extensions():
    """Default case (mid sits inside OR): exactly ±3 extensions."""
    or_high, or_low = 21500.0, 21400.0
    mid = (or_high + or_low) / 2
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    assert _labels(out) == ["+3", "+2", "+1", "OR-H", "OR-L", "-1", "-2", "-3"]


def test_mid_at_plus_5_rungs_adds_extensions_with_buffer():
    """Mid is 5 rungs above OR-H → ladder must reach +5 plus a +6 buffer."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 5 * RUNG + 1.0   # just past +5
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[0] == "+6"   # buffer above the reached level
    assert "+5" in labels
    assert "+4" in labels
    # Down side stays at minimum.
    assert labels[-1] == "-3"


def test_mid_at_minus_6_rungs_adds_extensions_with_buffer():
    """Mid is 6 rungs below OR-L → ladder must reach -6 plus a -7 buffer."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_low - 6 * RUNG - 1.0   # just past -6
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert "-6" in labels
    assert "-7" in labels         # buffer below
    assert labels[-1] == "-7"
    # Up side stays at minimum.
    assert labels[0] == "+3"


def test_extensions_capped_at_or_max_ext():
    """A pathological mid (20+ rungs away) must still be capped at ±OR_MAX_EXT."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 50 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[0] == f"+{d.OR_MAX_EXT}"
    # Down side untouched.
    assert labels[-1] == "-3"


def test_dynamic_prices_match_rung_arithmetic():
    """Generated prices must be or_high + n*rung above, or_low - n*rung below."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 4 * RUNG + 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    by_label = {l["label"]: l["price"] for l in out["levels"]}
    assert by_label["+4"] == pytest.approx(or_high + 4 * RUNG)
    assert by_label["+5"] == pytest.approx(or_high + 5 * RUNG)
    assert by_label["OR-H"] == pytest.approx(or_high)
    assert by_label["OR-L"] == pytest.approx(or_low)
    assert by_label["-3"] == pytest.approx(or_low - 3 * RUNG)


def test_levels_remain_sorted_above_to_below():
    """The level list goes from highest price down to lowest. Dynamic growth
    must not break that ordering."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 7 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    prices = [l["price"] for l in out["levels"]]
    assert prices == sorted(prices, reverse=True)


def test_sides_remain_above_and_below():
    or_high, or_low = 21500.0, 21400.0
    mid = or_low - 4 * RUNG - 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    for l in out["levels"]:
        if l["label"] == "OR-H" or l["label"].startswith("+"):
            assert l["side"] == "above"
        else:
            assert l["side"] == "below"
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_levels_dynamic_extensions.py -v`
Expected: FAIL — `test_mid_at_plus_5_rungs_adds_extensions_with_buffer` fails (current code is hardcoded to ±3).

- [ ] **Step 4: Implement dynamic extension generation**

Edit `mcp-server/bookmap_mcp/dashboard.py:874-883`. Replace this block:

```python
    rung = NQ_RUNG_PTS
    raw_levels = [
        ("+3",  or_high + 3 * rung, "above"),
        ("+2",  or_high + 2 * rung, "above"),
        ("+1",  or_high + 1 * rung, "above"),
        ("OR-H", or_high,           "above"),
        ("OR-L", or_low,            "below"),
        ("-1",  or_low - 1 * rung,  "below"),
        ("-2",  or_low - 2 * rung,  "below"),
        ("-3",  or_low - 3 * rung,  "below"),
    ]
```

With:

```python
    rung = NQ_RUNG_PTS
    # Grow each side independently to cover the current mid + one buffer rung,
    # always at least OR_MIN_EXT, never more than OR_MAX_EXT. This keeps the
    # default (mid inside OR) backwards-compatible at ±3 while letting late-day
    # rotations like +5 or -6 show their next magnet before price hits it.
    reach_above = max(0.0, mid - or_high)
    reach_below = max(0.0, or_low - mid)
    n_above = max(OR_MIN_EXT,
                  min(OR_MAX_EXT, math.ceil(reach_above / rung) + 1))
    n_below = max(OR_MIN_EXT,
                  min(OR_MAX_EXT, math.ceil(reach_below / rung) + 1))
    raw_levels: List[Tuple[str, float, str]] = []
    for n in range(n_above, 0, -1):
        raw_levels.append((f"+{n}", or_high + n * rung, "above"))
    raw_levels.append(("OR-H", or_high, "above"))
    raw_levels.append(("OR-L", or_low,  "below"))
    for n in range(1, n_below + 1):
        raw_levels.append((f"-{n}", or_low - n * rung, "below"))
```

- [ ] **Step 5: Run the dynamic-extension test to verify it passes**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_levels_dynamic_extensions.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Run the existing OR + composite tests for back-compat**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_level_composite.py tests/test_magnet_sync.py tests/test_csv_replay.py -v`
Expected: PASS. The existing `test_compute_or_levels_preserves_existing_fields_and_adds_composite` should still pass because the default fixture has mid inside OR → still 8 levels. If that assertion breaks under the new code, see Task 6.

- [ ] **Step 7: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_levels_dynamic_extensions.py
git commit -m "or_levels: grow extensions dynamically; cap at OR_MAX_EXT"
```

---

### Task 6: Pin existing `test_level_composite` behavior

This task is conditional: only run it if Step 6 of Task 5 reports a failure in `test_compute_or_levels_preserves_existing_fields_and_adds_composite`. If that test passed, skip to Task 7.

**Files:**
- Modify: `mcp-server/tests/test_level_composite.py:229-251`

- [ ] **Step 1: Inspect the failing assertion**

Open `mcp-server/tests/test_level_composite.py` and read lines 229-251.

- [ ] **Step 2: Relax the hardcoded length assertion**

Find:

```python
    assert len(out["levels"]) == 8
```

Replace with:

```python
    assert len(out["levels"]) >= 8, "default-mid fixture must still yield at least ±3 rungs"
    labels = [l["label"] for l in out["levels"]]
    assert "OR-H" in labels and "OR-L" in labels
    assert "+3" in labels and "-3" in labels
```

- [ ] **Step 3: Run the test**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_level_composite.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add mcp-server/tests/test_level_composite.py
git commit -m "test_level_composite: relax level-count to allow dynamic growth"
```

---

### Task 7: Frontend scroll for OR levels area

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py:3523` (the `.lvl-grid` CSS rule)
- Test: extend `mcp-server/tests/test_or_levels_dynamic_extensions.py`

- [ ] **Step 1: Write the failing snapshot serialization test**

Append to `mcp-server/tests/test_or_levels_dynamic_extensions.py`:

```python
def test_extended_levels_serialize_cleanly():
    """A snapshot with 14+ levels must survive json.dumps via safe_json
    (the dashboard sends this over /api/snapshot)."""
    import json
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 10 * d.NQ_RUNG_PTS
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    blob = json.dumps(d.safe_json(out))
    assert "OR-H" in blob and "+10" in blob and "+11" in blob  # +10 plus buffer
```

- [ ] **Step 2: Run the test**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest tests/test_or_levels_dynamic_extensions.py::test_extended_levels_serialize_cleanly -v`
Expected: PASS (this should already work since Task 5 wired dynamic generation through; it pins the wire shape).

- [ ] **Step 3: Modify the `.lvl-grid` CSS to be scrollable**

Edit `mcp-server/bookmap_mcp/dashboard.py:3523`. Replace:

```python
.lvl-grid { display:flex; flex-direction:column; gap:2px; }
```

With:

```python
.lvl-grid { display:flex; flex-direction:column; gap:2px;
            max-height:340px; overflow-y:auto; padding-right:4px;
            scrollbar-width: thin; scrollbar-color: #3a4055 #0f1218; }
.lvl-grid::-webkit-scrollbar { width:6px; }
.lvl-grid::-webkit-scrollbar-track { background:#0f1218; }
.lvl-grid::-webkit-scrollbar-thumb { background:#3a4055; border-radius:3px; }
```

The 340px max-height fits ~14 collapsed rows at the current 22px row height plus the inline `.lvl-detail` block on the in-proximity row. Tokyo-Night scrollbar colors match the rest of the palette so nothing visually disrupts.

- [ ] **Step 4: Smoke-load the dashboard HTML and confirm it still renders**

Run (in a separate terminal so it stays open):

```powershell
$env:JAVA_HOME = 'C:\Program Files\Bookmap\jre'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\dashboard-start.ps1
```

Open `http://127.0.0.1:18888/` in a browser. The OR Strategy card must:
- Look unchanged at default zoom with 8 levels.
- Show a thin scrollbar on the right of the levels area when content exceeds ~14 rows (this won't trigger live unless price extends; verify visually with browser devtools by temporarily inflating row count if needed).

If no Bookmap is attached the OR levels area will be empty — that's still fine, the CSS doesn't visually change unless the box has rows.

- [ ] **Step 5: Commit**

```bash
git add mcp-server/bookmap_mcp/dashboard.py mcp-server/tests/test_or_levels_dynamic_extensions.py
git commit -m "dashboard ui: scrollable OR levels area; pin extended serialization"
```

---

### Task 8: Full verification gate

**Files:** (no edits)

- [ ] **Step 1: Run the full Python suite**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m pytest`
Expected: every existing test plus the three new files pass. If anything else fails (e.g. `test_or_bias` regression), inspect — `or_latest_row()` was preserved with same shape, so back-compat should hold.

- [ ] **Step 2: Byte-compile sanity**

Run: `cd /c/Bookmap/addons/MCP/Bookmap/mcp-server && python -m compileall -q bookmap_mcp`
Expected: silent (zero output).

- [ ] **Step 3: Run the Java side**

Run:

```powershell
$env:JAVA_HOME = 'C:\Program Files\Bookmap\jre'
$env:Path = "$env:JAVA_HOME\bin;$env:Path"
.\gradlew.bat test --rerun-tasks
```

Expected: BUILD SUCCESSFUL. Java was not modified; this is a sanity check that no Python edit accidentally tripped a shared resource.

- [ ] **Step 4: Build OpenRange jar**

Run:

```powershell
cd indicators\OpenRange
powershell -ExecutionPolicy Bypass -File .\build.ps1
cd ..\..
```

Expected: openrange-release.jar produced under `indicators\OpenRange\build\libs\`. Skip this step if Bookmap is currently running and holding the jar open (per CLAUDE.md note).

- [ ] **Step 5: Summary report**

Produce a final summary listing:
- Files changed (dashboard.py + three new test files + one updated test file).
- Tests added (`test_or_csv_multi_symbol.py` x 11, `test_or_multi_alias_snapshot.py` x 3, `test_or_levels_dynamic_extensions.py` x 8).
- Risks: instrument-specific rung sizing (still NQ-only — ES/RTY/YM will be computed with NQ rungs); the OR CSV `symbol` column may not exactly equal the bridge's `symbol` field if either side filters punctuation (low risk — the Java side strips only commas, the bridge SDK reports the broker's symbol as-is).

---

## Self-Review

**Spec coverage:**
- Per-alias OR row selection: Tasks 1-4. ✓
- Tests proving NQ + ES get different rows + magnet posts: Task 4 step 1. ✓
- Dynamic OR extensions: Task 5. ✓
- Tests for +5 and -6 reach + buffer: Task 5 step 2. ✓
- Cap (12): Task 5 step 2, `test_extensions_capped_at_or_max_ext`. ✓
- Minimum ±3 preserved: Task 5, `test_mid_inside_or_yields_min_extensions`. ✓
- Frontend scroll: Task 7. ✓
- Same look (no new card, no typography change): Task 7 step 3 — only adds `max-height` + scrollbar. ✓
- Snapshot/schema test: Task 7 step 1. ✓

**Type consistency:**
- `_or_rows_by_symbol() -> Dict[str, Dict[str, Any]]` — used by `_build_or_rows_by_alias`.
- `_resolve_alias_symbol(alias, instruments) -> Optional[str]` — used by `_build_or_rows_by_alias`.
- `_build_or_rows_by_alias(alias_list, instruments) -> Dict[str, Optional[Dict[str, Any]]]` — consumed by `fetch_snapshot`, which calls `or_rows.get(alias)`.
- `or_latest_row() -> Optional[Dict[str, Any]]` — preserved shape.
- Constants `OR_MIN_EXT`, `OR_MAX_EXT` referenced in test file via `d.OR_MAX_EXT`. ✓

**Placeholder scan:** No `TBD`, no "handle edge cases", every test body and implementation body is shown verbatim. ✓
