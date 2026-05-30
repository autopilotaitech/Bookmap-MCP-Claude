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
    # runs from %SRV%: pushd before the report, popd present (CWD-independent).
    assert 'pushd "%SRV%"' in block
    assert block.index('pushd "%SRV%"') < block.index("pax_session_report")
    assert "popd" in block
    assert block.index("pax_session_report") < block.index("popd")
    assert block.index("popd") < block.index("call :stop_processes_only")
    # failure-tolerant: stop block does not 'exit /b 1' on report failure
    assert "exit /b 1" not in block.split("call :stop_processes_only")[0]


# ── STAGE 3: replay readiness in session report ────────────────────────────

def _ri(version=1):
    return {"version": version, "snapshot": {"health": "ok"},
            "status": {"position": {"size": 0}}, "now_ms": 1,
            "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
            "sim_broker_ok": True, "kill_switch_active": False}


def test_replay_readiness_counts_records():
    feed = [
        {"ts_ms": 1, "action": "NONE", "replay_input": _ri()},
        {"ts_ms": 2, "action": "NONE", "replay_input": _ri()},
        {"ts_ms": 3, "action": "NONE"},                       # missing
    ]
    rep = sr.build_session_report(feed=feed, equity={}, errors=[], now_ms=9)
    rr = rep["replay_readiness"]
    assert rr["total_records"] == 3
    assert rr["replay_input_records"] == 2
    assert rr["missing_replay_input"] == 1
    assert rr["replay_input_pct"] == 66.7
    assert rr["latest_replay_input_version"] == 1
    assert "mixed" in rr["note"]


def test_replay_readiness_counts_malformed():
    feed = [{"ts_ms": 1, "replay_input": "nope"},
            {"ts_ms": 2, "replay_input": _ri()}]
    rr = sr.build_session_report(feed=feed, equity={}, errors=[])["replay_readiness"]
    assert rr["malformed_replay_input"] == 1
    assert rr["replay_input_records"] == 1


def test_replay_readiness_empty_feed_safe():
    rr = sr.build_session_report(feed=[], equity={}, errors=[])["replay_readiness"]
    assert rr["total_records"] == 0
    assert rr["replay_input_records"] == 0
    assert rr["replay_input_pct"] == 0.0
    assert rr["note"] == "no records"


def test_replay_readiness_pre_replay_input_note():
    feed = [{"ts_ms": 1, "action": "NONE"}]
    rr = sr.build_session_report(feed=feed, equity={}, errors=[])["replay_readiness"]
    assert rr["replay_input_records"] == 0
    assert "pre-replay-input" in rr["note"]


def test_replay_summary_optional_field_default_none():
    rep = sr.build_session_report(feed=[], equity={}, errors=[])
    assert rep["replay_summary"] is None


# ── STAGE 4: evidence_summary in session report ────────────────────────────

def _ri_rec(ts):
    return {"ts_ms": ts, "heartbeat": True, "action": "NONE",
            "replay_input": {"version": 1, "snapshot": {"health": "ok"},
                             "status": {"position": {"size": 0}}, "now_ms": ts,
                             "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
                             "sim_broker_ok": True, "kill_switch_active": False}}


def test_session_report_includes_evidence_summary():
    feed = [_ri_rec(i) for i in range(3)]
    sc = {"min_samples": 30, "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
          "mean_realized_r": 0.3, "hit_rate": 0.62}]}
    rep = sr.build_session_report(feed=feed, equity={}, errors=[],
                                  scorecard=sc, now_ms=1)
    es = rep["evidence_summary"]
    assert es["evidence_grade"] == "promotion_candidate"
    assert es["candidate_setup_count"] == 1
    assert es["replay_input_pct"] == 100.0
    assert "next_required_data" in es


def test_session_report_evidence_summary_empty_feed_safe():
    rep = sr.build_session_report(feed=[], equity={}, errors=[])
    es = rep["evidence_summary"]
    assert es["evidence_grade"] == "no_data"


def test_session_report_evidence_no_scorecard_outcome():
    feed = [_ri_rec(i) for i in range(3)]
    rep = sr.build_session_report(feed=feed, equity={}, errors=[], now_ms=1)
    # replay-grade logs but no scorecard -> replayable, candidate_count 0.
    assert rep["evidence_summary"]["evidence_grade"] == "replayable"
    assert rep["evidence_summary"]["candidate_setup_count"] == 0
