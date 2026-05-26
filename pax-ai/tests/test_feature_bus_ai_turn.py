"""AiTurnRecord shape + null-sha rejection.

Phase 1 invariant: digest_sha256 and snapshot_sha256 are required and
non-empty; constructing an AiTurnRecord without them must raise.
"""
from __future__ import annotations

import pytest

from pax_ai import feature_bus


def _valid_kwargs(**overrides):
    base = dict(
        schema_version=1, ts_ms=0, chat_run_id="r1", deep=False, model="m",
        router_primary=None, router_secondary=None,
        user_text_raw="u", user_text_normalized="u",
        digest_text="d", digest_sha256="d" * 64,
        snapshot_json="{}", snapshot_sha256="s" * 64,
        snapshot_alias=None, snapshot_ts_ms=None, snapshot_age_ms=0,
        pax_text="", exit_code=0, elapsed_ms=0, api_duration_ms=None,
        total_cost_usd=None, input_tokens=None, output_tokens=None,
        cache_creation_tokens=None, cache_read_tokens=None,
        aborted=False, error=None,
        # Phase 4: turn-level audit trail.
        prompt_sha256="p" * 64,
        prompt_version="1.0.0",
        model_release_id="claude-haiku-4-5",
        skill_bundle_sha256="k" * 64,
        prompt_archive_path="",
    )
    base.update(overrides)
    return base


def test_aiturn_record_constructs_with_full_kwargs():
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    assert rec.digest_sha256 == "d" * 64
    assert rec.snapshot_sha256 == "s" * 64


def test_aiturn_record_rejects_empty_digest_sha256():
    with pytest.raises(ValueError, match="digest_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(digest_sha256=""))


def test_aiturn_record_rejects_empty_snapshot_sha256():
    with pytest.raises(ValueError, match="snapshot_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(snapshot_sha256=""))


def test_aiturn_record_is_frozen():
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    with pytest.raises(Exception):       # FrozenInstanceError
        rec.digest_sha256 = "xxxxxxxx"


# ---------------------------------------------------------------------------
# Phase 4: turn-level audit trail -- AiTurnRecord lineage fields
# ---------------------------------------------------------------------------

def test_aiturn_record_carries_phase4_lineage_fields():
    """The five Phase 4 fields must be surfaced as attributes."""
    rec = feature_bus.AiTurnRecord(**_valid_kwargs(
        prompt_sha256="a" * 64,
        prompt_version="1.0.0",
        model_release_id="claude-haiku-4-5",
        skill_bundle_sha256="b" * 64,
        prompt_archive_path=r"C:\some\archive\a.txt",
    ))
    assert rec.prompt_sha256       == "a" * 64
    assert rec.prompt_version      == "1.0.0"
    assert rec.model_release_id    == "claude-haiku-4-5"
    assert rec.skill_bundle_sha256 == "b" * 64
    assert rec.prompt_archive_path == r"C:\some\archive\a.txt"


def test_aiturn_record_rejects_empty_prompt_sha256():
    """prompt_sha256 is the turn's prompt-identity field; empty is a defect."""
    with pytest.raises(ValueError, match="prompt_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(prompt_sha256=""))


def test_aiturn_record_rejects_empty_prompt_version():
    with pytest.raises(ValueError, match="prompt_version"):
        feature_bus.AiTurnRecord(**_valid_kwargs(prompt_version=""))


def test_aiturn_record_rejects_empty_model_release_id():
    with pytest.raises(ValueError, match="model_release_id"):
        feature_bus.AiTurnRecord(**_valid_kwargs(model_release_id=""))


def test_aiturn_record_rejects_empty_skill_bundle_sha256():
    with pytest.raises(ValueError, match="skill_bundle_sha256"):
        feature_bus.AiTurnRecord(**_valid_kwargs(skill_bundle_sha256=""))


def test_aiturn_record_allows_empty_prompt_archive_path():
    """archive_path may be empty when archiving was skipped (e.g. empty
    prompt file) -- it's an optional audit pointer, not an identity."""
    rec = feature_bus.AiTurnRecord(**_valid_kwargs(prompt_archive_path=""))
    assert rec.prompt_archive_path == ""


def test_module_exports_status_returns_disabled_safe_default(monkeypatch):
    # Explicitly override the config so the test does NOT depend on
    # the developer's pax_ai_config.json (which may legitimately have
    # feature_bus.enabled=true) or on prior tests resetting _CACHE.
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {**(cfg_mod._CACHE.get("feature_bus") or {}),
                         "enabled": False},
    })
    # Reset module-level state so prior test files' pollution does not affect us.
    feature_bus._QUEUE.clear()
    feature_bus._HEALTHY = True
    feature_bus._RUNNING = False
    feature_bus._LAST_ERROR = None
    s = feature_bus.status()
    assert s["enabled"] is False
    assert s["healthy"] is True          # nothing has gone wrong yet
    assert s["running"] is False
    assert s["queueDepth"] == 0


import sqlite3
from pathlib import Path


@pytest.fixture
def bus_enabled(tmp_path, monkeypatch):
    """Override config to enable bus with tmp_path DB + blob roots."""
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
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    # Reset module-level counters.
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    feature_bus._ROWS_TODAY = 0
    feature_bus._BLOB_WRITES_TODAY = 0
    yield {"db": db_path, "snap_dir": snap_dir, "dig_dir": dig_dir}


import hashlib
import time
import sqlite3


# NOTE: the writer-thread persistence test for record_ai_turn lives in
# Task 7's tests/test_feature_bus_writer.py (see
# test_writer_drains_ai_turn_record_to_blobs_and_db) because it depends
# on the writer thread + the _drain_to_db `ai_turn` branch which Task 7
# introduces. Task 5 covers the synchronous contract only.


def test_record_ai_turn_noop_when_disabled(tmp_path, monkeypatch):
    """With feature_bus.enabled=False, no DB file and no blob files are ever created."""
    db_path = tmp_path / "must-not-exist.db"
    snap_dir = tmp_path / "snapshots-disabled"
    dig_dir  = tmp_path / "digests-disabled"
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {
        **cfg_mod._CACHE,
        "feature_bus": {
            "enabled":           False,
            "db_path":           str(db_path),
            "snapshot_blob_dir": str(snap_dir),
            "digest_blob_dir":   str(dig_dir),
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    feature_bus.record_ai_turn(rec)
    # Hard tmp-path assertions (not "or True"):
    assert not db_path.exists(),  f"DB file must not exist when disabled: {db_path}"
    assert not snap_dir.exists(), f"snapshot dir must not exist when disabled: {snap_dir}"
    assert not dig_dir.exists(),  f"digest dir must not exist when disabled: {dig_dir}"
    assert feature_bus.status()["rowsToday"] == 0


def test_record_ai_turn_never_raises_into_caller(bus_enabled, monkeypatch):
    """Forced enqueue failure must not raise into the caller."""
    # record_ai_turn is gated by _accepting_events() (enabled+healthy+running).
    # The fixture sets enabled+healthy; here we mark the writer running so the
    # gate lets us through to exercise the try/except in record_ai_turn itself.
    feature_bus._RUNNING = True
    def boom(*_, **__): raise RuntimeError("queue boom")
    monkeypatch.setattr(feature_bus, "_enqueue", boom)
    rec = feature_bus.AiTurnRecord(**_valid_kwargs(
        digest_sha256="d" * 64, snapshot_sha256="s" * 64,
    ))
    feature_bus.record_ai_turn(rec)             # MUST NOT raise
    s = feature_bus.status()
    assert s["healthy"] is False
    assert "queue boom" in (s["lastError"] or "")


def test_record_ai_turn_returns_quickly_under_slow_disk(bus_enabled, monkeypatch):
    """The caller must not block on disk I/O - the writer thread owns blob + DB writes.
    Simulate a slow filesystem by patching _write_*_blob with a 500 ms sleep, then
    confirm record_ai_turn returns in << 50 ms."""
    def slow_blob(*_, **__):
        time.sleep(0.5)
        return "x" * 64
    monkeypatch.setattr(feature_bus, "_write_snapshot_blob", slow_blob)
    monkeypatch.setattr(feature_bus, "_write_digest_blob",   slow_blob)
    # Don't start the writer; we just want to measure the caller's return time.
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    t0 = time.monotonic()
    feature_bus.record_ai_turn(rec)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 50, f"record_ai_turn blocked for {elapsed_ms:.1f} ms"


def test_record_methods_noop_when_unhealthy_or_not_running(bus_enabled, tmp_path):
    """Regression: when feature_bus is enabled in config but EITHER _HEALTHY=False
    OR _RUNNING=False, record_trigger and record_ai_turn must no-op cleanly:
    no enqueue, no DB file, no blob files."""
    # bus_enabled fixture leaves _RUNNING=False (we never called start()).
    # Also force _HEALTHY=False to simulate a post-lock-failure state.
    feature_bus._QUEUE.clear()
    feature_bus._HEALTHY = False
    feature_bus._RUNNING = False
    initial_depth = feature_bus.status()["queueDepth"]
    assert initial_depth == 0

    # Both APIs must no-op silently.
    feature_bus.record_trigger("NQM6", {"kind": "T", "severity": "HIGH",
                                          "label": "L"}, now_ms=1)
    rec = feature_bus.AiTurnRecord(**_valid_kwargs())
    feature_bus.record_ai_turn(rec)

    # Queue stays empty; no DB created.
    assert feature_bus.status()["queueDepth"] == 0
    assert not bus_enabled["db"].exists()
    assert not bus_enabled["snap_dir"].exists()
    assert not bus_enabled["dig_dir"].exists()

    # And when we flip back to healthy + running, the same calls DO enqueue.
    feature_bus._HEALTHY = True
    feature_bus._RUNNING = True
    feature_bus.record_trigger("NQM6", {"kind": "T", "severity": "HIGH",
                                          "label": "L"}, now_ms=1)
    feature_bus.record_ai_turn(rec)
    assert feature_bus.status()["queueDepth"] == 2

    # Cross-test hygiene: reset module state so we don't pollute later tests.
    feature_bus._QUEUE.clear()
    feature_bus._HEALTHY = True
    feature_bus._RUNNING = False
    feature_bus._LAST_ERROR = None
