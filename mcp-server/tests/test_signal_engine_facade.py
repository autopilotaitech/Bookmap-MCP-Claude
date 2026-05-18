"""Phase 1: pin the signal_engine facade.

The Phase 3 daemon, the Phase 5 replay tooling, and future research code all
must be able to do `from bookmap_mcp.signal_engine import ...` and get the
pure Pax decision surface. These tests pin the contract so a future cleanup
that drops a symbol from dashboard.py is caught immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_signal_engine_module_imports_clean():
    """Module must be importable; no NameError on missing dashboard symbol."""
    import bookmap_mcp.signal_engine as se   # noqa: F401


def test_signal_engine_exports_pax_decision():
    from bookmap_mcp.signal_engine import pax_decision
    # Smoke check: pure call on a minimal valid-ish snap → returns a dict.
    snap = {
        "alias": "TEST", "health": "ok",
        "or_levels": {"inProximity": False, "middleLock": True,
                       "orHigh": 20100.0, "orLow": 20000.0,
                       "orWidthPts": 100.0, "levels": []},
        "gates": {"session": {"code": "ACTIVE"}, "news": {"blocked": False},
                   "vwap_or": {"state": "ALLOW_LONG"}},
        "flow": {}, "book": {"mid": 20050.0},
        "vwap_bias": {"score": 0.0}, "vp_bias": {"score": 0.0},
    }
    out = pax_decision(snap)
    assert isinstance(out, dict)
    assert "decision" in out


def test_signal_engine_exports_compute_tape_flow():
    from bookmap_mcp.signal_engine import compute_tape_flow
    assert compute_tape_flow({}) is None


def test_signal_engine_exports_compute_or_levels():
    from bookmap_mcp.signal_engine import compute_or_levels
    assert compute_or_levels({}) is None


def test_signal_engine_exports_level_composite_helpers():
    """Every per-level driver helper is reachable through signal_engine."""
    from bookmap_mcp.signal_engine import (
        _book_at_level, _vwap_or_at_level, _vp_at_level,
        _conviction_at_level, _vwap_stretch_directional,
        _level_composite,
    )
    # Smoke calls.
    assert _book_at_level(None, 100.0, "above") == (0.0, 0.0, "no book")
    assert _vwap_or_at_level(None, "above")[1] == 0.0
    assert _vp_at_level(None, 100.0, "above")[1] == 0.0


def test_signal_engine_exports_all_17_conviction_sources():
    """The full conviction registry must be reachable through signal_engine."""
    import bookmap_mcp.signal_engine as se
    expected = {
        "_source_flow_ofi", "_source_flow_cvd", "_source_flow_vpt_absorption",
        "_source_regime", "_source_bias_score", "_source_vwap_dislocation",
        "_source_vwap_slope", "_source_vwap_or_gate", "_source_volume_profile",
        "_source_pull_stack", "_source_tape_large_lot", "_source_orderbook",
        "_source_lt_liquidity", "_source_micro_events",
        "_source_level_reaction", "_source_anchored_vwap_opening_drive",
        "_source_ib_context",
    }
    for name in expected:
        assert hasattr(se, name), f"signal_engine missing {name}"
    # And the registry dict itself.
    assert "orderbook" in se._CONVICTION_SOURCES
    assert "vwap_or_gate" in se._CONVICTION_SOURCES


def test_signal_engine_shares_state_with_dashboard():
    """Module-level caches are references — mutating via signal_engine must
    be visible to existing dashboard-side code (and tests) that read the
    same dict object. Verifies the facade is genuinely re-exporting, not
    copying."""
    import bookmap_mcp.signal_engine as se
    import bookmap_mcp.dashboard as dash
    assert se._LAST_CONVICTION is dash._LAST_CONVICTION
    assert se._LAST_LEVEL_REACTION is dash._LAST_LEVEL_REACTION
    assert se._CONVICTION_SOURCES is dash._CONVICTION_SOURCES
    assert se._CONVICTION_STATE is dash._CONVICTION_STATE
    assert se._PAX_WEIGHTS_CACHE is dash._PAX_WEIGHTS_CACHE


def test_signal_engine_does_not_export_bridge_or_fetch_snapshot():
    """The facade must NOT leak bridge orchestration. fetch_snapshot,
    _sync_magnet_levels, and BridgeClient are dashboard-process-only."""
    import bookmap_mcp.signal_engine as se
    assert "fetch_snapshot" not in se.__all__
    assert "_sync_magnet_levels" not in se.__all__
    assert "BridgeClient" not in se.__all__
    assert "BridgeError" not in se.__all__
