"""Direct tests for chat._capture_pax_forecast.

These do NOT exercise the SSE stream end-to-end (that lives in
test_chat_handler.py and requires the heavyweight subprocess + journal
isolation fixture). They drive the post-response hook in isolation so
the forecast capture contract is pinned without paying the SSE setup
cost.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from pax_ai import chat, forecast_store_writer


# -------------------------------------------------------------- fixtures

def _snap() -> Dict[str, Any]:
    return {
        "health": "ok",
        "alias":  "NQM6.CME@RITHMIC",
        "or_levels": {
            "levels": [
                {"label": "OR-H", "price": 20100.0, "distance": 1.0},
                {"label": "OR-L", "price": 20080.0, "distance": -19.0},
            ],
        },
    }


def _pax_text_with_forecast() -> str:
    block = {
        "alias":          "NQM6.CME@RITHMIC",
        "level":          "OR-H",
        "thesis":         "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction":      "LONG",
        "horizon_sec":    300,
        "prob_success":   0.62,
        "expected_r":     0.74,
        "invalidation":   "back below OR-H with absorption",
        "features_used": ["or_levels", "pull_stack"],
    }
    return (
        "Pax prose read goes here.\n"
        "<<PAX_FORECAST>>\n" + json.dumps(block) + "\n<<END_FORECAST>>\n"
    )


def _pax_text_with_bad_combo() -> str:
    block = {
        "alias":          "NQM6.CME@RITHMIC",
        "level":          "OR-H",
        "thesis":         "ACCEPTANCE_LONG",
        "execution_read": "WAIT_FOR_CONFIRM",
        "direction":      "LONG",   # invalid: WAIT must be NONE
        "horizon_sec":    300,
        "prob_success":   0.62,
        "expected_r":     0.74,
        "invalidation":   "x",
        "features_used":  ["or_levels"],
    }
    return f"prose\n<<PAX_FORECAST>>\n{json.dumps(block)}\n<<END_FORECAST>>\n"


@pytest.fixture
def isolated_writer(tmp_path, monkeypatch):
    """Point the writer at a tmp DB and let tests toggle the enabled flag."""
    real_get = forecast_store_writer.config.get
    overrides: Dict[str, Any] = {
        "forecast.enabled": False,
        "forecast.store_path": str(tmp_path / "pax-forecast.db"),
    }

    def patched_get(path: str, default: Any = None) -> Any:
        if path in overrides:
            return overrides[path]
        return real_get(path, default)

    monkeypatch.setattr(forecast_store_writer.config, "get", patched_get)
    return overrides


# -------------------------------------------------------------- behavior

def test_no_op_when_disabled(isolated_writer, tmp_path):
    chat._capture_pax_forecast(
        _pax_text_with_forecast(), _snap(),
        ts_ms=1_765_000_000_000,
        chat_run_id="run-1",
        digest_sha256="d",
        snapshot_sha256="s",
    )
    assert not (tmp_path / "pax-forecast.db").exists()


def test_persists_valid_forecast_when_enabled(isolated_writer, tmp_path):
    isolated_writer["forecast.enabled"] = True
    chat._capture_pax_forecast(
        _pax_text_with_forecast(), _snap(),
        ts_ms=1_765_000_000_000,
        chat_run_id="run-A",
        digest_sha256="d" * 64,
        snapshot_sha256="s" * 64,
    )
    db = tmp_path / "pax-forecast.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "SELECT alias, level, chat_run_id, digest_sha256, snapshot_sha256, "
            "       source_turn_id FROM forecasts")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "NQM6.CME@RITHMIC"
        assert row[1] == "OR-H"
        assert row[2] == "run-A"
        assert row[3] == "d" * 64
        assert row[4] == "s" * 64
        # Chat path does not have ai_turns.id yet; source_turn_id is NULL.
        assert row[5] is None
    finally:
        conn.close()


def test_skips_invalid_forecast(isolated_writer, tmp_path):
    isolated_writer["forecast.enabled"] = True
    chat._capture_pax_forecast(
        _pax_text_with_bad_combo(), _snap(),
        ts_ms=1, chat_run_id="r", digest_sha256="d", snapshot_sha256="s",
    )
    db = tmp_path / "pax-forecast.db"
    # The DB will be opened by the store (so file exists) but no row stored.
    if db.exists():
        conn = sqlite3.connect(str(db))
        try:
            cur = conn.execute("SELECT COUNT(*) FROM forecasts")
            assert cur.fetchone()[0] == 0
        finally:
            conn.close()


def test_skips_when_no_forecast_block(isolated_writer, tmp_path):
    isolated_writer["forecast.enabled"] = True
    chat._capture_pax_forecast(
        "Just prose, no forecast block.", _snap(),
        ts_ms=1, chat_run_id="r", digest_sha256="d", snapshot_sha256="s",
    )
    db = tmp_path / "pax-forecast.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        try:
            cur = conn.execute("SELECT COUNT(*) FROM forecasts")
            assert cur.fetchone()[0] == 0
        finally:
            conn.close()


def test_skips_when_snapshot_offline(isolated_writer, tmp_path):
    isolated_writer["forecast.enabled"] = True
    snap = _snap()
    snap["health"] = "offline"
    chat._capture_pax_forecast(
        _pax_text_with_forecast(), snap,
        ts_ms=1, chat_run_id="r", digest_sha256="d", snapshot_sha256="s",
    )
    db = tmp_path / "pax-forecast.db"
    if db.exists():
        conn = sqlite3.connect(str(db))
        try:
            cur = conn.execute("SELECT COUNT(*) FROM forecasts")
            assert cur.fetchone()[0] == 0
        finally:
            conn.close()


def test_never_raises_on_persistence_failure(isolated_writer, monkeypatch):
    """If the writer blows up for any reason, the chat path keeps going."""
    isolated_writer["forecast.enabled"] = True

    def boom(*a, **kw):
        raise RuntimeError("simulated disk explosion")

    monkeypatch.setattr(forecast_store_writer, "persist_validated", boom)
    # Must not raise.
    chat._capture_pax_forecast(
        _pax_text_with_forecast(), _snap(),
        ts_ms=1, chat_run_id="r", digest_sha256="d", snapshot_sha256="s",
    )


# ---------------------------------------------------------------------------
# Phase 7: forecast emission guardrails. Pin capture-side behavior for
# multi-block responses and explicit linkage propagation. Existing tests
# cover the other spec items (malformed JSON, missing fields, offline
# snapshot, never-raises) via forecast_signal and the disabled-flag path.
# ---------------------------------------------------------------------------

def _two_forecast_blocks_text() -> str:
    """Pax response containing two well-formed forecast blocks. The second
    block carries distinct prob_success / expected_r so we can tell which
    one was persisted (forecast_signal picks the LAST well-formed block)."""
    first = {
        "alias":          "NQM6.CME@RITHMIC",
        "level":          "OR-H",
        "thesis":         "ACCEPTANCE_LONG",
        "execution_read": "PAY_FOR_TRADE",
        "direction":      "LONG",
        "horizon_sec":    300,
        "prob_success":   0.55,
        "expected_r":     0.50,
        "invalidation":   "back below OR-H",
        "features_used":  ["or_levels"],
    }
    second = {**first, "prob_success": 0.80, "expected_r": 1.50,
               "invalidation": "back below OR-H with absorption"}
    return (
        "First read.\n"
        f"<<PAX_FORECAST>>\n{json.dumps(first)}\n<<END_FORECAST>>\n"
        "Refinement.\n"
        f"<<PAX_FORECAST>>\n{json.dumps(second)}\n<<END_FORECAST>>\n"
    )


def test_two_valid_forecast_blocks_persist_exactly_one_row_last_wins(
        isolated_writer, tmp_path):
    """Two well-formed blocks in one response -> exactly one row, and the
    persisted row is the LAST block (deterministic per
    forecast_signal.extract_block). Phase 1 outcome labeling joins on
    (chat_run_id, digest_sha256); if capture wrote both rows, the labeler
    would mark the turn FORECAST_MISSING_OR_AMBIGUOUS."""
    isolated_writer["forecast.enabled"] = True
    chat._capture_pax_forecast(
        _two_forecast_blocks_text(), _snap(),
        ts_ms=1_765_000_000_000,
        chat_run_id="run-M",
        digest_sha256="d" * 64,
        snapshot_sha256="s" * 64,
    )
    db = tmp_path / "pax-forecast.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "SELECT prob_success, expected_r, chat_run_id "
            "FROM forecasts")
        rows = cur.fetchall()
    finally:
        conn.close()
    assert len(rows) == 1, (
        f"capture must write exactly one row for two valid blocks, got {len(rows)}")
    # Last block won: prob_success=0.80, expected_r=1.50.
    assert rows[0][0] == pytest.approx(0.80)
    assert rows[0][1] == pytest.approx(1.50)
    assert rows[0][2] == "run-M"


def test_capture_propagates_linkage_metadata_to_persisted_row(
        isolated_writer, tmp_path):
    """All three linkage fields (chat_run_id / digest_sha256 /
    snapshot_sha256) must make it onto the persisted row when supplied.
    Phase 5 turn audit joins forecasts on (chat_run_id, digest_sha256);
    Phase 2 calibration's linkage-fallback lookup uses the same join."""
    isolated_writer["forecast.enabled"] = True
    chat._capture_pax_forecast(
        _pax_text_with_forecast(), _snap(),
        ts_ms=1_765_000_000_000,
        chat_run_id="run-LINK",
        digest_sha256="abc" * 21 + "f",            # 64 chars
        snapshot_sha256="xyz" * 21 + "f",          # 64 chars
    )
    db = tmp_path / "pax-forecast.db"
    conn = sqlite3.connect(str(db))
    try:
        cur = conn.execute(
            "SELECT chat_run_id, digest_sha256, snapshot_sha256 "
            "FROM forecasts")
        row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    assert row[0] == "run-LINK"
    assert row[1] == "abc" * 21 + "f"
    assert row[2] == "xyz" * 21 + "f"


def test_never_raises_on_garbage_input(isolated_writer):
    isolated_writer["forecast.enabled"] = True
    # None pax_text / snap, weird types: must not raise.
    chat._capture_pax_forecast(
        None, None,
        ts_ms=0, chat_run_id=None, digest_sha256=None, snapshot_sha256=None,
    )
    chat._capture_pax_forecast(
        "<<PAX_FORECAST>>\n{}\n<<END_FORECAST>>", {},
        ts_ms=0, chat_run_id=None, digest_sha256=None, snapshot_sha256=None,
    )
