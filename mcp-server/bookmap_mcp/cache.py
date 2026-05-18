"""Unified cache for the Bookmap MCP system — three layers, single API.

  L0  in-memory LRU per process    ─  hot path, ~10us lookups, capped at 1000
  L1  SQLite on D:\\BookmapLogs\\pax-cache.db  ─  persistent, TTL'd
  L2  (downstream) Anthropic prompt-cache  ─  applied by claude_cli.py via
                                              cache_control markers on the
                                              stable system prompt

Used by:
  - dashboard.compute_session_conviction (bucketed conviction reads)
  - pax_decision (decision-context fingerprint cache)
  - pax_replay (expensive group-aggregations)
  - claude_cli (system prompt + decision signature)
  - any future tool

Quick API:
  from bookmap_mcp.cache import cached, cache_get, cache_set, cache_stats

  @cached(ttl_sec=30, key_fields=("regime", "level"))
  def hard_call(snap, regime, level): ...

  cache_get("foo")                 # returns None on miss
  cache_set("foo", value, 3600)    # ttl in seconds
  cache_stats()                    # dict of hit/miss counters
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

CACHE_DIR  = Path(os.environ.get("PAX_LOG_DIR", r"D:\BookmapLogs"))
CACHE_DB   = CACHE_DIR / "pax-cache.db"
L0_MAX     = 1000
DEFAULT_TTL_SEC = 60.0


# ─────────────────────────────────────────────────────────────────────────────
# L0 — In-memory LRU with TTL (per-process, thread-safe)
# ─────────────────────────────────────────────────────────────────────────────

class _L0:
    def __init__(self, maxsize: int = L0_MAX) -> None:
        self._lock = threading.Lock()
        self._d: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self.maxsize = maxsize
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._d.get(key)
            if entry is None:
                self.misses += 1; return None
            expiry, val = entry
            if expiry <= time.time():
                del self._d[key]
                self.misses += 1; return None
            # LRU touch
            self._d.move_to_end(key)
            self.hits += 1
            return val

    def set(self, key: str, value: Any, ttl_sec: float) -> None:
        with self._lock:
            self._d[key] = (time.time() + ttl_sec, value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)
                self.evictions += 1

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


_L0_INST = _L0(L0_MAX)


# ─────────────────────────────────────────────────────────────────────────────
# L1 — SQLite persistent cache
# ─────────────────────────────────────────────────────────────────────────────

class _L1:
    _lock = threading.Lock()
    _conn: Optional[sqlite3.Connection] = None
    hits = 0
    misses = 0
    writes = 0
    errors = 0

    @classmethod
    def _connect(cls) -> Optional[sqlite3.Connection]:
        if cls._conn is not None: return cls._conn
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(CACHE_DB), check_same_thread=False, timeout=2.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                  k       TEXT PRIMARY KEY,
                  v       TEXT NOT NULL,
                  expiry  REAL NOT NULL,
                  written REAL NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_expiry ON kv(expiry)")
            conn.commit()
            cls._conn = conn
            return conn
        except (sqlite3.Error, OSError) as e:
            cls.errors += 1
            import sys; sys.stderr.write(f"[cache] L1 connect failed: {e}\n")
            return None

    @classmethod
    def get(cls, key: str) -> Optional[Any]:
        v, _ttl = cls.get_with_ttl(key)
        return v

    @classmethod
    def get_with_ttl(cls, key: str) -> Tuple[Optional[Any], float]:
        """Like get(), but also returns the remaining TTL (seconds) so callers
        can promote into L0 without resetting expiry. Returns (None, 0.0) on
        miss, expired, or error."""
        conn = cls._connect()
        if conn is None: cls.misses += 1; return None, 0.0
        try:
            with cls._lock:
                row = conn.execute("SELECT v, expiry FROM kv WHERE k=?", (key,)).fetchone()
            if row is None: cls.misses += 1; return None, 0.0
            v, expiry = row
            now = time.time()
            if expiry <= now:
                with cls._lock:
                    conn.execute("DELETE FROM kv WHERE k=?", (key,)); conn.commit()
                cls.misses += 1; return None, 0.0
            cls.hits += 1
            return json.loads(v), expiry - now
        except (sqlite3.Error, json.JSONDecodeError):
            cls.errors += 1
            return None, 0.0

    @classmethod
    def set(cls, key: str, value: Any, ttl_sec: float) -> None:
        conn = cls._connect()
        if conn is None: return
        try:
            payload = json.dumps(value, default=str)
            with cls._lock:
                conn.execute(
                    "INSERT INTO kv(k,v,expiry,written) VALUES(?,?,?,?) "
                    "ON CONFLICT(k) DO UPDATE SET v=excluded.v, expiry=excluded.expiry, written=excluded.written",
                    (key, payload, time.time() + ttl_sec, time.time()))
                conn.commit()
            cls.writes += 1
        except (sqlite3.Error, TypeError) as e:
            cls.errors += 1

    @classmethod
    def vacuum_expired(cls) -> int:
        """Delete expired rows. Returns count removed. Run periodically."""
        conn = cls._connect()
        if conn is None: return 0
        try:
            with cls._lock:
                cur = conn.execute("DELETE FROM kv WHERE expiry <= ?", (time.time(),))
                conn.commit()
            return cur.rowcount or 0
        except sqlite3.Error:
            return 0

    @classmethod
    def invalidate(cls, key: str) -> None:
        conn = cls._connect()
        if conn is None: return
        try:
            with cls._lock:
                conn.execute("DELETE FROM kv WHERE k=?", (key,)); conn.commit()
        except sqlite3.Error:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

_L0_PROMOTE_MIN_TTL_SEC = 0.05  # don't bother promoting near-expired rows


def cache_get(key: str) -> Optional[Any]:
    """Look up `key` in L0 then L1. Promotes L1 hits into L0, preserving the
    persisted expiry — a row written with a 5s TTL must not live for the
    DEFAULT_TTL_SEC just because it was rehydrated through L0."""
    v = _L0_INST.get(key)
    if v is not None: return v
    v, remaining = _L1.get_with_ttl(key)
    if v is not None and remaining > _L0_PROMOTE_MIN_TTL_SEC:
        _L0_INST.set(key, v, remaining)
    return v


def cache_set(key: str, value: Any, ttl_sec: float = DEFAULT_TTL_SEC,
              persist: bool = True) -> None:
    """Write to L0; optionally also persist to L1 (default True)."""
    _L0_INST.set(key, value, ttl_sec)
    if persist:
        _L1.set(key, value, ttl_sec)


def cache_invalidate(key: str) -> None:
    _L0_INST.invalidate(key); _L1.invalidate(key)


def cache_stats() -> Dict[str, Any]:
    return {
        "L0": {"hits": _L0_INST.hits, "misses": _L0_INST.misses,
               "evictions": _L0_INST.evictions, "size": len(_L0_INST._d),
               "hit_rate": _L0_INST.hits / max(1, _L0_INST.hits + _L0_INST.misses)},
        "L1": {"hits": _L1.hits, "misses": _L1.misses,
               "writes": _L1.writes, "errors": _L1.errors,
               "hit_rate": _L1.hits / max(1, _L1.hits + _L1.misses),
               "db_path": str(CACHE_DB)},
    }


def make_key(prefix: str, *parts: Any) -> str:
    """Canonical key builder — JSON-serialize then SHA256 the parts so that
    floating-point conviction values don't blow up cardinality. Use bucket()
    on numeric inputs first if you want near-identical hits to coalesce.
    """
    body = json.dumps([prefix, list(parts)], default=str, sort_keys=True)
    return f"{prefix}:{hashlib.sha256(body.encode()).hexdigest()[:16]}"


def bucket(x: Optional[float], step: float = 0.10) -> Optional[float]:
    """Snap a float to a bucket so near-identical values coalesce in the cache."""
    if x is None: return None
    try: return round(float(x) / step) * step
    except (TypeError, ValueError): return None


def cached(ttl_sec: float = DEFAULT_TTL_SEC,
           key_fields: Optional[Tuple[str, ...]] = None,
           bucket_fields: Optional[Dict[str, float]] = None,
           prefix: Optional[str] = None,
           persist: bool = True):
    """Decorator that caches function results.

    Cache key = sha256( prefix + filtered/bucketed kwargs + args ).

    Args:
      ttl_sec:        TTL for both L0 and L1
      key_fields:     If set, only these kwargs contribute to the key
      bucket_fields:  {field_name: bucket_step} to snap numerics
      prefix:         Override the default prefix (defaults to fn.__qualname__)
      persist:        Also write to L1 SQLite (default True)
    """
    def wrapper(fn: Callable) -> Callable:
        fn_prefix = prefix or fn.__qualname__

        @functools.wraps(fn)
        def inner(*args, **kwargs):
            parts: list = list(args)
            if key_fields is not None:
                kw_parts = []
                for k in key_fields:
                    v = kwargs.get(k)
                    if bucket_fields and k in bucket_fields:
                        v = bucket(v, bucket_fields[k])
                    kw_parts.append((k, v))
                parts.append(kw_parts)
            else:
                # Use sorted kwargs, applying bucketing where requested
                kw_parts = []
                for k in sorted(kwargs.keys()):
                    v = kwargs[k]
                    if bucket_fields and k in bucket_fields:
                        v = bucket(v, bucket_fields[k])
                    kw_parts.append((k, v))
                parts.append(kw_parts)
            key = make_key(fn_prefix, *parts)

            hit = cache_get(key)
            if hit is not None: return hit
            result = fn(*args, **kwargs)
            cache_set(key, result, ttl_sec, persist=persist)
            return result
        inner._cache_key_prefix = fn_prefix
        return inner
    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance
# ─────────────────────────────────────────────────────────────────────────────

_last_vacuum = 0.0
def maintenance_tick(min_interval_sec: float = 300.0) -> Dict[str, Any]:
    """Call from the dashboard's poll loop. Vacuums expired L1 rows on a slow
    cadence (default 5 min). Returns stats."""
    global _last_vacuum
    now = time.time()
    expired = 0
    if now - _last_vacuum > min_interval_sec:
        expired = _L1.vacuum_expired()
        _last_vacuum = now
    return {"vacuumed": expired, **cache_stats()}
