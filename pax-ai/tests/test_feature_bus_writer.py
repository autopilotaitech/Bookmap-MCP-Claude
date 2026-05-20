"""Writer thread + back-pressure + disabled=zero-writes invariants."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from pax_ai import feature_bus


@pytest.fixture
def bus_enabled(tmp_path, monkeypatch):
    db_path = tmp_path / "pax-bus.db"
    snap_dir = tmp_path / "snapshots"
    dig_dir = tmp_path / "digests"

    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           True,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         50,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    feature_bus._ROWS_TODAY = 0
    feature_bus._BLOB_WRITES_TODAY = 0
    yield {"db": db_path}
    feature_bus.stop()


def test_writer_disabled_means_zero_db_writes(tmp_path, monkeypatch):
    """With feature_bus.enabled=False, _enqueue is no-op; no DB ever opened."""
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(tmp_path / "must-not-exist.db"),
            "snapshot_blob_dir": str(tmp_path / "snap"),
            "digest_blob_dir":   str(tmp_path / "dig"),
            "queue_max":         2000,
            "writer_idle_ms":    100,
            "retention_days":    30,
        },
    })
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 1, "alias": "x", "level_label": "OR-H", "level_price": 0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0, "new_confidence": 1,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "test"}})
    assert not (tmp_path / "must-not-exist.db").exists()


def test_writer_drains_enqueued_events(bus_enabled):
    feature_bus.start()
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 1, "alias": "X", "level_label": "OR-H", "level_price": 100.0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0.3, "new_confidence": 0.7,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "composite_flip"}})
    # Wait up to 1s for the writer to drain.
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_back_pressure_drops_snapshots_not_events(bus_enabled, monkeypatch):
    """Fill the queue past queue_max with snapshot_features events; level_event still survives."""
    # Don't start the writer; we want the queue to fill.
    # Push 200 snapshot_features events into a queue capped at 50.
    for i in range(200):
        feature_bus._enqueue({"kind": "snapshot_features",
                               "payload": {"schema_version": 1, "ts_ms": i, "alias": "X",
                                            "health": "ok"}})
    # Now push one critical event.
    feature_bus._enqueue({"kind": "level_event", "payload": {"schema_version": 1,
        "ts_ms": 999, "alias": "X", "level_label": "OR-H", "level_price": 100.0,
        "prev_decision": "WAIT", "new_decision": "FOLLOW_LONG",
        "prev_confidence": 0.3, "new_confidence": 0.7,
        "prev_proximity": None, "new_proximity": None,
        "trigger_reason": "composite_flip"}})

    # Drain by starting the writer.
    feature_bus.start()
    for _ in range(100):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n_lvl = conn.execute("SELECT COUNT(*) FROM level_events").fetchone()[0]
                n_snap = conn.execute("SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
            except sqlite3.OperationalError:
                n_lvl, n_snap = 0, 0
        if n_lvl >= 1:
            break
        time.sleep(0.02)
    assert n_lvl == 1                                  # critical event preserved
    assert n_snap <= 50                                # snapshots were dropped


def test_writer_status_reflects_running(bus_enabled):
    feature_bus.start()
    time.sleep(0.05)
    s = feature_bus.status()
    assert s["enabled"] is True
    assert s["running"] is True


def test_writer_drains_ai_turn_record_to_blobs_and_db(bus_enabled):
    """record_ai_turn enqueues; the writer thread does the blob + DB writes.
    Moved here from Task 5 because the persistence path depends on the
    writer thread + _drain_to_db's `ai_turn` branch which lands in Task 7."""
    feature_bus.start()
    rec = feature_bus.AiTurnRecord(
        schema_version=1, ts_ms=1715000000000, chat_run_id="r1",
        deep=False, model="claude-haiku-4-5",
        router_primary="pax-or", router_secondary=None,
        user_text_raw="hi", user_text_normalized="hi",
        digest_text="DIGEST",
        digest_sha256=hashlib.sha256(b"DIGEST").hexdigest(),
        snapshot_json='{"a":1}',
        snapshot_sha256=hashlib.sha256(b'{"a":1}').hexdigest(),
        snapshot_alias="NQM6", snapshot_ts_ms=1715000000000, snapshot_age_ms=0,
        pax_text="ok",
        exit_code=0, elapsed_ms=5, api_duration_ms=4,
        total_cost_usd=0.001, input_tokens=100, output_tokens=1,
        cache_creation_tokens=0, cache_read_tokens=0,
        aborted=False, error=None,
    )
    feature_bus.record_ai_turn(rec)             # returns immediately (enqueue only)
    rows: list = []
    for _ in range(100):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                rows = conn.execute(
                    "SELECT digest_sha256, snapshot_sha256 FROM ai_turns"
                ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if rows:
            break
        time.sleep(0.02)
    assert len(rows) == 1
    assert rows[0][0] == rec.digest_sha256
    assert rows[0][1] == rec.snapshot_sha256

    snap_root = bus_enabled["db"].parent / "snapshots"
    dig_root  = bus_enabled["db"].parent / "digests"
    digest_blob = dig_root  / "2024-05-06" / f"{rec.digest_sha256}.txt"
    snap_blob   = snap_root / "2024-05-06" / f"{rec.snapshot_sha256}.json"
    assert digest_blob.read_text(encoding="utf-8") == "DIGEST"
    assert snap_blob.read_text(encoding="utf-8") == '{"a":1}'


def test_writer_thread_captures_snapshot_features_automatically(bus_enabled, monkeypatch):
    """With the writer running and poller.latest() monkeypatched to return a
    snapshot, snapshot_features rows must appear in pax-bus.db without any
    manual _enqueue() call from the test."""
    from pax_ai import poller
    snap = {"alias": "NQM6", "health": "ok",
             "book": {"mid": 23450.5, "spread": 0.25, "bestBid": 23450.25, "bestAsk": 23450.5},
             "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                            "levels": [], "middleLock": False, "inProximity": False},
             "flow": {"regime": "BALANCED", "regimeConfidence": 0.5,
                       "biasScore": 0.0, "biasTrajectory": "FLAT"},
             "conviction": {"score": 0.0, "trend": "NONE", "anchorMode": "LIVE"},
             "trend_signal": {"kind": "NONE", "eligible": False},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": False, "label": "clear"}},
             "micro_events": {"events": []}}
    monkeypatch.setattr(poller, "latest",
                          lambda: (snap, 1000, 0, 0, None))
    feature_bus.start()
    # Wait up to ~1 s for at least one snapshot_features row to land.
    n_snap = 0
    for _ in range(50):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                n_snap = conn.execute(
                    "SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
        except sqlite3.OperationalError:
            n_snap = 0
        if n_snap >= 1:
            break
        time.sleep(0.02)
    assert n_snap >= 1, "writer thread did not capture any snapshot_features row"


def test_writer_thread_captures_level_event_delta_automatically(bus_enabled, monkeypatch):
    """Writer must drive _detect_snapshot_deltas with snap pair (prev, new)
    and persist a level_event row when a level decision flips."""
    from pax_ai import poller
    snap_a = {"alias": "NQM6", "health": "ok",
               "or_levels": {"levels": [{"label": "OR-H", "price": 23475.0,
                                            "decision": "WAIT", "confidence": 0.3}],
                              "middleLock": False, "inProximity": False},
               "micro_events": {"events": []},
               "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                          "news": {"blocked": False, "label": "clear"}}}
    snap_b = {**snap_a,
                "or_levels": {"levels": [{"label": "OR-H", "price": 23475.0,
                                            "decision": "FOLLOW_LONG", "confidence": 0.7}],
                                "middleLock": False, "inProximity": False}}
    # Yield snap_a on first call, snap_b on every subsequent call.
    state = {"i": 0}
    def fake_latest():
        i = state["i"]
        state["i"] += 1
        return (snap_a if i == 0 else snap_b, 1000 + i, 0, 0, None)
    monkeypatch.setattr(poller, "latest", fake_latest)
    feature_bus.start()
    n_lvl = 0
    for _ in range(80):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                n_lvl = conn.execute(
                    "SELECT COUNT(*) FROM level_events").fetchone()[0]
        except sqlite3.OperationalError:
            n_lvl = 0
        if n_lvl >= 1:
            break
        time.sleep(0.02)
    assert n_lvl >= 1, "writer thread did not capture level_events delta"


def test_writer_thread_persists_full_snapshot_feature_columns(bus_enabled, monkeypatch):
    """The expanded _insert_snapshot_features writes every projected column.
    Verify mid, spread, OR high/low, flow_regime, conviction_score, news_blocked
    all land in the DB (Phase 1's stated useful-passive-capture goal)."""
    from pax_ai import poller
    snap = {"alias": "NQM6", "health": "ok",
             "book": {"mid": 23450.5, "spread": 0.25, "bestBid": 23450.25, "bestAsk": 23450.5},
             "or_levels": {"orHigh": 23475.0, "orLow": 23440.0, "orWidthPts": 35.0,
                            "levels": [], "middleLock": False, "inProximity": False},
             "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.72,
                       "biasScore": 0.4, "biasTrajectory": "RISING"},
             "conviction": {"score": 0.55, "trend": "BULL", "trajectory": "RISING",
                             "anchorMode": "LIVE"},
             "trend_signal": {"kind": "STRONG_BULL", "renderableKind": "STRONG_BULL",
                                "eligible": True},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": True, "label": "FOMC"}},
             "micro_events": {"events": []}}
    monkeypatch.setattr(poller, "latest", lambda: (snap, 1000, 0, 0, None))
    feature_bus.start()
    row = None
    for _ in range(80):
        try:
            with sqlite3.connect(bus_enabled["db"]) as conn:
                conn.row_factory = sqlite3.Row
                rs = conn.execute(
                    "SELECT * FROM snapshot_features ORDER BY id DESC LIMIT 1"
                ).fetchall()
            if rs:
                row = rs[0]
                break
        except sqlite3.OperationalError:
            pass
        time.sleep(0.02)
    assert row is not None, "no snapshot_features row landed"
    assert row["alias"]              == "NQM6"
    assert row["mid"]                == 23450.5
    assert row["spread"]             == 0.25
    assert row["or_high"]            == 23475.0
    assert row["or_low"]             == 23440.0
    assert row["or_width_pts"]       == 35.0
    assert row["flow_regime"]        == "TRENDING_UP"
    assert row["flow_regime_conf"]   == 0.72
    assert row["conviction_score"]   == 0.55
    assert row["conviction_trend"]   == "BULL"
    assert row["trend_renderable_kind"] == "STRONG_BULL"
    assert row["news_blocked"]       == 1
    assert row["news_label"]         == "FOMC"
    assert row["session_anchor_mode"] == "LIVE"


def test_writer_thread_tolerates_none_snapshot(bus_enabled, monkeypatch):
    """On cold start poller.latest() returns (None, 0, -1, 0, None).
    The writer must NOT raise; the DB simply gets no snapshot_features
    rows until a real snap arrives."""
    from pax_ai import poller
    monkeypatch.setattr(poller, "latest", lambda: (None, 0, -1, 0, None))
    feature_bus.start()
    time.sleep(0.15)            # let writer tick several times
    s = feature_bus.status()
    assert s["running"] is True
    assert s["healthy"] is True
    with sqlite3.connect(bus_enabled["db"]) as conn:
        try:
            n = conn.execute("SELECT COUNT(*) FROM snapshot_features").fetchone()[0]
        except sqlite3.OperationalError:
            n = 0
    assert n == 0


def test_concurrent_writer_lock_refuses_second_start(bus_enabled):
    """Lock contention prevents start(). Uses feature_bus._try_acquire_lock
    as the portable abstraction; no direct msvcrt / fcntl imports in this
    test, so it runs on both Windows and POSIX CI."""
    db = bus_enabled["db"]
    # Step 1: acquire the lock via the module's own portable primitive
    # (simulates another process holding it).
    assert feature_bus._try_acquire_lock(db) is True, "first acquire must succeed"
    held_handle = feature_bus._LOCK_FILE_HANDLE
    # Step 2: forget the module's handle reference so start() will try a
    # FRESH acquire that contends with the held OS-level lock.
    feature_bus._LOCK_FILE_HANDLE = None
    try:
        feature_bus.start()
        time.sleep(0.05)
        s = feature_bus.status()
        assert s["running"] is False
        assert s["healthy"] is False
        assert "lock" in (s["lastError"] or "").lower()
    finally:
        # Re-attach the held handle and use the module's release path so
        # we never leak file handles even on assertion failure.
        feature_bus._LOCK_FILE_HANDLE = held_handle
        feature_bus._release_lock()
