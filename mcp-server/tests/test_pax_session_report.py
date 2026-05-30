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


# ── STAGE 4: archive + auto-report-on-stop wiring ──────────────────────────

def test_archive_path_is_deterministic():
    out = Path(r"D:\BookmapLogs\pax-agent\session-report.json")
    a = sr.archive_path(out, 1_700_000_000_000)
    assert a.parent.name == "sessions"
    assert a.name.startswith("session-report-")
    assert a.name.endswith(".json")
    # deterministic for a fixed now_ms
    assert sr.archive_path(out, 1_700_000_000_000) == a


def test_gather_and_write_archives(tmp_path, monkeypatch):
    import bookmap_mcp.pax_session_report as SR
    from bookmap_mcp.journal import Journal

    db = tmp_path / "journal.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.end_run("done"); j.close()

    learn = tmp_path / "learn"; learn.mkdir()
    out = learn / "session-report.json"
    res = SR.gather_and_write(journal=db, sim_db=tmp_path / "sim.db",
                              learn_dir=learn, out_path=out, archive=True)
    assert res == out and out.exists()
    sessions = list((learn / "sessions").glob("session-report-*.json"))
    assert len(sessions) == 1
    # archive content matches the canonical report (same body).
    assert sessions[0].read_text(encoding="utf-8") == out.read_text(encoding="utf-8")


def test_paxi_stop_writes_session_report_before_kill():
    """Batch inspection: stop must invoke the session report (failure-tolerant)
    before stop_processes_only, and use --archive."""
    bat = (ROOT.parent / "paxi.bat").read_text(encoding="utf-8")
    stop_idx = bat.index("\n:stop")
    # bound the stop block at the next label
    after = bat[stop_idx + 1:]
    block = after[:after.index("\n:stop_processes_only")] if "\n:stop_processes_only" in after else after
    # the stop block references the report module before calling stop_processes_only
    assert "bookmap_mcp.pax_session_report" in block
    assert "--archive" in block
    pre_kill = block.index("pax_session_report") < block.index("call :stop_processes_only")
    assert pre_kill, "session report must run before processes are killed"
    # failure-tolerant: stop block does not 'exit /b 1' on report failure
    assert "exit /b 1" not in block.split("call :stop_processes_only")[0]
