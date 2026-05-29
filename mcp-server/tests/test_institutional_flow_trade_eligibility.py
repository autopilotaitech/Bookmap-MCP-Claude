"""Tests for flow.trade_eligibility (T4 dial-in change 2026-05-28).

Deterministic stand-aside gate mirroring lt_signal_quality's shape. Encodes
the operator's discipline rule: when conditions are "bullshit" (dead tape,
spoof-heavy chop, or a whipsaw lockout) the system says NO_TRADE; marginal
conditions (spoofed LT or a low-edge chop window) are DIAL_IN_ONLY; otherwise
LIVE_OK. No LLM, no new inputs -- pure function of already-computed signals.

Contract:
  NO_TRADE      : DEAD tape, OR whipsaw lockout active, OR (chop window AND
                  LIKELY_SPOOFED LT).
  DIAL_IN_ONLY  : LIKELY_SPOOFED LT (not in chop), OR chop window alone.
  LIVE_OK       : none of the above.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from bookmap_mcp import institutional_flow as iflow


_ALIAS = "TE.TEST"


@pytest.fixture(autouse=True)
def _reset_state():
    iflow.reset_state()
    yield
    iflow.reset_state()


def _ts(hh: int = 9, mm: int = 0) -> int:
    from datetime import datetime as _dt
    if iflow._CT is None:
        return 1_700_000_000_000
    return int(_dt(2026, 5, 27, hh, mm, 0, tzinfo=iflow._CT).timestamp() * 1000)


# --- pure decision-tree truth table ---------------------------------------


def test_clean_conditions_are_live_ok():
    elig, reason = iflow._compute_trade_eligibility("RELIABLE", None, False)
    assert elig == "LIVE_OK"
    assert reason == ""


def test_dead_tape_is_no_trade():
    elig, reason = iflow._compute_trade_eligibility("DEAD", None, False)
    assert elig == "NO_TRADE"
    assert reason


def test_whipsaw_lockout_is_no_trade():
    elig, reason = iflow._compute_trade_eligibility("RELIABLE", None, True)
    assert elig == "NO_TRADE"
    assert "lockout" in reason.lower()


def test_chop_plus_spoof_is_no_trade():
    elig, reason = iflow._compute_trade_eligibility("LIKELY_SPOOFED", "us_lunch", False)
    assert elig == "NO_TRADE"


def test_spoof_alone_is_dial_in_only():
    elig, reason = iflow._compute_trade_eligibility("LIKELY_SPOOFED", None, False)
    assert elig == "DIAL_IN_ONLY"
    assert reason


def test_chop_alone_is_dial_in_only():
    elig, reason = iflow._compute_trade_eligibility("RELIABLE", "us_lunch", False)
    assert elig == "DIAL_IN_ONLY"
    assert reason


def test_dead_takes_priority_over_chop_and_lockout():
    # Most-restrictive class wins regardless of other flags.
    elig, _ = iflow._compute_trade_eligibility("DEAD", "us_lunch", True)
    assert elig == "NO_TRADE"


# --- end-to-end wiring into compute_institutional_flow --------------------


def _snap(*, ts_ms: int, total_vol_30s: int = 1000) -> Dict[str, Any]:
    half = total_vol_30s // 2
    return {
        "ts_ms": ts_ms,
        "alias": _ALIAS,
        "book": {"mid": 30000.0},
        "or_levels": {"orHigh": 30050.0, "orLow": 29950.0, "levels": []},
        "flow": {"ofiZ": 0.0, "cvdDeltaZ": 0.0, "biasScore": 0.0,
                 "regime": "BALANCED"},
        "pull_stack": {"windows": [
            {"label": "BBO", "bias": "NEUTRAL", "zScore": 0.0},
            {"label": "1m", "bias": "NEUTRAL", "zScore": 0.0},
            {"label": "3m", "bias": "NEUTRAL", "zScore": 0.0},
        ]},
        "lt_liquidity": {"ratio": 0.0},
        "micro_events": {"events": []},
        "tape_buckets": {"buckets": [
            {"label": "1-10", "buyVol30s": half, "sellVol30s": half,
             "buyVol5m": 0, "sellVol5m": 0},
        ]},
        "trend_analyzer": {"warmedUp": False, "fast": {
            "direction": "NEUTRAL", "directionSign": 0, "chop": False,
        }},
    }


def test_field_present_and_live_ok_under_clean_conditions():
    out = iflow.compute_institutional_flow(_snap(ts_ms=_ts(hh=9)), _ALIAS)
    assert out["trade_eligibility"] == "LIVE_OK"
    assert out["trade_eligibility_reason"] == ""


def test_dead_tape_end_to_end_is_no_trade():
    out = iflow.compute_institutional_flow(
        _snap(ts_ms=_ts(hh=9), total_vol_30s=0), _ALIAS)
    assert out["trade_eligibility"] == "NO_TRADE"
    assert out["trade_eligibility_reason"]


def test_chop_window_end_to_end_is_dial_in_only():
    # 11:30 CT is inside the us_lunch chop window with healthy tape.
    out = iflow.compute_institutional_flow(_snap(ts_ms=_ts(hh=11, mm=30)), _ALIAS)
    assert out["trade_eligibility"] == "DIAL_IN_ONLY"
