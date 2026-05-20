"""Compact, quantized context payload for the strip + chat digest.

Pure function: snapshot in, context out. No I/O.

The shape is the contract for the UI strip (Phase 1) and the Claude chat
digest (Phase 3+). Numbers are quantized so consecutive 1Hz polls produce
identical bytes when nothing material has changed - that is what makes the
Anthropic prompt cache hit on the chat side.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


# Quantization step sizes. Picked so a stable market produces stable bytes.
_Q_MID         = 0.25       # NQ tick size
_Q_DISTANCE    = 0.50
_Q_CONFIDENCE  = 0.05
_Q_SCORE       = 0.05


def _q(v: Optional[float], step: float) -> Optional[float]:
    if v is None: return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fv):
        return None
    return round(round(fv / step) * step, 4)


def _nearest_level(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    if not isinstance(levels, list) or not levels:
        return None
    best = None
    best_dist = float("inf")
    for L in levels:
        if not isinstance(L, dict):
            continue
        d = L.get("distance")
        if d is None:
            continue
        try:
            ad = abs(float(d))
        except (TypeError, ValueError):
            continue
        if ad < best_dist:
            best_dist = ad
            best = L
    return best


def build_context(snap: Dict[str, Any], as_of_ms: int, age_ms: int,
                    stale_threshold_ms: int) -> Dict[str, Any]:
    """Build /api/pax/context payload.

    Args:
      snap: latest /api/snapshot body (may be the offline envelope).
      as_of_ms: epoch ms when the snapshot was received by the poller.
      age_ms: ms since as_of_ms.
      stale_threshold_ms: triggers / chat refuse to act past this.
    """
    health = snap.get("health")
    stale = age_ms > stale_threshold_ms

    if health != "ok":
        return {
            "alias":     None,
            "mid":       None,
            "spread":    None,
            "nearest":   None,
            "conviction":{"score": None, "trend": None, "anchorMode": None},
            "regime":    None,
            "regimeConfidence": None,
            "session":   {"code": None, "label": None, "anchorMode": None},
            "news":      {"blocked": None, "label": None},
            "asOfMs":    as_of_ms,
            "ageMs":     age_ms,
            "stale":     True,
            "health":    health or "unknown",
            "bridgeError": snap.get("bridgeError"),
        }

    book = snap.get("book") or {}
    conv = snap.get("conviction") or {}
    flow = snap.get("flow") or {}
    gates = snap.get("gates") or {}
    session = gates.get("session") or snap.get("session") or {}
    news = gates.get("news") or {}

    nearest = _nearest_level(snap)
    nearest_out = None
    if nearest is not None:
        nearest_out = {
            "label":      nearest.get("label"),
            "distance":   _q(nearest.get("distance"), _Q_DISTANCE),
            "side":       nearest.get("side"),
            "decision":   nearest.get("decision"),
            "confidence": _q(nearest.get("confidence"), _Q_CONFIDENCE),
        }

    return {
        "alias":     snap.get("alias"),
        "mid":       _q(book.get("mid"), _Q_MID),
        "spread":    _q(book.get("spread"), _Q_MID),
        "nearest":   nearest_out,
        "conviction": {
            "score":      _q(conv.get("score"), _Q_SCORE),
            "trend":      conv.get("trend"),
            "anchorMode": conv.get("anchorMode") or session.get("anchorMode"),
        },
        "regime":             flow.get("regime"),
        "regimeConfidence":   _q(flow.get("regimeConfidence"), _Q_CONFIDENCE),
        "session": {
            "code":       session.get("code"),
            "label":      session.get("label"),
            "anchorMode": session.get("anchorMode"),
        },
        "news": {
            "blocked": news.get("blocked"),
            "label":   news.get("label"),
        },
        "asOfMs": as_of_ms,
        "ageMs":  age_ms,
        "stale":  stale,
        "health": health,
    }
