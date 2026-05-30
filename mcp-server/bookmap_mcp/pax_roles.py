"""Read-side role annotation for agent-loop decision records.

The agent already records every decision in ``agent-loop.jsonl`` with the
fields that map onto explicit operational roles (observer / strategist /
risk / executor / auditor). This module derives those role sections from an
existing record WITHOUT changing how the record is written. It is a pure
projection for audit / dashboard visibility only -- it never feeds back into
the decision or order path.

Role mapping (from observed record shape):
- observer  : market inputs the decision saw (mid, level, alias, session)
- strategist: the chosen setup / action / thesis / expectancy
- risk      : the governor verdict + armed mode (the hard gate outcome)
- executor  : the order placed and its execution result
- auditor   : provenance (timestamp, model, sim-env scrub, lesson written)
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _g(rec: Dict[str, Any], *keys: str) -> Dict[str, Any]:
    return {k: rec.get(k) for k in keys if k in rec}


def llm_influenced(rec: Dict[str, Any]) -> bool:
    """True when the LLM shaped the *decision* (not just narration).

    A decide_cycle record carries raw_action / confidence / deviates from the
    model. A pure heartbeat may carry an ``llm`` narration block that did NOT
    gate the action -- that is advisory, not influence.
    """
    if any(k in rec for k in ("raw_action", "deviates", "deviation_reason")):
        return True
    return False


def govern_outcome(rec: Dict[str, Any]) -> str:
    gov = str(rec.get("governor") or "")
    if gov.startswith("VETO"):
        return "BLOCKED"
    if gov == "ok":
        return "ALLOWED"
    return "UNKNOWN"


def annotate_roles(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a roles dict for one agent-loop record. Pure; no mutation."""
    if not isinstance(rec, dict):
        return {}
    executed = bool(rec.get("executed")) and bool(rec.get("order"))
    return {
        "observer": _g(rec, "mid", "level", "alias", "stype",
                       "baseline_state", "ts_ct"),
        "strategist": _g(rec, "setup_type", "baseline_action", "action",
                         "raw_action", "confidence", "rationale",
                         "expectancy", "expectancy_source", "deviates",
                         "deviation_reason"),
        "risk": {
            "governor": rec.get("governor"),
            "outcome": govern_outcome(rec),
            "armed": bool(rec.get("armed")),
            "mode": "armed" if rec.get("armed") else "observe",
        },
        "executor": {
            "order": rec.get("order"),
            "exec": rec.get("exec"),
            "executed": executed,
            "exec_error": rec.get("exec_error"),
        },
        "auditor": {
            **_g(rec, "ts_ms", "ts_ct", "model", "live_trading_env_scrubbed",
                 "lesson_added", "lesson_error", "error"),
            "llm_influenced": llm_influenced(rec),
            "deterministic_path": not llm_influenced(rec),
        },
    }


def annotate_record(rec: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy of the record with a ``roles`` block added.

    Used by the read path to enrich feed items without touching the writer.
    """
    if not isinstance(rec, dict):
        return rec
    out = dict(rec)
    out["roles"] = annotate_roles(rec)
    return out
