"""Pins that trend_analyzer survives in EVERY config path.

The module constants (CONVICTION_SOURCE_WEIGHTS, CONVICTION_SOURCE_SHARE_CAPS)
are the last-resort fallback when pax_weights.json is missing or corrupted.
If trend_analyzer is omitted from any of them, the dashboard silently drops
the feature.

Also verifies the source-share cap enforces ≤10% normalized share when
other sources are present.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def test_module_constant_includes_trend_analyzer():
    assert "trend_analyzer" in d.CONVICTION_SOURCE_WEIGHTS, (
        "CONVICTION_SOURCE_WEIGHTS must include trend_analyzer as the last-resort fallback")
    assert d.CONVICTION_SOURCE_WEIGHTS["trend_analyzer"] == 0.06


def test_module_constant_share_cap_includes_trend_analyzer():
    assert "trend_analyzer" in d.CONVICTION_SOURCE_SHARE_CAPS
    assert d.CONVICTION_SOURCE_SHARE_CAPS["trend_analyzer"] == 0.10


def test_load_pax_weights_defaults_include_trend_analyzer(monkeypatch, tmp_path):
    """When pax_weights.json is missing entirely, _load_pax_weights uses
    the in-memory defaults. Those defaults MUST also include trend_analyzer."""
    # Point the loader at a path that doesn't exist; trigger the
    # FileNotFoundError branch.
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(d, "_PAX_WEIGHTS_PATH", missing)
    monkeypatch.setattr(d, "_PAX_WEIGHTS_MTIME", 0.0)
    # Clear the cache so the loader actually re-reads on this call.
    d._PAX_WEIGHTS_CACHE.clear()
    loaded = d._load_pax_weights()
    assert "conviction_source_weights" in loaded, (
        "fallback defaults must include conviction_source_weights")
    assert "trend_analyzer" in loaded["conviction_source_weights"]
    assert loaded["conviction_source_weights"]["trend_analyzer"] == 0.06
    assert "conviction_source_share_caps" in loaded
    assert loaded["conviction_source_share_caps"]["trend_analyzer"] == 0.10


def test_load_pax_weights_corrupted_json_falls_back(monkeypatch, tmp_path):
    """Corrupted pax_weights.json → fallback defaults still include trend_analyzer."""
    corrupted = tmp_path / "broken.json"
    corrupted.write_text("{garbage not json}")
    monkeypatch.setattr(d, "_PAX_WEIGHTS_PATH", corrupted)
    monkeypatch.setattr(d, "_PAX_WEIGHTS_MTIME", 0.0)
    d._PAX_WEIGHTS_CACHE.clear()
    loaded = d._load_pax_weights()
    assert "trend_analyzer" in loaded.get("conviction_source_weights", {})


def test_share_cap_caps_at_10_percent_share_when_others_present():
    """With at least one real other source, trend_analyzer's normalized share
    is capped at 10%. Verified analytically:
        max_w_trend = other × 0.10/0.90
        share = max_w_trend / (max_w_trend + other) = 0.10
    """
    eff = {"trend_analyzer": 0.99, "flow_ofi": 0.40, "regime": 0.20}
    out = d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.10})
    other = sum(abs(v) for k, v in out.items() if k != "trend_analyzer")
    share = abs(out["trend_analyzer"]) / (abs(out["trend_analyzer"]) + other)
    assert share == pytest.approx(0.10, abs=1e-9)


def test_missing_trend_data_contributes_zero_to_composite():
    """If the bridge endpoint returns nothing / errors, _source_trend_analyzer
    returns reliability=0 and the conviction engine's effective_weight is 0."""
    # Three independent missing-data cases that ALL must yield reliability 0.
    for ta in [None, {"_error": "BridgeError"}, {}]:
        snap = {"trend_analyzer": ta}
        out = d._source_trend_analyzer(snap)
        assert out["reliability"] == 0.0, f"reliability must be 0 for payload {ta!r}"


def test_trend_analyzer_contributes_under_normal_config(tmp_path, monkeypatch):
    """Sanity: with real pax_weights.json (config on disk), trend_analyzer
    is in the loaded weights table at its configured base weight."""
    d._PAX_WEIGHTS_CACHE.clear()
    cfg = d._load_pax_weights()
    weights = cfg.get("conviction_source_weights", {})
    assert weights.get("trend_analyzer") == 0.06
    share = cfg.get("conviction_source_share_caps", {})
    assert share.get("trend_analyzer") == 0.10
