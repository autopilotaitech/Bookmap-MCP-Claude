"""Phase-2 read-only helpers: recent_events + summary_today.

Tests here cover the helper layer only - the endpoint wrappers and
their HTTP-shape assertions live in test_feature_bus_endpoints.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pax_ai import feature_bus


def _enable_bus_at(tmp_path, monkeypatch):
    """Configure the bus pointing at a tmp_path DB. Caller must populate
    rows via sqlite3 directly; this fixture does NOT start the writer."""
    db_path  = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir  = tmp_path / "digests"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    return db_path


def _disable_bus(tmp_path, monkeypatch):
    db_path = tmp_path / "must-not-be-used.db"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir":   str(tmp_path / "d"),
            "queue_max":         2000,
            "writer_idle_ms":    100,
            "capture_ms":        1000,
            "retention_days":    30,
        },
    })
    return db_path


def _seed_level_events(db_path: Path, rows: int):
    """Create the bus schema in db_path and insert N level_events rows."""
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(rows):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 23475.0,
                        'WAIT', 'FOLLOW_LONG', 0.3, 0.7,
                        0, 1, 'composite_flip')
            """, (1_000_000_000_000 + i, 'NQM6' if i % 2 == 0 else 'ESM6'))


# -- recent_events contract --------------------------------------------------

def test_recent_events_disabled_returns_empty(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    assert feature_bus.recent_events("level_events") == []


def test_recent_events_db_missing_returns_empty(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    # DB file deliberately not created.
    assert feature_bus.recent_events("level_events") == []


def test_recent_events_rejects_snapshot_features_table(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        feature_bus.recent_events("snapshot_features")


def test_recent_events_rejects_arbitrary_table(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        feature_bus.recent_events("sqlite_master")


def test_recent_events_returns_newest_first(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=5)
    rows = feature_bus.recent_events("level_events", limit=5)
    assert [r["ts_ms"] for r in rows] == sorted([r["ts_ms"] for r in rows], reverse=True)


def test_recent_events_clamps_limit_to_1(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=5)
    assert len(feature_bus.recent_events("level_events", limit=0)) == 1
    assert len(feature_bus.recent_events("level_events", limit=-99)) == 1


def test_recent_events_clamps_limit_to_50(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=120)
    assert len(feature_bus.recent_events("level_events", limit=9999)) == 50


def test_recent_events_alias_filter(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=10)
    nqm = feature_bus.recent_events("level_events", limit=50, alias="NQM6")
    esm = feature_bus.recent_events("level_events", limit=50, alias="ESM6")
    assert all(r["alias"] == "NQM6" for r in nqm)
    assert all(r["alias"] == "ESM6" for r in esm)
    assert len(nqm) + len(esm) == 10


def test_recent_events_projection_columns_only(tmp_path, monkeypatch):
    """Each returned row contains exactly the columns in _RECENT_PROJECTION,
    nothing more."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_level_events(db, rows=1)
    [row] = feature_bus.recent_events("level_events", limit=1)
    expected = set(feature_bus._RECENT_PROJECTION["level_events"])
    assert set(row.keys()) == expected


def test_recent_events_ai_turns_excludes_blob_text_columns(tmp_path, monkeypatch):
    """ai_turns projection MUST NOT include digest_text, snapshot_json, pax_text.
    These columns either don't exist (digest_text, snapshot_json - blob-only) or
    are large (pax_text). Projection allowlist is the guard."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO ai_turns
              (schema_version, ts_ms, chat_run_id, deep, model,
               user_text_raw, user_text_normalized, pax_text,
               snapshot_sha256, digest_sha256, aborted)
            VALUES (1, 1000, 'r1', 0, 'claude-haiku-4-5',
                    'ping', 'ping', 'POTENTIALLY LONG PROSE TEXT HERE',
                    ?, ?, 0)
        """, ('s' * 64, 'd' * 64))
    [row] = feature_bus.recent_events("ai_turns", limit=1)
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row
    assert row["digest_sha256"]   == "d" * 64
    assert row["snapshot_sha256"] == "s" * 64


# -- summary_today contract --------------------------------------------------

def _all_tables_count_zero(d):
    return {
        "snapshot_features":     0,
        "level_events":          0,
        "microstructure_events": 0,
        "trigger_events":        0,
        "ai_turns":              0,
    } == d


def test_summary_today_disabled_returns_quiet_payload(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    s = feature_bus.summary_today()
    assert s["enabled"] is False
    assert s["counts"] == {}
    assert s["topAlias"] is None
    assert s["lastEventMs"] is None
    assert s["lastEventAgeMs"] is None
    # Even disabled, the date echoes the requested (or default) UTC date.
    assert s["date"]


def test_summary_today_default_date_is_current_utc_date(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    expected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = feature_bus.summary_today()
    assert s["date"] == expected


def test_summary_today_db_missing_returns_zero_counts_with_warning(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    s = feature_bus.summary_today()
    assert s["enabled"] is True
    assert _all_tables_count_zero(s["counts"])
    assert s["warning"] == "db not yet created"


def test_summary_today_counts_rows_within_utc_day(tmp_path, monkeypatch):
    """Rows whose ts_ms falls inside the UTC day [start, next_start) count.
    Rows outside (yesterday, tomorrow) are excluded."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone, timedelta
    today = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    tomorrow  = today + timedelta(days=1)
    ts = lambda d: int(d.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for d in (yesterday, today, today, tomorrow):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (ts(d),))
    s = feature_bus.summary_today()
    assert s["counts"]["level_events"] == 2  # only the two `today` rows


def test_summary_today_topAlias_uses_most_active_alias(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    base_ts = int(today.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(7):
            conn.execute("""
                INSERT INTO level_events
                  (schema_version, ts_ms, alias, level_label, level_price,
                   prev_decision, new_decision, prev_confidence, new_confidence,
                   prev_proximity, new_proximity, trigger_reason)
                VALUES (1, ?, ?, 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                        0.3, 0.7, 0, 1, 'composite_flip')
            """, (base_ts + i, "NQM6" if i < 5 else "ESM6"))
    s = feature_bus.summary_today()
    assert s["topAlias"] == "NQM6"


def test_summary_today_lastEventMs_is_max_across_event_tables(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    base_ts = int(today.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO level_events
              (schema_version, ts_ms, alias, level_label, level_price,
               prev_decision, new_decision, prev_confidence, new_confidence,
               prev_proximity, new_proximity, trigger_reason)
            VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                    0.3, 0.7, 0, 1, 'composite_flip')
        """, (base_ts + 100,))
        conn.execute("""
            INSERT INTO trigger_events
              (schema_version, ts_ms, alias, kind, severity, snapshot_ts_ms)
            VALUES (1, ?, 'NQM6', 'TREND_SIGNAL_FIRE', 'HIGH', ?)
        """, (base_ts + 200, base_ts + 200))
    s = feature_bus.summary_today()
    assert s["lastEventMs"] == base_ts + 200
    assert s["lastEventAgeMs"] is not None
    assert s["lastEventAgeMs"] >= 0


def test_summary_today_lastEventAgeMs_clamped_to_zero_for_future_ts(
        tmp_path, monkeypatch):
    """Pins the clamp: a future-stamped event must NOT produce a negative
    age. Regression for the pre-fix flake where summary_today() returned
    lastEventAgeMs < 0 when run before the event's wall-clock time."""
    import time
    db = _enable_bus_at(tmp_path, monkeypatch)
    from datetime import datetime, timezone
    now_ms = int(time.time() * 1000)
    # Stamp the event 1 hour in the future of wall-clock now, but still
    # inside today's UTC window so summary_today() picks it up.
    future_ts = now_ms + 3_600_000
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start.replace(hour=23, minute=59, second=59,
                                     microsecond=999_000)
    if future_ts > int(today_end.timestamp() * 1000):
        # Edge case: test running in the last hour of the UTC day; clamp
        # the future stamp into the window so the test stays deterministic.
        future_ts = int(today_end.timestamp() * 1000) - 1
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO level_events
              (schema_version, ts_ms, alias, level_label, level_price,
               prev_decision, new_decision, prev_confidence, new_confidence,
               prev_proximity, new_proximity, trigger_reason)
            VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                    0.3, 0.7, 0, 1, 'composite_flip')
        """, (future_ts,))
    s = feature_bus.summary_today()
    assert s["lastEventMs"] == future_ts, "lastEventMs must be preserved exactly"
    assert s["lastEventAgeMs"] == 0, (
        f"future-stamped event must clamp to 0, got {s['lastEventAgeMs']}")


def test_summary_today_explicit_date_param(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    # Insert at a specific UTC date.
    from datetime import datetime, timezone
    d = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    ts = int(d.timestamp() * 1000)
    with feature_bus._open_db(db) as conn:
        feature_bus._ensure_schema(conn)
        conn.execute("""
            INSERT INTO level_events
              (schema_version, ts_ms, alias, level_label, level_price,
               prev_decision, new_decision, prev_confidence, new_confidence,
               prev_proximity, new_proximity, trigger_reason)
            VALUES (1, ?, 'NQM6', 'OR-H', 0, 'WAIT', 'FOLLOW_LONG',
                    0.3, 0.7, 0, 1, 'composite_flip')
        """, (ts,))
    s = feature_bus.summary_today("2026-01-15")
    assert s["date"] == "2026-01-15"
    assert s["counts"]["level_events"] == 1
    # And a different UTC day finds zero.
    s2 = feature_bus.summary_today("2026-01-16")
    assert s2["counts"]["level_events"] == 0


# -- recent_ai_turns contract ------------------------------------------------

def _seed_ai_turn(db_path, alias="NQM6", n=1):
    import hashlib
    with feature_bus._open_db(db_path) as conn:
        feature_bus._ensure_schema(conn)
        for i in range(n):
            conn.execute("""
                INSERT INTO ai_turns
                  (schema_version, ts_ms, chat_run_id, deep, model,
                   user_text_raw, user_text_normalized, pax_text,
                   snapshot_alias, snapshot_sha256, digest_sha256,
                   total_cost_usd, input_tokens, output_tokens, exit_code, aborted)
                VALUES (1, ?, 'r1', 0, 'claude-haiku-4-5',
                        ?, ?, 'LONG PROSE OMITTED',
                        ?, ?, ?, 0.01, 10, 50, 0, 0)
            """, (1_000_000_000_000 + i,
                  f"ping {i}", f"ping {i}",
                  alias,
                  hashlib.sha256(f"s{i}".encode()).hexdigest(),
                  hashlib.sha256(f"d{i}".encode()).hexdigest()))


def test_recent_ai_turns_disabled_returns_empty(tmp_path, monkeypatch):
    _disable_bus(tmp_path, monkeypatch)
    assert feature_bus.recent_ai_turns() == []


def test_recent_ai_turns_db_missing_returns_empty(tmp_path, monkeypatch):
    _enable_bus_at(tmp_path, monkeypatch)
    assert feature_bus.recent_ai_turns() == []


def test_recent_ai_turns_returns_newest_first(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=5)
    rows = feature_bus.recent_ai_turns(limit=5)
    assert len(rows) == 5
    assert [r["ts_ms"] for r in rows] == sorted([r["ts_ms"] for r in rows], reverse=True)


def test_recent_ai_turns_alias_filter(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, alias="NQM6", n=3)
    _seed_ai_turn(db, alias="ESM6", n=2)
    nqm = feature_bus.recent_ai_turns(limit=10, alias="NQM6")
    assert all(r["snapshot_alias"] == "NQM6" for r in nqm)
    assert len(nqm) == 3


def test_recent_ai_turns_clamps_limit(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=20)
    assert len(feature_bus.recent_ai_turns(limit=0)) == 1
    assert len(feature_bus.recent_ai_turns(limit=9999)) == 20  # only 20 seeded


def test_recent_ai_turns_omits_blob_text_columns(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=1)
    [row] = feature_bus.recent_ai_turns(limit=1)
    assert "pax_text"      not in row
    assert "digest_text"   not in row
    assert "snapshot_json" not in row


def test_recent_ai_turns_before_ts_ms_excludes_newer(tmp_path, monkeypatch):
    """before_ts_ms filter: only rows with ts_ms < before_ts_ms returned."""
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=5)
    # n=5 seeds rows at ts_ms = base + 0..4 (base = 1_000_000_000_000).
    base = 1_000_000_000_000
    # Boundary at base+3: rows with ts_ms < base+3 are 0,1,2.
    rows = feature_bus.recent_ai_turns(limit=10, before_ts_ms=base + 3)
    assert len(rows) == 3
    assert all(r["ts_ms"] < base + 3 for r in rows)


def test_recent_ai_turns_before_ts_ms_none_returns_all(tmp_path, monkeypatch):
    db = _enable_bus_at(tmp_path, monkeypatch)
    _seed_ai_turn(db, n=4)
    rows = feature_bus.recent_ai_turns(limit=10, before_ts_ms=None)
    assert len(rows) == 4
