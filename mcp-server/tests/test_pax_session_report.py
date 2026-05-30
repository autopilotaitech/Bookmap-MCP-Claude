"""Tests for the session report builder (Stage 7)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_session_report as sr  # noqa: E402

_FEED = [
    {"ts_ms": 1, "action": "NONE", "governor": "ok"},
    {"ts_ms": 2, "action": "PLACE_SHORT", "governor": "ok",
     "setup_type": "OR_SWEEP_REJECT", "order": {"side": "SHORT"},
     "executed": True, "model": "claude-haiku-4-5"},
    {"ts_ms": 3, "action": "ENTER_LONG", "governor": "VETO: in position",
     "order": None, "executed": False},
]
_EQ = {"realized": 120.0, "wins": 2, "losses": 1}
_ERRS = [{"ts_ms": 9, "kind": "WARN", "message": "stale tape"}]


def test_report_shape_and_counts():
    rep = sr.build_session_report(feed=_FEED, equity=_EQ, errors=_ERRS,
                                  now_ms=999)
    assert rep["generated_ms"] == 999
    assert rep["decisions"]["total"] == 3
    assert rep["decisions"]["entry_intents"] == 2  # PLACE_SHORT + ENTER_LONG
    assert rep["executions"]["count"] == 1
    assert rep["risk_events"]["veto_count"] == 1
    assert rep["blocked"]["count"] == 1
    assert rep["pnl"]["realized_usd"] == 120.0
    assert rep["pnl"]["win_rate"] == 66.7
    assert rep["model_calls"] == 1
    assert rep["setup_stats"]["OR_SWEEP_REJECT"] == 1
    assert rep["errors"]["count"] == 1


_HALT_FEED = [
    {"ts_ms": 1, "action": "NONE", "governor": "ok"},
    {"ts_ms": 2, "action": "PLACE_LONG", "governor": "VETO: kill_switch_active",
     "risk_halt": "kill_switch_active", "risk_halt_code": "kill_switch_active",
     "risk_halt_message": "operator kill switch engaged",
     "order": None, "executed": False},
    {"ts_ms": 3, "action": "PLACE_LONG", "governor": "VETO: stale_market_data",
     "risk_halt_code": "stale_market_data",
     "risk_halt_message": "market stale", "order": None, "executed": False},
    {"ts_ms": 4, "action": "PLACE_SHORT", "governor": "VETO: stale_heartbeat",
     "risk_halt_code": "stale_heartbeat", "order": None, "executed": False},
    {"ts_ms": 5, "action": "PLACE_LONG", "governor": "VETO: max_trades_reached",
     "risk_halt_code": "max_trades_reached", "order": None, "executed": False},
    {"ts_ms": 6, "action": "PLACE_LONG", "governor": "VETO: sim_broker_unavailable",
     "risk_halt_code": "sim_broker_unavailable", "order": None, "executed": False},
]


def test_report_includes_risk_halt_counts():
    rep = sr.build_session_report(feed=_HALT_FEED, equity={}, errors=[])
    rh = rep["risk_halts"]
    assert rh["count"] == 5
    assert rh["by_code"]["kill_switch_active"] == 1
    assert rh["kill_switch"] == 1
    assert rh["stale_data"] == 2           # heartbeat + market
    assert rh["sim_broker"] == 1
    assert rh["session_limits"] == 1       # max_trades_reached
    assert any(h["code"] == "stale_market_data" for h in rh["recent"])


def test_risk_halts_empty_feed_safe():
    rep = sr.build_session_report(feed=[], equity={}, errors=[])
    assert rep["risk_halts"]["count"] == 0
    assert rep["risk_halts"]["by_code"] == {}


def test_risk_halts_malformed_safe():
    rep = sr.build_session_report(feed=[None, "junk",
                                        {"risk_halt_code": "stale_heartbeat"}],
                                  equity={}, errors=[])
    assert rep["risk_halts"]["count"] == 1
    assert rep["stale_data_events"]["malformed_records"] == 2


def test_empty_feed_is_safe():
    rep = sr.build_session_report(feed=[], equity={}, errors=[])
    assert rep["decisions"]["total"] == 0
    assert rep["pnl"]["win_rate"] is None
    assert rep["executions"]["count"] == 0


def test_malformed_records_counted_not_crashed():
    rep = sr.build_session_report(feed=[None, "junk", {"action": "WAIT"}],
                                  equity={}, errors=[])
    assert rep["stale_data_events"]["malformed_records"] == 2
    assert rep["decisions"]["total"] == 1
