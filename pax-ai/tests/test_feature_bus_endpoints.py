"""Phase-2 endpoint contract tests.

These exercise the handler functions directly (status, body) - the same
pattern used by tests/test_server_helpers.py for the existing Pax AI
endpoints. HTTP routing is intentionally not exercised here; the route
table is small and inspected by static review."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus
from pax_ai.server import (
    _api_pax_bus_status,
    _api_pax_bus_recent,
    _api_pax_bus_summary,
)


def _enable(tmp_path, monkeypatch):
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
    })
    return db_path


def _disable(tmp_path, monkeypatch):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled": False, "db_path": str(tmp_path / "nope.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "capture_ms": 1000,
            "retention_days": 30,
        },
    })


# -- /api/pax/bus/status ---------------------------------------------------

def test_status_endpoint_returns_200_and_status_block(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_status()
    assert status == 200
    for key in ("enabled", "healthy", "running", "lastWriteMs",
                "lastWriteAgeMs", "queueDepth", "rowsToday",
                "blobWritesToday", "lastError", "dbPath"):
        assert key in body


def test_status_endpoint_disabled_dbpath_is_null(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_status()
    assert status == 200
    assert body["enabled"] is False
    assert body["dbPath"] is None
    assert body["lastWriteAgeMs"] is None


def test_status_endpoint_lastWriteAgeMs_null_when_no_writes(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    feature_bus._LAST_WRITE_MS = 0
    status, body = _api_pax_bus_status()
    assert body["lastWriteAgeMs"] is None


def test_status_endpoint_lastWriteAgeMs_is_now_minus_last(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    import time
    feature_bus._LAST_WRITE_MS = int(time.time() * 1000) - 1500
    status, body = _api_pax_bus_status()
    assert 1400 < body["lastWriteAgeMs"] < 1700


# -- /api/pax/bus/recent ---------------------------------------------------

def _seed_level(db_path, n=3, alias="NQM6"):
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(n):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (1_000_000_000_000 + i, alias))


def test_recent_endpoint_disabled_returns_quiet_empty(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is False
    assert body["table"] == "level_events"
    assert body["rows"] == []


def test_recent_endpoint_unknown_table_returns_400_with_allowed(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "sqlite_master"})
    assert status == 400
    assert "unknown table" in body["error"]
    assert set(body["allowed"]) == {
        "level_events", "trigger_events",
        "microstructure_events", "ai_turns",
    }


def test_recent_endpoint_rejects_snapshot_features(tmp_path, monkeypatch):
    """snapshot_features is summary-only in Phase 2 - explicit rejection."""
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "snapshot_features"})
    assert status == 400
    assert "snapshot_features" in body["error"]
    assert "snapshot_features" not in body["allowed"]


def test_recent_endpoint_missing_table_param_returns_400(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({})
    assert status == 400
    assert "table" in body["error"].lower()


def test_recent_endpoint_db_missing_returns_warning(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is True
    assert body["rows"] == []
    assert body.get("warning") == "db not yet created"


def test_recent_endpoint_default_limit_is_10(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=25)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert len(body["rows"]) == 10


def test_recent_endpoint_clamps_limit_to_50(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=120)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "9999"})
    assert len(body["rows"]) == 50


def test_recent_endpoint_clamps_limit_to_1(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=5)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "0"})
    assert len(body["rows"]) == 1
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "-99"})
    assert len(body["rows"]) == 1


def test_recent_endpoint_garbage_limit_falls_back_to_default(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=25)
    status, body = _api_pax_bus_recent({"table": "level_events", "limit": "abc"})
    assert len(body["rows"]) == 10


def test_recent_endpoint_alias_filter(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=5, alias="NQM6")
    _seed_level(db, n=3, alias="ESM6")
    status, body = _api_pax_bus_recent({"table": "level_events",
                                          "limit": "50", "alias": "ESM6"})
    assert all(r["alias"] == "ESM6" for r in body["rows"])
    assert len(body["rows"]) == 3


def test_recent_endpoint_ai_turns_omits_blob_text_columns(tmp_path, monkeypatch):
    """Regression: even if a future schema migration adds digest_text / snapshot_json
    / pax_text columns, the projection allowlist keeps them out of the API."""
    db = _enable(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_sha256, digest_sha256, aborted)
            VALUES (1, 1, 'r1', 0, 'claude-haiku-4-5',
                    'ping', 'ping', 'LONG PROSE',
                    ?, ?, 0)
        """, ('s' * 64, 'd' * 64))
    status, body = _api_pax_bus_recent({"table": "ai_turns", "limit": "1"})
    assert status == 200
    [row] = body["rows"]
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row
    assert row["digest_sha256"] == "d" * 64


def test_recent_endpoint_response_shape_when_rows_present(tmp_path, monkeypatch):
    db = _enable(tmp_path, monkeypatch)
    _seed_level(db, n=2)
    status, body = _api_pax_bus_recent({"table": "level_events"})
    assert status == 200
    assert body["enabled"] is True
    assert body["table"] == "level_events"
    assert "rows" in body
    assert "warning" not in body


# -- /api/pax/bus/summary --------------------------------------------------

def test_summary_endpoint_disabled_returns_quiet(tmp_path, monkeypatch):
    _disable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({})
    assert status == 200
    assert body["enabled"] is False
    assert body["counts"] == {}
    assert body["topAlias"]    is None
    assert body["lastEventMs"] is None
    assert body["lastEventAgeMs"] is None


def test_summary_endpoint_default_date_is_utc_today(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    status, body = _api_pax_bus_summary({})
    assert body["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_summary_endpoint_explicit_date_param_passes_through(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({"date": "2026-01-15"})
    assert body["date"] == "2026-01-15"


def test_summary_endpoint_garbage_date_falls_back_to_utc_today(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    status, body = _api_pax_bus_summary({"date": "not-a-date"})
    assert body["date"] == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_summary_endpoint_db_missing_returns_zero_counts_warning(tmp_path, monkeypatch):
    _enable(tmp_path, monkeypatch)
    status, body = _api_pax_bus_summary({})
    assert status == 200
    assert body["enabled"] is True
    assert body["counts"]["level_events"] == 0
    assert body.get("warning") == "db not yet created"
