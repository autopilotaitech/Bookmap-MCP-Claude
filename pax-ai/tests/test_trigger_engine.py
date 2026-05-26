"""Trigger engine tests.

Every test creates its OWN ``TriggerEngine()`` instance so module-level
state cannot leak across tests. ``poller.latest`` is monkeypatched to a
"no snapshot" stub so the tick loop never touches the live dashboard.
``config.get`` is patched per-test through a helper that takes an
overrides dict.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Dict, List

import pytest

from pax_ai import trigger_engine


# ---------------------------------------------------------------- helpers

def _patch_config(monkeypatch, overrides: Dict[str, Any]) -> None:
    real_get = trigger_engine.config.get

    def patched(path: str, default: Any = None) -> Any:
        if path in overrides:
            return overrides[path]
        return real_get(path, default)

    monkeypatch.setattr(trigger_engine.config, "get", patched)


def _patch_poller_offline(monkeypatch) -> None:
    monkeypatch.setattr(
        trigger_engine.poller, "latest",
        lambda: (None, 0, 10**9, 0, "test stub"),
    )


def _default_overrides(**kw: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "trigger_engine.enabled":                 False,
        "trigger_engine.min_global_interval_sec": 0.01,
        "trigger_engine.cooldown_per_kind_sec":   60.0,
        "trigger_engine.tick_interval_sec":       0.05,
        "trigger_engine.deep":                    False,
        "trigger_engine.fireable_kinds":          ["LEVEL_APPROACH",
                                                    "TREND_SIGNAL_FIRE",
                                                    "CONVICTION_FLIP"],
    }
    base.update(kw)
    return base


def _trig(kind: str = "LEVEL_APPROACH", label: str = "OR-H",
          severity: str = "MED", headline: str = "approach",
          details: str = "details") -> Dict[str, Any]:
    return {
        "kind":     kind,
        "severity": severity,
        "label":    label,
        "headline": headline,
        "details":  details,
        "asOfMs":   1_000,
    }


def _snap(alias: str = "NQM6.CME@RITHMIC") -> Dict[str, Any]:
    return {"health": "ok", "alias": alias,
            "or_levels": {"levels": [{"label": "OR-H", "price": 20100.0}]}}


# ---------------------------------------------------------------- enabled flag

def test_is_enabled_default_off(monkeypatch):
    _patch_config(monkeypatch, _default_overrides())
    assert trigger_engine.is_enabled() is False


def test_is_enabled_true_when_override_true(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    assert trigger_engine.is_enabled() is True


# ---------------------------------------------------------- consider_triggers
# Pure function: no threads, no stop() needed.

def test_consider_triggers_returns_empty_when_disabled(monkeypatch):
    _patch_config(monkeypatch, _default_overrides())
    eng = trigger_engine.TriggerEngine()
    selected = eng.consider_triggers(_snap(), [_trig()], now=100.0)
    assert selected == []


def test_consider_triggers_returns_new_trigger_when_enabled(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    eng = trigger_engine.TriggerEngine()
    selected = eng.consider_triggers(_snap(), [_trig()], now=100.0)
    assert len(selected) == 1
    assert selected[0]["kind"] == "LEVEL_APPROACH"
    assert selected[0]["_alias"] == "NQM6.CME@RITHMIC"


def test_consider_triggers_dedups_within_cooldown(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled":               True,
        "trigger_engine.cooldown_per_kind_sec": 60.0,
    }))
    eng = trigger_engine.TriggerEngine()
    first = eng.consider_triggers(_snap(), [_trig()], now=100.0)
    repeat = eng.consider_triggers(_snap(), [_trig()], now=100.5)
    assert len(first) == 1
    assert repeat == []
    assert eng.stats()["skips_total"] >= 1


def test_consider_triggers_refires_after_cooldown(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled":               True,
        "trigger_engine.cooldown_per_kind_sec": 10.0,
    }))
    eng = trigger_engine.TriggerEngine()
    first = eng.consider_triggers(_snap(), [_trig()], now=100.0)
    repeat = eng.consider_triggers(_snap(), [_trig()], now=120.0)
    assert len(first) == 1
    assert len(repeat) == 1


def test_consider_triggers_separate_keys_for_different_label(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    eng = trigger_engine.TriggerEngine()
    a = eng.consider_triggers(_snap(), [_trig(label="OR-H")], now=100.0)
    b = eng.consider_triggers(_snap(), [_trig(label="OR-L")], now=100.5)
    assert len(a) == 1 and len(b) == 1


def test_consider_triggers_separate_keys_for_different_alias(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    eng = trigger_engine.TriggerEngine()
    a = eng.consider_triggers(_snap(alias="NQM6.CME@RITHMIC"),
                               [_trig()], now=100.0)
    b = eng.consider_triggers(_snap(alias="ESM6.CME@RITHMIC"),
                               [_trig()], now=100.5)
    assert len(a) == 1 and len(b) == 1


def test_consider_triggers_skips_non_fireable_kinds(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    eng = trigger_engine.TriggerEngine()
    skipped = eng.consider_triggers(_snap(),
                                     [_trig(kind="BRIDGE_DEGRADED"),
                                      _trig(kind="EOD_RISK")],
                                     now=100.0)
    assert skipped == []


# -------------------------------------------------------------- format

def test_format_user_text_contains_trigger_fields():
    txt = trigger_engine._format_user_text(_trig(
        kind="TREND_SIGNAL_FIRE", label="STRONG_BULL",
        severity="HIGH", headline="STRONG_BULL fired",
        details="trend_signal bucketEnteredMs=123"))
    assert "TRIGGER FIRED" in txt
    assert "TREND_SIGNAL_FIRE" in txt
    assert "STRONG_BULL" in txt
    assert "PAX_FORECAST" in txt
    assert "PAX_AI_CHART_SIGNAL" in txt


# ---------------------------------------------------------------- worker
# These tests start threads. Each one creates a local engine and stops it
# explicitly inside a try/finally so a test crash cannot leave a daemon
# thread spinning across tests.

def _start_engine(monkeypatch, overrides: Dict[str, Any]) -> trigger_engine.TriggerEngine:
    _patch_config(monkeypatch, overrides)
    _patch_poller_offline(monkeypatch)
    eng = trigger_engine.TriggerEngine()
    assert eng.start() is True
    return eng


def test_worker_fires_callable_when_enabled(monkeypatch):
    eng = _start_engine(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    fired: List[str] = []
    fire_done = threading.Event()

    def fake_fire(user_text: str, *, deep: bool) -> Dict[str, Any]:
        fired.append(user_text)
        fire_done.set()
        return {"exit_code": 0, "elapsed_ms": 1, "pax_text_len": 0,
                "model": "haiku", "error": None}

    eng._fire_callable = fake_fire
    try:
        eng._queue.put_nowait({"kind": "LEVEL_APPROACH", "label": "OR-H",
                                "_alias": "NQM6"})
        assert fire_done.wait(timeout=2.0), "worker never fired"
    finally:
        eng.stop(timeout_s=1.0)

    assert len(fired) == 1
    assert "TRIGGER FIRED" in fired[0]


def test_worker_survives_callable_exception(monkeypatch):
    monkeypatch.setattr(trigger_engine, "_min_global_interval_sec",
                        lambda: 0.0)
    eng = _start_engine(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    fires = []
    second_fire = threading.Event()

    def fake_fire(user_text: str, *, deep: bool) -> Dict[str, Any]:
        fires.append(user_text)
        if len(fires) == 1:
            raise RuntimeError("simulated CLI failure")
        second_fire.set()
        return {"exit_code": 0, "elapsed_ms": 1, "pax_text_len": 0,
                "model": "haiku", "error": None}

    eng._fire_callable = fake_fire
    try:
        eng._queue.put_nowait({"kind": "LEVEL_APPROACH", "label": "OR-H",
                                "_alias": "NQM6"})
        eng._queue.put_nowait({"kind": "LEVEL_APPROACH", "label": "OR-L",
                                "_alias": "NQM6"})
        # Both should be processed; worker must survive the first
        # exception and pick up the second item.
        ok = second_fire.wait(timeout=3.0)
    finally:
        eng.stop(timeout_s=1.0)

    assert ok, "worker did not process second item after first raised"
    assert len(fires) >= 2


# ---------------------------------------------------------------- lifecycle

def test_start_is_idempotent(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    _patch_poller_offline(monkeypatch)
    eng = trigger_engine.TriggerEngine()
    try:
        a = eng.start()
        b = eng.start()
        assert a is True
        assert b is False
    finally:
        eng.stop(timeout_s=1.0)


def test_start_no_op_when_disabled_in_module_facade(monkeypatch):
    _patch_config(monkeypatch, _default_overrides())
    # Module facade reads config to decide whether to start.
    assert trigger_engine.start() is False


def test_stop_is_clean(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    _patch_poller_offline(monkeypatch)
    eng = trigger_engine.TriggerEngine()
    eng.start()
    assert eng.is_running()
    ok = eng.stop(timeout_s=2.0)
    assert ok is True
    assert eng.is_running() is False


def test_stats_reports_running_state(monkeypatch):
    _patch_config(monkeypatch, _default_overrides(**{
        "trigger_engine.enabled": True,
    }))
    _patch_poller_offline(monkeypatch)
    eng = trigger_engine.TriggerEngine()
    s0 = eng.stats()
    assert s0["enabled"] is True
    assert s0["running"] is False
    eng.start()
    try:
        s1 = eng.stats()
        assert s1["running"] is True
    finally:
        eng.stop(timeout_s=1.0)
