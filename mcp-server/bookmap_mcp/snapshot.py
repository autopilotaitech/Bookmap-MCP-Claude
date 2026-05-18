"""Normalized snapshot schema — required fields, optional fields, validator.

Every adapter MUST emit a dict matching this contract. The validator is
cheap; daemons can call it on every snapshot in DEBUG mode and sample at
1% in PROD to catch drift without burning CPU.
"""

from __future__ import annotations

from typing import Any, Dict, List


# Required keys — every adapter must populate. Missing → validation error.
REQUIRED_KEYS = ("alias", "health", "book", "trades", "_source")

# Optional keys — signal-engine sources return reliability=0 when absent,
# so missing is non-fatal but worth documenting.
OPTIONAL_KEYS = (
    "ts",            # ISO-8601 timestamp
    "or_row",        # {orHigh, orLow, ...}
    "vwap_obj",      # {vwap, stddev, lastTradePrice, ...}
    "flow",          # {regime, biasScore, biasTrajectory, ofiZ, cvdDeltaZ, ...}
    "volume_profile",
    "tape_buckets",
    "pull_stack",
    "lt_liquidity",
    "micro_events",
    "gates",         # {session, news, vwap_or}
    "position",
    "working",
    "balance",
    "fills",
    "_synthetic",    # list of keys whose values were derived rather than observed
)


def validate_snapshot(snap: Any) -> List[str]:
    """Return list of validation errors. Empty list = valid."""
    errors: List[str] = []
    if not isinstance(snap, dict):
        return [f"snap must be dict, got {type(snap).__name__}"]
    for k in REQUIRED_KEYS:
        if k not in snap:
            errors.append(f"missing required key: {k}")
    if "book" in snap and snap["book"] is not None:
        b = snap["book"]
        if not isinstance(b, dict):
            errors.append(f"book must be dict or None, got {type(b).__name__}")
        else:
            # mid is the one field downstream signal helpers really depend on.
            if "mid" not in b:
                errors.append("book.mid missing")
    if "trades" in snap and not isinstance(snap["trades"], (list, tuple)):
        errors.append(f"trades must be list, got {type(snap['trades']).__name__}")
    if "_source" in snap and not isinstance(snap["_source"], str):
        errors.append("_source must be string")
    if "_synthetic" in snap:
        s = snap["_synthetic"]
        if not isinstance(s, (list, tuple)):
            errors.append("_synthetic must be list of strings")
        elif not all(isinstance(x, str) for x in s):
            errors.append("_synthetic must contain only strings")
    return errors


def is_valid(snap: Any) -> bool:
    """True iff the snapshot has no schema errors."""
    return not validate_snapshot(snap)
