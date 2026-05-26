from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from bookmap_mcp import pax_forecast_schema as schema
from bookmap_mcp import pax_forecast_store as store


def _raw_forecast(**overrides):
    base = {
        "alias": "NQM6.CME@RITHMIC",
        "level": "OR-H",
        "thesis": "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction": "LONG",
        "horizon_sec": 300,
        "prob_success": 0.62,
        "expected_r": 0.74,
        "invalidation": "back below OR-H with ask absorption",
        "features_used": ["or_levels", "pull_stack", "tape_buckets"],
    }
    base.update(overrides)
    return base


def _open_store(tmp_path: Path) -> store.PaxForecastStore:
    return store.PaxForecastStore(tmp_path / "pax-forecasts.db")


def test_empty_store_iter_returns_nothing(tmp_path):
    s = _open_store(tmp_path)
    assert list(s.iter_forecasts()) == []
    assert s.count() == 0
    s.close()


def test_record_round_trips_all_fields(tmp_path):
    s = _open_store(tmp_path)
    raw = _raw_forecast()

    out = s.record(raw, ts_ms=1_765_000_000_000, source_turn_id=42)

    rows = list(s.iter_forecasts())
    assert len(rows) == 1
    row = rows[0]

    assert row["forecast_id"] == out["forecast_id"]
    assert row["schema_version"] == schema.SCHEMA_VERSION
    assert row["ts_ms"] == 1_765_000_000_000
    assert row["source_turn_id"] == 42
    assert row["alias"] == "NQM6.CME@RITHMIC"
    assert row["level"] == "OR-H"
    assert row["thesis"] == "ACCEPTANCE_LONG"
    assert row["execution_read"] == "PAY_FOR_TRADE"
    assert row["direction"] == "LONG"
    assert row["horizon_sec"] == 300
    assert row["prob_success"] == pytest.approx(0.62)
    assert row["expected_r"] == pytest.approx(0.74)
    assert row["invalidation"] == "back below OR-H with ask absorption"
    assert row["features_used"] == ["or_levels", "pull_stack", "tape_buckets"]
    assert row["setup_bucket"] == "OR-H|ACCEPTANCE_LONG|PAY_FOR_TRADE|LONG|300"
    assert row["probability_bucket"] == "0.60-0.65"
    assert isinstance(row["ingested_ms"], int) and row["ingested_ms"] > 0
    s.close()


def test_record_is_idempotent(tmp_path):
    s = _open_store(tmp_path)
    raw = _raw_forecast()

    out1 = s.record(raw, ts_ms=1_765_000_000_000, source_turn_id=42)
    out2 = s.record(raw, ts_ms=1_765_000_000_000, source_turn_id=42)

    assert out1["forecast_id"] == out2["forecast_id"]
    assert s.count() == 1
    s.close()


def test_record_duplicate_does_not_rewrite_existing_row(tmp_path, monkeypatch):
    s = _open_store(tmp_path)
    raw = _raw_forecast()

    monkeypatch.setattr(store.time, "time", lambda: 1000.0)
    s.record(raw, ts_ms=1_765_000_000_000, source_turn_id=42)
    first = list(s.iter_forecasts())[0]["ingested_ms"]

    monkeypatch.setattr(store.time, "time", lambda: 2000.0)
    s.record(raw, ts_ms=1_765_000_000_000, source_turn_id=42)
    second = list(s.iter_forecasts())[0]["ingested_ms"]

    assert first == 1_000_000
    assert second == first
    assert s.count() == 1
    s.close()


def test_iter_filters_by_time_window(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1_000, source_turn_id=1)
    s.record(_raw_forecast(prob_success=0.65), ts_ms=2_000, source_turn_id=2)
    s.record(_raw_forecast(prob_success=0.75), ts_ms=3_000, source_turn_id=3)

    rows = list(s.iter_forecasts(start_ms=1_500, end_ms=2_500))
    assert [r["source_turn_id"] for r in rows] == [2]
    s.close()


def test_iter_filters_half_open_end_excluded(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1_000, source_turn_id=1)
    s.record(_raw_forecast(prob_success=0.65), ts_ms=2_000, source_turn_id=2)

    rows = list(s.iter_forecasts(start_ms=1_000, end_ms=2_000))
    assert [r["source_turn_id"] for r in rows] == [1]
    s.close()


def test_iter_filters_by_alias(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(alias="NQM6.CME@RITHMIC"), ts_ms=1_000, source_turn_id=1)
    s.record(_raw_forecast(alias="ESM6.CME@RITHMIC", prob_success=0.55),
             ts_ms=2_000, source_turn_id=2)

    rows = list(s.iter_forecasts(alias="ESM6.CME@RITHMIC"))
    assert [r["source_turn_id"] for r in rows] == [2]
    s.close()


def test_iter_returns_ts_ordered(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(prob_success=0.75), ts_ms=3_000, source_turn_id=3)
    s.record(_raw_forecast(), ts_ms=1_000, source_turn_id=1)
    s.record(_raw_forecast(prob_success=0.65), ts_ms=2_000, source_turn_id=2)

    rows = list(s.iter_forecasts())
    assert [r["ts_ms"] for r in rows] == [1_000, 2_000, 3_000]
    s.close()


def test_record_requires_ts_ms(tmp_path):
    s = _open_store(tmp_path)
    with pytest.raises(ValueError, match="ts_ms"):
        s.record(_raw_forecast(), source_turn_id=1)
    s.close()


def test_record_propagates_schema_validation_errors(tmp_path):
    s = _open_store(tmp_path)
    bad = _raw_forecast()
    bad["prob_success"] = 1.5
    with pytest.raises(ValueError, match="prob_success"):
        s.record(bad, ts_ms=1, source_turn_id=1)
    s.close()


def test_record_validated_accepts_already_validated_dict(tmp_path):
    s = _open_store(tmp_path)
    validated = schema.validate_forecast(_raw_forecast(),
                                         ts_ms=1_765_000_000_000,
                                         source_turn_id=42)

    out = s.record_validated(validated)
    assert out["forecast_id"] == validated["forecast_id"]
    assert s.count() == 1
    s.close()


def test_db_is_persistent_across_handles(tmp_path):
    path = tmp_path / "pax-forecasts.db"
    a = store.PaxForecastStore(path)
    a.record(_raw_forecast(), ts_ms=1_000, source_turn_id=1)
    a.close()

    b = store.PaxForecastStore(path)
    rows = list(b.iter_forecasts())
    assert len(rows) == 1
    assert rows[0]["source_turn_id"] == 1
    b.close()


def test_features_used_stored_as_json(tmp_path):
    path = tmp_path / "pax-forecasts.db"
    s = store.PaxForecastStore(path)
    s.record(_raw_forecast(features_used=["a", "b", "c"]),
             ts_ms=1, source_turn_id=1)
    s.close()

    raw_conn = sqlite3.connect(path)
    try:
        cur = raw_conn.execute("SELECT features_used FROM forecasts")
        row = cur.fetchone()
        assert row is not None
        assert json.loads(row[0]) == ["a", "b", "c"]
    finally:
        raw_conn.close()


def test_context_manager_closes(tmp_path):
    path = tmp_path / "pax-forecasts.db"
    with store.PaxForecastStore(path) as s:
        s.record(_raw_forecast(), ts_ms=1, source_turn_id=1)
        assert s.count() == 1
    # Reopen after exit - should still be usable.
    with store.PaxForecastStore(path) as s2:
        assert s2.count() == 1


# ---------------------------------------------------------------------------
# Linkage metadata (Phase 3): chat_run_id / digest_sha256 / snapshot_sha256
# ---------------------------------------------------------------------------

def test_linkage_metadata_round_trips(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1, source_turn_id=None,
             chat_run_id="run-abc", digest_sha256="d" * 64,
             snapshot_sha256="s" * 64)
    rows = list(s.iter_forecasts())
    assert len(rows) == 1
    r = rows[0]
    assert r["chat_run_id"] == "run-abc"
    assert r["digest_sha256"] == "d" * 64
    assert r["snapshot_sha256"] == "s" * 64
    assert r["source_turn_id"] is None
    s.close()


def test_iter_forecasts_filter_by_chat_run_id(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1, source_turn_id=1, chat_run_id="run-A")
    s.record(_raw_forecast(prob_success=0.65), ts_ms=2, source_turn_id=2,
             chat_run_id="run-B")
    rows = list(s.iter_forecasts(chat_run_id="run-B"))
    assert [r["source_turn_id"] for r in rows] == [2]
    s.close()


def test_iter_forecasts_filter_by_digest_sha256(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1, source_turn_id=1, digest_sha256="aaaa")
    s.record(_raw_forecast(prob_success=0.65), ts_ms=2, source_turn_id=2,
             digest_sha256="bbbb")
    rows = list(s.iter_forecasts(digest_sha256="bbbb"))
    assert [r["source_turn_id"] for r in rows] == [2]
    s.close()


def test_linkage_metadata_defaults_to_null(tmp_path):
    s = _open_store(tmp_path)
    s.record(_raw_forecast(), ts_ms=1, source_turn_id=1)
    row = next(iter(s.iter_forecasts()))
    assert row["chat_run_id"] is None
    assert row["digest_sha256"] is None
    assert row["snapshot_sha256"] is None
    s.close()


def test_record_validated_kwargs_override_dict(tmp_path):
    """Explicit kwargs win over linkage values already on the dict."""
    s = _open_store(tmp_path)
    validated = schema.validate_forecast(_raw_forecast(), ts_ms=1,
                                         source_turn_id=1)
    validated["chat_run_id"] = "dict-value"
    s.record_validated(validated, chat_run_id="kwarg-wins")
    row = next(iter(s.iter_forecasts()))
    assert row["chat_run_id"] == "kwarg-wins"
    s.close()
