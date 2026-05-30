"""Dashboard market-data freshness vs compose time (truth split).

compute_market_freshness must return a BRIDGE/FEED timestamp that proves the
underlying market data is live -- never the dashboard's own compose wall-clock.
When no real feed timestamp exists it returns None so the downstream risk gate
fails closed.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import dashboard  # noqa: E402

cmf = dashboard.compute_market_freshness


def test_prefers_trend_updated_at_ms():
    r = cmf({"updatedAtMs": 1_779_917_709_894, "eventMs": 1_779_915_598_311},
            {"generatedNanos": 1_779_915_598_411_228_600},
            [{"nanos": 1_779_915_598_411_228_600}])
    assert r["ms"] == 1_779_917_709_894
    assert r["source"] == "trend_analyzer.updatedAtMs"
    assert r["reason"] is None


def test_falls_back_to_recent_trade_nanos():
    r = cmf({"_error": "bridge down"}, {},
            [{"nanos": 1_779_915_598_000_000_000},
             {"nanos": 1_779_915_599_000_000_000}])  # newest is the 2nd
    assert r["ms"] == 1_779_915_599_000  # max nanos // 1e6
    assert r["source"] == "recent_trade.nanos"


def test_falls_back_to_orderbook_generated_nanos():
    r = cmf(None, {"generatedNanos": 1_779_915_598_411_228_600}, [])
    assert r["ms"] == 1_779_915_598_411
    assert r["source"] == "orderbook.generatedNanos"


def test_none_when_no_real_feed_timestamp():
    # No bridge timestamps anywhere -> cannot prove freshness.
    r = cmf({"_error": "x"}, {"mid": 30000.0}, [])
    assert r["ms"] is None
    assert r["source"] is None
    assert r["reason"] == "no_market_feed_timestamp"


def test_ignores_garbage_timestamps():
    r = cmf({"updatedAtMs": "nope"}, {"generatedNanos": 0},
            [{"nanos": None}, {"nanos": -5}])
    assert r["ms"] is None
    assert r["reason"] == "no_market_feed_timestamp"


def test_dashboard_snapshot_uses_split_fields_not_compose_time():
    # Source-contract guard: the top-level snapshot must carry composedAtMs +
    # marketDataAsOfMs (from compute_market_freshness), and must NOT resurrect a
    # top-level `asOfMs` market field fed by time.time().
    src = Path(dashboard.__file__).read_text(encoding="utf-8")
    assert '"composedAtMs"' in src
    assert '"marketDataAsOfMs": _mf["ms"]' in src
    assert '"marketFreshnessSource"' in src
    # The top-level snapshot literal must not set a bare "asOfMs" key.
    assert not re.search(r'\n        "asOfMs":\s*int\(time', src)
