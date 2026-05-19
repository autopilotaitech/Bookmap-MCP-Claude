"""Pins the source-share cap math used to bound a single source's contribution.

Cluster caps cannot guarantee a per-source share bound because the composite
normalizes by sum(|effective|): if every other source has reliability 0, a
single source's cluster-scaled weight still produces a composite equal to its
own score. The source-share cap solves this:

    For cap c, max |w_self| satisfies |w_self| / (|w_self| + other) <= c
    =>  |w_self| <= c * other / (1 - c)
    When other == 0, |w_self| is forced to 0.

These tests pin that contract.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402


def test_trend_alone_zeroed_when_others_silent():
    eff = {"trend_analyzer": 0.06, "flow_ofi": 0.0, "regime": 0.0}
    out = d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.10})
    assert out["trend_analyzer"] == 0.0
    # Others untouched.
    assert out["flow_ofi"] == 0.0
    assert out["regime"] == 0.0


def test_trend_share_bounded_when_others_present():
    eff = {"trend_analyzer": 0.50,
           "flow_ofi":       0.20,
           "regime":         0.10,
           "vwap_slope":     0.30}
    # other = 0.60 ;  max = 0.10 * 0.60 / 0.90 = 0.0666...
    out = d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.10})
    expected_max = 0.10 * 0.60 / 0.90
    assert out["trend_analyzer"] == pytest.approx(expected_max, abs=1e-9)
    # Realized share is exactly the cap.
    other = sum(abs(w) for n, w in out.items() if n != "trend_analyzer")
    share = abs(out["trend_analyzer"]) / (abs(out["trend_analyzer"]) + other)
    assert share == pytest.approx(0.10, abs=1e-9)


def test_share_cap_preserves_sign():
    eff_neg = {"trend_analyzer": -0.99, "flow_ofi": 0.10, "regime": 0.10}
    out = d._conv_apply_source_share_caps(eff_neg, {"trend_analyzer": 0.10})
    assert out["trend_analyzer"] < 0, "sign preserved when capped"
    expected_abs = 0.10 * 0.20 / 0.90
    assert abs(out["trend_analyzer"]) == pytest.approx(expected_abs, abs=1e-9)


def test_share_cap_does_not_inflate_underweighted_source():
    # Source's existing weight is already below the cap: no change.
    eff = {"trend_analyzer": 0.01, "flow_ofi": 0.20, "regime": 0.20}
    out = d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.10})
    assert out["trend_analyzer"] == 0.01


def test_share_cap_no_op_when_source_not_in_effective():
    eff = {"flow_ofi": 0.20}
    out = d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.10})
    assert out == eff


def test_share_cap_no_op_for_empty_caps():
    eff = {"trend_analyzer": 0.99, "flow_ofi": 0.01}
    assert d._conv_apply_source_share_caps(eff, {}) == eff
    assert d._conv_apply_source_share_caps(eff, None) == eff


def test_share_cap_rejects_degenerate_cap_values():
    eff = {"trend_analyzer": 0.99, "flow_ofi": 0.10}
    # cap >= 1 would divide by zero in the formula. Should be a no-op.
    assert d._conv_apply_source_share_caps(eff, {"trend_analyzer": 1.0})["trend_analyzer"] == 0.99
    assert d._conv_apply_source_share_caps(eff, {"trend_analyzer": 0.0})["trend_analyzer"] == 0.99
    assert d._conv_apply_source_share_caps(eff, {"trend_analyzer": -0.5})["trend_analyzer"] == 0.99
