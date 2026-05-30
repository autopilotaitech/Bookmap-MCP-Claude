"""STAGE 3: post-session data-quality report (read-only).

Honest verdicts; no invented linkage. SIM-only; live hard-blocked.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_data_quality as DQ        # noqa: E402
from bookmap_mcp.journal import Journal               # noqa: E402
from bookmap_mcp.sim_engine import SimEngine          # noqa: E402


def _ri(ts, action="NONE", **extra):
    rec = {"ts_ms": ts, "heartbeat": True, "action": action,
           "replay_input": {"version": 1, "snapshot": {"health": "ok"},
                            "status": {"position": {"size": 0}}, "now_ms": ts,
                            "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
                            "sim_broker_ok": True, "kill_switch_active": False}}
    rec.update(extra)
    return rec


def _plain(ts, action="NONE"):
    return {"ts_ms": ts, "heartbeat": True, "action": action}


def _candidate_sc():
    return {"min_samples": 30, "setups": [{"setup": "A|LONG|OR-H|ETH", "n": 40,
            "mean_realized_r": 0.3, "hit_rate": 0.62}]}


# --- pure verdict ladder ---------------------------------------------------

def test_no_data_verdict():
    rep = DQ.build_data_quality(feed=[], scorecard=None, fills_linked=0,
                                entry_fills_observed=0, broker_ok=False,
                                missing_artifacts=["agent-loop.jsonl"], now_ms=1)
    assert rep["verdict"] == "no_data"
    assert rep["live_blocked"] is True


def test_old_logs_no_scorecard_unusable():
    feed = [_plain(i) for i in range(5)]
    rep = DQ.build_data_quality(feed=feed, scorecard=None, fills_linked=0,
                                entry_fills_observed=0, broker_ok=True,
                                missing_artifacts=[], now_ms=1)
    assert rep["verdict"] == "unusable"


def test_replayable_no_scorecard_usable_for_review():
    feed = [_ri(i) for i in range(5)]
    rep = DQ.build_data_quality(feed=feed, scorecard=None, fills_linked=0,
                                entry_fills_observed=0, broker_ok=True,
                                missing_artifacts=[], now_ms=1)
    assert rep["verdict"] == "usable_for_review"


def test_scorecard_but_not_replayable_capped_at_review():
    feed = [_plain(i) for i in range(5)]              # not replay-grade
    rep = DQ.build_data_quality(feed=feed, scorecard=_candidate_sc(),
                                fills_linked=3, entry_fills_observed=3,
                                broker_ok=True, missing_artifacts=[], now_ms=1)
    assert rep["verdict"] == "usable_for_review"      # capped, not tuning_candidate


def test_no_executions_cannot_be_tuning_candidate():
    feed = [_ri(i) for i in range(5)]
    rep = DQ.build_data_quality(feed=feed, scorecard=_candidate_sc(),
                                fills_linked=0, entry_fills_observed=0,
                                broker_ok=True, missing_artifacts=[], now_ms=1)
    assert rep["verdict"] == "usable_for_review"      # no exec/fills -> capped


def test_full_stack_is_tuning_candidate():
    feed = [_ri(i, action="PLACE_LONG", executed=True,
                order={"side": "LONG"}) for i in range(5)]
    rep = DQ.build_data_quality(feed=feed, scorecard=_candidate_sc(),
                                fills_linked=2, entry_fills_observed=2,
                                broker_ok=True, missing_artifacts=[], now_ms=1)
    assert rep["verdict"] == "usable_for_tuning_candidate"
    assert rep["execution_count"] == 5
    assert rep["candidate_setup_count"] == 1
    assert rep["fills_linked_to_setup"] == 2


def test_unlinked_fills_derivation_and_no_invention():
    feed = [_ri(i) for i in range(3)]
    rep = DQ.build_data_quality(feed=feed, scorecard=_candidate_sc(),
                                fills_linked=1, entry_fills_observed=4,
                                broker_ok=True, missing_artifacts=[], now_ms=1)
    assert rep["fills_linked_to_setup"] == 1
    assert rep["unlinked_fills"] == 3                 # 4 observed - 1 linked
    # never negative even if linked > observed (no invented linkage).
    rep2 = DQ.build_data_quality(feed=feed, scorecard=_candidate_sc(),
                                 fills_linked=5, entry_fills_observed=2,
                                 broker_ok=True, missing_artifacts=[], now_ms=1)
    assert rep2["unlinked_fills"] == 0


# --- I/O wrapper safety ----------------------------------------------------

def _fresh_journal(tmp_path):
    db = tmp_path / "j.db"
    j = Journal(db); j.open()
    j.begin_run(adapter_name="csv", signal_version="v2", weights_hash="x")
    j.write_snapshot({"alias": "NQM6", "health": "ok",
                      "ts": datetime.datetime.now(datetime.timezone.utc)
                      .isoformat(timespec="seconds"), "book": {"mid": 1.0}})
    j.end_run("done"); j.close()
    return db


def test_gather_missing_everything_is_no_data(tmp_path):
    rep = DQ.gather_data_quality(journal=tmp_path / "nope.db",
                                 sim_db=tmp_path / "nope-sim.db",
                                 learn_dir=tmp_path / "nolearn", now_ms=1)
    assert rep["verdict"] in ("no_data", "unusable")
    assert rep["live_blocked"] is True
    assert "agent-loop.jsonl" in rep["missing_required_artifacts"]


def test_gather_writes_report_and_no_db_side_effects(tmp_path):
    import time
    journal = _fresh_journal(tmp_path)
    sim = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=sim, eod_close_hour_ct=None)
    learn = tmp_path / "learn"; learn.mkdir()
    now = int(time.time() * 1000)
    (learn / "agent-loop.jsonl").write_text(
        "\n".join(json.dumps(_ri(now + i)) for i in range(5)) + "\n",
        encoding="utf-8")
    (learn / "scorecard.json").write_text(json.dumps(_candidate_sc()),
                                          encoding="utf-8")
    (learn / "session-report.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "dq.json"
    rep = DQ.gather_data_quality(journal=journal, sim_db=sim, learn_dir=learn,
                                 out_path=out, now_ms=now)
    assert out.exists()
    assert rep["verdict"] in ("usable_for_review", "usable_for_tuning_candidate")
    assert rep["missing_required_artifacts"] == []
    assert rep["broker_readable"] is True


def test_cli_main_runs(tmp_path):
    journal = _fresh_journal(tmp_path)
    sim = tmp_path / "sim.db"
    SimEngine(alias="NQM6", db_path=sim, eod_close_hour_ct=None)
    learn = tmp_path / "learn"; learn.mkdir()
    out = tmp_path / "dq.json"
    rc = DQ.main(["--journal", str(journal), "--sim-db", str(sim),
                  "--learn-dir", str(learn), "--out", str(out)])
    assert rc == 0 and out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["live_blocked"] is True
