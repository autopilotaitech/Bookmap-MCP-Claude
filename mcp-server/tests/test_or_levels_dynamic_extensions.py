"""Dynamic OR extension generation.

`compute_or_levels` must grow the magnet ladder as price travels further from
the open range. Minimum +/-OR_MIN_EXT rungs (back-compat). When mid is past
the topmost existing extension, add rungs until mid + 1 buffer is covered.
Cap at +/-OR_MAX_EXT to bound DOM growth.

Math is `floor(reach / rung) + 1`:
  - reach = 0 (inside OR)       -> n = 1 -> clamped up to OR_MIN_EXT = 3
  - reach exactly 5 * rung       -> floor(5.0) + 1 = 6 -> +6 buffer above +5
  - reach 6 * rung + 1.0         -> floor(6.015) + 1 = 7 -> -7 buffer below -6
  - reach 50 * rung              -> 51 -> clamped to OR_MAX_EXT = 12
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402

RUNG = d.NQ_RUNG_PTS  # 65.0


def _snap(or_high: float, or_low: float, mid: float):
    return {
        "or_row": {"orHigh": or_high, "orLow": or_low, "symbol": "NQM6"},
        "book":   {"mid": mid, "bestBid": mid - 0.25, "bestAsk": mid + 0.25},
        "vwap_obj": None,
        "pull_stack": None,
        "lt_liquidity": None,
        "tape_flow": {"deltaScore": 0.0},
        "micro_events": None,
        "volume_profile": None,
        "alias": "NQM6.CME@RITHMIC",
    }


def _labels(out):
    return [l["label"] for l in out["levels"]]


def test_mid_inside_or_yields_min_extensions():
    """Default case: exactly +/-3 extensions in the canonical order."""
    or_high, or_low = 21500.0, 21400.0
    mid = (or_high + or_low) / 2
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    assert _labels(out) == ["+3", "+2", "+1", "OR-H", "OR-L", "-1", "-2", "-3"]


def test_mid_just_past_plus_5_includes_plus_6_buffer_not_plus_7():
    """Mid is just past +5 -> floor(5.015)+1 = 6 -> ladder reaches +6 (buffer),
    not +7."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 5 * RUNG + 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[0] == "+6"
    assert "+7" not in labels
    assert "+5" in labels and "+4" in labels
    # Down side stays at minimum.
    assert labels[-1] == "-3"


def test_mid_exactly_at_plus_5_includes_plus_6_buffer():
    """Mid landing exactly on +5 -> floor(5.0)+1 = 6 -> +6 buffer."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 5 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[0] == "+6"
    assert "+7" not in labels


def test_mid_at_minus_6_includes_minus_7_buffer():
    """Mid at exactly -6 -> -7 buffer."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_low - 6 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[-1] == "-7"
    assert "-8" not in labels
    assert "-6" in labels
    # Up side at minimum.
    assert labels[0] == "+3"


def test_mid_just_past_minus_6_includes_minus_7_buffer():
    or_high, or_low = 21500.0, 21400.0
    mid = or_low - 6 * RUNG - 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[-1] == "-7"
    assert "-8" not in labels


def test_extensions_capped_at_or_max_ext():
    """Pathological mid (50+ rungs away) must clamp to +/-OR_MAX_EXT."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 50 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    labels = _labels(out)
    assert labels[0] == f"+{d.OR_MAX_EXT}"
    assert f"+{d.OR_MAX_EXT + 1}" not in labels
    # Down side untouched.
    assert labels[-1] == "-3"


def test_dynamic_prices_match_rung_arithmetic():
    """Generated prices must be or_high + n*rung above, or_low - n*rung below."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 4 * RUNG + 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    by_label = {l["label"]: l["price"] for l in out["levels"]}
    assert by_label["+5"] == pytest.approx(or_high + 5 * RUNG)
    assert by_label["+4"] == pytest.approx(or_high + 4 * RUNG)
    assert by_label["OR-H"] == pytest.approx(or_high)
    assert by_label["OR-L"] == pytest.approx(or_low)
    assert by_label["-3"] == pytest.approx(or_low - 3 * RUNG)


def test_levels_remain_sorted_high_to_low():
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 7 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    prices = [l["price"] for l in out["levels"]]
    assert prices == sorted(prices, reverse=True)


def test_sides_remain_above_and_below():
    or_high, or_low = 21500.0, 21400.0
    mid = or_low - 4 * RUNG - 1.0
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    for l in out["levels"]:
        if l["label"] == "OR-H" or l["label"].startswith("+"):
            assert l["side"] == "above"
        else:
            assert l["side"] == "below"


def test_extended_levels_serialize_cleanly():
    """A snapshot with the extended ladder must survive d.safe_json
    round-trip (that is what /api/snapshot ships to the frontend)."""
    or_high, or_low = 21500.0, 21400.0
    mid = or_high + 10 * RUNG
    out = d.compute_or_levels(_snap(or_high, or_low, mid))
    blob = d.safe_json(out)
    parsed = json.loads(blob)
    labels = [l["label"] for l in parsed["levels"]]
    assert "OR-H" in labels and "OR-L" in labels
    assert "+10" in labels and "+11" in labels
