"""Tests for pax_ai.journal -- SQLite chat persistence.

Use tmp_path so tests never touch the real D:\\BookmapLogs\\pax-chat.db.
"""

from __future__ import annotations

import time

import pytest

from pax_ai import journal


@pytest.fixture
def fresh_journal(tmp_path, monkeypatch):
    """Each test gets its own DB and a clean module state."""
    db = tmp_path / "pax-chat.db"
    journal._reset_for_tests()
    run_id = journal.init(db_path=db)
    yield {"db": db, "run_id": run_id}
    journal._reset_for_tests()


def test_init_creates_db_and_run_id(fresh_journal):
    assert fresh_journal["db"].exists()
    assert fresh_journal["run_id"]                  # non-empty
    assert journal.current_run_id() == fresh_journal["run_id"]


def test_init_is_idempotent_within_process(fresh_journal):
    rid1 = journal.current_run_id()
    # Calling init() a second time should NOT change run_id.
    rid2 = journal.init(db_path=fresh_journal["db"])
    assert rid1 == rid2


def test_record_and_recent_round_trip(fresh_journal):
    journal.record("YOU", "is +1 in play?", meta={"router": "pax-or"})
    journal.record("PAX", "yes -- STRONG_BULL holding", meta={"model": "haiku"})
    rows = journal.recent(limit=10)
    assert len(rows) == 2
    # Chronological order: oldest first
    assert rows[0]["role"] == "YOU"
    assert rows[0]["text"] == "is +1 in play?"
    assert rows[0]["meta"] == {"router": "pax-or"}
    assert rows[1]["role"] == "PAX"
    assert rows[1]["text"].startswith("yes")
    assert rows[1]["meta"]["model"] == "haiku"


def test_recent_filter_by_run_id(fresh_journal, tmp_path):
    # Write a row in current run
    journal.record("YOU", "current run msg")
    cur_run = journal.current_run_id()
    # Force a new run by resetting + re-initing the same DB
    journal._reset_for_tests()
    journal.init(db_path=fresh_journal["db"])
    new_run = journal.current_run_id()
    assert new_run != cur_run
    journal.record("YOU", "new run msg")

    cross = journal.recent(limit=50, run_id=None)
    assert len(cross) == 2
    only_current = journal.recent(limit=50, run_id=new_run)
    assert len(only_current) == 1
    assert only_current[0]["text"] == "new run msg"
    only_prior = journal.recent(limit=50, run_id=cur_run)
    assert len(only_prior) == 1
    assert only_prior[0]["text"] == "current run msg"


def test_forget_current_run_only(fresh_journal):
    journal.record("YOU", "to be forgotten")
    journal.record("PAX", "also to be forgotten")
    # Simulate a past run by inserting with a different run_id directly
    # via the public API: reset + reinit + record + reset back.
    prior_run = journal.current_run_id()
    journal._reset_for_tests()
    other_run = journal.init(db_path=fresh_journal["db"], run_id="OTHER")
    journal.record("YOU", "do NOT forget this")
    assert other_run == "OTHER"

    # Now forget OTHER explicitly
    n = journal.forget(other_run)
    assert n == 1
    assert journal.recent(limit=50) == [] or all(
        r["run_id"] != "OTHER" for r in journal.recent(limit=50))

    # Prior-run rows still there
    rows = journal.recent(limit=50, run_id=prior_run)
    assert len(rows) == 2


def test_forget_star_wipes_everything(fresh_journal):
    journal.record("YOU", "a")
    journal.record("PAX", "b")
    deleted = journal.forget("*")
    assert deleted >= 2
    assert journal.recent(limit=50) == []


def test_recent_limit_clamp(fresh_journal):
    for i in range(20):
        journal.record("YOU", f"msg{i}")
    rows = journal.recent(limit=5)
    assert len(rows) == 5
    # Should be the 5 most recent ones, in chronological order
    assert [r["text"] for r in rows] == [f"msg{i}" for i in range(15, 20)]


def test_record_no_op_when_init_failed(tmp_path, monkeypatch):
    """If init() fails (unwritable path), record() must be a silent no-op
    so the chat keeps working without history."""
    journal._reset_for_tests()
    # Point at a path we KNOW we can't create -- monkeypatch _connect to raise
    def bad_connect(_path):
        import sqlite3
        raise sqlite3.OperationalError("simulated unwritable")
    monkeypatch.setattr(journal, "_connect", bad_connect)
    run_id = journal.init(db_path=tmp_path / "x.db")
    assert run_id == ""
    # record/recent/forget must not throw
    journal.record("YOU", "msg")
    assert journal.recent() == []
    assert journal.forget() == 0
    journal._reset_for_tests()


def test_schema_persists_across_reconnects(fresh_journal):
    journal.record("YOU", "msg-a")
    # Simulate a process restart: reset module state but keep the DB file
    journal._reset_for_tests()
    journal.init(db_path=fresh_journal["db"])
    rows = journal.recent(limit=50)
    assert len(rows) == 1
    assert rows[0]["text"] == "msg-a"


def test_meta_round_trip_jsonable_types(fresh_journal):
    journal.record("PAX", "answer", meta={
        "model": "haiku", "elapsed_ms": 1234,
        "tokens_emitted": 47, "aborted": False, "score": 0.62,
        "list_field": ["a", "b"],
    })
    rows = journal.recent()
    assert rows[0]["meta"]["model"] == "haiku"
    assert rows[0]["meta"]["elapsed_ms"] == 1234
    assert rows[0]["meta"]["list_field"] == ["a", "b"]
