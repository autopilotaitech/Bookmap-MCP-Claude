"""Sim-agent calibration — the agent's self-feedback signal (sim only).

NOTE: distinct from `pax_calibration.py` (forecast-reliability reports). This is
the lightweight behavioral + P&L feedback the autonomous sim agent reads back
before each decision.

Closed loop for now: aggregate the agent's own decision log
(`agent-loop.jsonl`) into action distribution + governor-veto rate + deviation
rate, and fold in the live sim scoreboard (realized P&L, losers). The agent
reads this via `pax_sim_tools.read_calibration()` so it can see how it has been
behaving and how the sim is doing, and adjust.

A fuller forward-return / realized-R calibration (matching each decision to its
matured outcome at horizons) is a follow-up that needs the outcomes labeler;
this gives the agent honest behavioral + P&L feedback today without it.
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Dict

from . import pax_sim_tools


def summarize(agent_log_lines, sim_status: Dict[str, Any],
              window: int = 200) -> Dict[str, Any]:
    actions: Counter = Counter()
    vetoes = deviations = executed = n = 0
    for line in list(agent_log_lines)[-window:]:
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        n += 1
        actions[rec.get("action") or "?"] += 1
        if str(rec.get("governor") or "").startswith("VETO"):
            vetoes += 1
        if rec.get("deviates"):
            deviations += 1
        if rec.get("executed"):
            executed += 1
    pos = sim_status.get("position") or {}
    return {
        "decisions": n,
        "by_action": dict(actions),
        "veto_rate": round(vetoes / n, 3) if n else 0.0,
        "deviation_rate": round(deviations / n, 3) if n else 0.0,
        "executed": executed,
        "realized_today_usd": sim_status.get("realized_today_usd"),
        "losers_today": sim_status.get("losers_today"),
        "open_position": pos.get("size"),
    }


def update(sim_status: Dict[str, Any], log_path=None, out_path=None) -> Dict[str, Any]:
    """Recompute calibration from the agent log + sim status, persist it."""
    lp = log_path or (pax_sim_tools.LEARN_DIR / "agent-loop.jsonl")
    # Tail only the last 200 lines (not the whole growing file each cycle).
    lines = pax_sim_tools.tail_lines(lp, 200)
    calib = summarize(lines, sim_status)
    op = out_path or pax_sim_tools.CALIBRATION_PATH
    try:
        pax_sim_tools._ensure_dir()
        op.write_text(json.dumps(calib, indent=2), encoding="utf-8")
    except OSError:
        pass
    return calib
