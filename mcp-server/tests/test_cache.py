"""Tests for the L0/L1 cache promotion logic.

Focus: an L1 entry with a short remaining TTL must NOT live for
DEFAULT_TTL_SEC in L0 just because it got rehydrated through a cache_get().
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest


@pytest.fixture
def fresh_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Force the cache to use a tmp SQLite DB and reset all in-process state."""
    from bookmap_mcp import cache as cache_mod

    db_path = tmp_path / "pax-cache.db"
    monkeypatch.setattr(cache_mod, "CACHE_DIR", tmp_path, raising=True)
    monkeypatch.setattr(cache_mod, "CACHE_DB", db_path, raising=True)

    # Reset L0
    cache_mod._L0_INST.clear()
    cache_mod._L0_INST.hits = 0
    cache_mod._L0_INST.misses = 0
    cache_mod._L0_INST.evictions = 0

    # Reset L1: drop any cached connection so the new CACHE_DB path is used.
    if cache_mod._L1._conn is not None:
        try:
            cache_mod._L1._conn.close()
        except Exception:
            pass
        cache_mod._L1._conn = None
    cache_mod._L1.hits = 0
    cache_mod._L1.misses = 0
    cache_mod._L1.writes = 0
    cache_mod._L1.errors = 0
    return cache_mod


def test_l1_promote_uses_remaining_ttl_not_default(fresh_cache, monkeypatch):
    """L0 promotion must inherit the L1 row's remaining expiry, not stretch it
    out to DEFAULT_TTL_SEC. Otherwise a 5-second persisted entry would live a
    full minute in L0 after a single read."""
    cache_mod = fresh_cache

    # Persist to L1 only (so cache_get must promote from L1 -> L0).
    short_ttl = 0.5
    cache_mod._L1.set("k", {"v": 1}, short_ttl)

    # First read promotes from L1 to L0.
    assert cache_mod.cache_get("k") == {"v": 1}

    # Confirm it landed in L0 and that the L0 expiry is bounded by
    # remaining L1 TTL, NOT DEFAULT_TTL_SEC.
    entry = cache_mod._L0_INST._d.get("k")
    assert entry is not None, "promotion should have populated L0"
    expiry, _val = entry
    remaining = expiry - time.time()
    assert remaining <= short_ttl + 0.05, (
        f"L0 row TTL stretched to {remaining:.2f}s, expected <= {short_ttl:.2f}s — "
        "promotion is using DEFAULT_TTL_SEC instead of L1 remaining TTL")
    assert remaining < cache_mod.DEFAULT_TTL_SEC, (
        f"L0 row TTL {remaining:.2f}s reached DEFAULT_TTL_SEC ({cache_mod.DEFAULT_TTL_SEC:.2f}s) "
        "— bug regressed")


def test_l1_promotion_expires_at_persisted_time(fresh_cache):
    """After the L1 TTL elapses, the promoted L0 entry must be gone too."""
    cache_mod = fresh_cache
    cache_mod._L1.set("k", "hello", 0.1)
    assert cache_mod.cache_get("k") == "hello"
    time.sleep(0.2)
    # L0 entry should now be expired and treated as a miss.
    assert cache_mod._L0_INST.get("k") is None


def test_l1_near_expired_entry_not_promoted(fresh_cache):
    """An L1 row with effectively zero remaining TTL shouldn't be promoted
    into L0 (would only flap)."""
    cache_mod = fresh_cache
    # Inject directly with an expiry already in the past + epsilon so
    # get_with_ttl reports 0.0 remaining.
    conn = cache_mod._L1._connect()
    assert conn is not None
    import json as _json
    conn.execute(
        "INSERT INTO kv(k,v,expiry,written) VALUES(?,?,?,?) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, expiry=excluded.expiry, written=excluded.written",
        ("k", _json.dumps("v"), time.time() - 1.0, time.time()),
    )
    conn.commit()
    assert cache_mod.cache_get("k") is None
    assert "k" not in cache_mod._L0_INST._d
