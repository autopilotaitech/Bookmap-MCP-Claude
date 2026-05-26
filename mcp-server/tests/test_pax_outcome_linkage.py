"""Phase 4: outcome linkage tests for pax_calibration.bus_outcome_lookup.

The forecasts captured at chat time generally do NOT yet have a
source_turn_id (the feature_bus writer thread assigns ai_turns.id
asynchronously). The fallback lookup joins forecast linkage metadata
(chat_run_id + digest_sha256) to ai_turns to recover the turn_id, then
joins through to trade_outcomes by the strict horizon column.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from bookmap_mcp import pax_calibration as calib
from bookmap_mcp import pax_forecast_store as store


# ---------------------------------------------------------------- helpers

def _make_bus_db(path: Path,
                  ai_turns_rows,
                  trade_outcomes_rows) -> None:
    """Build a minimal bus DB with the columns the linkage code reads."""
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
        CREATE TABLE ai_turns (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ts_ms INTEGER NOT NULL,
          chat_run_id TEXT NOT NULL,
          digest_sha256 TEXT NOT NULL,
          snapshot_sha256 TEXT NOT NULL,
          snapshot_alias TEXT
        );
        CREATE TABLE trade_outcomes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          ai_turn_id INTEGER NOT NULL,
          realized_r_at_t60s REAL,
          realized_r_at_t180s REAL,
          realized_r_at_t300s REAL,
          realized_r_at_t900s REAL
        );
        """)
        for r in ai_turns_rows:
            conn.execute(
                "INSERT INTO ai_turns "
                "(id, ts_ms, chat_run_id, digest_sha256, snapshot_sha256, snapshot_alias) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (r["id"], r["ts_ms"], r["chat_run_id"], r["digest_sha256"],
                 r["snapshot_sha256"], r.get("snapshot_alias")),
            )
        for r in trade_outcomes_rows:
            conn.execute(
                "INSERT INTO trade_outcomes "
                "(ai_turn_id, realized_r_at_t60s, realized_r_at_t180s, "
                "realized_r_at_t300s, realized_r_at_t900s) "
                "VALUES (?, ?, ?, ?, ?)",
                (r["ai_turn_id"], r.get("realized_r_at_t60s"),
                 r.get("realized_r_at_t180s"),
                 r.get("realized_r_at_t300s"),
                 r.get("realized_r_at_t900s")),
            )
        conn.commit()
    finally:
        conn.close()


def _raw(**overrides):
    base = {
        "alias": "NQM6.CME@RITHMIC",
        "level": "OR-H",
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction": "LONG",
        "horizon_sec": 300,
        "prob_success": 0.62,
        "expected_r": 0.74,
        "invalidation": "back below OR-H",
        "features_used": ["or_levels"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- direct

def test_direct_source_turn_id_lookup_returns_outcome(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 42, "realized_r_at_t300s": 1.5},
                 ])
    forecast = {"source_turn_id": 42, "horizon_sec": 300}
    out = calib.bus_outcome_lookup(bus)(forecast)
    assert out is not None
    assert out["realized_r"] == 1.5
    assert out["source"] == "bus_trade_outcomes"


def test_strict_horizon_matching_preserved(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus, [], [
        {"ai_turn_id": 1, "realized_r_at_t60s": 0.4},
    ])
    # Forecast asks for 300s but only 60s exists; must return None.
    forecast = {"source_turn_id": 1, "horizon_sec": 300}
    assert calib.bus_outcome_lookup(bus)(forecast) is None


# ---------------------------------------------------------------- fallback

def test_fallback_lookup_when_source_turn_id_missing(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 7, "ts_ms": 1_000,
                     "chat_run_id": "run-A",
                     "digest_sha256": "d-hash",
                     "snapshot_sha256": "s-hash",
                 }],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 7, "realized_r_at_t300s": -0.5},
                 ])
    forecast = {
        "source_turn_id": None,
        "chat_run_id": "run-A",
        "digest_sha256": "d-hash",
        "horizon_sec": 300,
    }
    out = calib.bus_outcome_lookup(bus)(forecast)
    assert out is not None
    assert out["realized_r"] == -0.5
    assert out["source"] == "bus_trade_outcomes_via_linkage"
    assert out["linkage"]["ai_turn_id"] == 7


def test_fallback_disabled_explicit_returns_none(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 7, "ts_ms": 1_000,
                     "chat_run_id": "run-A",
                     "digest_sha256": "d-hash",
                     "snapshot_sha256": "s-hash",
                 }],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 7, "realized_r_at_t300s": 1.0},
                 ])
    forecast = {
        "source_turn_id": None,
        "chat_run_id": "run-A",
        "digest_sha256": "d-hash",
        "horizon_sec": 300,
    }
    out = calib.bus_outcome_lookup(
        bus, allow_linkage_fallback=False)(forecast)
    assert out is None


def test_fallback_without_linkage_metadata_returns_none(tmp_path):
    bus = tmp_path / "bus.db"
    _make_bus_db(bus, [], [])
    # No source_turn_id, no digest/run id -> nothing to join on.
    forecast = {
        "source_turn_id": None,
        "chat_run_id": None,
        "digest_sha256": None,
        "horizon_sec": 300,
    }
    assert calib.bus_outcome_lookup(bus)(forecast) is None


def test_ambiguous_linkage_match_returns_none(tmp_path):
    """If multiple ai_turns rows share the same (digest_sha, chat_run_id)
    we MUST NOT pick one arbitrarily. Return None instead."""
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[
                     {"id": 1, "ts_ms": 100, "chat_run_id": "run-A",
                      "digest_sha256": "dup", "snapshot_sha256": "s1"},
                     {"id": 2, "ts_ms": 200, "chat_run_id": "run-A",
                      "digest_sha256": "dup", "snapshot_sha256": "s2"},
                 ],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 1, "realized_r_at_t300s": 1.0},
                     {"ai_turn_id": 2, "realized_r_at_t300s": -1.0},
                 ])
    forecast = {
        "source_turn_id": None,
        "chat_run_id": "run-A",
        "digest_sha256": "dup",
        "horizon_sec": 300,
    }
    assert calib.bus_outcome_lookup(bus)(forecast) is None


def test_missing_outcome_row_returns_none(tmp_path):
    """ai_turn matched via linkage but no trade_outcomes row -> None."""
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 9, "ts_ms": 1, "chat_run_id": "run-A",
                     "digest_sha256": "d", "snapshot_sha256": "s",
                 }],
                 trade_outcomes_rows=[])
    forecast = {
        "source_turn_id": None,
        "chat_run_id": "run-A",
        "digest_sha256": "d",
        "horizon_sec": 300,
    }
    assert calib.bus_outcome_lookup(bus)(forecast) is None


def test_direct_lookup_falls_back_when_outcomes_row_missing(tmp_path):
    """source_turn_id set but no trade_outcomes row -> try linkage."""
    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 99, "ts_ms": 1, "chat_run_id": "run-A",
                     "digest_sha256": "dd", "snapshot_sha256": "ss",
                 }],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 99, "realized_r_at_t300s": 0.5},
                 ])
    # source_turn_id points at 999 which has NO trade_outcome row, but
    # linkage metadata matches ai_turn id 99 which does. The lookup must
    # try the fallback path.
    forecast = {
        "source_turn_id": 999,
        "chat_run_id": "run-A",
        "digest_sha256": "dd",
        "horizon_sec": 300,
    }
    out = calib.bus_outcome_lookup(bus)(forecast)
    assert out is not None
    assert out["realized_r"] == 0.5
    assert out["source"] == "bus_trade_outcomes_via_linkage"


# ---------------------------------------------------------------- audit

def test_linkage_audit_counts(tmp_path):
    db = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db)
    # F1: source_turn_id direct hit
    s.record(_raw(), ts_ms=1, source_turn_id=42)
    # F2: no source_turn_id, linkage fallback
    s.record(_raw(prob_success=0.55), ts_ms=2, source_turn_id=None,
             chat_run_id="run-A", digest_sha256="d-hash",
             snapshot_sha256="s-hash")
    # F3: no source_turn_id, no linkage -> unpaired
    s.record(_raw(prob_success=0.45), ts_ms=3, source_turn_id=None)
    s.close()

    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 7, "ts_ms": 100,
                     "chat_run_id": "run-A",
                     "digest_sha256": "d-hash",
                     "snapshot_sha256": "s-hash",
                 }],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 42, "realized_r_at_t300s": 1.0},
                     {"ai_turn_id": 7,  "realized_r_at_t300s": -0.5},
                 ])

    counts = calib.linkage_audit(db, bus)
    assert counts["n_forecasts"] == 3
    assert counts["paired_via_source_id"] == 1
    assert counts["paired_via_fallback"] == 1
    assert counts["unpaired"] == 1


def test_linkage_audit_with_missing_forecasts_db_returns_zeros(tmp_path):
    counts = calib.linkage_audit(tmp_path / "missing.db", tmp_path / "bus.db")
    assert counts["n_forecasts"] == 0
    assert counts["unpaired"] == 0


def test_calibration_for_day_uses_linkage_outcomes(tmp_path):
    db = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(db)
    base_ms = calib.utc_day_window("2026-05-25")[0]
    s.record(_raw(), ts_ms=base_ms + 1, source_turn_id=None,
             chat_run_id="run-A", digest_sha256="d-hash",
             snapshot_sha256="s-hash")
    s.close()

    bus = tmp_path / "bus.db"
    _make_bus_db(bus,
                 ai_turns_rows=[{
                     "id": 13, "ts_ms": 1,
                     "chat_run_id": "run-A",
                     "digest_sha256": "d-hash",
                     "snapshot_sha256": "s-hash",
                 }],
                 trade_outcomes_rows=[
                     {"ai_turn_id": 13, "realized_r_at_t300s": 1.0},
                 ])

    report = calib.calibration_for_day(
        forecasts_path=db,
        outcome_lookup=calib.bus_outcome_lookup(bus),
        date_utc="2026-05-25",
        min_samples=1,
    )
    assert report["global"]["n_paired"] == 1
    assert report["global"]["actual_hit_rate"] == pytest.approx(1.0)
