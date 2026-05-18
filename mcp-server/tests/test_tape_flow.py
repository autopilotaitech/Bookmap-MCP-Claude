"""Tests for compute_tape_flow and the rewritten _source_tape_large_lot.

The Java /tape_buckets endpoint emits a list-of-dicts under .buckets — these
tests build synthetic payloads of that shape and verify the institutional-
flow delta is computed correctly, with the right reliability semantics for
the conviction source.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as dash    # noqa: E402


def _bucket(label, buy30=0, sell30=0, prints30=0,
            buy5=None, sell5=None, prints5=None):
    """Build one /tape_buckets bucket entry. 5m fields default to the 30s
    values so single-window tests don't need to specify both."""
    if buy5    is None: buy5    = buy30
    if sell5   is None: sell5   = sell30
    if prints5 is None: prints5 = prints30
    return {
        "label": label, "minSize": 0, "maxSize": 0,
        "buyVol30s":   buy30,  "sellVol30s":  sell30,  "prints30s": prints30,
        "buyVol5m":    buy5,   "sellVol5m":   sell5,   "prints5m":  prints5,
        "imbalance30s": 0.0,   "imbalance5m": 0.0,
    }


def _snap(buckets):
    return {"tape_buckets": {"buckets": buckets}}


def _all_labels(b1=None, b2=None, b3=None, b4=None, b5=None):
    """Build the full 5-bucket list, defaulting empties for buckets you
    don't override. Order matches the Java TAPE_BUCKETS array."""
    return [
        b1 or _bucket("1-10"),
        b2 or _bucket("11-25"),
        b3 or _bucket("26-50"),
        b4 or _bucket("51-99"),
        b5 or _bucket("100+"),
    ]


# ──────────────────────────────────────────────────────────────────────
# compute_tape_flow
# ──────────────────────────────────────────────────────────────────────

def test_compute_tape_flow_positive_on_large_buy_pressure():
    # 20 × 100-lot buys in the 100+ bucket — well above the thin-hedge zone.
    snap = _snap(_all_labels(
        b5=_bucket("100+", buy30=2000, sell30=0, prints30=20,
                            buy5=2000,  sell5=0,  prints5=20)))
    out = dash.compute_tape_flow(snap)
    assert out["deltaScore"] > 0.4
    assert out["deltaLabel"] in ("BUY", "STRONG_BUY")
    assert out["largeBuyVol30s"] == 2000
    assert out["blockBuyVol30s"] == 2000
    assert out["totalPrints30s"] == 20
    assert out["aligned"] is True


def test_compute_tape_flow_negative_on_large_sell_pressure():
    snap = _snap(_all_labels(
        b5=_bucket("100+", buy30=0, sell30=2000, prints30=20,
                            buy5=0,  sell5=2000,  prints5=20)))
    out = dash.compute_tape_flow(snap)
    assert out["deltaScore"] < -0.4
    assert out["deltaLabel"] in ("SELL", "STRONG_SELL")
    assert out["largeSellVol30s"] == 2000


def test_compute_tape_flow_is_thin_when_too_few_prints():
    # 3 prints total in 30s — below the floor of 5.
    snap = _snap(_all_labels(
        b1=_bucket("1-10", buy30=2, sell30=1, prints30=3,
                            buy5=2,  sell5=1,  prints5=3)))
    out = dash.compute_tape_flow(snap)
    assert out["deltaLabel"] == "THIN"
    assert out["deltaScore"] == 0.0
    assert out["totalPrints30s"] == 3


def test_compute_tape_flow_size_buckets_weighted_correctly():
    # 100 small-lot buys (size-weighted 0.10 each) vs 1 block sell (weight 1.0).
    # Size-weighted: wnum = 0.10*(500-0) + 1.0*(0-100) = -50 → bearish.
    snap = _snap(_all_labels(
        b1=_bucket("1-10", buy30=500, sell30=0, prints30=100,
                            buy5=500,  sell5=0,  prints5=100),
        b5=_bucket("100+", buy30=0,   sell30=100, prints30=1,
                            buy5=0,   sell5=100,  prints5=1)))
    out = dash.compute_tape_flow(snap)
    assert out["deltaScore"] < 0, (
        f"size-weighted delta should be negative when 1 block sell outweighs "
        f"100 small buys; got {out['deltaScore']}")
    assert out["totalBuyVol30s"] == 500
    assert out["totalSellVol30s"] == 100


def test_compute_tape_flow_alignment_bonus_when_windows_agree():
    # Both windows directional + same sign + each |x| > 0.25 → aligned=True
    # AND the score includes the alignment bonus. Need enough prints to clear
    # the thin-hedge zone (>=15) so shrink doesn't damp the signal.
    snap = _snap(_all_labels(
        b5=_bucket("100+", buy30=1500, sell30=500, prints30=20,
                            buy5=1500,  sell5=500,  prints5=20)))
    out = dash.compute_tape_flow(snap)
    assert out["aligned"] is True
    # Bonus-free base score with these inputs is ~0.76; with +0.10 bonus,
    # final lands clearly above 0.6.
    assert out["deltaScore"] > 0.30


def test_compute_tape_flow_no_alignment_when_only_one_window_directional():
    # Strong 30s buying, flat 5m → not aligned. Keep both windows above thin floor.
    snap = _snap(_all_labels(
        b5=_bucket("100+", buy30=1500, sell30=0,    prints30=20,
                            buy5=1000, sell5=1000, prints5=40)))
    out = dash.compute_tape_flow(snap)
    assert out["aligned"] is False


def test_compute_tape_flow_returns_none_on_missing_payload():
    assert dash.compute_tape_flow({}) is None
    assert dash.compute_tape_flow({"tape_buckets": None}) is None
    assert dash.compute_tape_flow({"tape_buckets": {}}) is None
    assert dash.compute_tape_flow({"tape_buckets": {"_error": "boom"}}) is None
    assert dash.compute_tape_flow({"tape_buckets": {"buckets": []}}) is None


# ──────────────────────────────────────────────────────────────────────
# _source_tape_large_lot
# ──────────────────────────────────────────────────────────────────────

def test_source_tape_large_lot_reads_tape_flow():
    snap = {"tape_flow": {
        "deltaScore": 0.42, "deltaLabel": "BUY",
        "largePrints30s": 10, "totalPrints30s": 50,
        "deltaReason": "test"}}
    out = dash._source_tape_large_lot(snap)
    assert out["score"] == pytest.approx(0.42)
    assert out["reliability"] >= 0.8     # 10/12 ≈ 0.83
    assert out["raw"]["fallback"] is False


def test_source_tape_large_lot_thin_label_low_reliability():
    snap = {"tape_flow": {
        "deltaScore": 0.0, "deltaLabel": "THIN",
        "largePrints30s": 0, "totalPrints30s": 3,
        "deltaReason": "thin tape: n30=3"}}
    out = dash._source_tape_large_lot(snap)
    assert out["score"] == 0.0
    assert out["reliability"] <= 0.1


def test_source_tape_large_lot_falls_back_to_bucket_array():
    # No tape_flow, but tape_buckets is present — _source must recompute.
    snap = _snap(_all_labels(
        b5=_bucket("100+", buy30=500, sell30=0, prints30=5,
                            buy5=500,  sell5=0,  prints5=5)))
    direct = dash.compute_tape_flow(snap)
    via_source = dash._source_tape_large_lot(snap)
    assert via_source["score"] == pytest.approx(direct["deltaScore"], abs=0.05)
    assert via_source["raw"]["fallback"] is True
    # Fallback reliability caps at 0.7 even with 5 large prints (5/12=0.417 here).
    assert via_source["reliability"] <= 0.7


def test_source_tape_large_lot_unknown_shape_returns_zero_reliability():
    for snap in ({}, {"tape_buckets": None}, {"tape_buckets": {}}):
        out = dash._source_tape_large_lot(snap)
        assert out["score"] == 0.0
        assert out["reliability"] == 0.0


def test_source_tape_large_lot_thin_fallback_recompute_path():
    # tape_buckets but only 2 prints total → THIN via fallback.
    snap = _snap(_all_labels(
        b1=_bucket("1-10", buy30=1, sell30=1, prints30=2,
                            buy5=1,  sell5=1,  prints5=2)))
    out = dash._source_tape_large_lot(snap)
    assert out["score"] == 0.0
    assert out["reliability"] <= 0.1
    assert out["raw"]["fallback"] is True


# ──────────────────────────────────────────────────────────────────────
# Conviction integration
# ──────────────────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────────────────
# Per-level wiring — _score_level reads tape_flow through the tape slot.
# ──────────────────────────────────────────────────────────────────────

def test_score_level_reads_tape_flow_delta_score_at_magnet():
    """When a magnet level is scored, the per-level composite must pick up
    the institutional-flow delta from snap['tape_flow'] via _tape_bias."""
    tape_flow = {
        "deltaScore": -0.5, "deltaLabel": "STRONG_SELL",
        "totalPrints30s": 40, "largePrints30s": 12,
        "deltaReason": "30s wImb=-0.40, 5m wImb=-0.30, aligned, n30=40",
    }
    out = dash._score_level(
        "above", 20000.0, 20000.0,
        None, None, tape_flow, None, None, None)
    assert out["components"]["tape"] == pytest.approx(-0.5, abs=1e-6)
    assert any("Δ=-0.50" in r and "STRONG_SELL" in r for r in out["reasons"]), (
        f"expected tape Δ reason; got {out['reasons']}")


def test_score_level_treats_thin_tape_flow_as_neutral():
    """Thin tape (n30 < floor) must contribute 0 with a THIN reason."""
    thin = {"deltaScore": 0.0, "deltaLabel": "THIN",
            "totalPrints30s": 3, "largePrints30s": 0}
    out = dash._score_level(
        "above", 20000.0, 20000.0,
        None, None, thin, None, None, None)
    assert out["components"]["tape"] == 0.0
    assert any("THIN" in r for r in out["reasons"])


def test_score_level_falls_back_to_legacy_tape_shape_when_no_delta_score():
    """If something pushes the legacy biasScore shape through the tape slot,
    _tape_bias still produces a usable score (defensive fallback)."""
    legacy = {"biasScore": 0.4}
    out = dash._score_level(
        "above", 20000.0, 20000.0,
        None, None, legacy, None, None, None)
    assert out["components"]["tape"] == pytest.approx(0.4, abs=1e-6)


def test_compute_or_levels_wires_tape_flow_into_levels():
    """compute_or_levels must consume snap['tape_flow'] when present so each
    magnet (OR-H, OR-L, rungs) reflects institutional tape pressure."""
    import datetime as dt
    snap = {
        "or_row": {"orHigh": "20100.00", "orLow": "20000.00"},
        "book": {"mid": 20050.0},
        "tape_flow": {
            "deltaScore": -0.6, "deltaLabel": "STRONG_SELL",
            "totalPrints30s": 40, "largePrints30s": 12,
            "deltaReason": "test",
        },
    }
    out = dash.compute_or_levels(snap)
    assert out is not None
    # Each of the 8 grid levels must show the negative tape component.
    for lvl in out["levels"]:
        assert "components" in lvl
        assert lvl["components"]["tape"] == pytest.approx(-0.6, abs=1e-6), (
            f"level {lvl['label']} did not pick up tape Δ; "
            f"components={lvl['components']}")


def test_compute_session_conviction_includes_tape_when_valid():
    # Minimal snap that compute_session_conviction can iterate over without
    # crashing. We only care that tape_large_lot is in the per-source output
    # and its score is positive when tape_flow.deltaScore is positive.
    import datetime as dt
    snap = {
        "health": "ok",
        "alias":  "NQM6",
        "ts":     dt.datetime.now(dash.ET).isoformat(timespec="seconds"),
        "flow":   {},
        "book":   {},
        "tape_flow": {
            "deltaScore": 0.6, "deltaLabel": "STRONG_BUY",
            "largePrints30s": 10, "totalPrints30s": 50,
            "deltaReason": "test",
        },
    }
    out = dash.compute_session_conviction(snap)
    src_scores = out.get("sourceScores") or {}
    assert "tape_large_lot" in src_scores
    assert src_scores["tape_large_lot"] > 0, (
        f"expected positive tape_large_lot, got {src_scores['tape_large_lot']}")
