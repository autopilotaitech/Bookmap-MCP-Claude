"""Multi-alias snapshot composition.

When Bookmap has more than one instrument attached, the dashboard must
fetch and compute per-alias state for every alias — magnet sync,
conviction, trend_signal — not just the first one. The top-level
single-alias API shape is preserved (top-level fields point at the
default/first alias) and the per-alias data is exposed under
`snap["aliases"]`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d                                                # noqa: E402
from bookmap_mcp.config import BridgeConfig                                      # noqa: E402


def _per_alias_payload(alias: str):
    """Build a plausible per-alias bridge response set. Values differ by
    alias prefix so we can distinguish payloads in assertions."""
    base = {"NQM6.CME@RITHMIC": 17500.0, "ESM6.CME@RITHMIC": 5400.0}
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
    """In-process stand-in for BridgeClient. Records every (path, alias)
    call so tests can assert both aliases were polled."""
    def __init__(self, *_a, **_kw):
        self.calls: list[tuple[str, str]] = []
    def __enter__(self): return self
    def __exit__(self, *exc): return None
    def get_json(self, path: str, params=None):
        params = params or {}
        alias = params.get("alias", "")
        self.calls.append((path, alias))
        if path in ("/ping",):
            return {"ok": True}
        if path == "/instruments":
            return self._instruments  # set by the test
        # Per-alias endpoint — look up the alias-specific payload.
        per = _per_alias_payload(alias)
        return per.get(path, {})
    def post_json(self, *a, **kw):
        return {}


@pytest.fixture
def fake_bridge(monkeypatch):
    """Wire fake config + fake client into the dashboard module."""
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="t")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))

    holder: dict = {}
    def _factory(*a, **kw):
        c = _FakeClient(*a, **kw)
        c._instruments = holder["instruments"]
        holder["client"] = c
        return c
    monkeypatch.setattr(d, "BridgeClient", _factory)
    return holder


def _disable_one_shot_side_effects(monkeypatch):
    """The collector + pax_record + sync_bridge_config are filesystem /
    network side effects unrelated to multi-alias composition. Stub them
    out so tests don't touch the disk or the network."""
    monkeypatch.setattr(d, "_get_pax_collector", lambda: None)
    monkeypatch.setattr(d, "pax_record", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_bridge_config", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_magnet_levels", lambda *a, **kw: None)


# ─── tests ───────────────────────────────────────────────────────────────

def test_two_aliases_are_both_fetched(fake_bridge, monkeypatch):
    """Both aliases get the full per-alias endpoint sweep, not just the first."""
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    assert snap["health"] == "ok"
    calls = fake_bridge["client"].calls

    nq_calls = [path for path, alias in calls if alias == "NQM6.CME@RITHMIC"]
    es_calls = [path for path, alias in calls if alias == "ESM6.CME@RITHMIC"]

    expected = {
        "/orderbook", "/recent_trades", "/position", "/working_orders",
        "/balance", "/recent_fills", "/vwap", "/momentum", "/volume_profile",
        "/tape_buckets", "/lt_liquidity", "/pull_stack",
        "/microstructure_events", "/trend_analyzer",
    }
    assert set(nq_calls) >= expected, (
        f"NQ missing endpoints: {expected - set(nq_calls)}")
    assert set(es_calls) >= expected, (
        f"ES missing endpoints: {expected - set(es_calls)}")


def test_top_level_fields_point_to_default_alias(fake_bridge, monkeypatch):
    """Existing single-alias API: top-level `alias`, `book`, etc. point at the
    FIRST attached instrument so legacy consumers don't break."""
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    assert snap["alias"] == "NQM6.CME@RITHMIC"
    # NQ payload was 17500.0 mid; ES would be 5400.0.
    assert snap["book"]["mid"] == pytest.approx(17500.0)


def test_aliases_field_contains_every_attached_alias(fake_bridge, monkeypatch):
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    assert "aliases" in snap, "fetch_snapshot must expose per-alias map under 'aliases'"
    aliases = snap["aliases"]
    assert set(aliases.keys()) == {"NQM6.CME@RITHMIC", "ESM6.CME@RITHMIC"}
    # Each per-alias entry must carry its own book + alias name.
    assert aliases["NQM6.CME@RITHMIC"]["alias"] == "NQM6.CME@RITHMIC"
    assert aliases["NQM6.CME@RITHMIC"]["book"]["mid"] == pytest.approx(17500.0)
    assert aliases["ESM6.CME@RITHMIC"]["alias"] == "ESM6.CME@RITHMIC"
    assert aliases["ESM6.CME@RITHMIC"]["book"]["mid"] == pytest.approx(5400.0)


def test_per_alias_derived_signals_run_for_every_alias(fake_bridge, monkeypatch):
    """Per-alias state (conviction, trend_signal) must be populated under
    each alias entry, not only the default."""
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    for alias_name, sub in snap["aliases"].items():
        for key in ("tape_flow", "or_levels", "vwap_bias", "vp_bias",
                    "decision", "conviction", "trend_signal", "pax", "sim"):
            assert key in sub, f"alias {alias_name} missing {key}"


def test_magnet_sync_runs_for_every_alias(fake_bridge, monkeypatch):
    """The magnet POST must happen per-alias; the bridge maintains separate
    magnet sets per instrument."""
    _disable_one_shot_side_effects_except_magnets = lambda mp: (
        mp.setattr(d, "_get_pax_collector", lambda: None),
        mp.setattr(d, "pax_record", lambda *a, **kw: None),
        mp.setattr(d, "_sync_bridge_config", lambda *a, **kw: None),
    )
    _disable_one_shot_side_effects_except_magnets(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    seen_aliases = []
    real_sync = d._sync_magnet_levels
    def _capture(cfg, alias, or_levels):
        seen_aliases.append(alias)
        return None  # short-circuit; don't actually post
    monkeypatch.setattr(d, "_sync_magnet_levels", _capture)

    d.fetch_snapshot()
    assert "NQM6.CME@RITHMIC" in seen_aliases
    assert "ESM6.CME@RITHMIC" in seen_aliases


def test_aliases_field_is_json_serializable(fake_bridge, monkeypatch):
    """Top-level `snap` and `snap["aliases"][default]` must NOT be the same
    object — otherwise `json.dumps` recurses forever. The shallow-copy at
    fetch_snapshot keeps them distinct."""
    import json
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
        {"alias": "ESM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    # Identity check: top-level dict is NOT the same object as the default's per-alias entry.
    assert snap is not snap["aliases"]["NQM6.CME@RITHMIC"]
    # And it must actually serialize cleanly.
    blob = json.dumps(d.safe_json(snap))
    assert "NQM6.CME@RITHMIC" in blob
    assert "ESM6.CME@RITHMIC" in blob


def test_single_alias_still_works(fake_bridge, monkeypatch):
    """Single-instrument path must continue to work unchanged — top-level
    legacy fields populated, aliases map carries the single entry."""
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC"},
    ]}

    snap = d.fetch_snapshot()
    assert snap["alias"] == "NQM6.CME@RITHMIC"
    assert set(snap["aliases"].keys()) == {"NQM6.CME@RITHMIC"}
    assert snap["book"]["mid"] == pytest.approx(17500.0)


def test_no_instrument_still_returns_minimal_ok_payload(fake_bridge, monkeypatch):
    """The legacy 'no instrument attached' branch is preserved."""
    _disable_one_shot_side_effects(monkeypatch)
    fake_bridge["instruments"] = {"instruments": []}

    snap = d.fetch_snapshot()
    assert snap["health"] == "ok"
    assert snap["alias"] is None
    assert "aliases" not in snap, "no-instruments branch should not have aliases field"
