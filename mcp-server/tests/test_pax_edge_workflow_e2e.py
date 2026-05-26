"""End-to-end smoke test for the Pax self-training edge workflow.

Runs the full chain on tmp paths, no live Claude / broker / bus daemon:

  forecast_store + fake bus DB
        |
        v
  pax_calibration --date ... --forecasts ... --bus-db ...
        |  reports/calibration-DATE.json
        v
  pax_research_claude --date ... --calibration ...
        |  reports/policy-candidates-DATE.json
        |  reports/prompt-lessons-DATE.md
        v
  pax_policy_replay --candidates ... --outcomes-json ... --forecasts ...
           reports/replay-DATE.json

Asserts each artifact exists, its schema is sane, and the deterministic
extraction + promotion behavior matches the inputs.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from bookmap_mcp import (
    pax_calibration as calib,
    pax_forecast_store as store,
    pax_policy_replay as replay,
    pax_research_claude as research,
)


def _raw(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG",
         horizon=300, prob=0.62, expected_r=0.75):
    return {
        "alias": "NQM6.CME@RITHMIC",
        "level": level,
        "thesis": thesis,
        "execution_read": "PAY_FOR_TRADE",
        "direction": direction,
        "horizon_sec": horizon,
        "prob_success": prob,
        "expected_r": expected_r,
        "invalidation": "back below level",
        "features_used": ["or_levels", "pull_stack"],
    }


def _make_bus_db(path: Path, ai_turns_rows, trade_outcomes_rows):
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
        CREATE TABLE ai_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts_ms INTEGER NOT NULL,
          chat_run_id TEXT NOT NULL,
          digest_sha256 TEXT NOT NULL,
          snapshot_sha256 TEXT NOT NULL
        );
        CREATE TABLE trade_outcomes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ai_turn_id INTEGER NOT NULL,
          realized_r_at_t300s REAL
        );
        """)
        for r in ai_turns_rows:
            conn.execute(
                "INSERT INTO ai_turns (id, ts_ms, chat_run_id, "
                "digest_sha256, snapshot_sha256) VALUES (?, ?, ?, ?, ?)",
                (r["id"], r["ts_ms"], r["chat_run_id"],
                 r["digest_sha256"], r["snapshot_sha256"]),
            )
        for r in trade_outcomes_rows:
            conn.execute(
                "INSERT INTO trade_outcomes (ai_turn_id, realized_r_at_t300s) "
                "VALUES (?, ?)",
                (r["ai_turn_id"], r["realized_r_at_t300s"]),
            )
        conn.commit()
    finally:
        conn.close()


def test_edge_workflow_end_to_end(tmp_path):
    date_utc = "2026-05-25"
    forecasts_db = tmp_path / "pax-forecasts.db"
    bus_db = tmp_path / "pax-bus.db"
    reports_dir = tmp_path / "reports"

    # 1) Populate the forecast store. The GOOD setup has +1R outcomes,
    #    the BAD setup has -1R outcomes. Linkage metadata is present so
    #    the linkage-fallback path is exercised end-to-end.
    base_ms = calib.utc_day_window(date_utc)[0]
    s = store.PaxForecastStore(forecasts_db)
    ai_turns = []
    outcomes = []
    sid = 1
    ts = base_ms + 1
    for i in range(60):
        s.record(_raw(level="OR-H", thesis="ACCEPTANCE_LONG", direction="LONG"),
                 ts_ms=ts, source_turn_id=None,
                 chat_run_id=f"run-{i}-good",
                 digest_sha256=f"good-digest-{i}",
                 snapshot_sha256=f"good-snap-{i}")
        ai_turns.append({"id": sid, "ts_ms": ts,
                          "chat_run_id": f"run-{i}-good",
                          "digest_sha256": f"good-digest-{i}",
                          "snapshot_sha256": f"good-snap-{i}"})
        outcomes.append({"ai_turn_id": sid, "realized_r_at_t300s": 1.0})
        sid += 1; ts += 1
        s.record(_raw(level="OR-L", thesis="REJECTION_SHORT", direction="SHORT"),
                 ts_ms=ts, source_turn_id=None,
                 chat_run_id=f"run-{i}-bad",
                 digest_sha256=f"bad-digest-{i}",
                 snapshot_sha256=f"bad-snap-{i}")
        ai_turns.append({"id": sid, "ts_ms": ts,
                          "chat_run_id": f"run-{i}-bad",
                          "digest_sha256": f"bad-digest-{i}",
                          "snapshot_sha256": f"bad-snap-{i}"})
        outcomes.append({"ai_turn_id": sid, "realized_r_at_t300s": -1.0})
        sid += 1; ts += 1
    s.close()

    _make_bus_db(bus_db, ai_turns, outcomes)

    # 2) Calibration.
    calibration_path = reports_dir / f"calibration-{date_utc}.json"
    rc = calib.main([
        "--date", date_utc,
        "--forecasts", str(forecasts_db),
        "--bus-db", str(bus_db),
        "--report", str(calibration_path),
        "--min-samples", "5",
    ])
    assert rc == 0
    assert calibration_path.exists()
    calibration = json.loads(calibration_path.read_text(encoding="utf-8"))
    assert calibration["date_utc"] == date_utc
    # 60 good + 60 bad = 120 PAY_FOR_TRADE forecasts, all paired via linkage.
    assert calibration["global"]["n_paired"] == 120
    setups = {b["setup"]: b for b in calibration["setup_buckets"]}
    bad_setup_key = "OR-L|REJECTION_SHORT|PAY_FOR_TRADE|SHORT|300"
    good_setup_key = "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    assert bad_setup_key in setups
    assert good_setup_key in setups
    assert setups[bad_setup_key]["mean_realized_r"] < 0
    assert setups[good_setup_key]["mean_realized_r"] > 0

    # 3) Research / candidate lessons.
    rc = research.main([
        "--date", date_utc,
        "--calibration", str(calibration_path),
        "--out-dir", str(reports_dir),
        "--min-samples", "5",
        "--dry-run",
    ])
    assert rc == 0
    policy_candidates_path = reports_dir / f"policy-candidates-{date_utc}.json"
    prompt_lessons_path = reports_dir / f"prompt-lessons-{date_utc}.md"
    assert policy_candidates_path.exists()
    assert prompt_lessons_path.exists()
    candidates_doc = json.loads(
        policy_candidates_path.read_text(encoding="utf-8"))
    assert candidates_doc["promotion_status"] == "research_only"
    assert candidates_doc["lessons"], "expected at least one candidate lesson"
    bad_lesson = next(L for L in candidates_doc["lessons"]
                       if L["setup"] == bad_setup_key)
    assert bad_lesson["change"]["kind"] == "filter"
    assert bad_lesson["promotion_status"] == "research_only"

    # 4) Policy replay. Provide outcomes via JSON so the replay is fully
    # deterministic and does not need the bus DB at this step.
    outcomes_json_path = tmp_path / "outcomes.json"
    # In replay we still need (source_turn_id, realized_r) rows. Since
    # our forecasts have None source_turn_id, we feed the linkage-derived
    # ai_turn_id - but the replay outcomes-json path keys on
    # source_turn_id by design. To keep the smoke test focused on the
    # replay layer, we synthesize a "scenarios" outcomes file keyed on a
    # surrogate id we inject into the forecast store with source_turn_id.
    #
    # Build a second, replay-focused store/outcomes pair so the smoke
    # does not depend on a real bus DB.
    replay_db = tmp_path / "replay-forecasts.db"
    rs = store.PaxForecastStore(replay_db)
    replay_outcomes = []
    rid = 1
    ts = base_ms + 1
    # Interleave good and bad so train / validation / test each contain
    # both setups. Without this, the time split puts all bad rows in the
    # later windows and filtering the bad setup leaves 0 test samples.
    for _ in range(60):
        for r_val, level, thesis, direction in (
            (1.0,  "OR-H", "ACCEPTANCE_LONG",  "LONG"),
            (-1.0, "OR-L", "REJECTION_SHORT", "SHORT"),
        ):
            rs.record(_raw(level=level, thesis=thesis, direction=direction),
                      ts_ms=ts, source_turn_id=rid)
            replay_outcomes.append({"source_turn_id": rid,
                                     "realized_r": r_val,
                                     "horizon_used_sec": 300})
            rid += 1; ts += 1
    rs.close()
    outcomes_json_path.write_text(json.dumps(replay_outcomes),
                                   encoding="utf-8")

    replay_report_path = reports_dir / f"replay-{date_utc}.json"
    rc = replay.main([
        "--forecasts", str(replay_db),
        "--candidates", str(policy_candidates_path),
        "--outcomes-json", str(outcomes_json_path),
        "--report", str(replay_report_path),
        "--min-samples", "10",
    ])
    assert rc == 0
    assert replay_report_path.exists()
    replay_doc = json.loads(replay_report_path.read_text(encoding="utf-8"))
    assert replay_doc["n_forecasts"] == 120
    assert replay_doc["candidates"], "replay must evaluate at least one candidate"
    # The bad-setup filter should improve metrics and earn replay_passed
    # or paper_candidate. Never beyond.
    promotions = {c["promotion_status"] for c in replay_doc["candidates"]}
    allowed = {"research_only", "replay_passed", "paper_candidate"}
    assert promotions.issubset(allowed), (
        f"unexpected promotion: {promotions - allowed}")
    bad_replay = next(c for c in replay_doc["candidates"]
                       if c.get("setup") == bad_setup_key)
    assert bad_replay["promotion_status"] in {"replay_passed", "paper_candidate"}
    assert bad_replay["false_positive_reduction"] > 0


def test_workflow_does_not_touch_active_config(tmp_path):
    """Belt-and-braces: running the full chain must not write to any of
    the production config / weights / prompt files."""
    sentinels = {
        tmp_path / "pax_ai_config.json": "ORIG_CONFIG",
        tmp_path / "pax_weights.json":   "ORIG_WEIGHTS",
        tmp_path / "prompts.py":         "ORIG_PROMPTS",
        tmp_path / "learned_playbook.json": "ORIG_PLAYBOOK",
    }
    for p, content in sentinels.items():
        p.write_text(content, encoding="utf-8")

    date_utc = "2026-05-25"
    forecasts_db = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(forecasts_db)
    base_ms = calib.utc_day_window(date_utc)[0]
    for i in range(6):
        s.record(_raw(prob=0.6), ts_ms=base_ms + i,
                 source_turn_id=i + 1)
    s.close()

    calib.calibration_for_day(
        forecasts_path=forecasts_db,
        outcome_lookup=lambda _f: {"realized_r": 0.5,
                                    "horizon_used_sec": 300},
        date_utc=date_utc, min_samples=3,
    )
    research.write_candidate_artifacts(
        date_utc=date_utc,
        calibration={"date_utc": date_utc, "global": {}, "setup_buckets": [],
                      "probability_buckets": [], "horizon_breakdown": {},
                      "non_pay_for_trade": {"n_forecasts": 0}, "notes": []},
        reports_dir=tmp_path / "reports",
        dry_run=True,
    )

    for p, content in sentinels.items():
        assert p.read_text(encoding="utf-8") == content
