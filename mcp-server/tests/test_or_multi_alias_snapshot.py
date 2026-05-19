"""Per-alias OR row selection inside fetch_snapshot.

When NQM6 and ESM6 are attached together each alias must receive its own
OR row, produce its own or_levels grid (different magnet prices), and
post different magnet payloads to the bridge.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d                                              # noqa: E402
from bookmap_mcp.config import BridgeConfig                                    # noqa: E402

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
        self.calls: list[tuple[str, str]] = []
        self.posts: list[tuple[str, dict]] = []
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
    holder: dict = {"clients": []}
    def _factory(*a, **kw):
        c = _FakeClient(*a, **kw)
        c._instruments = holder["instruments"]
        holder["clients"].append(c)
        return c
    monkeypatch.setattr(d, "BridgeClient", _factory)
    monkeypatch.setattr(d, "_get_pax_collector", lambda: None)
    monkeypatch.setattr(d, "pax_record", lambda *a, **kw: None)
    monkeypatch.setattr(d, "_sync_bridge_config", lambda *a, **kw: None)
    return holder


@pytest.fixture(autouse=True)
def _reset_magnet_cache():
    """Each test starts with an empty magnet cache so fresh prices aren't
    TTL-suppressed."""
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()
    yield
    with d._LAST_MAGNETS_LOCK:
        d._LAST_MAGNETS.clear()


def test_each_alias_gets_its_own_or_row(fake_bridge, monkeypatch, tmp_path):
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6", "fullName": "Nasdaq"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6", "fullName": "S&P"},
    ]}

    snap = d.fetch_snapshot()
    nq = snap["aliases"]["NQM6.CME@RITHMIC"]
    es = snap["aliases"]["ESM6.CME@RITHMIC"]

    assert float(nq["or_row"]["orHigh"]) == pytest.approx(21500.0)
    assert float(nq["or_row"]["orLow"])  == pytest.approx(21400.0)
    assert float(es["or_row"]["orHigh"]) == pytest.approx(5900.0)
    assert float(es["or_row"]["orLow"])  == pytest.approx(5870.0)


def test_each_alias_gets_its_own_or_levels_grid(fake_bridge, monkeypatch, tmp_path):
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6"},
    ]}

    snap = d.fetch_snapshot()
    nq_levels = snap["aliases"]["NQM6.CME@RITHMIC"]["or_levels"]
    es_levels = snap["aliases"]["ESM6.CME@RITHMIC"]["or_levels"]

    assert nq_levels is not None and es_levels is not None
    assert float(nq_levels["orHigh"]) == pytest.approx(21500.0)
    assert float(es_levels["orHigh"]) == pytest.approx(5900.0)
    # Prices must come from each alias's own OR, not a shared one.
    nq_prices = [l["price"] for l in nq_levels["levels"]]
    es_prices = [l["price"] for l in es_levels["levels"]]
    assert min(nq_prices) > 10000.0
    assert max(es_prices) < 10000.0


def test_per_alias_magnet_post_uses_per_alias_grid(fake_bridge, monkeypatch, tmp_path):
    """The magnet POST payload must contain prices anchored at each alias's OR."""
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6"},
    ]}

    d.fetch_snapshot()

    all_posts = [p for c in fake_bridge["clients"]
                 for p in c.posts if p[0] == "/magnet_levels"]
    by_alias = {payload["alias"]: payload["levels"] for _, payload in all_posts}

    assert "NQM6.CME@RITHMIC" in by_alias
    assert "ESM6.CME@RITHMIC" in by_alias
    nq_prices = [float(p) for p in by_alias["NQM6.CME@RITHMIC"].split(",")]
    assert min(nq_prices) > 10000.0
    es_prices = [float(p) for p in by_alias["ESM6.CME@RITHMIC"].split(",")]
    assert max(es_prices) < 10000.0


def test_top_level_or_row_is_default_alias_row(fake_bridge, monkeypatch, tmp_path):
    """Legacy top-level `or_row` must mirror the default (first) alias."""
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    es_csv = tmp_path / "openrange-signals-ESM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    _write_csv(es_csv, "ESM6", 5900.0, 5870.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6"},
    ]}

    snap = d.fetch_snapshot()
    assert snap["alias"] == "NQM6.CME@RITHMIC"
    assert float(snap["or_row"]["orHigh"]) == pytest.approx(21500.0)


def test_alias_without_csv_gets_none_or_row(fake_bridge, monkeypatch, tmp_path):
    """An attached alias whose symbol has no OR CSV must show or_row=None
    and or_levels=None — not borrow another alias's range."""
    nq_csv = tmp_path / "openrange-signals-NQM6.csv"
    _write_csv(nq_csv, "NQM6", 21500.0, 21400.0)
    monkeypatch.setattr(d, "OR_SIGNAL_GLOBS",
                        [str(tmp_path / "openrange-signals-*.csv")])

    fake_bridge["instruments"] = {"instruments": [
        {"alias": "NQM6.CME@RITHMIC", "symbol": "NQM6"},
        {"alias": "ESM6.CME@RITHMIC", "symbol": "ESM6"},
    ]}
    snap = d.fetch_snapshot()
    nq = snap["aliases"]["NQM6.CME@RITHMIC"]
    es = snap["aliases"]["ESM6.CME@RITHMIC"]
    assert nq["or_row"] is not None
    assert es["or_row"] is None
    assert es["or_levels"] is None
