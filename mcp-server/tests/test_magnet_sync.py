"""Tests for the _sync_magnet_levels helper in dashboard.py.

Covers cache, TTL refresh, payload format, and failure handling. No real
network or bridge — BridgeClient is patched at the module path where
dashboard.py imports it.
"""

from __future__ import annotations

import sys
import time as _t
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow `import bookmap_mcp.dashboard` when pytest runs from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as dash                                  # noqa: E402
from bookmap_mcp.bridge_client import BridgeError                     # noqa: E402


def _grid(prices):
    """Build an or_levels-shaped dict from a list of prices."""
    return {"levels": [{"label": f"L{i}", "price": float(p), "side": "above"}
                       for i, p in enumerate(prices)]}


def _fake_cfg():
    """A BridgeConfig stand-in — only used so the helper has something to pass
    to BridgeClient. The constructor is patched, so the value doesn't matter."""
    return MagicMock(name="BridgeConfig")


_FULL_GRID = [20002.0, 20003.0, 20004.0, 20005.0,
              20006.0, 20007.0, 20008.0, 20009.0]


@pytest.fixture(autouse=True)
def _reset_cache():
    """Every test starts and ends with an empty cache."""
    with dash._LAST_MAGNETS_LOCK:
        dash._LAST_MAGNETS.clear()
    yield
    with dash._LAST_MAGNETS_LOCK:
        dash._LAST_MAGNETS.clear()


@pytest.fixture
def patched_client():
    """Patch BridgeClient at the dashboard module's import site. Yields
    (ctor_mock, client_instance_mock). The instance is what `with BridgeClient
    (...) as client:` evaluates to."""
    with patch.object(dash, "BridgeClient", autospec=True) as ctor:
        instance = MagicMock(name="BridgeClientInstance")
        ctor.return_value.__enter__.return_value = instance
        ctor.return_value.__exit__.return_value = None
        instance.post_json.return_value = {}    # 2xx-equivalent default
        yield ctor, instance


# ──────────────────────────────────────────────────────────────────────
# 1. Happy path + payload format
# ──────────────────────────────────────────────────────────────────────

def test_first_call_posts_full_grid(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", _grid(_FULL_GRID))
    assert client.post_json.call_count == 1
    args = client.post_json.call_args.args
    assert args[0] == "/magnet_levels"
    payload = args[1]
    assert payload["alias"] == "NQM6"
    # All 8 prices, sorted ascending, comma-separated, no spaces.
    assert payload["levels"] == "20002,20003,20004,20005,20006,20007,20008,20009"


def test_payload_is_sorted_even_when_input_unsorted(patched_client):
    ctor, client = patched_client
    unsorted = [20009.0, 20002.0, 20005.0, 20003.0, 20008.0, 20004.0, 20007.0, 20006.0]
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", _grid(unsorted))
    payload = client.post_json.call_args.args[1]
    assert payload["levels"] == "20002,20003,20004,20005,20006,20007,20008,20009"


# ──────────────────────────────────────────────────────────────────────
# 2. Cache + TTL
# ──────────────────────────────────────────────────────────────────────

def test_repeat_with_identical_levels_inside_ttl_does_not_repost(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    grid = _grid(_FULL_GRID)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 1


def test_identical_levels_after_ttl_reposts(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    grid = _grid(_FULL_GRID)
    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 1

    # Age the cached timestamp past the TTL by rewriting it directly. This is
    # more robust than monkey-patching time.monotonic because monotonic is
    # called from two distinct spots inside the helper.
    with dash._LAST_MAGNETS_LOCK:
        tup, _ts = dash._LAST_MAGNETS["NQM6"]
        dash._LAST_MAGNETS["NQM6"] = (
            tup, _t.monotonic() - dash._MAGNET_REFRESH_SECS - 1.0)

    dash._sync_magnet_levels(cfg, "NQM6", grid)
    assert client.post_json.call_count == 2

    # Cache timestamp must be refreshed (now back inside the TTL window).
    with dash._LAST_MAGNETS_LOCK:
        _, new_ts = dash._LAST_MAGNETS["NQM6"]
        assert (_t.monotonic() - new_ts) < dash._MAGNET_REFRESH_SECS


def test_changed_levels_repost(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    a = _grid(_FULL_GRID)
    b = _grid(_FULL_GRID[:-1] + [20010.0])
    dash._sync_magnet_levels(cfg, "NQM6", a)
    dash._sync_magnet_levels(cfg, "NQM6", b)
    assert client.post_json.call_count == 2
    last_payload = client.post_json.call_args.args[1]
    # The changed (10) is the new max → ends the sorted CSV.
    assert last_payload["levels"].endswith("20010")
    assert "20009" not in last_payload["levels"]


def test_cache_isolates_by_alias(patched_client):
    ctor, client = patched_client
    cfg = _fake_cfg()
    grid_a = _grid(_FULL_GRID)
    grid_b = _grid([19500.0, 19510.0, 19520.0, 19530.0,
                    19540.0, 19550.0, 19560.0, 19570.0])
    dash._sync_magnet_levels(cfg, "NQM6", grid_a)
    dash._sync_magnet_levels(cfg, "ESM6", grid_b)
    assert client.post_json.call_count == 2
    # Repeats inside TTL stay quiet for both aliases.
    dash._sync_magnet_levels(cfg, "NQM6", grid_a)
    dash._sync_magnet_levels(cfg, "ESM6", grid_b)
    assert client.post_json.call_count == 2
    with dash._LAST_MAGNETS_LOCK:
        assert set(dash._LAST_MAGNETS.keys()) == {"NQM6", "ESM6"}


# ──────────────────────────────────────────────────────────────────────
# 3. Noop branches
# ──────────────────────────────────────────────────────────────────────

def test_none_or_levels_is_noop(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", None)
    assert client.post_json.call_count == 0
    with dash._LAST_MAGNETS_LOCK:
        assert dash._LAST_MAGNETS == {}


def test_error_shape_or_levels_is_noop(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6",
                             {"_error": "compute_or_levels: boom"})
    assert client.post_json.call_count == 0
    with dash._LAST_MAGNETS_LOCK:
        assert dash._LAST_MAGNETS == {}


def test_missing_alias_is_noop(patched_client):
    ctor, client = patched_client
    grid = _grid(_FULL_GRID)
    dash._sync_magnet_levels(_fake_cfg(), None, grid)
    dash._sync_magnet_levels(_fake_cfg(), "", grid)
    assert client.post_json.call_count == 0


def test_missing_levels_key_is_noop(patched_client):
    ctor, client = patched_client
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", {"orHigh": 20000.0})
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", {"levels": []})
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", {"levels": "not-a-list"})
    assert client.post_json.call_count == 0


# ──────────────────────────────────────────────────────────────────────
# 4. Failure handling
# ──────────────────────────────────────────────────────────────────────

def test_bridge_error_is_swallowed_and_cache_not_updated(patched_client):
    ctor, client = patched_client
    client.post_json.side_effect = BridgeError("bridge offline")
    grid = _grid(_FULL_GRID)
    # Must not raise.
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    assert client.post_json.call_count == 1
    with dash._LAST_MAGNETS_LOCK:
        assert "NQM6" not in dash._LAST_MAGNETS


def test_unexpected_exception_is_swallowed(patched_client):
    ctor, client = patched_client
    client.post_json.side_effect = RuntimeError("nope")
    grid = _grid(_FULL_GRID)
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    with dash._LAST_MAGNETS_LOCK:
        assert "NQM6" not in dash._LAST_MAGNETS


def test_failure_then_recovery_posts_again(patched_client):
    """After a failed post leaves the cache empty, the next call retries
    instead of being held off by the TTL of a non-existent entry."""
    ctor, client = patched_client
    grid = _grid(_FULL_GRID)
    client.post_json.side_effect = BridgeError("offline")
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    # Bridge is back.
    client.post_json.side_effect = None
    client.post_json.return_value = {}
    dash._sync_magnet_levels(_fake_cfg(), "NQM6", grid)
    assert client.post_json.call_count == 2
    with dash._LAST_MAGNETS_LOCK:
        assert "NQM6" in dash._LAST_MAGNETS
