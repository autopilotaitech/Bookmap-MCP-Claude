"""Pins that the fallback CONVICTION_* module constants in dashboard.py mirror
pax_weights.json for every registered source.

The fallback constants are the last-resort defaults used when pax_weights.json
is missing or corrupted. Any source registered in `_CONVICTION_SOURCES` MUST
have a non-zero base weight in `CONVICTION_SOURCE_WEIGHTS` and live in the
correct correlation cluster — otherwise the dashboard silently drops the
source on the fallback path.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def test_every_registered_source_has_nonzero_fallback_weight():
    missing = [name for name in d._CONVICTION_SOURCES
               if name not in d.CONVICTION_SOURCE_WEIGHTS
               or d.CONVICTION_SOURCE_WEIGHTS[name] <= 0.0]
    assert not missing, (
        f"Registered conviction sources missing from fallback weight table: "
        f"{missing}. Add to CONVICTION_SOURCE_WEIGHTS or remove from "
        f"_CONVICTION_SOURCES.")


def test_vwap_or_gate_present_in_fallback_constants():
    assert "vwap_or_gate" in d.CONVICTION_SOURCE_WEIGHTS
    assert d.CONVICTION_SOURCE_WEIGHTS["vwap_or_gate"] > 0.0
    assert "vwap_or_gate" in d.CONVICTION_CLUSTERS["vwap"], (
        "vwap_or_gate must be a member of the vwap correlation cluster")


def test_orderbook_present_in_fallback_constants():
    assert "orderbook" in d.CONVICTION_SOURCE_WEIGHTS
    assert d.CONVICTION_SOURCE_WEIGHTS["orderbook"] > 0.0
    assert "orderbook" in d.CONVICTION_CLUSTERS["microstructure"], (
        "orderbook must be a member of the microstructure correlation cluster")


def test_vwap_cluster_cap_matches_config():
    assert d.CONVICTION_CLUSTER_CAPS["vwap"] == pytest.approx(0.30, abs=1e-9), (
        "CONVICTION_CLUSTER_CAPS['vwap'] must equal pax_weights.json (0.30); "
        "0.25 was the pre-vwap_or_gate value.")


def test_fallback_constants_match_pax_weights_json():
    """The on-disk pax_weights.json IS the source of truth. Module constants
    must mirror it exactly for every registered source / cluster / cap."""
    import json
    with open(ROOT / "bookmap_mcp" / "pax_weights.json", "r", encoding="utf-8") as fh:
        cfg = d._strip_meta(json.load(fh))

    json_weights = cfg["conviction_source_weights"]
    for name in d._CONVICTION_SOURCES:
        assert name in json_weights, f"pax_weights.json missing source {name}"
        assert d.CONVICTION_SOURCE_WEIGHTS.get(name) == json_weights[name], (
            f"Fallback weight for {name} ({d.CONVICTION_SOURCE_WEIGHTS.get(name)}) "
            f"does not match pax_weights.json ({json_weights[name]})")

    json_clusters = cfg["conviction_clusters"]
    for cluster, members in json_clusters.items():
        assert set(d.CONVICTION_CLUSTERS.get(cluster, [])) == set(members), (
            f"Fallback cluster '{cluster}' members differ from pax_weights.json: "
            f"fallback={d.CONVICTION_CLUSTERS.get(cluster)} json={members}")

    json_caps = cfg["conviction_cluster_caps"]
    for cluster, cap in json_caps.items():
        assert d.CONVICTION_CLUSTER_CAPS.get(cluster) == pytest.approx(cap, abs=1e-9), (
            f"Fallback cap for cluster '{cluster}' differs from pax_weights.json")


def test_corrupted_pax_weights_still_carries_vwap_or_gate_and_orderbook(monkeypatch, tmp_path):
    """When pax_weights.json is corrupted, the fallback defaults must still
    register vwap_or_gate and orderbook so they cannot silently drop."""
    corrupted = tmp_path / "broken.json"
    corrupted.write_text("{not valid json at all}")
    monkeypatch.setattr(d, "_PAX_WEIGHTS_PATH", corrupted)
    monkeypatch.setattr(d, "_PAX_WEIGHTS_MTIME", 0.0)
    d._PAX_WEIGHTS_CACHE.clear()

    loaded = d._load_pax_weights()
    weights = loaded["conviction_source_weights"]
    assert weights.get("vwap_or_gate", 0.0) > 0.0
    assert weights.get("orderbook", 0.0) > 0.0

    clusters = loaded["conviction_clusters"]
    assert "vwap_or_gate" in clusters["vwap"]
    assert "orderbook" in clusters["microstructure"]

    caps = loaded["conviction_cluster_caps"]
    assert caps["vwap"] == pytest.approx(0.30, abs=1e-9)


def test_missing_pax_weights_still_carries_vwap_or_gate_and_orderbook(monkeypatch, tmp_path):
    """File-not-found path: fallback defaults still register both sources."""
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(d, "_PAX_WEIGHTS_PATH", missing)
    monkeypatch.setattr(d, "_PAX_WEIGHTS_MTIME", 0.0)
    d._PAX_WEIGHTS_CACHE.clear()

    loaded = d._load_pax_weights()
    weights = loaded["conviction_source_weights"]
    assert weights.get("vwap_or_gate", 0.0) > 0.0
    assert weights.get("orderbook", 0.0) > 0.0
    assert "vwap_or_gate" in loaded["conviction_clusters"]["vwap"]
    assert "orderbook" in loaded["conviction_clusters"]["microstructure"]
