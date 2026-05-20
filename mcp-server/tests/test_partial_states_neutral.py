"""Tests proving that VWAP/VP partial-availability labels (NO_SIGMA /
NO_VALUE_AREA / UNAVAILABLE) do NOT count as adverse directional bias in
pax_decision."""
from __future__ import annotations

from bookmap_mcp import dashboard


def _stub_snap(level_decision: str, vwap_label, vp_label,
               *, level_score: float = 0.8) -> dict:
    """Build the smallest snap accepted by pax_decision."""
    return {
        "alias": "NQM6",
        "health": "ok",
        "ping":   {"ok": True, "bridge": "test"},
        # v18 stale-policy: pax_decision blocks on anchorMode != LIVE.
        # Tests of downstream gates must set anchorMode=LIVE to bypass.
        "gates": {
            "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
            "news":    {"blocked": False, "label": "clear"},
        },
        "book":  {"mid": 17000.0, "bestBid": 16999.75, "bestAsk": 17000.25},
        "or_levels": {
            "anchor": "2026-05-19T08:30",
            "orHigh": 17005.0, "orLow": 16995.0, "mid": 17000.0,
            "orWidthPts": 10.0, "rungPts": 5.0,
            "proxTicks": 4, "proxPts": 1.0, "inProximity": True,
            "middleLock": False,
            "levels": [{
                "label": "ORH", "price": 17005.0, "side": "above",
                "distance": 5.0, "proximity": True,
                "decision": level_decision,
                "score": level_score, "confidence": level_score,
                "composite": {
                    "direction": level_decision,
                    "score": level_score, "confidence": level_score,
                    "drivers": [],
                },
            }],
        },
        "flow": {"regime": "BALANCED", "regimeConfidence": 0.5,
                 "biasScore": 0.0, "biasTrajectory": "FLAT",
                 "vwapSlope": {"label": "WARMUP"}},
        "vwap_bias": {"label": vwap_label, "score": 0.0, "available": vwap_label not in (
            "UNAVAILABLE", "NO_SIGMA")},
        "vp_bias":   {"label": vp_label, "score": 0.0, "available": vp_label not in (
            "UNAVAILABLE", "NO_VALUE_AREA")},
    }


def test_no_sigma_does_not_count_as_against_direction():
    """NO_SIGMA / NO_VALUE_AREA must be treated as neutral — pax_decision
    must not return WAIT 'both VWAP-bias and VP-bias against direction'."""
    snap = _stub_snap("FOLLOW_LONG", "NO_SIGMA", "NO_VALUE_AREA")
    decision = dashboard.pax_decision(snap)
    # Expect a real ENTER decision (not the "both against direction" WAIT).
    assert decision["decision"] != "WAIT" or "against direction" not in decision.get("reason", "")


def test_unavailable_does_not_count_as_against_direction():
    snap = _stub_snap("FOLLOW_LONG", "UNAVAILABLE", "UNAVAILABLE")
    decision = dashboard.pax_decision(snap)
    assert decision["decision"] != "WAIT" or "against direction" not in decision.get("reason", "")


def test_explicit_bearish_still_blocks_long():
    """Confirm BEARISH actually-directional VWAP+VP triggers the
    'both against direction' WAIT path (the same path that partial
    labels must NOT trigger)."""
    snap = _stub_snap("FOLLOW_LONG", "BEARISH", "BEARISH")
    decision = dashboard.pax_decision(snap)
    # Decision could be WAIT (from bias gate) or STAND_DOWN (from a later
    # gate) — the proof that we hit the bias-against-direction check is
    # the reason text. Partial-label tests above verify the inverse:
    # NO_SIGMA/UNAVAILABLE NEVER produce this reason.
    reason_text = (decision.get("reason") or "") + " " + " ".join(decision.get("reasons") or [])
    assert "against direction" in reason_text or "got VWAP=BEARISH VP=BEARISH" in reason_text
