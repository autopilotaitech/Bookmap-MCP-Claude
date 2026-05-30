"""STAGE 3: read-only SIM acceptance/doctor CLI.

No service start, no broker order, no LLM. Verdict from existing read-only
surfaces. SIM-only; live hard-blocked.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_acceptance as ACC       # noqa: E402
from bookmap_mcp.journal import Journal             # noqa: E402
from bookmap_mcp.sim_engine import SimEngine        # noqa: E402


def _fresh_journal(tmp_path):
    db = tmp_path / "journal.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat(
        timespec="seconds")
    j.write_snapshot({"alias": "NQM6", "health": "ok", "ts": now_iso,
                      "book": {"mid": 1.0}})
    j.end_run("done"); j.close()
    return db


def _empty_journal(tmp_path, name="empty.db"):
    db = tmp_path / name
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.end_run("done"); j.close()
    return db


def _ri_heartbeat(now):
    ri = {"version": 1, "snapshot": {"health": "ok"},
          "status": {"position": {"size": 0}}, "now_ms": now,
          "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
          "sim_broker_ok": True, "kill_switch_active": False}
    return {"ts_ms": now, "heartbeat": True, "armed": False, "action": "NONE",
            "replay_input": ri}


def _clean_setup(tmp_path, *, scorecard=True, kill_switch=False, session=True):
    """Operationally clean stack: fresh market+heartbeat, valid SIM DB."""
    import time
    journal = _fresh_journal(tmp_path)
    sim = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=sim, eod_close_hour_ct=None)
    learn = tmp_path / "learn"; learn.mkdir()
    now = int(time.time() * 1000)
    (learn / "agent-loop.jsonl").write_text(
        json.dumps(_ri_heartbeat(now)) + "\n", encoding="utf-8")
    if scorecard:
        (learn / "scorecard.json").write_text(json.dumps(
            {"min_samples": 30, "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
             "mean_realized_r": 0.3, "hit_rate": 0.62}]}), encoding="utf-8")
    if session:
        (learn / "session-report.json").write_text("{}", encoding="utf-8")
    if kill_switch:
        (learn / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    return journal, sim, learn


# --- verdict logic ---------------------------------------------------------

def test_clean_stack_is_pass_or_warn_not_fail(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             now_ms=1)
    assert rep["overall"] in ("pass", "warn")
    assert rep["overall"] != "fail"
    assert rep["live_blocked"] is True
    assert rep["sim_only"] is True
    # required FAIL drivers are all clean.
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["kill_switch_absent"] == "pass"
    assert by["heartbeat_fresh"] == "pass"
    assert by["market_data_fresh"] == "pass"
    assert by["sim_broker_readable"] == "pass"
    assert by["live_blocked"] == "pass"


def test_kill_switch_is_fail(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path, kill_switch=True)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             now_ms=1)
    assert rep["overall"] == "fail"
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["kill_switch_absent"] == "fail"
    assert rep["live_blocked"] is True


def test_stale_market_is_fail(tmp_path):
    # empty journal -> no snapshot -> market never_updated -> stale.
    sim = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=sim, eod_close_hour_ct=None)
    learn = tmp_path / "learn"; learn.mkdir()
    import time
    (learn / "agent-loop.jsonl").write_text(
        json.dumps(_ri_heartbeat(int(time.time() * 1000))) + "\n",
        encoding="utf-8")
    rep = ACC.run_acceptance(journal=_empty_journal(tmp_path), sim_db=sim,
                             learn_dir=learn, now_ms=1)
    assert rep["overall"] == "fail"
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["market_data_fresh"] == "fail"


def test_sim_broker_unreadable_is_fail(tmp_path):
    bad = tmp_path / "corrupt.db"
    bad.write_bytes(b"not a database")
    journal = _fresh_journal(tmp_path)
    learn = tmp_path / "learn"; learn.mkdir()
    import time
    (learn / "agent-loop.jsonl").write_text(
        json.dumps(_ri_heartbeat(int(time.time() * 1000))) + "\n",
        encoding="utf-8")
    rep = ACC.run_acceptance(journal=journal, sim_db=bad, learn_dir=learn,
                             now_ms=1)
    assert rep["overall"] == "fail"
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["sim_broker_readable"] == "fail"


def test_missing_scorecard_and_session_are_warnings_not_fail(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path, scorecard=False, session=False)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             now_ms=1)
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["scorecard_present"] == "warn"
    assert by["session_report_present"] == "warn"
    # warnings do not turn into FAIL.
    assert rep["overall"] in ("warn",)


def test_r_counters_unavailable_is_warn(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             now_ms=1)
    by = {c["code"]: c["status"] for c in rep["checks"]}
    assert by["risk_counters_available"] == "warn"
    assert any("R-denominated" in lim for lim in rep["limitations"])


def test_no_data_paths_are_safe_not_exception(tmp_path):
    rep = ACC.run_acceptance(journal=tmp_path / "nope.db",
                             sim_db=tmp_path / "nope-sim.db",
                             learn_dir=tmp_path / "nolearn", now_ms=1)
    assert rep["overall"] in ("fail", "warn")
    assert rep["live_blocked"] is True


def test_replay_flag_embeds_summary(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             replay=True, now_ms=1)
    assert "replay_summary" in rep
    assert rep["replay_summary"]["replay_input_count"] >= 1


def test_cli_main_writes_and_exit_code(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    out = tmp_path / "acceptance.json"
    rc = ACC.main(["--journal", str(journal), "--sim-db", str(sim),
                   "--learn-dir", str(learn), "--out", str(out)])
    assert rc == 0                       # pass/warn -> 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["overall"] in ("pass", "warn")
    assert data["live_blocked"] is True


def test_cli_main_fail_exit_code(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path, kill_switch=True)
    rc = ACC.main(["--journal", str(journal), "--sim-db", str(sim),
                   "--learn-dir", str(learn)])
    assert rc == 1                       # fail -> 1


# --- STAGE 1: fail-closed when health is unavailable/empty/malformed -------

class _StubQ:
    """Minimal OverviewQueries stand-in to exercise fail-closed paths."""
    def __init__(self, *, health, learn_dir, ks=False,
                 sim_pf=None, evidence=None, promotion=None, arming=None):
        self.learn_dir = Path(learn_dir)
        self.sim_db_path = None
        self._health = health
        self._ks = ks
        self._sim_pf = sim_pf or {"openable": True, "readable": True}
        self._evidence = evidence or {}
        self._promotion = promotion or {}
        self._arming = arming or {}
    def health(self):
        if isinstance(self._health, Exception):
            raise self._health
        return self._health
    def arming_check(self): return self._arming
    def evidence_report(self): return self._evidence
    def promotion_report(self): return self._promotion
    def sim_broker_preflight(self): return self._sim_pf
    def kill_switch_active(self): return self._ks


def _run_with_stub(monkeypatch, tmp_path, **stub_kw):
    import bookmap_mcp.overview_ui as oui
    stub = _StubQ(learn_dir=tmp_path / "learn", **stub_kw)
    monkeypatch.setattr(oui, "OverviewQueries", lambda *a, **k: stub)
    return ACC.run_acceptance(journal=tmp_path / "j.db",
                              sim_db=tmp_path / "s.db", now_ms=1)


def _by(rep):
    return {c["code"]: c["status"] for c in rep["checks"]}


_FRESH_SOURCES = {"heartbeat": {"is_stale": False}, "market": {"is_stale": False}}


def test_health_exception_is_overall_fail(monkeypatch, tmp_path):
    rep = _run_with_stub(monkeypatch, tmp_path, health=RuntimeError("boom"))
    assert rep["overall"] == "fail"
    by = _by(rep)
    assert by["health_available"] == "fail"
    assert by["live_blocked"] == "fail"          # unknown -> fail
    assert by["heartbeat_fresh"] == "fail"
    assert by["market_data_fresh"] == "fail"
    assert "health_error" in rep["source_errors"]
    assert rep["live_blocked"] is True           # SIM hard-block preserved in output


def test_empty_health_is_overall_fail(monkeypatch, tmp_path):
    rep = _run_with_stub(monkeypatch, tmp_path, health={})
    assert rep["overall"] == "fail"
    by = _by(rep)
    assert by["health_available"] == "fail"
    assert by["live_blocked"] == "fail"
    assert by["heartbeat_fresh"] == "fail"
    assert by["market_data_fresh"] == "fail"


def test_missing_live_blocked_is_fail(monkeypatch, tmp_path):
    health = {"sources": _FRESH_SOURCES, "git_commit": "abc"}  # no live_blocked
    rep = _run_with_stub(monkeypatch, tmp_path, health=health)
    by = _by(rep)
    assert by["live_blocked"] == "fail"
    assert any("live_blocked_unknown" in c["message"]
               for c in rep["checks"] if c["code"] == "live_blocked")
    assert rep["overall"] == "fail"


def test_live_blocked_false_is_fail(monkeypatch, tmp_path):
    health = {"live_blocked": False, "sources": _FRESH_SOURCES}
    rep = _run_with_stub(monkeypatch, tmp_path, health=health)
    assert _by(rep)["live_blocked"] == "fail"
    assert rep["overall"] == "fail"


def test_missing_sources_block_heartbeat_and_market(monkeypatch, tmp_path):
    health = {"live_blocked": True}                # no sources dict
    rep = _run_with_stub(monkeypatch, tmp_path, health=health)
    by = _by(rep)
    assert by["heartbeat_fresh"] == "fail"
    assert by["market_data_fresh"] == "fail"
    assert by["live_blocked"] == "pass"            # explicitly True
    assert rep["overall"] == "fail"


def test_partial_sources_missing_market_fails(monkeypatch, tmp_path):
    # heartbeat present+fresh, market source entirely absent -> market fails.
    health = {"live_blocked": True,
              "sources": {"heartbeat": {"is_stale": False}}}
    rep = _run_with_stub(monkeypatch, tmp_path, health=health)
    by = _by(rep)
    assert by["heartbeat_fresh"] == "pass"
    assert by["market_data_fresh"] == "fail"       # missing source != fresh


def test_clean_stub_health_is_not_fail(monkeypatch, tmp_path):
    health = {"live_blocked": True, "sources": _FRESH_SOURCES,
              "git_commit": "abc", "evidence": {"evidence_grade": "replayable"},
              "replay_readiness": {"replay_input_pct_recent": 100.0}}
    rep = _run_with_stub(monkeypatch, tmp_path, health=health,
                         evidence={"evidence_grade": "replayable",
                                   "scorecard_summary": {"has_outcome_data": False,
                                                         "candidate_count": 0}})
    by = _by(rep)
    assert by["health_available"] == "pass"
    assert by["heartbeat_fresh"] == "pass"
    assert by["market_data_fresh"] == "pass"
    assert by["live_blocked"] == "pass"
    assert rep["overall"] != "fail"                # warn or pass


# --- STAGE 2: provenance + bundle ------------------------------------------

def test_provenance_fields_present(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    rep = ACC.run_acceptance(journal=journal, sim_db=sim, learn_dir=learn,
                             now_ms=1)
    assert set(rep["input_paths"]) == {"journal", "sim_db", "learn_dir",
                                       "agent_log", "scorecard", "session_report"}
    art = rep["artifact_status"]
    for k in ("agent_loop", "scorecard", "session_report", "evidence_report",
              "runtime_policy", "calibration"):
        assert k in art and "exists" in art[k]
    assert art["agent_loop"]["exists"] is True
    assert "current_git_commit" in rep
    assert "source_errors" in rep
    assert "repo_dirty" in rep


def test_bundle_out_writes_single_json(tmp_path):
    journal, sim, learn = _clean_setup(tmp_path)
    # write an evidence + session report so the bundle has compact summaries.
    (learn / "evidence-report.json").write_text(json.dumps(
        {"generated_ms": 1, "evidence_grade": "promotion_candidate",
         "scorecard_summary": {"candidate_count": 1}}), encoding="utf-8")
    (learn / "session-report.json").write_text(json.dumps(
        {"generated_ms": 1, "decisions": {"total": 3}}), encoding="utf-8")
    out = tmp_path / "acc.json"
    bundle = tmp_path / "bundle.json"
    rc = ACC.main(["--journal", str(journal), "--sim-db", str(sim),
                   "--learn-dir", str(learn), "--out", str(out),
                   "--bundle-out", str(bundle)])
    assert rc in (0, 1)
    assert out.exists() and bundle.exists()
    b = json.loads(bundle.read_text(encoding="utf-8"))
    assert b["live_blocked"] is True
    assert b["acceptance"]["overall"] in ("pass", "warn", "fail")
    assert b["evidence_summary"]["evidence_grade"] == "promotion_candidate"
    assert b["session_summary"]["decisions"]["total"] == 3
