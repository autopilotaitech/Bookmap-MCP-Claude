"""record_trigger contract: fire-and-forget, no raise, no compute_triggers call,
no perturbation of triggers.py linger cache."""
from __future__ import annotations

import sqlite3
import time

import pytest

from pax_ai import feature_bus, triggers


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
            "queue_max":         2000,
            "writer_idle_ms":    20,
            "capture_ms":        20,
            "retention_days":    30,
        },
    })
    feature_bus._HEALTHY = True
    feature_bus._LAST_ERROR = None
    feature_bus._ROWS_TODAY = 0
    feature_bus._BLOB_WRITES_TODAY = 0
    yield {"db": db_path, "snap_dir": snap_dir, "dig_dir": dig_dir}
    feature_bus.stop()


def test_record_trigger_inserts_row_when_enabled(bus_enabled):
    feature_bus.start()
    feature_bus.record_trigger("NQM6", {
        "kind": "TREND_SIGNAL_FIRE",
        "severity": "HIGH",
        "label": "STRONG_BULL",
        "headline": "trend fire",
        "details": "details",
    }, now_ms=1715000000000)
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_record_trigger_noop_when_disabled(tmp_path, monkeypatch):
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": str(tmp_path / "no.db"),
            "snapshot_blob_dir": str(tmp_path / "s"), "digest_blob_dir": str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})
    feature_bus.record_trigger("X", {"kind": "X", "severity": "LOW"}, now_ms=1)
    assert not (tmp_path / "no.db").exists()


def test_record_trigger_never_raises(bus_enabled, monkeypatch):
    # record_trigger is gated by _accepting_events(); set _RUNNING=True so
    # the gate lets us through to exercise the inner try/except.
    feature_bus._RUNNING = True
    monkeypatch.setattr(feature_bus, "_enqueue",
                          lambda evt: (_ for _ in ()).throw(RuntimeError("boom")))
    feature_bus.record_trigger("X", {"kind": "X", "severity": "LOW"}, now_ms=1)
    # No exception escaped.


def test_compute_triggers_never_called_by_feature_bus():
    """Static guard: feature_bus.py must NOT import or call triggers.compute_triggers."""
    import ast
    src = (feature_bus.__file__)
    with open(src, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            assert not (isinstance(node.value, ast.Name) and node.value.id == "triggers"
                          and node.attr == "compute_triggers"), \
                "feature_bus must not reference triggers.compute_triggers"
        if isinstance(node, ast.ImportFrom):
            if node.module and "triggers" in node.module:
                pytest.fail("feature_bus must not import from pax_ai.triggers")


def test_emit_edge_calls_feature_bus_record_trigger(bus_enabled, monkeypatch):
    feature_bus.start()
    state = triggers._new_alias_state()
    trig = {"kind": "TREND_SIGNAL_FIRE", "severity": "HIGH",
            "label": "STRONG_BULL", "headline": "x", "details": "y"}
    triggers._emit_edge(state, trig, 1715000000000, alias="NQM6")
    for _ in range(50):
        with sqlite3.connect(bus_enabled["db"]) as conn:
            try:
                n = conn.execute("SELECT COUNT(*) FROM trigger_events").fetchone()[0]
            except sqlite3.OperationalError:
                n = 0
        if n >= 1:
            break
        time.sleep(0.02)
    assert n == 1


def test_emit_edge_does_not_perturb_linger_cache_when_bus_disabled(tmp_path, monkeypatch):
    """With the bus disabled, _emit_edge behavior on the linger cache must be unchanged."""
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": "", "snapshot_blob_dir": "",
            "digest_blob_dir": "", "queue_max": 2000, "writer_idle_ms": 100,
            "retention_days": 30}})
    state = triggers._new_alias_state()
    trig = {"kind": "CONVICTION_FLIP", "label": "+1",
            "severity": "MED", "headline": "", "details": ""}
    triggers._emit_edge(state, trig, 1000, alias="X")
    assert ("CONVICTION_FLIP", "+1") in state["active_edges"]
    assert state["active_edges"][("CONVICTION_FLIP", "+1")]["firstSeenMs"] == 1000


def test_emit_edge_signature_accepts_alias_kwarg_safely():
    """Old call sites without alias kwarg must still work (alias defaults to None)."""
    state = triggers._new_alias_state()
    triggers._emit_edge(state, {"kind": "X", "label": "Y", "severity": "LOW"}, 1)
    # No exception; the bus simply doesn't get called (alias=None branch).
    assert ("X", "Y") in state["active_edges"]
