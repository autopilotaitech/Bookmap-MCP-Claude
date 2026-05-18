# Magnet-Levels OR-Strategy Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` (inline). This plan is tightly coupled — one helper, one cache, one wire-up site, one new test file — so per CLAUDE.md "single-author for tightly coupled work" do NOT dispatch parallel subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the bridge's `/magnet_levels` endpoint into `fetch_snapshot` so STOP_SWEEP detection fires in production, with a 60-second refresh TTL bounding the bridge-restart failure window.

**Architecture:** One new helper `_sync_magnet_levels` plus one module-level cache `_LAST_MAGNETS: Dict[str, Tuple[Tuple[float, ...], float]]` (value = sorted-price-tuple, last-successful-post `time.monotonic()` timestamp) in `dashboard.py`. `fetch_snapshot` calls the helper immediately after `compute_or_levels`. The helper short-circuits when the cached tuple is identical AND the last post is younger than `_MAGNET_REFRESH_SECS = 60.0`. Network failures are caught, logged to stderr, and never update the cache (so the next snapshot retries).

**Tech Stack:** Python 3, existing `BridgeClient`/`BridgeError` from `bookmap_mcp.bridge_client`, existing `BridgeConfig` from `bookmap_mcp.config`. Pytest for tests with `unittest.mock` for stubbing the client.

---

## File structure

| File | Change | Lines (approx) |
|---|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Add `import threading`, `import time` if missing; add `_LAST_MAGNETS`, `_LAST_MAGNETS_LOCK`, `_MAGNET_REFRESH_SECS`; add `_sync_magnet_levels(cfg, alias, or_levels)`; insert one call site in `fetch_snapshot` right after `snap["or_levels"]` is set. | ~50 |
| `mcp-server/tests/test_magnet_sync.py` | New test file, 9 tests. | ~180 |

No other files change. Java, server.py, bridge_client.py, compute_or_levels, CSV parsing all untouched.

---

## Task 1: Test scaffolding + red phase

**Files:**
- Create: `mcp-server/tests/test_magnet_sync.py`

- [ ] **Step 1.1: Create the test file with all 9 tests**

The full test file is in Task 4 (one file = one test). The "write all tests first, watch them fail" loop is justified here because every test sits behind the same single helper and the same single cache.

Contents shown in Task 4 below.

- [ ] **Step 1.2: Run the suite to confirm tests are red (helper does not exist yet)**

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -m pytest tests/test_magnet_sync.py -q
```

Expected: ImportError or AttributeError ("_sync_magnet_levels"/`_LAST_MAGNETS`/`_MAGNET_REFRESH_SECS` not defined in `bookmap_mcp.dashboard`). All 9 tests collected-but-errored or failed.

---

## Task 2: Add cache + constants + helper

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py`

- [ ] **Step 2.1: Ensure `threading` and `time` are imported**

Open `mcp-server/bookmap_mcp/dashboard.py`. The file already imports many stdlib modules near the top. Look at the existing import block (the `import csv` is on line 9). After the existing stdlib imports and BEFORE the third-party imports, ensure these lines exist (add only the ones that are missing — leave existing imports alone):

```python
import threading
import time
```

Verify via:

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -c "import bookmap_mcp.dashboard as d; print(d.threading, d.time)"
```

Expected: prints two `<module ...>` repr strings, no error.

- [ ] **Step 2.2: Add module-level cache and TTL constant**

Add this block to `dashboard.py` in a stable location — immediately AFTER the existing `OR_SIGNAL_GLOBS` block (it's a small module-level constant cluster near the top). Search for `OR_SIGNAL_GLOBS = [` and insert AFTER its closing `]`:

```python
# ─────────────────────────────────────────────────────────────────────────────
# Magnet-levels sync cache.
# Value: (sorted tuple of rounded prices, monotonic seconds of last 2xx post).
# Refresh TTL bounds the "Bookmap restart wiped magnets but dashboard cache
# still thinks they're set" failure mode to _MAGNET_REFRESH_SECS.
_LAST_MAGNETS: Dict[str, Tuple[Tuple[float, ...], float]] = {}
_LAST_MAGNETS_LOCK = threading.Lock()
_MAGNET_REFRESH_SECS = 60.0
```

If `Dict` or `Tuple` aren't yet imported from `typing` in this file, add them to the existing `from typing import ...` line (search for `from typing import` near the top — they're almost certainly there; verify and only add if missing).

- [ ] **Step 2.3: Add the helper function**

Add this function to `dashboard.py`. Place it right BEFORE `def fetch_snapshot(` (search for `def fetch_snapshot` to find the right spot):

```python
def _sync_magnet_levels(cfg: BridgeConfig, alias: Optional[str],
                        or_levels: Optional[Dict[str, Any]]) -> None:
    """Push the OR-Strategy level grid to the bridge as stop-sweep magnets.

    Idempotent across snapshots: only posts when the level set changes OR the
    cached set is older than _MAGNET_REFRESH_SECS. Catches every exception so a
    transient bridge failure cannot break fetch_snapshot.
    """
    if not alias:
        return
    if not isinstance(or_levels, dict) or "_error" in or_levels:
        return
    raw_levels = or_levels.get("levels")
    if not isinstance(raw_levels, list) or not raw_levels:
        return

    prices: List[float] = []
    for entry in raw_levels:
        if not isinstance(entry, dict):
            continue
        p = entry.get("price")
        if isinstance(p, bool):
            # bool is a subclass of int in Python — exclude explicitly.
            continue
        if not isinstance(p, (int, float)):
            continue
        try:
            fp = float(p)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(fp):
            continue
        prices.append(round(fp, 2))
    if not prices:
        return

    new_tuple: Tuple[float, ...] = tuple(sorted(prices))
    now_mono = time.monotonic()

    with _LAST_MAGNETS_LOCK:
        cached = _LAST_MAGNETS.get(alias)
        if cached is not None:
            cached_tuple, cached_ts = cached
            if cached_tuple == new_tuple and (now_mono - cached_ts) < _MAGNET_REFRESH_SECS:
                return

    payload = ",".join(f"{p:g}" for p in new_tuple)
    try:
        with BridgeClient(cfg, timeout_s=2.0) as client:
            client.post_json("/magnet_levels", {"alias": alias, "levels": payload})
    except BridgeError as exc:
        sys.stderr.write(f"[dashboard] magnet_levels POST failed: {exc}\n")
        return
    except Exception as exc:
        sys.stderr.write(f"[dashboard] magnet_levels POST crashed: "
                         f"{type(exc).__name__}: {exc}\n")
        return

    with _LAST_MAGNETS_LOCK:
        _LAST_MAGNETS[alias] = (new_tuple, time.monotonic())
```

Verify the function symbol resolves:

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -c "from bookmap_mcp.dashboard import _sync_magnet_levels, _LAST_MAGNETS, _MAGNET_REFRESH_SECS; print('OK', _MAGNET_REFRESH_SECS)"
```

Expected: `OK 60.0`.

---

## Task 3: Wire into fetch_snapshot

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py`

- [ ] **Step 3.1: Insert the call site**

Open `dashboard.py`. Find the line:

```python
    snap["or_levels"] = _safe_call(compute_or_levels, "compute_or_levels")
```

Insert directly after it:

```python
    # Push the OR grid to the bridge as STOP_SWEEP magnets. Failure is logged
    # but never propagates — magnet sync is best-effort, snapshot composition
    # is critical-path.
    try:
        _sync_magnet_levels(cfg, alias, snap["or_levels"])
    except Exception as exc:
        sys.stderr.write(f"[dashboard] _sync_magnet_levels outer guard: "
                         f"{type(exc).__name__}: {exc}\n")
```

The outer try/except is belt-and-suspenders — `_sync_magnet_levels` already catches internally. This guard exists in case the helper's signature or import surface ever drifts; it ensures snapshot composition can never break because of magnets.

- [ ] **Step 3.2: Byte-compile sanity check**

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -m compileall -q bookmap_mcp
```

Expected: silent / clean exit.

---

## Task 4: Test file contents + green phase

**Files:**
- Replace: `mcp-server/tests/test_magnet_sync.py` (this is the file whose skeleton was created in Task 1; here is the full content)

- [ ] **Step 4.1: Write the complete test file**

```python
"""Tests for the _sync_magnet_levels helper in dashboard.py.

These tests cover the cache, TTL refresh, payload format, and failure
handling. They do NOT touch the bridge or the network — BridgeClient is
patched at the module path where dashboard.py imports it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow `import bookmap_mcp.dashboard` when running pytest from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as dash    # noqa: E402
from bookmap_mcp.bridge_client import BridgeError   # noqa: E402


def _grid(prices):
    """Build an or_levels-shaped dict from a list of prices."""
    return {"levels": [{"label": f"L{i}", "price": float(p), "side": "above"}
                       for i, p in enumerate(prices)]}


def _fake_cfg():
    """A BridgeConfig stand-in — only used so the helper has something to pass
    to BridgeClient. The constructor is patched, so the value doesn't matter."""
    return MagicMock(name="BridgeConfig")


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test starts with an empty cache."""
    with dash._LAST_MAGNETS_LOCK:
        dash._LAST_MAGNETS.clear()
    yield
    with dash._LAST_MAGNETS_LOCK:
        dash._LAST_MAGNETS.clear()


@pytest.fixture
def patched_client():
    """Patch BridgeClient at the dashboard module's import site. Yields the
    mock CLIENT INSTANCE that the helper's `with BridgeClient(...)` returns."""
    with patch.object(dash, "BridgeClient", autospec=True) as ctor:
        instance = MagicMock(name="BridgeClientInstance")
        # Context-manager protocol: __enter__ returns the instance, __exit__ returns None.
        ctor.return_value.__enter__.return_value = instance
        ctor.return_value.__exit__.return_value = None
        # Default: post_json returns an empty dict (2xx-equivalent).
        instance.post_json.return_value = {}
        yield ctor, instance


def test_first_call_posts_full_grid(patched_client):
    ctor, client = patched_client
    prices = [20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0]
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", _grid(prices))
    assert client.post_json.call_count == 1
    call = client.post_json.call_args
    assert call.args[0] == "/magnet_levels"
    payload = call.args[1] if len(call.args) > 1 else call.kwargs
    assert payload["alias"] == "NQM6"
    # All 8 prices appear, sorted ascending, comma-separated, no spaces.
    assert payload["levels"] == "20002,20003,20004,20005,20006,20007,20008,20009"


def test_repeat_with_identical_levels_inside_ttl_does_not_repost(patched_client):
    ctor, client = patched_client
    grid = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    cfg = _fake_cfg()
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 1


def test_identical_levels_after_ttl_reposts(patched_client):
    ctor, client = patched_client
    grid = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    cfg = _fake_cfg()
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 1
    # Age the cached timestamp past the TTL by rewriting it directly.
    with dash._LAST_MAGNETS_LOCK:
        tup, _ = dash._LAST_MAGNETS["NQM6"]
        # 60.0 seconds back in monotonic time — older than _MAGNET_REFRESH_SECS=60.0.
        import time as _t
        dash._LAST_MAGNETS["NQM6"] = (tup, _t.monotonic() - dash._MAGNET_REFRESH_SECS - 1.0)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 2
    # Cache timestamp must be refreshed (now within the TTL again).
    import time as _t
    with dash._LAST_MAGNETS_LOCK:
        _, new_ts = dash._LAST_MAGNETS["NQM6"]
        assert (_t.monotonic() - new_ts) < dash._MAGNET_REFRESH_SECS


def test_changed_levels_repost(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    a = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    b = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20010.0])
    dash._sync_magnet_levels(cfg, "NQM6", a)
    dash._sync_magnet_levels(cfg, "NQM6", b)
    assert client.post_json.call_count == 2
    last_payload = client.post_json.call_args.args[1]
    # The changed (10) appears in the second post; the old (9) does not.
    assert last_payload["levels"].endswith("20010")


def test_none_or_levels_is_noop(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", None)
    assert client.post_json.call_count == 0
    with dash._LAST_MAGNETS_LOCK:
        assert dash._LAST_MAGNETS == {}


def test_error_shape_or_levels_is_noop(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", {"_error": "compute_or_levels: boom"})
    assert client.post_json.call_count == 0
    with dash._LAST_MAGNETS_LOCK:
        assert dash._LAST_MAGNETS == {}


def test_missing_alias_is_noop(patched_client):
    ctor, client = patched_client
    grid = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    dash._sync_magnet_levels(_fake_cfg(), None, grid)
    dash._sync_magnet_levels(_fake_cfg(), "", grid)
    assert client.post_json.call_count == 0


def test_bridge_error_is_swallowed_and_cache_not_updated(patched_client):
    ctor, client = patched_client
    client.post_json.side_effect = BridgeError("bridge offline")
    grid = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    # Must not raise.
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    assert client.post_json.call_count == 1
    with dash._LAST_MAGNETS_LOCK:
        assert "NQM6" not in dash._LAST_MAGNETS


def test_unexpected_exception_is_swallowed(patched_client):
    ctor, client = patched_client
    client.post_json.side_effect = RuntimeError("nope")
    grid = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    with dash._LAST_MAGNETS_LOCK:
        assert "NQM6" not in dash._LAST_MAGNETS


def test_cache_isolates_by_alias(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    grid_a = _grid([20002.0, 20003.0, 20004.0, 20005.0, 20006.0, 20007.0, 20008.0, 20009.0])
    grid_b = _grid([19500.0, 19510.0, 19520.0, 19530.0, 19540.0, 19550.0, 19560.0, 19570.0])
    dash._sync_magnet_levels(cfg, "NQM6", grid_a)
    dash._sync_magnet_levels(cfg, "ESM6", grid_b)
    # Each alias caused exactly one post.
    assert client.post_json.call_count == 2
    # Repeats inside TTL stay quiet.
    dash._sync_magnet_levels(cfg, "NQM6", grid_a)
    dash._sync_magnet_levels(cfg, "ESM6", grid_b)
    assert client.post_json.call_count == 2
    # Caches are separate.
    with dash._LAST_MAGNETS_LOCK:
        assert set(dash._LAST_MAGNETS.keys()) == {"NQM6", "ESM6"}
```

- [ ] **Step 4.2: Run the new tests in isolation**

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -m pytest tests/test_magnet_sync.py -v
```

Expected: `10 passed` (9 named tests; if the file lands with the 10th `test_missing_alias_is_noop` from Task 4, count it). All green.

- [ ] **Step 4.3: Run the full Python suite**

```bash
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server && \
  .venv\Scripts\python.exe -m pytest tests -q
```

Expected: the prior baseline (`70 passed`) plus the 10 new tests = `80 passed` (or `79 passed` if the 10th test is absent — the spec asked for 9 tests but adding the missing-alias one is a free safety net).

---

## Self-review

**Spec coverage:**
- Module cache + lock + TTL constant — Task 2.2 ✓
- `_sync_magnet_levels` helper with all noop branches, payload format, error catches, cache update only on 2xx — Task 2.3 ✓
- Wire into `fetch_snapshot` after `compute_or_levels` — Task 3.1 ✓
- Tests for first post, identical-inside-TTL noop, identical-after-TTL repost, changed-prices repost, None/`_error` noop, BridgeError swallowed, alias isolation, payload format — Task 4 ✓
- "Magnet sync failure must not affect snap[\"or_levels\"], pax, conviction, or dashboard output" — Task 3.1's outer try/except guard ✓
- "Do not change Java code, server.py, bridge_client.py, OR CSV parsing, or compute_or_levels behavior" — file matrix confirms only `dashboard.py` and the new test file are touched ✓

**Placeholder scan:** No TBD/TODO/"fill in"/"appropriate handling". Every step shows the actual code or exact command.

**Type consistency:** `_LAST_MAGNETS: Dict[str, Tuple[Tuple[float, ...], float]]` used consistently. `_sync_magnet_levels(cfg, alias, or_levels)` signature matches the call site. `BridgeError` imported from `bookmap_mcp.bridge_client` in both implementation and tests.

One gap I will note for the implementer: the spec listed 9 test names; I added `test_unexpected_exception_is_swallowed` and `test_missing_alias_is_noop` as extras (10 + 1 = 11 actually — let me recount: 9 spec tests minus `test_payload_format_is_comma_separated` because the payload assertion is folded into `test_first_call_posts_full_grid`. So: 8 from spec + 2 extras = 10 total). Net: ~10 tests, all green expected.
