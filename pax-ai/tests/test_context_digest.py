"""Tests for pax_ai.context - quantization + offline shape stability."""

from __future__ import annotations

from pax_ai import context as ctx


def _live_snap():
    return {
        "health": "ok",
        "alias": "NQM6.CME@RITHMIC",
        "book": {"mid": 21326.12, "spread": 0.25},
        "conviction": {"score": 0.347, "trend": "RISING", "anchorMode": "LIVE"},
        "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.713},
        "or_levels": {
            "middleLock": False, "inProximity": True, "orHigh": 21340.0, "orLow": 21320.0,
            "levels": [
                {"label": "+1", "price": 21391.0, "side": "above", "distance": 12.583,
                 "proximity": True, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.624},
                {"label": "OR-H", "price": 21340.0, "side": "above", "distance": -38.4,
                 "proximity": False, "decision": "ENTER_LONG_FOLLOW", "confidence": 0.51},
            ],
        },
        "gates": {
            "session": {"code": "ACTIVE", "label": "RTH ACTIVE", "anchorMode": "LIVE"},
            "news": {"blocked": False, "label": None},
        },
        "session": {"anchorMode": "LIVE"},
    }


def test_live_context_basic_shape():
    snap = _live_snap()
    c = ctx.build_context(snap, as_of_ms=1000, age_ms=100, stale_threshold_ms=5000)
    assert c["health"] == "ok"
    assert c["alias"] == "NQM6.CME@RITHMIC"
    # mid quantized to 0.25
    assert c["mid"] == 21326.0
    assert c["nearest"]["label"] == "+1"
    # distance quantized to 0.5
    assert c["nearest"]["distance"] == 12.5
    # confidence quantized to 0.05
    assert c["nearest"]["confidence"] == 0.60
    # conviction score quantized to 0.05
    assert c["conviction"]["score"] == 0.35
    assert c["regimeConfidence"] == 0.70
    assert c["stale"] is False


def test_offline_snapshot_returns_offline_envelope():
    snap = {"health": "offline", "bridgeError": "refused"}
    c = ctx.build_context(snap, as_of_ms=1000, age_ms=0, stale_threshold_ms=5000)
    assert c["health"] == "offline"
    assert c["alias"] is None
    assert c["stale"] is True
    assert c["bridgeError"] == "refused"
    assert c["nearest"] is None


def test_stale_flag_when_age_exceeds_threshold():
    snap = _live_snap()
    c = ctx.build_context(snap, as_of_ms=0, age_ms=8000, stale_threshold_ms=5000)
    assert c["stale"] is True


def test_quantization_is_byte_stable_across_close_inputs():
    """Two snapshots in the same 0.25 quantum bucket quantize to the same mid.

    The quantum step is 0.25 (NQ tick). Pick a base near the centre of a
    bucket (21326.00 -> bucket [21325.875, 21326.125)) and a delta that
    keeps the perturbed value INSIDE that bucket -- this asserts the
    contract without depending on whatever side of the boundary the seed
    happens to fall on.
    """
    s1 = _live_snap(); s1["book"]["mid"] = 21326.00
    s2 = _live_snap(); s2["book"]["mid"] = 21326.05    # same bucket
    c1 = ctx.build_context(s1, as_of_ms=1000, age_ms=100, stale_threshold_ms=5000)
    c2 = ctx.build_context(s2, as_of_ms=1100, age_ms=100, stale_threshold_ms=5000)
    assert c1["mid"] == c2["mid"], "same-bucket mid changes must not bust the cache"


def test_no_nearest_when_levels_empty():
    snap = _live_snap()
    snap["or_levels"]["levels"] = []
    c = ctx.build_context(snap, as_of_ms=1000, age_ms=100, stale_threshold_ms=5000)
    assert c["nearest"] is None
