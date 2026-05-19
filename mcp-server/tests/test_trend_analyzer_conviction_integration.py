"""End-to-end: trend_analyzer flows into compute_session_conviction output.

These tests stand up a synthetic snap with a single fully-warm trend_analyzer
payload (all other sources silent) and verify:
  - sourceScores["trend_analyzer"] is populated
  - effectiveWeights["trend_analyzer"] is zeroed when no other source has
    any reliability (the source-share cap fires)
  - composite is exactly 0 in that single-source scenario
  - the legacy components/instantaneous/weights keys still hold the v1 vocabulary
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def _reset():
    d._CONVICTION_STATE.clear()


def _bare_snap(trend_analyzer_payload=None):
    """Snapshot fixture: every other source returns reliability=0 by absence,
    so only trend_analyzer can possibly contribute weight."""
    return {
        "health": "ok",
        "alias": "NQM6.CME@RITHMIC",
        "trend_analyzer": trend_analyzer_payload,
        # Every other field is intentionally absent so the source helpers
        # return reliability 0.
    }


def _full_trend_payload():
    now_ms = int(time.time() * 1000)
    return {
        "alias": "NQ", "asOfNanos": 0,
        "eventMs": now_ms, "updatedAtMs": now_ms,
        "lastClose": 100.0, "warmedUp": True,
        "score": 1.0, "reliabilityHint": 1.0,
        "fast": {"direction": "UP", "directionSign": 1, "confidence": 100,
                 "switched": False, "chop": False, "trendLine": 100.0,
                 "candleIntervalMillis": 15000, "candleCount": 100},
        "slow": {"direction": "UP", "directionSign": 1, "confidence": 100,
                 "switched": False, "chop": False, "trendLine": 100.0,
                 "candleIntervalMillis": 60000, "candleCount": 25},
    }


def test_trend_analyzer_appears_in_source_scores():
    _reset()
    snap = _bare_snap(_full_trend_payload())
    out = d.compute_session_conviction(snap)
    assert out is not None
    assert "trend_analyzer" in out["sourceScores"]
    assert "trend_analyzer" in out["sourceReliability"]
    assert "trend_analyzer" in out["effectiveWeights"]


def test_trend_analyzer_alone_cannot_move_composite():
    """The audit fix: with every other source silent, the source-share cap
    forces trend_analyzer's effective weight to 0 so the composite is 0,
    not 1.0."""
    _reset()
    snap = _bare_snap(_full_trend_payload())
    out = d.compute_session_conviction(snap)
    assert out is not None
    assert out["effectiveWeights"]["trend_analyzer"] == 0.0
    assert out["score"] == 0.0
    assert out["trend"] == "CHOP" or out["trend"] == "MIXED" or out["trend"] == "CHOP"


def test_trend_analyzer_zero_share_when_only_source():
    _reset()
    snap = _bare_snap(_full_trend_payload())
    d.compute_session_conviction(snap)  # warm ring
    # Slide multiple ticks; the share cap still holds.
    for _ in range(5):
        out = d.compute_session_conviction(snap)
    assert out["effectiveWeights"]["trend_analyzer"] == 0.0
    assert out["score"] == 0.0


def test_legacy_keys_intact_after_trend_added():
    """compute_session_conviction's `components` / `instantaneous` / `weights`
    are read by dashboard.js. They MUST keep the 7 legacy v1 keys. Adding a
    new v2 source must not change that shape."""
    _reset()
    snap = _bare_snap(_full_trend_payload())
    out = d.compute_session_conviction(snap)
    expected = {"regime", "bias", "vwap", "vp", "slope", "level", "ib"}
    assert set(out["components"]) == expected
    assert set(out["instantaneous"]) == expected
    assert set(out["weights"]) == expected
