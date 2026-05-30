"""STAGE 4: report-only tuning suggestions.

Must never write policy/config; never auto-promote. SIM-only; live hard-blocked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp import pax_tuning_report as T        # noqa: E402


def _sc(setups):
    return {"min_samples": 30, "setups": setups}


def test_candidate_blocked_undersampled_buckets():
    sc = _sc([
        {"setup": "A|LONG|OR-H|ETH", "n": 40, "mean_realized_r": 0.3, "hit_rate": 0.62},
        {"setup": "B|SHORT|OR-L|RTH", "n": 50, "mean_realized_r": -0.4, "hit_rate": 0.3},
        {"setup": "C|LONG|OR-H|ETH", "n": 5, "mean_realized_r": 0.4, "hit_rate": 0.6},
    ])
    rep = T.build_tuning_report(scorecard=sc, now_ms=1)
    assert [r["setup"] for r in rep["candidate_setups"]] == ["A|LONG|OR-H|ETH"]
    assert [r["setup"] for r in rep["blocked_setups"]] == ["B|SHORT|OR-L|RTH"]
    assert [r["setup"] for r in rep["under_sampled_setups"]] == ["C|LONG|OR-H|ETH"]
    assert rep["report_only"] is True
    assert rep["policy_written"] is False
    assert rep["live_blocked"] is True


def test_no_auto_promotion_language():
    sc = _sc([{"setup": "A|LONG|OR-H|ETH", "n": 40, "mean_realized_r": 0.3,
               "hit_rate": 0.62}])
    rep = T.build_tuning_report(scorecard=sc, now_ms=1)
    blob = json.dumps(rep).lower()
    assert "report-only" in blob or "report_only" in blob
    # candidate review must explicitly NOT auto-promote.
    assert any("not auto" in tr["review"].lower() or "human" in tr["review"].lower()
               for tr in rep["suggested_threshold_review"])


def test_empty_scorecard_suggests_collecting_outcomes():
    rep = T.build_tuning_report(scorecard=None, now_ms=1)
    assert rep["candidate_setups"] == []
    assert any("scorecard" in s for s in rep["suggested_next_data_to_collect"])


def test_under_sampled_suggests_more_samples():
    sc = _sc([{"setup": "C|LONG|OR-H|ETH", "n": 5, "mean_realized_r": 0.4,
               "hit_rate": 0.6}])
    rep = T.build_tuning_report(scorecard=sc, now_ms=1)
    assert any("under-sampled" in s for s in rep["suggested_next_data_to_collect"])


def test_deterministic_except_generated_ms():
    sc = _sc([{"setup": "A|LONG|OR-H|ETH", "n": 40, "mean_realized_r": 0.3,
               "hit_rate": 0.62}])
    a = T.build_tuning_report(scorecard=sc, now_ms=10)
    b = T.build_tuning_report(scorecard=sc, now_ms=20)
    a.pop("generated_ms"); b.pop("generated_ms")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# --- the load-bearing safety property: it NEVER writes policy --------------

def test_gather_never_writes_policy_or_config(tmp_path):
    learn = tmp_path / "learn"; learn.mkdir()
    sc = learn / "scorecard.json"
    sc.write_text(json.dumps(_sc([{"setup": "A|LONG|OR-H|ETH", "n": 40,
                  "mean_realized_r": 0.3, "hit_rate": 0.62}])), encoding="utf-8")
    rp = learn / "runtime-policy.json"
    rp.write_text(json.dumps({"suggestions": [{"setup": "X", "action": "KEEP"}]}),
                  encoding="utf-8")
    cal = learn / "calibration.json"
    cal.write_text(json.dumps({"buckets": []}), encoding="utf-8")

    before = {p.name: p.read_text(encoding="utf-8")
              for p in (sc, rp, cal)}
    before_files = sorted(p.name for p in learn.iterdir())

    out = learn / "tuning-report.json"
    rep = T.gather_tuning_report(learn_dir=learn, out_path=out, now_ms=1)

    # policy/config files are byte-identical (never written).
    for p in (sc, rp, cal):
        assert p.read_text(encoding="utf-8") == before[p.name], \
            f"{p.name} was modified -- tuning report must be report-only"
    # the only new file is the report we asked for.
    after_files = sorted(p.name for p in learn.iterdir())
    assert set(after_files) - set(before_files) == {"tuning-report.json"}
    assert rep["policy_written"] is False
    assert rep["context"]["has_runtime_policy"] is True   # read for context only


def test_cli_main_no_out_does_not_write(tmp_path, capsys):
    learn = tmp_path / "learn"; learn.mkdir()
    (learn / "scorecard.json").write_text(json.dumps(_sc([])), encoding="utf-8")
    before = sorted(p.name for p in learn.iterdir())
    rc = T.main(["--learn-dir", str(learn)])
    assert rc == 0
    assert sorted(p.name for p in learn.iterdir()) == before   # no writes
