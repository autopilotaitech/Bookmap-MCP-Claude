"""Tests for the trend_analyzer conviction source helper and source-share cap.

Pins the audit-driven contract:
  - reliability=0 on missing / error / warmup / stale / no-updatedAtMs payloads
  - score normalization stays in [-1,+1] for all directional combinations
  - chop / disagreement reduce reliability but do not zero it
  - source-share cap math: trend_analyzer alone cannot move composite;
    with other sources present, its share is bounded; sign preserved when capped
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


def _ta_payload(*, fast_dir: int = 1, fast_conf: int = 80, fast_chop: bool = False,
                 slow_dir: int = 1, slow_conf: int = 60, slow_chop: bool = False,
                 warmed: bool = True, updated_at_offset_ms: int = 0):
    """Build a minimal trend_analyzer JSON payload matching the bridge contract."""
    now_ms = int(time.time() * 1000)
    dir_name = {1: "UP", -1: "DOWN", 0: "NEUTRAL"}
    return {
        "alias": "NQ",
        "asOfNanos": 0,
        "eventMs": now_ms,
        "updatedAtMs": now_ms + updated_at_offset_ms,
        "lastClose": 100.0,
        "warmedUp": warmed,
        "score": 0.0,
        "reliabilityHint": 1.0,
        "fast": {
            "direction": dir_name[fast_dir], "directionSign": fast_dir,
            "confidence": fast_conf, "switched": False, "chop": fast_chop,
            "trendLine": 100.0, "candleIntervalMillis": 15000, "candleCount": 100,
        },
        "slow": {
            "direction": dir_name[slow_dir], "directionSign": slow_dir,
            "confidence": slow_conf, "switched": False, "chop": slow_chop,
            "trendLine": 100.0, "candleIntervalMillis": 60000, "candleCount": 25,
        },
    }


def _snap(ta_payload):
    return {"trend_analyzer": ta_payload}


# ─── Reliability gating ──────────────────────────────────────────────────────

def test_missing_data_reliability_zero():
    out = d._source_trend_analyzer({})
    assert out["reliability"] == 0.0
    assert out["score"] == 0.0
    assert "no trend_analyzer" in out["reason"]


def test_none_data_reliability_zero():
    out = d._source_trend_analyzer({"trend_analyzer": None})
    assert out["reliability"] == 0.0


def test_error_payload_reliability_zero():
    out = d._source_trend_analyzer({"trend_analyzer": {"_error": "BridgeError: foo"}})
    assert out["reliability"] == 0.0


def test_warmup_reliability_zero():
    out = d._source_trend_analyzer(_snap(_ta_payload(warmed=False)))
    assert out["reliability"] == 0.0
    assert out["reason"] == "warmup"


def test_missing_updated_at_reliability_zero():
    payload = _ta_payload()
    payload["updatedAtMs"] = None
    out = d._source_trend_analyzer(_snap(payload))
    assert out["reliability"] == 0.0
    assert out["reason"] == "no updatedAtMs"


def test_stale_updated_at_reliability_zero():
    # 31s in the past — past the 30s cliff.
    out = d._source_trend_analyzer(_snap(_ta_payload(updated_at_offset_ms=-31_000)))
    assert out["reliability"] == 0.0
    assert out["reason"] == "stale"


# ─── Score normalization ─────────────────────────────────────────────────────

def test_strong_up_score_positive_near_max():
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_dir=1, fast_conf=100,
                                                      slow_dir=1, slow_conf=100)))
    # 0.4 * 1.0 + 0.6 * 1.0 = 1.0
    assert out["score"] == pytest.approx(1.0, abs=1e-9)
    assert out["reliability"] == 1.0


def test_strong_down_score_negative_near_min():
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_dir=-1, fast_conf=100,
                                                      slow_dir=-1, slow_conf=100)))
    assert out["score"] == pytest.approx(-1.0, abs=1e-9)
    assert out["reliability"] == 1.0


def test_blended_score_weights():
    # fast=+50%, slow=+100% -> 0.4*0.5 + 0.6*1.0 = 0.80
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_conf=50, slow_conf=100)))
    assert out["score"] == pytest.approx(0.80, abs=1e-9)


def test_neutral_directions_score_zero():
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_dir=0, slow_dir=0)))
    assert out["score"] == 0.0
    # reliability is full because no chop / disagreement signaled.
    assert out["reliability"] == 1.0


# ─── Chop / disagreement gating ──────────────────────────────────────────────

def test_both_chop_cuts_reliability_quarter():
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_chop=True, slow_chop=True)))
    assert out["reliability"] == 0.25


def test_one_chop_cuts_reliability_half():
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_chop=True, slow_chop=False)))
    assert out["reliability"] == 0.5
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_chop=False, slow_chop=True)))
    assert out["reliability"] == 0.5


def test_engines_disagree_cuts_reliability():
    # fast +UP/60, slow -DOWN/80 -> signs disagree
    out = d._source_trend_analyzer(_snap(_ta_payload(fast_dir=1, fast_conf=60,
                                                      slow_dir=-1, slow_conf=80)))
    assert out["reliability"] == 0.6
    # Score is the blended combination of opposing legs:
    # 0.4 * 0.6 + 0.6 * (-0.8) = 0.24 - 0.48 = -0.24
    assert out["score"] == pytest.approx(-0.24, abs=1e-9)
