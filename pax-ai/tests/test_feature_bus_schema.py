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


# ---------------------------------------------------------------------------
# Phase 4: turn-level audit trail -- ai_turns lineage columns
# ---------------------------------------------------------------------------

PHASE4_AI_TURNS_COLUMNS = (
    "prompt_sha256",
    "prompt_version",
    "model_release_id",
    "skill_bundle_sha256",
    "prompt_archive_path",
)


def test_ai_turns_table_has_phase4_lineage_columns(fresh_db: Path):
    """A fresh DB created via _ensure_schema must include the five Phase 4
    lineage columns on ai_turns."""
    with sqlite3.connect(fresh_db) as conn:
        cols = {row[1] for row in
                conn.execute("PRAGMA table_info(ai_turns)").fetchall()}
    for col in PHASE4_AI_TURNS_COLUMNS:
        assert col in cols, f"ai_turns missing Phase 4 column: {col}"


def test_phase4_columns_added_to_pre_existing_db_via_migration(tmp_path: Path):
    """Existing DBs (pre-Phase-4 ai_turns shape, no lineage columns) MUST be
    migrated additively when _ensure_schema runs. Existing rows must remain
    valid and queryable; new rows can populate the new columns."""
    db = tmp_path / "pax-bus-legacy.db"
    # Hand-build a pre-Phase-4 ai_turns table -- the shape that existed before
    # this phase. NOT NULL on digest_sha256/snapshot_sha256 (Phase 1 invariant).
    with feature_bus._open_db(db) as conn:
        conn.execute("""
            CREATE TABLE ai_turns (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              schema_version INTEGER NOT NULL,
              ts_ms INTEGER NOT NULL,
              chat_run_id TEXT NOT NULL,
              deep INTEGER NOT NULL,
              model TEXT NOT NULL,
              router_primary TEXT, router_secondary TEXT,
              user_text_raw TEXT NOT NULL,
              user_text_normalized TEXT NOT NULL,
              pax_text TEXT,
              snapshot_alias TEXT, snapshot_ts_ms INTEGER, snapshot_age_ms INTEGER,
              snapshot_sha256 TEXT NOT NULL,
              digest_sha256 TEXT NOT NULL,
              exit_code INTEGER,
              elapsed_ms INTEGER, api_duration_ms INTEGER,
              total_cost_usd REAL,
              input_tokens INTEGER, output_tokens INTEGER,
              cache_creation_tokens INTEGER, cache_read_tokens INTEGER,
              aborted INTEGER NOT NULL, error TEXT
            )
        """)
        # Insert a legacy row so we can prove old rows survive the migration.
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized,
               snapshot_sha256, digest_sha256, aborted)
            VALUES (1, 1, 'r-legacy', 0, 'legacy-model',
                    'pre-phase-4', 'pre-phase-4',
                    ?, ?, 0)
        """, ("s" * 64, "d" * 64))

    # Now run the migration path.
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)

    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in
                conn.execute("PRAGMA table_info(ai_turns)").fetchall()}
        legacy_row = conn.execute(
            "SELECT chat_run_id, snapshot_sha256, digest_sha256 "
            "FROM ai_turns WHERE chat_run_id='r-legacy'").fetchone()

    for col in PHASE4_AI_TURNS_COLUMNS:
        assert col in cols, f"migration missed column: {col}"
    # Legacy row still present, columns intact.
    assert legacy_row is not None
    assert legacy_row[0] == "r-legacy"
    assert legacy_row[1] == "s" * 64
    assert legacy_row[2] == "d" * 64


def test_phase4_migration_is_idempotent(tmp_path: Path):
    """Running _ensure_schema twice on a freshly migrated DB must not raise
    (the additive ALTERs should be guarded against 'duplicate column' errors)."""
    db = tmp_path / "pax-bus-idem.db"
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        feature_bus._ensure_schema(conn)   # MUST NOT raise on duplicate column
    with sqlite3.connect(db) as conn:
        cols = {row[1] for row in
                conn.execute("PRAGMA table_info(ai_turns)").fetchall()}
    for col in PHASE4_AI_TURNS_COLUMNS:
        assert col in cols


def test_phase4_migration_reraises_non_duplicate_operational_errors(
        tmp_path, monkeypatch):
    """Audit hardening: the migration loop must NOT swallow arbitrary
    OperationalErrors. A genuine failure (locked DB, disk full, malformed
    SQL, missing target table, etc.) MUST propagate so the operator
    sees it. Only 'duplicate column name: ...' may be silently absorbed,
    because that one IS the desired post-state for additive ADD COLUMN.

    We inject a deliberately broken migration that targets a non-existent
    table; SQLite raises ``OperationalError: no such table`` which is
    NOT a duplicate-column error and therefore must propagate.
    """
    monkeypatch.setattr(
        feature_bus,
        "_AI_TURNS_ADDITIVE_MIGRATIONS",
        ["ALTER TABLE __no_such_table__ ADD COLUMN x TEXT"],
    )
    db = tmp_path / "pax-bus-noisy.db"
    with feature_bus._open_db(db) as conn:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            feature_bus._ensure_schema(conn)
