from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest

from pax_ai import forecast_store_writer


# -------------------------------------------------------------- helpers

def _validated_forecast(**overrides: Any) -> Dict[str, Any]:
    base = {
        "forecast_id":   "pax_fcst|0123456789abcdef",
        "schema_version": 1,
        "ts_ms":         1_765_000_000_000,
        "source_turn_id": None,
        "alias":         "NQM6.CME@RITHMIC",
        "level":         "OR-H",
        "thesis":        "ACCEPTANCE_LONG",
        "execution_read":"PAY_FOR_TRADE",
        "direction":     "LONG",
        "horizon_sec":   300,
        "prob_success":  0.62,
        "expected_r":    0.74,
        "invalidation":  "back below OR-H",
        "features_used": ["or_levels"],
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    """Force config.get to return controlled values for forecast.* keys.

    Tests opt into ``forecast.enabled = True`` explicitly via
    ``_enable_capture`` so the default-OFF posture cannot leak.
    """
    real_get = forecast_store_writer.config.get
    overrides: Dict[str, Any] = {"forecast.enabled": False,
                                  "forecast.store_path": ""}

    def patched_get(path: str, default: Any = None) -> Any:
        if path in overrides:
            return overrides[path]
        return real_get(path, default)

    monkeypatch.setattr(forecast_store_writer.config, "get", patched_get)
    yield overrides


def _enable_capture(overrides, store_path: Path):
    overrides["forecast.enabled"] = True
    overrides["forecast.store_path"] = str(store_path)


# -------------------------------------------------------------- behavior

def test_is_enabled_default_off(_isolate_config):
    assert forecast_store_writer.is_enabled() is False


def test_persist_noop_when_disabled(tmp_path):
    out = forecast_store_writer.persist_validated(_validated_forecast())
    assert out is False
    # No DB created at the default path either.
    assert not (tmp_path / "pax-forecast.db").exists()


def test_persist_persists_via_real_store(tmp_path, _isolate_config):
    db_path = tmp_path / "pax-forecast.db"
    _enable_capture(_isolate_config, db_path)
    ok = forecast_store_writer.persist_validated(
        _validated_forecast(),
        chat_run_id="run-1",
        digest_sha256="d" * 64,
        snapshot_sha256="s" * 64,
    )
    assert ok is True
    assert db_path.exists()
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT chat_run_id, digest_sha256, snapshot_sha256, alias "
            "FROM forecasts")
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "run-1"
        assert row[1] == "d" * 64
        assert row[2] == "s" * 64
        assert row[3] == "NQM6.CME@RITHMIC"
    finally:
        conn.close()


def test_persist_silent_when_mcp_install_missing(tmp_path, monkeypatch,
                                                  _isolate_config):
    db_path = tmp_path / "pax-forecast.db"
    _enable_capture(_isolate_config, db_path)

    # Simulate a missing mcp-server install by forcing the import to fail.
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "bookmap_mcp.pax_forecast_store":
            raise ImportError("simulated: bookmap_mcp not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    # Reset the one-shot log flag so the warning would fire (test stderr).
    forecast_store_writer._LAST_MISSING_LOG = False

    out = forecast_store_writer.persist_validated(_validated_forecast())
    assert out is False
    assert not db_path.exists()


def test_persist_silent_on_bad_forecast_dict(tmp_path, _isolate_config):
    db_path = tmp_path / "pax-forecast.db"
    _enable_capture(_isolate_config, db_path)
    # Missing required fields -> record_validated raises -> writer eats it.
    out = forecast_store_writer.persist_validated({"forecast_id": "x"})
    assert out is False


def test_persist_silent_on_non_dict_input(tmp_path, _isolate_config):
    db_path = tmp_path / "pax-forecast.db"
    _enable_capture(_isolate_config, db_path)
    assert forecast_store_writer.persist_validated(None) is False
    assert forecast_store_writer.persist_validated("string") is False
    assert forecast_store_writer.persist_validated(123) is False


def test_resolved_store_path_uses_config(tmp_path, _isolate_config):
    _isolate_config["forecast.store_path"] = str(tmp_path / "custom.db")
    assert forecast_store_writer.resolved_store_path() == \
           Path(str(tmp_path / "custom.db"))


def test_persist_store_path_kwarg_wins_over_config(tmp_path, _isolate_config):
    cfg_path = tmp_path / "from-config.db"
    kw_path  = tmp_path / "from-kwarg.db"
    _enable_capture(_isolate_config, cfg_path)
    ok = forecast_store_writer.persist_validated(
        _validated_forecast(), store_path=kw_path)
    assert ok is True
    assert kw_path.exists()
    assert not cfg_path.exists()
