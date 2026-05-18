"""Phase 3: Journal tests — schema, run lifecycle, crash recovery, writers."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.journal import Journal      # noqa: E402


@pytest.fixture
def journal(tmp_path):
    j = Journal(tmp_path / "test.db")
    j.open()
    yield j
    j.close()


def test_schema_created_on_open(journal):
    """All 9 tables present after open()."""
    expected = {"runs", "snapshots", "signals", "orders", "fills",
                 "positions", "daily_stats", "adapter_health", "events"}
    rows = journal._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    actual = {r["name"] for r in rows}
    missing = expected - actual
    assert not missing, f"missing tables: {missing}"


def test_wal_mode_active(journal):
    mode = journal._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_begin_run_returns_uuid_and_records_row(journal):
    run_id = journal.begin_run(adapter_name="test_adapter",
                                 signal_version="v_test")
    assert isinstance(run_id, str) and len(run_id) >= 30
    row = journal._conn.execute(
        "SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    assert row is not None
    assert row["adapter_name"] == "test_adapter"
    assert row["signal_version"] == "v_test"
    assert row["ended_ms"] is None


def test_end_run_marks_ended_ms(journal):
    rid = journal.begin_run("a")
    journal.end_run("clean")
    row = journal._conn.execute(
        "SELECT ended_ms, notes FROM runs WHERE run_id=?", (rid,)).fetchone()
    assert row["ended_ms"] is not None
    assert "clean" in row["notes"]


def test_crash_recovery_logs_run_crashed_event(journal):
    """If a previous run is unended (process killed), starting a new run
    logs RUN_CRASHED in the OLD run and opens a fresh one."""
    crashed_id = journal.begin_run("a")
    journal.write_event("INFO", "test", "before crash")
    # Simulate crash: no end_run called. Open a new run.
    new_id = journal.begin_run("b")
    assert new_id != crashed_id

    # OLD run should now be ended.
    old = journal._conn.execute(
        "SELECT ended_ms, notes FROM runs WHERE run_id=?",
        (crashed_id,)).fetchone()
    assert old["ended_ms"] is not None
    assert "unclean-shutdown-recovered" in old["notes"]

    # A RUN_CRASHED event must exist in the old run.
    ev = journal._conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE run_id=? AND kind=?",
        (crashed_id, "RUN_CRASHED")).fetchone()
    assert ev["n"] == 1


def test_crash_recovery_across_process_restart(tmp_path):
    """Daemon crashes mid-run; the next *process* gets a fresh Journal
    instance with _event_seq=0. begin_run must still recover without
    colliding on (run_id, seq) when it appends RUN_CRASHED into the
    prior run's existing event sequence."""
    db = tmp_path / "restart.db"
    j1 = Journal(db); j1.open()
    rid1 = j1.begin_run("a")
    j1.write_event("INFO", "test", "e1")
    j1.write_event("INFO", "test", "e2")
    j1.commit()
    j1.close()                       # simulate ungraceful exit

    j2 = Journal(db); j2.open()
    rid2 = j2.begin_run("b")         # must not raise IntegrityError
    assert rid2 != rid1

    rows = j2._conn.execute(
        "SELECT seq, kind FROM events WHERE run_id=? ORDER BY seq",
        (rid1,)).fetchall()
    assert [r["seq"]  for r in rows] == [1, 2, 3]
    assert [r["kind"] for r in rows] == ["INFO", "INFO", "RUN_CRASHED"]

    old = j2._conn.execute(
        "SELECT ended_ms FROM runs WHERE run_id=?", (rid1,)).fetchone()
    assert old["ended_ms"] is not None
    j2.close()


def test_write_snapshot_populates_row(journal):
    journal.begin_run("test")
    snap = {
        "alias": "NQM6", "health": "ok",
        "ts": "2026-05-18T13:30:00+00:00",
        "book": {"bestBid": 20050.0, "bestAsk": 20050.5,
                  "mid": 20050.25, "spread": 0.5},
        "vwap_obj": {"vwap": 20040.0, "stddev": 8.0},
        "or_row": {"orHigh": "20100.0", "orLow": "20000.0"},
        "flow": {"regime": "TRENDING_UP", "biasScore": 0.5,
                  "biasTrajectory": "RISING"},
        "_synthetic": ["book.bestBid"],
    }
    journal.write_snapshot(snap)
    row = journal._conn.execute(
        "SELECT * FROM snapshots WHERE alias=?", ("NQM6",)).fetchone()
    assert row is not None
    assert row["mid"] == 20050.25
    assert row["regime"] == "TRENDING_UP"
    assert row["bias_score"] == 0.5
    assert row["bias_trajectory"] == "RISING"
    assert row["or_high"] == 20100.0
    assert row["synthetic"] == "book.bestBid"


def test_write_signal_extracts_decision_fields(journal):
    journal.begin_run("test")
    snap = {
        "alias": "NQM6", "health": "ok",
        "ts": "2026-05-18T13:30:00+00:00",
        "or_levels": {"levels": [
            {"label": "OR-H", "composite":
                {"score": 0.5, "direction": "FOLLOW_LONG"}}
        ]},
        "conviction": {"score": 0.6, "trajectory": "RISING"},
    }
    decision = {
        "decision": "ENTER_LONG_FOLLOW", "size_tier": "FULL",
        "confidence": 0.75, "level_label": "OR-H",
        "entry": 20100.0, "components": {"a": 1}, "reasons": ["r1", "r2"],
    }
    journal.write_signal(snap, decision)
    row = journal._conn.execute(
        "SELECT * FROM signals WHERE alias=?", ("NQM6",)).fetchone()
    assert row is not None
    assert row["decision"] == "ENTER_LONG_FOLLOW"
    assert row["size_tier"] == "FULL"
    assert row["confidence"] == 0.75
    assert row["level_label"] == "OR-H"
    assert row["composite_score"] == 0.5
    assert row["composite_dir"] == "FOLLOW_LONG"
    assert row["conviction_score"] == 0.6
    assert row["conviction_trajectory"] == "RISING"


def test_write_event_increments_seq(journal):
    journal.begin_run("test")
    journal.write_event("INFO", "test", "first")
    journal.write_event("WARN", "test", "second")
    rows = journal._conn.execute(
        "SELECT seq, kind FROM events ORDER BY seq").fetchall()
    assert [r["seq"] for r in rows] == [1, 2]
    assert [r["kind"] for r in rows] == ["INFO", "WARN"]


def test_write_adapter_health_accepts_dataclass_and_dict(journal):
    journal.begin_run("test")
    # Dict-shaped
    journal.write_adapter_health({"status": "ok", "detail": "from dict",
                                    "snapshots_emitted": 5})
    # Dataclass
    from bookmap_mcp.adapters.base import AdapterHealth
    journal.write_adapter_health(AdapterHealth(status="stale",
                                                  detail="from dc",
                                                  snapshots_emitted=10))
    rows = journal._conn.execute(
        "SELECT status, detail, snapshots_emitted FROM adapter_health "
        "ORDER BY ts_ms").fetchall()
    statuses = [r["status"] for r in rows]
    assert "ok" in statuses and "stale" in statuses


def test_context_manager_lifecycle(tmp_path):
    """`with Journal(path) as j` opens and closes deterministically."""
    db = tmp_path / "ctx.db"
    with Journal(db) as j:
        j.begin_run("ctx")
        j.write_event("INFO", "test", "in context")
    # After exit, the file is closed. Open standalone connection and read.
    c = sqlite3.connect(str(db))
    c.row_factory = sqlite3.Row
    rows = c.execute("SELECT kind FROM events").fetchall()
    assert rows[0]["kind"] == "INFO"
    c.close()


def test_concurrent_reader_works_under_wal(tmp_path):
    """While the daemon writes, a separate connection can read."""
    db = tmp_path / "wal.db"
    j = Journal(db); j.open(); j.begin_run("wal-test")
    j.write_snapshot({
        "alias": "X", "health": "ok",
        "ts": "2026-05-18T13:30:00+00:00",
        "book": {"mid": 100.0, "bestBid": 99.75, "bestAsk": 100.25, "spread": 0.5},
    })
    j.commit()
    # Reader connection.
    c = sqlite3.connect(str(db))
    rows = c.execute("SELECT alias, mid FROM snapshots").fetchall()
    assert len(rows) == 1
    assert rows[0][1] == 100.0
    c.close()
    j.close()
