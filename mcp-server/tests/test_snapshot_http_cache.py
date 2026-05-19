from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bookmap_mcp.dashboard as d  # noqa: E402


def _clear_snapshot_cache() -> None:
    with d._SNAPSHOT_CACHE_LOCK:
        d._SNAPSHOT_CACHE = None


def test_cached_fetch_snapshot_reuses_fresh_snapshot(monkeypatch):
    _clear_snapshot_cache()
    calls = {"count": 0}

    def fake_fetch_snapshot():
        calls["count"] += 1
        return {"health": "ok", "seq": calls["count"]}

    monkeypatch.setattr(d, "fetch_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(d.time, "monotonic", lambda: 100.0)

    first = d.cached_fetch_snapshot()
    second = d.cached_fetch_snapshot()

    assert first is second
    assert first["seq"] == 1
    assert calls["count"] == 1
    _clear_snapshot_cache()


def test_cached_fetch_snapshot_refreshes_after_ttl(monkeypatch):
    _clear_snapshot_cache()
    calls = {"count": 0}
    now = {"value": 100.0}

    def fake_fetch_snapshot():
        calls["count"] += 1
        return {"health": "ok", "seq": calls["count"]}

    monkeypatch.setattr(d, "fetch_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(d.time, "monotonic", lambda: now["value"])

    first = d.cached_fetch_snapshot()
    now["value"] += d._SNAPSHOT_CACHE_TTL_SECS + 0.001
    second = d.cached_fetch_snapshot()

    assert first["seq"] == 1
    assert second["seq"] == 2
    assert calls["count"] == 2
    _clear_snapshot_cache()
