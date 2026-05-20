"""v20 final invariant: the full OR start time (hour, minute, second,
timezone) propagates to the bridge.

Pins:
  1. _sync_bridge_config posts rth_open as HH:MM:SS, including seconds when
     the OR config has a non-zero startSecond.
  2. The cache key (_LAST_BRIDGE_CONFIG) accounts for seconds — changing only
     startSecond forces a fresh POST.
  3. compute_session_conviction's anchorIso remains second-accurate when the
     OR config publishes startSecond > 0.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                                                    # noqa: E402

from bookmap_mcp import dashboard, or_session                                    # noqa: E402
from bookmap_mcp import settings as _settings                                    # noqa: E402


CT = ZoneInfo("America/Chicago")


def _fake_anchor(hour: int, minute: int, second: int = 0,
                 *, mode: str = "LIVE",
                 source: str = "or_config",
                 tz: str = "America/Chicago",
                 path: str = "C:/BookmapLogs/or-session-config.json",
                 ) -> Dict[str, Any]:
    return {
        "hour":         hour,
        "minute":       minute,
        "second":       second,
        "rangeSeconds": 30,
        "timezone":     tz,
        "source":       source,
        "anchorMode":   mode,
        "available":    True,
        "reason":       None,
        "ageMs":        50,
        "updatedAtMs":  1_700_000_000_000,
        "path":         path,
    }


class _FakeBridgeClient:
    """Captures every /config POST so tests can inspect rth_open precision."""
    posts: List[Tuple[str, Dict[str, str]]] = []

    def __init__(self, *_args, **_kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def post_json(self, path, params):
        _FakeBridgeClient.posts.append((path, dict(params)))
        return {"ok": True}


@pytest.fixture
def fresh_bridge_state(monkeypatch):
    """Reset the in-memory bridge-sync cache + capture list before each test."""
    monkeypatch.setattr(dashboard, "BridgeClient", _FakeBridgeClient)
    dashboard._LAST_BRIDGE_CONFIG = None
    _FakeBridgeClient.posts = []
    # Make sure settings cache is loaded so vp_value_area_pct is available.
    _settings.load_settings()
    yield
    dashboard._LAST_BRIDGE_CONFIG = None


# ─── 1. Seconds are included in the rth_open POST ───────────────────────────

def test_sync_bridge_config_posts_seconds(monkeypatch, fresh_bridge_state):
    """When OR anchor has second=15, rth_open MUST be '17:00:15' not '17:00'."""
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 15))
    dashboard._sync_bridge_config(object())
    assert len(_FakeBridgeClient.posts) == 1, _FakeBridgeClient.posts
    path, params = _FakeBridgeClient.posts[0]
    assert path == "/config"
    assert params["rth_open"] == "17:00:15", (
        f"bridge sync truncated seconds: rth_open={params['rth_open']!r}"
    )


def test_sync_bridge_config_posts_hhmmss_even_when_second_zero(
        monkeypatch, fresh_bridge_state):
    """Always-HH:MM:SS for consistency. second=0 -> rth_open '08:30:00'."""
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(8, 30, 0))
    dashboard._sync_bridge_config(object())
    assert len(_FakeBridgeClient.posts) == 1, _FakeBridgeClient.posts
    assert _FakeBridgeClient.posts[0][1]["rth_open"] == "08:30:00"


# ─── 2. Cache invalidates on seconds-only change ────────────────────────────

def test_sync_bridge_config_cache_invalidates_on_seconds_change(
        monkeypatch, fresh_bridge_state):
    """First call at 17:00:00, second call at 17:00:15 → 2 POSTs."""
    # First push at 17:00:00
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 0))
    dashboard._sync_bridge_config(object())
    # Second push at 17:00:15 — operator changed the OR UI seconds only.
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 15))
    dashboard._sync_bridge_config(object())

    assert len(_FakeBridgeClient.posts) == 2, _FakeBridgeClient.posts
    assert _FakeBridgeClient.posts[0][1]["rth_open"] == "17:00:00"
    assert _FakeBridgeClient.posts[1][1]["rth_open"] == "17:00:15"


def test_sync_bridge_config_caches_when_seconds_unchanged(
        monkeypatch, fresh_bridge_state):
    """Two consecutive calls with same HH:MM:SS → only one POST."""
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 15))
    dashboard._sync_bridge_config(object())
    dashboard._sync_bridge_config(object())
    assert len(_FakeBridgeClient.posts) == 1, _FakeBridgeClient.posts


# ─── 3. Conviction anchor remains second-accurate ───────────────────────────

def test_conviction_anchor_carries_seconds(monkeypatch):
    """compute_session_conviction's anchorIso must include :15 when the OR
    config publishes startSecond=15."""
    dashboard._CONVICTION_STATE.clear()
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 15))
    snap = {
        "health": "ok",
        "alias":  "NQM6.CME@RITHMIC",
        "book":   {"mid": 17000.0, "bestBid": 16999.75, "bestAsk": 17000.25},
    }
    out = dashboard.compute_session_conviction(snap)
    assert out is not None
    # anchorIso is RFC-3339 / ISO-8601 with seconds field.
    assert "T17:00:15" in out["anchorIso"], out["anchorIso"]
    # anchorMs must align with second=15 (not :00). Pin to-the-millisecond.
    anchor_dt = dt.datetime.fromisoformat(out["anchorIso"])
    assert anchor_dt.second == 15
    # And the HHMM convenience field carries seconds-truncated text only
    # (legacy 5-char display), so test it does NOT promise :00 when there's a :15.
    # We do not constrain its exact value here — the canonical source is anchorIso.
    assert out["anchorHHMM"] == "17:00"


# ─── 4. _conv_session_anchor honors seconds directly ────────────────────────

def test_conv_session_anchor_today_includes_seconds(monkeypatch):
    monkeypatch.setattr(or_session, "effective_session_anchor",
                        lambda *a, **kw: _fake_anchor(17, 0, 15))
    now = dt.datetime(2026, 5, 19, 19, 0, tzinfo=CT)
    anchor_ms, anchor_dt, meta = dashboard._conv_session_anchor(now)
    expected = dt.datetime(2026, 5, 19, 17, 0, 15, tzinfo=CT)
    assert anchor_dt == expected, anchor_dt
    assert anchor_ms == int(expected.timestamp() * 1000)
