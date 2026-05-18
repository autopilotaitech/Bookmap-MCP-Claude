"""Sign-convention tests for _micro_at_level.

Pin the mapping so iceberg / spoof / stop-sweep directionality never
gets flipped again. Convention everywhere:

    +score = BULLISH      −score = BEARISH

Rules (matches dashboard.py:_micro_at_level and the dashboard JS micro-bias):

    ICEBERG    isBid=True  → +0.4   (bid-side defender = absorbing sells, bullish)
               isBid=False → −0.4   (ask-side defender = absorbing buys,  bearish)
    SPOOF      isBid=True  → −0.3   (fake bid below inside = real flow sells, bearish)
               isBid=False → +0.3   (fake ask above inside = real flow buys,  bullish)
    STOP_SWEEP isBid=True  → −0.5   (bids swept, price drove down, bearish)
               isBid=False → +0.5   (asks swept, price drove up,   bullish)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `import bookmap_mcp.dashboard` when running pytest from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

from bookmap_mcp.dashboard import _micro_at_level, NQ_TICK                       # noqa: E402


def _ev(kind: str, *, is_bid: bool, price: float, size: int = 100):
    return {"kind": kind, "isBid": is_bid, "price": price, "size": size,
            "reason": "test", "timeMs": 0}


def _wrap(events):
    return {"events": events}


@pytest.mark.parametrize("kind,is_bid,expected_sign", [
    ("ICEBERG",    True,  +1),
    ("ICEBERG",    False, -1),
    ("SPOOF",      True,  -1),
    ("SPOOF",      False, +1),
    ("STOP_SWEEP", True,  -1),
    ("STOP_SWEEP", False, +1),
])
def test_single_event_sign(kind, is_bid, expected_sign):
    """Each kind × side combination resolves to the documented sign."""
    px = 20000.0
    score, _ = _micro_at_level(_wrap([_ev(kind, is_bid=is_bid, price=px)]), px)
    assert score != 0.0, f"{kind} isBid={is_bid} should contribute"
    assert (1 if score > 0 else -1) == expected_sign, (
        f"{kind} isBid={is_bid}: got {score:+.2f}, expected sign {expected_sign:+d}"
    )


def test_iceberg_bid_vs_ask_cancel_to_neutral():
    """One iceberg per side at the same level → bull + bear cancel near zero."""
    px = 20000.0
    score, _ = _micro_at_level(_wrap([
        _ev("ICEBERG", is_bid=True,  price=px),
        _ev("ICEBERG", is_bid=False, price=px),
    ]), px)
    assert abs(score) < 1e-9, f"expected cancellation, got {score:+.3f}"


def test_spoof_bid_is_bearish_not_bullish():
    """Regression: previous JS had this inverted."""
    px = 20000.0
    score, _ = _micro_at_level(
        _wrap([_ev("SPOOF", is_bid=True, price=px)]), px)
    assert score < 0, f"SPOOF on bid must be BEARISH, got {score:+.2f}"


def test_stop_sweep_bid_is_bearish():
    """Bids swept = aggressive sellers ran through support = bearish."""
    px = 20000.0
    score, _ = _micro_at_level(
        _wrap([_ev("STOP_SWEEP", is_bid=True, price=px)]), px)
    assert score < 0


def test_out_of_band_event_ignored():
    """Events outside the ±window_ticks band are skipped."""
    px = 20000.0
    far = px + 100 * NQ_TICK   # 25 pts away, way beyond default 4-tick band
    score, _ = _micro_at_level(
        _wrap([_ev("ICEBERG", is_bid=True, price=far)]), px)
    assert score == 0.0


def test_score_clipped_to_unit_interval():
    """Stacking many same-direction events stays in [-1, +1]."""
    px = 20000.0
    events = [_ev("STOP_SWEEP", is_bid=False, price=px) for _ in range(20)]
    score, _ = _micro_at_level(_wrap(events), px)
    assert score == 1.0


def test_empty_inputs_dont_crash():
    score, reason = _micro_at_level(None, 20000.0)
    assert score == 0.0 and isinstance(reason, str)
    score, reason = _micro_at_level({"events": []}, 20000.0)
    assert score == 0.0 and isinstance(reason, str)
    score, reason = _micro_at_level({"_error": "oops"}, 20000.0)
    assert score == 0.0


def test_side_string_fallback_when_no_is_bid():
    """Old clients may emit 'side':'BID' instead of isBid:bool — still works."""
    px = 20000.0
    ev = {"kind": "ICEBERG", "side": "BID", "price": px, "size": 100, "timeMs": 0}
    score, _ = _micro_at_level(_wrap([ev]), px)
    assert score > 0   # bid iceberg → bullish
