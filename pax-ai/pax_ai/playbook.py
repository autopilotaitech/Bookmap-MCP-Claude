"""Scenario tree for /api/pax/playbook.

Pure function: given the snapshot, builds the WAIT_FOR_LEVEL / AT_LEVEL_ENTRY
/ IN_TRADE / RUNNER / EXIT_WATCH state + the conditional branches the trader
should be reading.

Deterministic. No I/O. Spec: section 5.4.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


_PROX_TICKS_DEFAULT = 8


def _current_state(snap: Dict[str, Any], position_size: Optional[float]) -> str:
    or_levels = snap.get("or_levels") or {}
    if or_levels.get("middleLock"):
        return "WAIT_FOR_LEVEL"
    if position_size is None or position_size == 0:
        if or_levels.get("inProximity"):
            return "AT_LEVEL_ENTRY"
        return "WAIT_FOR_LEVEL"
    # In a position
    pax = snap.get("pax") or {}
    decision = (pax.get("decision") or "").upper()
    if "RUNNER" in decision:
        return "RUNNER"
    if "EXIT" in decision or "SCRATCH" in decision:
        return "EXIT_WATCH"
    return "IN_TRADE"


def _active_level(snap: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    if not isinstance(levels, list):
        return None
    # First proximity-true; else nearest by abs(distance).
    in_prox = [L for L in levels if isinstance(L, dict) and L.get("proximity")]
    if in_prox:
        return min(in_prox, key=lambda L: abs(L.get("distance") or 1e9))
    levels_with_d = [L for L in levels
                       if isinstance(L, dict) and L.get("distance") is not None]
    if not levels_with_d:
        return None
    return min(levels_with_d, key=lambda L: abs(L.get("distance") or 1e9))


def build_playbook(snap: Dict[str, Any]) -> Dict[str, Any]:
    pos = snap.get("position") or {}
    pos_size = pos.get("size") if isinstance(pos, dict) else None

    state = _current_state(snap, pos_size if isinstance(pos_size, (int, float)) else None)
    active = _active_level(snap)
    flow = snap.get("flow") or {}
    vwap_bias = snap.get("vwap_bias") or {}
    gates = snap.get("gates") or {}
    session = gates.get("session") or {}
    news = gates.get("news") or {}
    or_levels = snap.get("or_levels") or {}

    branches: List[Dict[str, str]] = []
    if active is not None:
        decision = (active.get("decision") or "").upper()
        label = active.get("label", "")
        price = active.get("price")
        if "LONG" in decision and "FOLLOW" in decision and price is not None:
            branches.append({
                "name": f"FOLLOW long break of {label}",
                "if":   f"first print >= {price:.2f} with sustained bid confirmation",
                "then": "ENTER long, size per composite confidence",
            })
        if "SHORT" in decision and "FOLLOW" in decision and price is not None:
            branches.append({
                "name": f"FOLLOW short break of {label}",
                "if":   f"first print <= {price:.2f} with sustained offer confirmation",
                "then": "ENTER short, size per composite confidence",
            })
        if "LONG" in decision and "FADE" in decision and price is not None:
            branches.append({
                "name": f"FADE long rotation at {label}",
                "if":   f"STRONG_BULL fires at {label} within proximity window",
                "then": "ENTER long HALF; tight scratch stop",
            })
        if "SHORT" in decision and "FADE" in decision and price is not None:
            branches.append({
                "name": f"FADE short rotation at {label}",
                "if":   f"STRONG_BEAR fires at {label} within proximity window",
                "then": "ENTER short HALF; tight scratch stop",
            })
        # Always-on STAND DOWN branch
        vw_regime = (vwap_bias.get("components") or {}).get("regime") \
                    or vwap_bias.get("regime")
        if vw_regime == "BLOWOFF_REVERT":
            branches.append({
                "name": "STAND DOWN (VWAP blowoff)",
                "if":   "vwap_bias regime == BLOWOFF_REVERT",
                "then": "no entry; consider FADE at next extension",
            })
        if news.get("blocked"):
            branches.append({
                "name": "STAND DOWN (news blackout)",
                "if":   f"news.blocked == true ({news.get('label')})",
                "then": "no new entries inside blackout window",
            })

    return {
        "current_state": state,
        "active_level":  active.get("label") if active else None,
        "branches":      branches,
        "gates": {
            "session":   session.get("code"),
            "news":      "BLOCKED" if news.get("blocked") else "CLEAR",
            "anchor":    session.get("anchorMode"),
            "middleLock": bool(or_levels.get("middleLock")),
            "regime":    flow.get("regime"),
        },
    }
