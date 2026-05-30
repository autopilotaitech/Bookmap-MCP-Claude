"""Data-truth / freshness helpers for the read-only overview UI.

Pure functions. No I/O, no global mutable state. Every overview endpoint
that surfaces trading / agent / SIM data wraps its payload with an
``envelope`` so the UI can never present stale state as live: the caller
declares the source, where it came from, when it was last updated, and the
staleness threshold, and the envelope computes age + is_stale + a
human-readable stale_reason.

Thresholds are configurable via environment variables (see
``stale_threshold_sec``) so an operator can tighten/loosen without a code
change. Defaults are deliberately conservative.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

# Default staleness thresholds in seconds, keyed by logical source.
# A source older than its threshold is reported is_stale=True. These are
# the "this looks live" budgets; tune per source.
_DEFAULT_THRESHOLDS: Dict[str, float] = {
    "heartbeat": 30.0,      # autopilot writes ~every 15s; 30s => missed beats
    "sim_db": 120.0,        # SIM order/fill DB; only changes on trade events
    "market": 15.0,         # journal snapshot age; live market should be < 15s
    "journal": 120.0,       # journal run/health metadata
    "learn_file": 86_400.0,  # scorecard/policy/calibration: historical is OK
}

# Sources whose data is legitimately historical (a stale learn artifact is
# not a lie, it just has not been recomputed). These are labelled
# historical=True instead of treated as a freshness failure in aggregate
# health, but is_stale is still computed honestly.
_HISTORICAL_SOURCES = {"learn_file"}

_ENV_PREFIX = "PAX_STALE_SEC_"


def now_ms() -> int:
    """Wall-clock milliseconds. Single choke point so tests can monkeypatch."""
    return int(time.time() * 1000)


def stale_threshold_sec(source: str) -> float:
    """Resolve the staleness threshold for a source.

    Environment override wins: ``PAX_STALE_SEC_HEARTBEAT=10`` overrides the
    heartbeat default. Garbage values fall back to the default.
    """
    env_key = _ENV_PREFIX + source.upper()
    raw = os.environ.get(env_key)
    if raw is not None:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return _DEFAULT_THRESHOLDS.get(source, 60.0)


def staleness(updated_at_ms: Optional[int],
              source: str,
              now: Optional[int] = None) -> Dict[str, Any]:
    """Compute age / is_stale / stale_reason for a single timestamp.

    ``updated_at_ms`` of None or 0 means "never seen" -> is_stale=True,
    age_sec=None, stale_reason="never_updated".
    """
    threshold = stale_threshold_sec(source)
    cur = now_ms() if now is None else now
    if not updated_at_ms:
        return {
            "updated_at": None,
            "age_sec": None,
            "is_stale": True,
            "stale_reason": "never_updated",
            "threshold_sec": threshold,
        }
    age_sec = round((cur - int(updated_at_ms)) / 1000.0, 3)
    is_stale = age_sec > threshold
    reason: Optional[str] = None
    if is_stale:
        reason = f"age {age_sec:.0f}s exceeds {threshold:.0f}s budget for {source}"
    elif age_sec < 0:
        # Clock skew / playback time ahead of wall clock; not stale but flag it.
        reason = "timestamp_ahead_of_wall_clock"
    return {
        "updated_at": int(updated_at_ms),
        "age_sec": age_sec,
        "is_stale": is_stale,
        "stale_reason": reason,
        "threshold_sec": threshold,
    }


def envelope(data: Any,
             *,
             source: str,
             source_path: Optional[str] = None,
             updated_at_ms: Optional[int] = None,
             mode: Optional[str] = None,
             now: Optional[int] = None,
             items_key: str = "items",
             extra_meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Wrap a payload with a freshness/source envelope.

    For a dict payload the envelope keys are merged in under a single
    ``_meta`` key so existing consumers keep reading their fields. For a
    list payload the list is placed under ``items_key`` alongside ``_meta``
    (callers/JS must read ``.items``). ``None`` is treated as an empty dict
    body but still carries ``_meta``.
    """
    fresh = staleness(updated_at_ms, source, now=now)
    historical = source in _HISTORICAL_SOURCES
    meta: Dict[str, Any] = {
        "source": source,
        "source_path": source_path,
        "mode": mode,
        "historical": historical,
        **fresh,
    }
    if extra_meta:
        meta.update(extra_meta)

    if isinstance(data, list):
        return {items_key: data, "_meta": meta}
    if data is None:
        return {"_meta": meta}
    if isinstance(data, dict):
        out = dict(data)
        out["_meta"] = meta
        return out
    # Scalar / other: nest under "value".
    return {"value": data, "_meta": meta}


def worst_of(*metas: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate several ``_meta`` blocks into a single worst-case summary
    for a health endpoint. Non-historical stale sources dominate.

    Returns {is_stale, stale_sources: [source...], any_never_updated}.
    """
    stale_sources = []
    any_never = False
    for m in metas:
        if not isinstance(m, dict):
            continue
        if m.get("stale_reason") == "never_updated":
            any_never = True
        if m.get("is_stale") and not m.get("historical"):
            stale_sources.append(m.get("source"))
    return {
        "is_stale": bool(stale_sources),
        "stale_sources": stale_sources,
        "any_never_updated": any_never,
    }
