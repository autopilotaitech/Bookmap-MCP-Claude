"""Startup ordering invariant: feature_bus.start() must run AFTER poller.start()
and BEFORE journal.init(). Verified by reading the module source via AST."""
from __future__ import annotations

import ast
from pathlib import Path


def test_startup_order_main_poller_bus_journal():
    """In __main__.py::main, the call sequence on the success path is:
        poller.start()
        <feature_bus.start() (guarded by config.feature_bus.enabled)>
        journal.init()
    """
    src = Path(__file__).resolve().parent.parent / "pax_ai" / "__main__.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    main_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            main_fn = node
            break
    assert main_fn, "main() not found in __main__.py"

    # Flatten Calls in textual order.
    calls = []
    for node in ast.walk(main_fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                name = (f.value.id if isinstance(f.value, ast.Name) else "") + "." + f.attr
                calls.append((name, node.lineno))

    interesting = [c for c in calls
                    if c[0] in {"poller.start", "feature_bus.start", "journal.init"}]
    names_in_order = [c[0] for c in interesting]
    assert names_in_order == ["poller.start", "feature_bus.start", "journal.init"], names_in_order


import json
import sqlite3

import pytest


def _drive_triggers(monkeypatch, snap):
    """Pump a snapshot through compute_triggers and return the JSON bytes whynow returns."""
    from pax_ai import triggers, poller
    monkeypatch.setattr(poller, "latest", lambda: (snap, 1000, 0, 0, None))
    triggs = triggers.compute_triggers(snap, 0)
    return json.dumps(triggs, sort_keys=True, separators=(",", ":")).encode("utf-8")


def test_whynow_byte_identical_with_and_without_bus(monkeypatch, tmp_path):
    """compute_triggers output byte-identical regardless of bus state."""
    snap = {"alias": "NQM6", "health": "ok",
             "or_levels": {"levels": [{"label": "OR-H", "price": 100,
                                          "decision": "FOLLOW_LONG", "confidence": 0.7}],
                            "middleLock": False, "inProximity": True},
             "conviction": {"score": 0.5, "trend": "BULL", "anchorMode": "LIVE"},
             "flow": {"regime": "TRENDING_UP", "regimeConfidence": 0.7,
                       "biasScore": 0.4, "biasTrajectory": "RISING"},
             "micro_events": {"events": []},
             "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": False, "label": "clear"}},
             "trend_signal": {"kind": "STRONG_BULL", "eligible": True,
                                "changedSinceLastTick": False,
                                "bucketEnteredMs": 0,
                                "eventMsSource": "trend_analyzer", "mid": 100},
             "session": {"code": "ACTIVE", "anchorMode": "LIVE"}}

    # Reset per-alias state so the two runs start identical.
    from pax_ai import triggers as t
    with t._STATE_LOCK:
        t._PER_ALIAS_STATE.clear()
    from pax_ai import config as cfg_mod
    monkeypatch.setattr(cfg_mod, "_reload_if_stale", lambda: None)
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": False, "db_path": "", "snapshot_blob_dir": "",
            "digest_blob_dir": "", "queue_max": 2000, "writer_idle_ms": 100,
            "retention_days": 30}})
    bytes_disabled = _drive_triggers(monkeypatch, snap)

    # Reset state and run with bus enabled.
    with t._STATE_LOCK:
        t._PER_ALIAS_STATE.clear()
    monkeypatch.setattr(cfg_mod, "_CACHE", {**cfg_mod._CACHE,
        "feature_bus": {"enabled": True, "db_path": str(tmp_path / "b.db"),
            "snapshot_blob_dir": str(tmp_path / "s"),
            "digest_blob_dir": str(tmp_path / "d"),
            "queue_max": 2000, "writer_idle_ms": 100, "retention_days": 30}})
    bytes_enabled = _drive_triggers(monkeypatch, snap)

    assert bytes_disabled == bytes_enabled, \
      f"compute_triggers output diverged:\nDISABLED: {bytes_disabled!r}\nENABLED:  {bytes_enabled!r}"
