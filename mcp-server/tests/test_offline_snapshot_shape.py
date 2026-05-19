"""Pins the structured offline payload shape on /api/snapshot.

When the bridge is unreachable, the dashboard must emit enough detail for
the operator (and the OpenRange chart overlay) to diagnose without opening
a debugger: which URL was tried, what failed, where the token came from,
and what to do next.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

import bookmap_mcp.dashboard as d                                                # noqa: E402
from bookmap_mcp.bridge_client import BridgeError                                # noqa: E402
from bookmap_mcp.config import BridgeConfig, MissingTokenError                   # noqa: E402


_EXPECTED_KEYS = {
    "health", "bridgeUrl", "bridgeReachable", "bridgeError",
    "dashboardPort", "expectedBridgeConfigPath", "tokenConfigured",
    "nextSteps", "error",
}


def test_offline_payload_shape_when_bridge_times_out(monkeypatch):
    """fetch_snapshot must build the structured offline payload, not a bare error dict."""
    d._DASHBOARD_PORT = 18888

    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="dummy-token")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def get_json(self, *a, **kw):
            raise BridgeError("Bridge unreachable at http://127.0.0.1:8765/ping: timed out")
    monkeypatch.setattr(d, "BridgeClient", _Client)

    snap = d.fetch_snapshot()
    assert snap["health"] == "offline"
    assert set(snap.keys()) >= _EXPECTED_KEYS, (
        f"missing keys: {_EXPECTED_KEYS - set(snap.keys())}")
    assert snap["bridgeUrl"] == "http://127.0.0.1:8765"
    assert snap["bridgeReachable"] is False
    assert snap["dashboardPort"] == 18888
    assert "bridge.properties" in snap["expectedBridgeConfigPath"]
    assert snap["tokenConfigured"] is True, "token was set on the fake config"
    assert "timed out" in snap["bridgeError"]
    assert isinstance(snap["nextSteps"], list)
    assert len(snap["nextSteps"]) >= 1
    # First step must specifically mention timeout / not-loaded.
    assert "not loaded" in snap["nextSteps"][0].lower() or "did not respond" in snap["nextSteps"][0].lower()


def test_offline_payload_when_token_missing(monkeypatch):
    d._DASHBOARD_PORT = 18888
    monkeypatch.setattr(d.BridgeConfig, "load",
        classmethod(lambda cls: (_ for _ in ()).throw(MissingTokenError("no token in env or props"))))
    snap = d.fetch_snapshot()
    assert snap["health"] == "offline"
    assert snap["tokenConfigured"] is False
    assert snap["bridgeUrl"] is None
    assert "no token" in snap["bridgeError"].lower()
    assert any("bridge.properties" in s.lower() for s in snap["nextSteps"]), (
        "next-steps must point operator at properties file")


def test_offline_payload_distinguishes_connection_refused(monkeypatch):
    d._DASHBOARD_PORT = 18888
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="dummy")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def get_json(self, *a, **kw):
            raise BridgeError("Bridge unreachable at http://127.0.0.1:8765/ping: connection refused")
    monkeypatch.setattr(d, "BridgeClient", _Client)
    snap = d.fetch_snapshot()
    assert snap["health"] == "offline"
    assert "refused" in snap["bridgeError"].lower()
    # First nextStep should specifically mention the connection-refused diagnosis.
    joined = " | ".join(snap["nextSteps"]).lower()
    assert "refused" in joined or "not running" in joined


def test_offline_payload_distinguishes_401(monkeypatch):
    d._DASHBOARD_PORT = 18888
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token="wrong-token")
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def get_json(self, *a, **kw):
            raise BridgeError("HTTP 401 unauthorized")
    monkeypatch.setattr(d, "BridgeClient", _Client)
    snap = d.fetch_snapshot()
    joined = " | ".join(snap["nextSteps"]).lower()
    assert "401" in joined or "unauthor" in joined


def test_offline_payload_never_leaks_token(monkeypatch):
    d._DASHBOARD_PORT = 18888
    secret = "super-secret-bridge-token-xyz123"
    fake_cfg = BridgeConfig(url="http://127.0.0.1:8765", token=secret)
    monkeypatch.setattr(d.BridgeConfig, "load", classmethod(lambda cls: fake_cfg))

    class _Client:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *exc): pass
        def get_json(self, *a, **kw): raise BridgeError("timeout")
    monkeypatch.setattr(d, "BridgeClient", _Client)
    snap = d.fetch_snapshot()
    blob = str(snap)
    assert secret not in blob, "token must NOT appear in any offline payload field"
