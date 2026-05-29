"""Runtime SIM policy overlay for Pax setup decisions.

This is not the active-policy promotion gate. It is an in-process overlay fed by
measured SIM outcomes. It can throttle or modestly promote a setup at runtime
without editing repo policy files, prompts, weights, or skills.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


def setup_signature(setup_type: Any, side: Any, level: Any,
                    session_type: Any) -> str:
    return "|".join((
        str(setup_type or "UNKNOWN").upper(),
        str(side or "UNKNOWN").upper(),
        str(level or "UNKNOWN").upper(),
        str(session_type or "UNKNOWN").upper(),
    ))


def normalize_policy(policy: Optional[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not policy:
        return out
    for item in policy.get("suggestions") or []:
        setup = str(item.get("setup") or "").upper()
        action = str(item.get("action") or "").upper()
        if not setup or action not in ("PROMOTE", "KEEP", "THROTTLE"):
            continue
        out[setup] = {
            "action": action,
            "reason": str(item.get("reason") or ""),
        }
    return out


def lookup_policy(policy: Optional[Mapping[str, Any]], *,
                  setup_type: Any, side: Any, level: Any,
                  session_type: Any) -> Dict[str, Any]:
    normalized = normalize_policy(policy)
    exact = setup_signature(setup_type, side, level, session_type)
    found = normalized.get(exact)
    if found:
        return {"setup": exact, **found}
    return {"setup": exact, "action": "KEEP", "reason": "no runtime override"}


def guard_runtime_policy(policy: Optional[Mapping[str, Any]],
                         scorecard: Optional[Mapping[str, Any]],
                         *,
                         min_samples: int = 3) -> Dict[str, Any]:
    """Return only suggestions backed by current scorecard sample counts."""
    if not policy:
        return {"suggestions": []}
    rows = {
        str(r.get("setup") or "").upper(): r
        for r in (scorecard or {}).get("setups") or []
    }
    guarded = []
    for item in policy.get("suggestions") or []:
        setup = str(item.get("setup") or "").upper()
        row = rows.get(setup)
        if not row:
            continue
        try:
            n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        if n < min_samples or row.get("warning"):
            continue
        guarded.append(dict(item))
    return {"suggestions": guarded}


def adjusted_floor(base_floor: float, action: str) -> float:
    if action == "PROMOTE":
        return round(max(0.15, base_floor - 0.05), 4)
    return base_floor
