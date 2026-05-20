"""Snapshot delta detector emits level_events, microstructure_events,
and state-condition trigger_events. Pure function, no DB side-effects."""
from __future__ import annotations

from pax_ai import feature_bus


def _snap(alias="NQM6", **kw):
    base = {
        "alias": alias,
        "health": "ok",
        "or_levels": {"levels": [], "middleLock": False, "inProximity": False},
        "micro_events": {"events": []},
        "gates": {"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                   "news": {"blocked": False, "label": "clear"}},
    }
    base.update(kw)
    return base


def test_level_event_emitted_on_decision_change():
    prev = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "WAIT", "confidence": 0.3,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    new = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "FOLLOW_LONG", "confidence": 0.7,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    lvl = [e for e in events if e["kind"] == "level_event"]
    assert len(lvl) == 1
    assert lvl[0]["payload"]["prev_decision"] == "WAIT"
    assert lvl[0]["payload"]["new_decision"] == "FOLLOW_LONG"


def test_no_level_event_when_unchanged():
    s = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "WAIT", "confidence": 0.3,
         "proxTicks": 100}
    ], "middleLock": False, "inProximity": False})
    events = feature_bus._detect_snapshot_deltas(s, s, now_ms=1)
    assert not [e for e in events if e["kind"] == "level_event"]


def test_microstructure_event_emitted_on_new_event():
    prev = _snap(micro_events={"events": []})
    new = _snap(micro_events={"events": [
        {"type": "STOP_SWEEP", "price": 23450.0, "side": "buy",
         "size": 5, "tsMs": 1000}
    ]})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1500)
    micro = [e for e in events if e["kind"] == "microstructure_event"]
    assert len(micro) == 1
    assert micro[0]["payload"]["event_type"] == "STOP_SWEEP"


def test_microstructure_event_dedup_by_type_price_ts():
    """Same (type, price, tsMs) in both snapshots must NOT re-emit."""
    ev = {"type": "ICEBERG", "price": 23450.0, "side": "ask",
          "size": 10, "tsMs": 1000}
    prev = _snap(micro_events={"events": [ev]})
    new = _snap(micro_events={"events": [ev]})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=2000)
    assert not [e for e in events if e["kind"] == "microstructure_event"]


def test_bridge_degraded_state_trigger():
    prev = _snap(health="ok")
    new = _snap(health="offline", bridgeError="connection refused")
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "BRIDGE_DEGRADED" for t in trig)


def test_news_blackout_state_trigger():
    prev = _snap()  # news.blocked=False
    new = _snap(gates={"session": {"code": "ACTIVE", "anchorMode": "LIVE"},
                        "news": {"blocked": True, "label": "FOMC"}})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "NEWS_BLACKOUT" for t in trig)


def test_level_approach_state_trigger():
    """Crossed inProximity edge."""
    prev = _snap(or_levels={"levels": [], "middleLock": False, "inProximity": False})
    new = _snap(or_levels={"levels": [], "middleLock": False, "inProximity": True})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    trig = [e for e in events if e["kind"] == "trigger_event"]
    assert any(t["payload"]["kind"] == "LEVEL_APPROACH" for t in trig)


def test_no_event_when_decision_confidence_proximity_unchanged():
    """Regression: identical level dict on both sides must NOT emit a level_event,
    even when `proximity` is present and identical."""
    s = _snap(or_levels={"levels": [
        {"label": "OR-H", "price": 23450.0, "decision": "WAIT",
         "confidence": 0.3, "proximity": True}
    ], "middleLock": False, "inProximity": True})
    events = feature_bus._detect_snapshot_deltas(s, s, now_ms=1)
    assert not [e for e in events if e["kind"] == "level_event"]


def test_level_event_emitted_on_proximity_flip_false_to_true():
    """Regression: per-level `proximity` flipping False -> True emits a level_event
    with prev_proximity=0, new_proximity=1, and trigger_reason='proximity_flip'."""
    lvl_base = {"label": "OR-H", "price": 23450.0, "decision": "WAIT", "confidence": 0.3}
    prev = _snap(or_levels={"levels": [{**lvl_base, "proximity": False}],
                             "middleLock": False, "inProximity": False})
    new  = _snap(or_levels={"levels": [{**lvl_base, "proximity": True}],
                             "middleLock": False, "inProximity": True})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    lvl = [e for e in events if e["kind"] == "level_event"]
    assert len(lvl) == 1
    p = lvl[0]["payload"]
    assert p["prev_proximity"] == 0
    assert p["new_proximity"]  == 1
    assert p["trigger_reason"] == "proximity_flip"
    assert p["prev_decision"]  == p["new_decision"]
    assert p["prev_confidence"] == p["new_confidence"]


def test_level_event_emitted_on_proximity_flip_true_to_false():
    lvl_base = {"label": "+1", "price": 23515.0, "decision": "FOLLOW_LONG", "confidence": 0.6}
    prev = _snap(or_levels={"levels": [{**lvl_base, "proximity": True}],
                             "middleLock": False, "inProximity": True})
    new  = _snap(or_levels={"levels": [{**lvl_base, "proximity": False}],
                             "middleLock": False, "inProximity": False})
    events = feature_bus._detect_snapshot_deltas(prev, new, now_ms=1)
    lvl = [e for e in events if e["kind"] == "level_event"]
    assert len(lvl) == 1
    p = lvl[0]["payload"]
    assert p["prev_proximity"] == 1
    assert p["new_proximity"]  == 0
    assert p["trigger_reason"] == "proximity_flip"


def test_detector_called_on_first_tick_with_none_prev():
    """First tick: prev=None must not raise; should emit no delta-events."""
    events = feature_bus._detect_snapshot_deltas(None, _snap(), now_ms=1)
    assert events == [] or all(e["kind"] == "snapshot_features" for e in events)
