"""Stage 4 tests for the /api/pax/attack-response handler.

Contracts:
  * Cold poller -> 503.
  * Healthy snapshot -> 200 with payload having closed-vocab states.
  * Never calls claude_stream.
  * Never mutates the snapshot.
  * Stale snapshot threshold honored from config.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from pax_ai import server
from pax_ai import attack_response as attack_response_mod
from pax_ai import poller


def _ev(event_type, label, side, **kw):
    base = {
        "event_type": event_type,
        "label": label,
        "side": side,
        "direction": kw.get("direction", "NONE"),
        "marker_text": kw.get("marker_text", ""),
        "alias": "NQM6.CME@RITHMIC",
        "price": 30150.0,
    }
    base.update({k: v for k, v in kw.items() if k not in ("direction", "marker_text")})
    return base


def _bullish_snap():
    return {
        "alias": "NQM6.CME@RITHMIC",
        "health": "ok",
        "session": {"code": "ACTIVE", "anchorMode": "LIVE"},
        "or_levels": {
            "orHigh": 30192.0,
            "orLow": 30145.0,
            "levels": [{
                "label": "OR-L", "price": 30145.0, "side": "below",
                "proximity": True, "distance": -1.0,
            }],
        },
        "institutional_chart_events": [
            _ev("LIQUIDITY_SWEEP", "OR-L", "below"),
            _ev("ICEBERG_DEFENSE", "OR-L", "below", marker_text="ICE-B"),
            _ev("STACKING", "OR-L", "below"),
        ],
        "tape_flow": {"deltaScore": 0.7, "prints30s": 30},
        "book": {"mid": 30150.0},
    }


def _install_poller(monkeypatch, snap, as_of_ms=1_700_000_000_000,
                     age_ms=100, fails=0, err=None):
    def fake_latest():
        return (snap, as_of_ms, age_ms, fails, err)
    monkeypatch.setattr(poller, "latest", fake_latest)


# --- behavior tests ------------------------------------------------------

def test_endpoint_returns_503_when_no_snapshot(monkeypatch):
    _install_poller(monkeypatch, None, as_of_ms=None, age_ms=None,
                    fails=3, err="dashboard timeout")
    status, body = server._api_pax_attack_response()
    assert status == 503
    assert body.get("error") == "no_snapshot_yet"
    assert body.get("lastError") == "dashboard timeout"


def test_endpoint_returns_200_with_bullish_state(monkeypatch):
    snap = _bullish_snap()
    _install_poller(monkeypatch, snap)
    status, body = server._api_pax_attack_response()
    assert status == 200
    assert body["alias"] == "NQM6.CME@RITHMIC"
    assert body["health"] == "ok"
    assert body["blocked"] == {"health": False, "stale": False, "anchor": False}
    assert len(body["states"]) == 1
    s = body["states"][0]
    assert s["state"] == attack_response_mod.STATE_OR_L_SWEEP_RECLAIM
    assert s["bias"] == attack_response_mod.BIAS_BULL


def test_endpoint_closed_vocabulary_strict(monkeypatch):
    snap = _bullish_snap()
    _install_poller(monkeypatch, snap)
    _, body = server._api_pax_attack_response()
    for s in body["states"]:
        assert s["state"] in attack_response_mod.ALLOWED_STATES
        assert s["bias"]  in attack_response_mod.ALLOWED_BIASES
        # never claim measured edge from this build
        assert s["proven_edge"] is False
        assert s["sample_n"] is None
        assert s["edge_R_60s"] is None


def test_endpoint_does_not_mutate_snapshot(monkeypatch):
    snap = _bullish_snap()
    snap_clone = deepcopy(snap)
    _install_poller(monkeypatch, snap)
    server._api_pax_attack_response()
    server._api_pax_attack_response()
    assert snap == snap_clone, "endpoint must NOT mutate the polled snapshot"


def test_endpoint_does_not_call_claude_stream(monkeypatch):
    """Pinned: chat path is unreachable from the attack-response handler.

    We hook every public claude_stream entry point with a tripwire and
    confirm none fire during a healthy-snapshot call.
    """
    from pax_ai import claude_stream
    called = {"stream_chat": 0, "claude_available": 0}

    def trip(name):
        def _f(*a, **kw):
            called[name] += 1
            raise AssertionError(f"{name} called from attack-response endpoint")
        return _f

    if hasattr(claude_stream, "stream_chat"):
        monkeypatch.setattr(claude_stream, "stream_chat", trip("stream_chat"))
    if hasattr(claude_stream, "claude_available"):
        monkeypatch.setattr(claude_stream, "claude_available", lambda: False)

    snap = _bullish_snap()
    _install_poller(monkeypatch, snap)
    status, _ = server._api_pax_attack_response()
    assert status == 200
    assert called["stream_chat"] == 0


def test_endpoint_stale_snapshot_blocks(monkeypatch):
    snap = _bullish_snap()
    _install_poller(monkeypatch, snap, age_ms=60_000)
    status, body = server._api_pax_attack_response()
    assert status == 200
    assert body["health"] == "stale"
    assert body["states"] == []
    assert body["blocked"]["stale"] is True


def test_endpoint_offline_snapshot_blocks(monkeypatch):
    snap = _bullish_snap()
    snap["health"] = "offline"
    _install_poller(monkeypatch, snap)
    status, body = server._api_pax_attack_response()
    assert status == 200
    assert body["health"] == "offline"
    assert body["blocked"]["health"] is True
    assert body["states"] == []


def test_endpoint_anchor_not_live_blocks(monkeypatch):
    snap = _bullish_snap()
    snap["session"]["anchorMode"] = "LAST_KNOWN_STALE"
    _install_poller(monkeypatch, snap)
    status, body = server._api_pax_attack_response()
    assert status == 200
    assert body["blocked"]["anchor"] is True
    assert body["states"] == []


# --- routing wiring ------------------------------------------------------

def test_route_path_string_present_in_server_source():
    """Sanity guard: a future server-refactor mustn't drop the route
    without also removing the test (so we catch the dispatch deletion).
    """
    from pathlib import Path
    src = (Path(__file__).parent.parent / "pax_ai" / "server.py").read_text(
        encoding="utf-8")
    assert "/api/pax/attack-response" in src
    assert "_api_pax_attack_response" in src
