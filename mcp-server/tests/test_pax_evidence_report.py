"""Evidence-quality reporting (STAGE 1 grading + STAGE 2 setup table).

Honest: grades describe evidence quantity/quality, never profitability;
'candidate' is never 'validated'; SIM-only, live hard-blocked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_evidence_report as E   # noqa: E402


# --- helpers ---------------------------------------------------------------

def _ri_record(ts):
    return {"ts_ms": ts, "heartbeat": True, "action": "NONE",
            "replay_input": {"version": 1, "snapshot": {"health": "ok"},
                             "status": {"position": {"size": 0}}, "now_ms": ts,
                             "market_age_sec": 1.0, "heartbeat_age_sec": 1.0,
                             "sim_broker_ok": True, "kill_switch_active": False}}


def _plain_record(ts):
    return {"ts_ms": ts, "heartbeat": True, "action": "NONE"}


def _scorecard(setups):
    return {"min_samples": 30, "setups": setups}


def _candidate_scorecard():
    return _scorecard([{"setup": "A|LONG|OR-H|ETH", "n": 40,
                        "mean_realized_r": 0.3, "hit_rate": 0.62}])


# --- STAGE 1: grades -------------------------------------------------------

def test_empty_inputs_is_no_data():
    rep = E.build_evidence_report(feed=[], scorecard=None, now_ms=1)
    assert rep["evidence_grade"] == "no_data"
    assert "no_agent_records" in rep["blockers"]
    assert "no_scorecard_or_outcomes" in rep["blockers"]
    assert rep["live_blocked"] is True


def test_old_logs_without_replay_input_is_logging_only():
    feed = [_plain_record(i) for i in range(5)]
    rep = E.build_evidence_report(feed=feed, scorecard=None, now_ms=1)
    assert rep["evidence_grade"] == "logging_only"
    assert any("low_replay_input_coverage" in w for w in rep["warnings"])


def test_replay_input_without_scorecard_is_replayable():
    feed = [_ri_record(i) for i in range(5)]
    rep = E.build_evidence_report(feed=feed, scorecard=None, now_ms=1)
    assert rep["evidence_grade"] == "replayable"
    assert "no_scorecard_or_outcomes" in rep["blockers"]


def test_replay_input_with_scorecard_is_outcome_linked():
    feed = [_ri_record(i) for i in range(5)]
    sc = _scorecard([{"setup": "A|LONG|OR-H|ETH", "n": 10,
                      "mean_realized_r": 0.2, "hit_rate": 0.5}])  # not candidate
    rep = E.build_evidence_report(feed=feed, scorecard=sc, now_ms=1)
    assert rep["evidence_grade"] == "outcome_linked"
    assert rep["scorecard_summary"]["candidate_count"] == 0


def test_candidate_setup_is_promotion_candidate():
    feed = [_ri_record(i) for i in range(5)]
    rep = E.build_evidence_report(feed=feed, scorecard=_candidate_scorecard(),
                                  now_ms=1)
    assert rep["evidence_grade"] == "promotion_candidate"
    assert rep["scorecard_summary"]["candidate_count"] == 1


def test_report_shape_is_stable():
    rep = E.build_evidence_report(feed=[_ri_record(1)],
                                  scorecard=_candidate_scorecard(), now_ms=1)
    for k in ("generated_ms", "input_paths", "replay_readiness",
              "replay_summary", "session_summary", "scorecard_summary",
              "setup_evidence", "evidence_grade", "blockers", "warnings",
              "next_required_data", "live_blocked", "limitations"):
        assert k in rep
    # deterministic except generated_ms
    a = E.build_evidence_report(feed=[_ri_record(1)],
                                scorecard=_candidate_scorecard(), now_ms=10)
    b = E.build_evidence_report(feed=[_ri_record(1)],
                                scorecard=_candidate_scorecard(), now_ms=20)
    a.pop("generated_ms"); b.pop("generated_ms")
    a["scorecard_summary"].pop("min_samples", None)  # stable anyway
    b["scorecard_summary"].pop("min_samples", None)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --- STAGE 2: setup evidence table -----------------------------------------

def _table_for(setups):
    from bookmap_mcp import pax_promotion_report as P
    promo = P.build_promotion_report(_scorecard(setups), now_ms=1)
    return E.build_setup_evidence_table(promo)


def test_small_sample_collect_more_data():
    row = _table_for([{"setup": "A", "n": 5, "mean_realized_r": 0.4,
                       "hit_rate": 0.6}])[0]
    assert row["evidence_status"] == "insufficient"
    assert row["recommended_action"] == "collect_more_data"
    assert row["live_blocked"] is True


def test_negative_enough_sample_block_or_throttle():
    row = _table_for([{"setup": "C", "n": 50, "mean_realized_r": -0.4,
                       "hit_rate": 0.3}])[0]
    assert row["evidence_status"] == "blocked"
    assert row["recommended_action"] == "block_or_throttle"


def test_positive_exploratory_keep_observing():
    row = _table_for([{"setup": "B", "n": 30, "mean_realized_r": 0.01,
                       "hit_rate": 0.5}])[0]
    assert row["evidence_status"] == "exploratory"
    assert row["recommended_action"] == "keep_observing"


def test_candidate_for_paper_focus():
    row = _table_for([{"setup": "A", "n": 40, "mean_realized_r": 0.3,
                       "hit_rate": 0.62}])[0]
    assert row["evidence_status"] == "candidate"
    assert row["recommended_action"] == "candidate_for_paper_focus"
    assert row["promotion_status"] == "candidate"


def test_missing_avg_r_handled_honestly():
    # enough n but no mean_realized_r/avg_r -> review_manually, not faked.
    row = _table_for([{"setup": "D", "n": 40, "hit_rate": None}])[0]
    assert "avg_r" in row["missing_fields"]
    assert "win_rate" in row["missing_fields"]
    assert row["recommended_action"] == "review_manually"
    assert row["evidence_status"] == "insufficient"


# --- compact summary -------------------------------------------------------

def test_compute_evidence_summary_compact():
    feed = [_ri_record(i) for i in range(5)]
    s = E.compute_evidence_summary(feed, _candidate_scorecard(), now_ms=1)
    assert s["evidence_grade"] == "promotion_candidate"
    assert s["candidate_setup_count"] == 1
    assert s["replay_input_pct"] == 100.0
    assert s["evidence_blockers"] == 0
    assert s["live_blocked"] is True


# --- I/O wrapper safety ----------------------------------------------------

def test_gather_missing_files_is_no_data(tmp_path):
    rep = E.gather_evidence_report(agent_log=tmp_path / "nope.jsonl",
                                   scorecard_path=tmp_path / "nope.json",
                                   now_ms=1)
    assert rep["evidence_grade"] == "no_data"
    assert rep["input_paths"]["replay_ran"] is False


def test_gather_writes_and_reads(tmp_path):
    log = tmp_path / "agent-loop.jsonl"
    log.write_text("\n".join(json.dumps(_ri_record(i)) for i in range(5)) + "\n",
                   encoding="utf-8")
    sc = tmp_path / "scorecard.json"
    sc.write_text(json.dumps(_candidate_scorecard()), encoding="utf-8")
    out = tmp_path / "evidence-report.json"
    rep = E.gather_evidence_report(agent_log=log, scorecard_path=sc,
                                   out_path=out, now_ms=1)
    assert out.exists()
    assert rep["evidence_grade"] == "promotion_candidate"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["evidence_grade"] == "promotion_candidate"


# --- STAGE 1 (strict ladder): scorecard alone cannot lift the grade --------

def test_scorecard_with_zero_records_is_not_outcome_linked():
    rep = E.build_evidence_report(feed=[], scorecard=_candidate_scorecard(),
                                  now_ms=1)
    assert rep["evidence_grade"] not in ("outcome_linked", "promotion_candidate")
    assert "no_agent_records" in rep["blockers"]


def test_scorecard_with_old_logs_stays_logging_only():
    feed = [_plain_record(i) for i in range(5)]            # no replay_input
    rep = E.build_evidence_report(feed=feed, scorecard=_candidate_scorecard(),
                                  now_ms=1)
    assert rep["evidence_grade"] == "logging_only"
    assert "scorecard_present_but_logs_not_replayable" in rep["blockers"]


def test_candidate_scorecard_low_coverage_not_promotion_candidate():
    # 2 of 5 records carry replay_input -> 40% < 50% floor -> not replayable.
    feed = [_ri_record(0), _ri_record(1), _plain_record(2), _plain_record(3),
            _plain_record(4)]
    rep = E.build_evidence_report(feed=feed, scorecard=_candidate_scorecard(),
                                  now_ms=1)
    assert rep["evidence_grade"] == "logging_only"
    assert rep["evidence_grade"] != "promotion_candidate"
    assert "scorecard_present_but_logs_not_replayable" in rep["blockers"]


def test_replayable_logs_noncandidate_scorecard_is_outcome_linked():
    feed = [_ri_record(i) for i in range(5)]
    sc = _scorecard([{"setup": "A|LONG|OR-H|ETH", "n": 10,
                      "mean_realized_r": 0.2, "hit_rate": 0.5}])  # not candidate
    rep = E.build_evidence_report(feed=feed, scorecard=sc, now_ms=1)
    assert rep["evidence_grade"] == "outcome_linked"


def test_replayable_logs_candidate_scorecard_is_promotion_candidate():
    feed = [_ri_record(i) for i in range(5)]
    rep = E.build_evidence_report(feed=feed, scorecard=_candidate_scorecard(),
                                  now_ms=1)
    assert rep["evidence_grade"] == "promotion_candidate"
