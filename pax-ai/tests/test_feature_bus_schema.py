"""SQLite schema invariants for pax-bus.db.

Tests open a fresh DB in a tmp_path, run _ensure_schema, then introspect.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus


EXPECTED_TABLES = {
    "snapshot_features",
    "level_events",
    "microstructure_events",
    "trigger_events",
    "ai_turns",
    "trade_outcomes",
    "settings_versions",
    "replay_sessions",
}


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    db = tmp_path / "pax-bus-test.db"
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
    return db


def test_eight_tables_present(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES.issubset(names), names


def test_journal_mode_is_wal(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_every_table_has_schema_version_column(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        for tbl in EXPECTED_TABLES:
            cols = {row[1] for row in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "schema_version" in cols, f"{tbl} missing schema_version"


def test_ai_turns_requires_both_shas(fresh_db: Path):
    """Both digest_sha256 and snapshot_sha256 are NOT NULL."""
    with sqlite3.connect(fresh_db) as conn:
        info = {row[1]: (row[2], row[3]) for row in
                conn.execute("PRAGMA table_info(ai_turns)").fetchall()}
        # row tuple is (cid, name, type, notnull, dflt_value, pk)
        # but we mapped to (type, notnull)
    assert info["digest_sha256"][1] == 1, "digest_sha256 must be NOT NULL"
    assert info["snapshot_sha256"][1] == 1, "snapshot_sha256 must be NOT NULL"


def test_ai_turns_indexes_on_both_shas(fresh_db: Path):
    with sqlite3.connect(fresh_db) as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='ai_turns'").fetchall()}
    assert "idx_aiturn_snapshot_sha" in idx
    assert "idx_aiturn_digest_sha" in idx
    assert "idx_aiturn_ts" in idx


def test_ensure_schema_is_idempotent(tmp_path: Path):
    db = tmp_path / "pax-bus-test.db"
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        feature_bus._ensure_schema(conn)        # second call must not raise
    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert EXPECTED_TABLES.issubset(names)
