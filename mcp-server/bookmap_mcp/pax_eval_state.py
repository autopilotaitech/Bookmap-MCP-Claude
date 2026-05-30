"""Read-only evaluation / autonomy gate.

Computes where the SIM autopilot sits on the autonomy ladder from data that
ALREADY exists (the learning scorecard, the runtime policy, live agent stats,
and ops freshness). This module is a *reporter*: it never mutates policy,
never places orders, and -- critically -- never unlocks live trading. Live is
hard-blocked here as a contract, independent of any SIM result.

Ladder (achievable SIM autonomy):
    observe_only  -> sim_armed -> sim_restricted -> sim_candidate

`live` is NOT on the achievable ladder. ``live_blocked`` is always True with a
reason; promotion past SIM requires a human + an explicit future config that
does not exist in this codebase.

Determinism + no-false-promotion: a setup is only "eligible" when its sample
count meets ``min_samples`` AND its observed expectancy is positive. Small or
unprofitable samples are labelled, never promoted.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

LEVELS = ("observe_only", "sim_armed", "sim_restricted", "sim_candidate")

_DEFAULTS = {
    "min_samples": 30,        # per-setup samples to be a candidate
    "candidate_net_r": 1.0,   # net R over the sample to reach sim_candidate
    "candidate_avg_r": 0.05,  # mean R per trade floor for candidate
    "max_drawdown_r": -5.0,   # below this -> restricted regardless of net
    "max_loss_streak": 6,     # at/above this -> restricted
}


def _num(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def setup_eligibility(scorecard: Dict[str, Any],
                      thresholds: Optional[Dict[str, Any]] = None
                      ) -> List[Dict[str, Any]]:
    """Per-setup eligible/ineligible verdict with a reason. Pure."""
    t = {**_DEFAULTS, **(thresholds or {})}
    min_samples = int(scorecard.get("min_samples") or t["min_samples"])
    out: List[Dict[str, Any]] = []
    for s in (scorecard.get("setups") or []):
        if not isinstance(s, dict):
            continue
        n = int(_num(s.get("n")))
        avg_r = _num(s.get("mean_realized_r", s.get("avg_r")))
        if n < min_samples:
            verdict, reason = "ineligible", f"insufficient_sample (n={n}<{min_samples})"
        elif avg_r <= 0:
            verdict, reason = "ineligible", f"non_positive_expectancy (avgR={avg_r:.3f})"
        else:
            verdict, reason = "eligible", f"n={n} avgR={avg_r:.3f}"
        out.append({
            "setup": s.get("setup"),
            "n": n,
            "avg_r": round(avg_r, 4),
            "verdict": verdict,
            "reason": reason,
        })
    return out


def compute_eval_state(*,
                       scorecard: Optional[Dict[str, Any]] = None,
                       runtime_policy: Optional[Dict[str, Any]] = None,
                       agent_stats: Optional[Dict[str, Any]] = None,
                       ops: Optional[Dict[str, Any]] = None,
                       kill_switch_active: bool = False,
                       thresholds: Optional[Dict[str, Any]] = None
                       ) -> Dict[str, Any]:
    """Return the current evaluation state. Pure; inputs are plain dicts.

    agent_stats: {armed, cycles, executed, realized_r, net_r, max_drawdown_r,
                  loss_streak, wins, losses}
    ops: {is_stale, heartbeat_stale, malformed_count} -- ops reliability.
    """
    t = {**_DEFAULTS, **(thresholds or {})}
    scorecard = scorecard or {}
    agent_stats = agent_stats or {}
    ops = ops or {}

    elig = setup_eligibility(scorecard, t)
    eligible_setups = [e for e in elig if e["verdict"] == "eligible"]

    armed = bool(agent_stats.get("armed"))
    cycles = int(_num(agent_stats.get("cycles")))
    executed = int(_num(agent_stats.get("executed")))
    net_r = _num(agent_stats.get("net_r"))
    avg_r = _num(agent_stats.get("avg_r"))
    max_dd = _num(agent_stats.get("max_drawdown_r"))
    loss_streak = int(_num(agent_stats.get("loss_streak")))

    # Operational blockers that prevent SIM candidate/armed execution. These
    # mirror pax_risk_gate's halt codes so health / eval / the live gate all
    # report the same truth. live trading stays hard-blocked regardless.
    heartbeat_stale = bool(ops.get("heartbeat_stale"))
    market_stale = bool(ops.get("market_stale"))
    sim_broker_unavailable = bool(ops.get("sim_broker_unavailable"))
    risk_halt_code = ops.get("risk_halt_code")
    operational_blockers: List[Dict[str, Any]] = []
    if heartbeat_stale:
        operational_blockers.append({"code": "stale_heartbeat",
                                     "message": "agent heartbeat stale"})
    if market_stale:
        operational_blockers.append({"code": "stale_market_data",
                                     "message": "market data stale"})
    if sim_broker_unavailable:
        operational_blockers.append({"code": "sim_broker_unavailable",
                                     "message": "sim broker DB unavailable"})
    if risk_halt_code and risk_halt_code not in [b["code"] for b in operational_blockers]:
        operational_blockers.append(
            {"code": risk_halt_code,
             "message": ops.get("risk_halt_message") or str(risk_halt_code)})

    ops_stale = bool(ops.get("is_stale") or heartbeat_stale or market_stale
                     or sim_broker_unavailable)
    malformed = int(_num(ops.get("malformed_count")))
    risk_halt_active = bool(operational_blockers)

    requirements: List[str] = []

    # --- terminal contract: live is always blocked ---
    live_blocked = True
    live_block_reason = ("live trading is hard-blocked: no live route exists in "
                         "the SIM autopilot path and BOOKMAP_ALLOW_TRADING is "
                         "scrubbed; promotion past SIM requires a human + "
                         "explicit future config")

    # --- kill switch dominates everything ---
    if kill_switch_active:
        return {
            "level": "observe_only",
            "blocked": True,
            "reason": "kill_switch_active",
            "live_blocked": live_blocked,
            "live_block_reason": live_block_reason,
            "kill_switch_active": True,
            "risk_halt_active": True,
            "risk_halt_code": "kill_switch_active",
            "operational_blockers": ([{"code": "kill_switch_active",
                                       "message": "operator kill switch engaged"}]
                                     + operational_blockers),
            "setup_eligibility": elig,
            "eligible_setup_count": 0,
            "requirements_for_next": ["clear the kill switch file"],
            "inputs": {"armed": armed, "cycles": cycles, "executed": executed,
                       "net_r": net_r, "ops_stale": ops_stale,
                       "malformed_count": malformed},
        }

    # --- not running / not armed ---
    if not armed or cycles == 0:
        requirements.append("arm the SIM autopilot (paxi.bat armed) with a live heartbeat")
        level = "observe_only"
        reason = "observe-only: autopilot not armed or no current heartbeat"
        next_level = "sim_armed"
    else:
        # armed and running -> at least sim_armed; decide restriction/promotion
        bad_ops = ops_stale or malformed > 0
        bad_perf = (max_dd <= t["max_drawdown_r"]
                    or loss_streak >= t["max_loss_streak"]
                    or net_r < 0)
        if bad_ops or bad_perf:
            level = "sim_restricted"
            why = []
            for b in operational_blockers:
                why.append(b["code"])
            if ops.get("is_stale") and not operational_blockers:
                why.append("stale data")
            if malformed > 0:
                why.append(f"{malformed} malformed records")
            if max_dd <= t["max_drawdown_r"]:
                why.append(f"drawdown {max_dd:.1f}R <= {t['max_drawdown_r']}R")
            if loss_streak >= t["max_loss_streak"]:
                why.append(f"loss streak {loss_streak}")
            if net_r < 0:
                why.append(f"net {net_r:.2f}R negative")
            reason = "restricted: " + "; ".join(why)
            next_level = "sim_candidate"
            requirements.append("resolve restriction causes: " + "; ".join(why))
        elif (eligible_setups
              and net_r >= t["candidate_net_r"]
              and avg_r >= t["candidate_avg_r"]):
            level = "sim_candidate"
            reason = (f"candidate: {len(eligible_setups)} eligible setup(s), "
                      f"net {net_r:.2f}R, avg {avg_r:.3f}R -- still live_blocked")
            next_level = None
        else:
            level = "sim_armed"
            if not eligible_setups:
                requirements.append(
                    f"reach >= {scorecard.get('min_samples') or t['min_samples']} "
                    "samples on a positive-expectancy setup")
            if net_r < t["candidate_net_r"]:
                requirements.append(f"net R >= {t['candidate_net_r']} (now {net_r:.2f})")
            if avg_r < t["candidate_avg_r"]:
                requirements.append(f"avg R >= {t['candidate_avg_r']} (now {avg_r:.3f})")
            reason = "armed and clean, accumulating qualifying sample"
            next_level = "sim_candidate"

    return {
        "level": level,
        "blocked": level in ("observe_only", "sim_restricted"),
        "reason": reason,
        "next_level": next_level,
        "requirements_for_next": requirements,
        "live_blocked": live_blocked,
        "live_block_reason": live_block_reason,
        "kill_switch_active": False,
        "risk_halt_active": risk_halt_active,
        "risk_halt_code": (operational_blockers[0]["code"]
                           if operational_blockers else None),
        "operational_blockers": operational_blockers,
        "setup_eligibility": elig,
        "eligible_setup_count": len(eligible_setups),
        "inputs": {"armed": armed, "cycles": cycles, "executed": executed,
                   "net_r": round(net_r, 3), "avg_r": round(avg_r, 4),
                   "max_drawdown_r": round(max_dd, 3), "loss_streak": loss_streak,
                   "ops_stale": ops_stale, "malformed_count": malformed},
    }
