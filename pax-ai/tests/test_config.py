"""Tests for pax_ai.config - hot-reload + dotted-path getter + defaults."""

from __future__ import annotations

import json

from pax_ai import config


def test_defaults_present():
    cfg = config.get_config()
    assert cfg["models"]["live"] == "claude-haiku-4-5"
    assert cfg["models"]["deep"] == "claude-sonnet-4-6"
    assert cfg["poll_ms"] == 1000
    assert cfg["stale_snapshot_ms"] == 5000


def test_get_dotted_path():
    assert config.get("models.live") == "claude-haiku-4-5"
    assert config.get("models.opus_opt_in") is False
    assert config.get("size_tiers.FULL_min_confidence") == 0.50
    assert config.get("tick_size.NQ") == 0.25
    assert config.get("tick_size.ES") == 0.25
    assert config.get("payline_pts.NQ") == 10.0
    assert config.get("payline_pts.ES") == 2.5
    assert config.get("nonexistent.path", "fallback") == "fallback"


def test_meta_keys_stripped(tmp_path, monkeypatch):
    # Point CONFIG_PATH at a temp file with a comment + override.
    cfg_file = tmp_path / "pax_ai_config.json"
    cfg_file.write_text(json.dumps({
        "_comment": "test override",
        "poll_ms": 750,
        "models": {"live": "claude-haiku-4-5", "opus_opt_in": True},
    }))
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_file)
    # Force a reload by resetting the mtime cache
    monkeypatch.setattr(config, "_LOADED_MTIME", 0.0)

    cfg = config.get_config()
    assert "_comment" not in cfg
    assert cfg["poll_ms"] == 750
    assert cfg["models"]["live"] == "claude-haiku-4-5"
    assert cfg["models"]["opus_opt_in"] is True
    # Defaults that were not overridden persist
    assert cfg["stale_snapshot_ms"] == 5000


def test_deep_merge_preserves_unrelated_branches(tmp_path, monkeypatch):
    cfg_file = tmp_path / "pax_ai_config.json"
    cfg_file.write_text(json.dumps({"models": {"deep": "claude-opus-4-7"}}))
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_file)
    monkeypatch.setattr(config, "_LOADED_MTIME", 0.0)

    cfg = config.get_config()
    assert cfg["models"]["deep"] == "claude-opus-4-7"
    # `live` is in DEFAULTS but not in the override - must still be present
    assert cfg["models"]["live"] == "claude-haiku-4-5"


def test_feature_bus_defaults_present_and_disabled():
    """feature_bus.* keys must exist with safe Phase-1 defaults."""
    from pax_ai import config
    assert config.get("feature_bus.enabled") is False
    assert isinstance(config.get("feature_bus.db_path"), str)
    assert isinstance(config.get("feature_bus.snapshot_blob_dir"), str)
    assert isinstance(config.get("feature_bus.digest_blob_dir"), str)
    assert config.get("feature_bus.queue_max") == 2000
    assert config.get("feature_bus.writer_idle_ms") == 100
    assert config.get("feature_bus.capture_ms") == 1000
    assert config.get("feature_bus.retention_days") == 30
