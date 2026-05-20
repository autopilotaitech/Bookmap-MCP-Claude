"""Phase 4A outcomes daemon tests.

Contract under test:
  - daemon disabled (outcomes.enabled=False) -> no thread, no writes
  - daemon enabled -> labels ai_turns rows older than 15 minutes
  - missing snapshot_features rows -> NULL mids (no crash)
  - start/stop idempotent
  - never raises into the parent process
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest


def _enable_outcomes(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": True, "db_path": str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 20, "capture_ms": 20,
            "retention_days": 30,
        },
        "outcomes": {
            "enabled": True,
            "wake_interval_ms": 50,           # fast for tests
            "match_tolerance_ms": 5_000,
        },
    })
    return db_path


def _disable_outcomes(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": True, "db_path": str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 30,
        },
        "outcomes": {"enabled": False, "wake_interval_ms": 50, "match_tolerance_ms": 5_000},
    })
    return db_path


def _seed_schema_and_rows(db_path, ai_ts_ms, alias="NQM6",
                            snapshot_offsets_s=(0, 60, 180, 300, 900),
                            snapshot_mids=(100.0, 101.0, 102.5, 100.5, 99.0)):
    """Create the bus schema, insert one ai_turn at ai_ts_ms, and one
    snapshot_features row at each (ai_ts_ms + offset_s * 1000)."""
    from pax_ai import feature_bus
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'enter long ping', 'enter long ping', 'OMITTED',
                    ?, ?, ?, 0)
        """, (ai_ts_ms, alias, 's' * 64, 'd' * 64))
        for offset_s, mid in zip(snapshot_offsets_s, snapshot_mids):
            conn.execute("""
                INSERT INTO snapshot_features
                  (schema_version, ts_ms, alias, health, mid)
                VALUES (1, ?, ?, 'ok', ?)
            """, (ai_ts_ms + offset_s * 1000, alias, mid))


def test_outcomes_disabled_no_thread_no_writes(tmp_path, monkeypatch):
    """outcomes.enabled=False -> start() spawns no thread, no rows written."""
    from pax_ai import outcomes
    db = _disable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    time.sleep(0.1)
    s = outcomes.status()
    assert s["enabled"] is False
    assert s["running"] is False
    # No DB created and no trade_outcomes rows.
    assert not db.exists()
    outcomes.stop()


def test_outcomes_start_is_idempotent(tmp_path, monkeypatch):
    from pax_ai import outcomes
    _enable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    outcomes.start()  # second call must be a no-op
    s = outcomes.status()
    assert s["running"] is True
    outcomes.stop()


def test_outcomes_never_raises_into_start(tmp_path, monkeypatch):
    """A startup failure (e.g. bad DB path) must NEVER raise."""
    from pax_ai import config as cfg_mod, outcomes
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": "Z:/does/not/exist/bus.db",
            "snapshot_blob_dir": "Z:/x", "digest_blob_dir": "Z:/y",
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 30},
        "outcomes": {"enabled": True, "wake_interval_ms": 50, "match_tolerance_ms": 5_000},
    })
    outcomes.start()                                   # MUST NOT raise
    time.sleep(0.1)
    outcomes.stop()


def test_outcomes_labels_existing_ai_turn(tmp_path, monkeypatch):
    """Daemon enabled + an ai_turn older than 15 min + matching snapshot_features rows
    -> a trade_outcomes row appears."""
    from pax_ai import outcomes
    db = _enable_outcomes(tmp_path, monkeypatch)
    # ai_turn ts well in the past (e.g. 1 day ago) so the "older than 15 min" gate fires.
    ai_ts = int(time.time() * 1000) - 86_400_000  # 1 day ago
    _seed_schema_and_rows(db, ai_ts_ms=ai_ts)

    outcomes.start()
    # Wait up to 2 seconds for the daemon to label.
    for _ in range(100):
        with sqlite3.connect(db) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    outcomes.stop()
    assert n == 1

    # Verify mids match.
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM trade_outcomes LIMIT 1").fetchone()
    assert row is not None
    assert row["alias"] == "NQM6"
    assert row["mid_at_t0"]    == 100.0
    assert row["mid_at_t60s"]  == 101.0
    assert row["mid_at_t180s"] == 102.5
    assert row["mid_at_t300s"] == 100.5
    assert row["mid_at_t900s"] == 99.0
    assert row["verdict"] == "ENTER_LONG"


def test_outcomes_missing_snapshot_features_null_mids(tmp_path, monkeypatch):
    """When no snapshot_features rows exist within +/-tolerance of the target,
    the corresponding mid column is NULL. No crash."""
    from pax_ai import feature_bus, outcomes
    db = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_alias, snapshot_sha256, digest_sha256, aborted)
            VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                    'info request', 'info request', 'OMITTED',
                    'NQM6', ?, ?, 0)
        """, (ai_ts, 's' * 64, 'd' * 64))
        # Deliberately no snapshot_features rows in the matching window.

    outcomes.start()
    for _ in range(100):
        with sqlite3.connect(db) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    outcomes.stop()
    assert n == 1
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM trade_outcomes LIMIT 1").fetchone()
    assert row["mid_at_t0"]    is None
    assert row["mid_at_t60s"]  is None
    assert row["mid_at_t180s"] is None
    assert row["mid_at_t300s"] is None
    assert row["mid_at_t900s"] is None
    assert row["verdict"] == "INFO"


def test_outcomes_does_not_relabel_already_labeled_rows(tmp_path, monkeypatch):
    """A second daemon pass must NOT re-insert the same trade_outcomes row."""
    from pax_ai import outcomes
    db = _enable_outcomes(tmp_path, monkeypatch)
    ai_ts = int(time.time() * 1000) - 86_400_000
    _seed_schema_and_rows(db, ai_ts_ms=ai_ts)

    outcomes.start()
    # Wait for first label.
    for _ in range(100):
        with sqlite3.connect(db) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    # Allow several wake_interval cycles to ensure no duplicate.
    time.sleep(0.3)
    with sqlite3.connect(db) as conn:
        n2 = conn.execute("SELECT COUNT(*) FROM trade_outcomes").fetchone()[0]
    outcomes.stop()
    assert n  == 1
    assert n2 == 1


def test_outcomes_status_shape(tmp_path, monkeypatch):
    from pax_ai import outcomes
    _enable_outcomes(tmp_path, monkeypatch)
    outcomes.start()
    s = outcomes.status()
    for key in ("enabled", "running", "healthy", "labeled_today",
                "last_run_ms", "last_error"):
        assert key in s
    outcomes.stop()
