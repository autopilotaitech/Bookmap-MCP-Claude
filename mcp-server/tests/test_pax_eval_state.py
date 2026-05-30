"""Tests for the read-only evaluation gate (Stage 6)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_eval_state as ev  # noqa: E402


def test_no_trades_is_observe_only():
    st = ev.compute_eval_state(
        scorecard={"setups": []},
        agent_stats={"armed": False, "cycles": 0})
    assert st["level"] == "observe_only"
    assert st["live_blocked"] is True


def test_good_small_sample_is_not_candidate():
    # 10 samples, positive avg, but below min_samples=30 -> not promotable.
    st = ev.compute_eval_state(
        scorecard={"min_samples": 30,
                   "setups": [{"setup": "A", "n": 10, "mean_realized_r": 0.4}]},
        agent_stats={"armed": True, "cycles": 50, "net_r": 0.5, "avg_r": 0.1})
    assert st["level"] == "sim_armed"
    assert st["eligible_setup_count"] == 0
    elig = [e for e in st["setup_eligibility"] if e["setup"] == "A"][0]
    assert elig["verdict"] == "ineligible"
    assert "insufficient_sample" in elig["reason"]


def test_bad_performance_is_restricted():
    st = ev.compute_eval_state(
        scorecard={"setups": []},
        agent_stats={"armed": True, "cycles": 100, "net_r": -3.0,
                     "max_drawdown_r": -8.0, "loss_streak": 7})
    assert st["level"] == "sim_restricted"
    assert st["blocked"] is True


def test_good_sufficient_sample_is_candidate_but_live_blocked():
    st = ev.compute_eval_state(
        scorecard={"min_samples": 30,
                   "setups": [{"setup": "A", "n": 40, "mean_realized_r": 0.3}]},
        agent_stats={"armed": True, "cycles": 200, "net_r": 2.0, "avg_r": 0.1,
                     "max_drawdown_r": -1.0, "loss_streak": 2})
    assert st["level"] == "sim_candidate"
    assert st["eligible_setup_count"] == 1
    assert st["live_blocked"] is True  # contract: never unlock live


def test_kill_switch_forces_blocked():
    st = ev.compute_eval_state(
        scorecard={"min_samples": 30,
                   "setups": [{"setup": "A", "n": 99, "mean_realized_r": 0.9}]},
        agent_stats={"armed": True, "cycles": 999, "net_r": 50.0, "avg_r": 0.5},
        kill_switch_active=True)
    assert st["level"] == "observe_only"
    assert st["blocked"] is True
    assert st["reason"] == "kill_switch_active"
    assert st["live_blocked"] is True


def test_ops_staleness_restricts_even_with_good_perf():
    st = ev.compute_eval_state(
        scorecard={"min_samples": 30,
                   "setups": [{"setup": "A", "n": 40, "mean_realized_r": 0.3}]},
        agent_stats={"armed": True, "cycles": 200, "net_r": 5.0, "avg_r": 0.2},
        ops={"heartbeat_stale": True})
    assert st["level"] == "sim_restricted"
