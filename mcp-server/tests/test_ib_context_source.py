"""Pins _source_ib_context against the real bridge field names.

MomentumHandler.buildIb emits `ibHigh`, `ibLow`, `ibComplete` (plus `ibRange`,
`ibSizeTag`, `sessionHigh`, ...). Earlier versions of _source_ib_context read
`high`/`low`/`complete`, which never matched bridge output, so the source
silently contributed reliability=0 in every live snapshot.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def _snap(ib_payload: dict, mid: float):
    return {"flow": {"ib": ib_payload}, "book": {"mid": mid}}


# --- bridge-shaped (canonical) payloads -------------------------------------

def test_bridge_breakout_above_ib_high_is_bullish():
    ib = {"ibHigh": 17500.0, "ibLow": 17400.0, "ibComplete": True,
          "ibRange": 100.0, "ibSizeTag": "NORMAL"}
    out = d._source_ib_context(_snap(ib, mid=17510.0))
    assert out["score"] > 0.0, "mid above ibHigh must produce positive score"
    assert out["reliability"] == 1.0
    assert "IB-H" in out["reason"]


def test_bridge_breakdown_below_ib_low_is_bearish():
    ib = {"ibHigh": 17500.0, "ibLow": 17400.0, "ibComplete": True,
          "ibRange": 100.0, "ibSizeTag": "NORMAL"}
    out = d._source_ib_context(_snap(ib, mid=17390.0))
    assert out["score"] < 0.0, "mid below ibLow must produce negative score"
    assert out["reliability"] == 1.0
    assert "IB-L" in out["reason"]


def test_bridge_inside_ib_is_neutral_but_reliable():
    ib = {"ibHigh": 17500.0, "ibLow": 17400.0, "ibComplete": True}
    out = d._source_ib_context(_snap(ib, mid=17450.0))
    assert out["score"] == 0.0
    assert out["reliability"] == pytest.approx(0.6, abs=1e-9)
    assert "inside" in out["reason"].lower()


def test_bridge_incomplete_ib_is_reliability_zero():
    ib = {"ibHigh": 17500.0, "ibLow": 17400.0, "ibComplete": False,
          "dayType": "TREND_DAY"}
    out = d._source_ib_context(_snap(ib, mid=17510.0))
    assert out["score"] == 0.0
    assert out["reliability"] == 0.0


def test_bridge_missing_ib_is_reliability_zero():
    out = d._source_ib_context({"flow": {"ib": {}}, "book": {"mid": 17500.0}})
    assert out["score"] == 0.0
    assert out["reliability"] == 0.0
    assert out["reason"] == "no ib"


def test_missing_flow_payload_is_reliability_zero():
    out = d._source_ib_context({"book": {"mid": 17500.0}})
    assert out["score"] == 0.0
    assert out["reliability"] == 0.0


def test_bridge_zero_or_inverted_range_is_reliability_zero():
    """ibHigh <= ibLow is impossible from a healthy bridge but must not blow up."""
    ib = {"ibHigh": 17400.0, "ibLow": 17500.0, "ibComplete": True}
    out = d._source_ib_context(_snap(ib, mid=17450.0))
    assert out["reliability"] == 0.0


def test_bridge_missing_mid_is_reliability_zero():
    ib = {"ibHigh": 17500.0, "ibLow": 17400.0, "ibComplete": True}
    out = d._source_ib_context({"flow": {"ib": ib}, "book": {}})
    assert out["reliability"] == 0.0


# --- legacy field-name fallback (for old replays / archived CSV snapshots) --

def test_legacy_high_low_complete_still_accepted():
    """Pre-fix replays emitted `high`/`low`/`complete`. They must still parse."""
    ib = {"high": 17500.0, "low": 17400.0, "complete": True}
    out = d._source_ib_context(_snap(ib, mid=17510.0))
    assert out["score"] > 0.0
    assert out["reliability"] == 1.0


def test_bridge_field_takes_precedence_over_legacy_when_both_present():
    """If a payload somehow carries both, the bridge canonical field wins."""
    ib = {"ibHigh": 17500.0, "high": 99999.0,
          "ibLow": 17400.0, "low": -1.0,
          "ibComplete": True, "complete": False}
    out = d._source_ib_context(_snap(ib, mid=17510.0))
    assert out["score"] > 0.0
    assert out["reliability"] == 1.0
    # Range used was the bridge 100-pt range, not the bogus legacy 100_000+ range
    raw = out["raw"]
    assert raw["ib_high"] == pytest.approx(17500.0)
