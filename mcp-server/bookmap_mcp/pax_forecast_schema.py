"""Structured Pax AI forecast schema.

This module is intentionally small and deterministic. It validates the
forecast object that Pax Live Claude may emit, and returns a normalized dict
that offline calibration/replay code can score later.

It does not call Claude, broker APIs, settings writers, or live-order tools.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = 1

VALID_DIRECTIONS = {"LONG", "SHORT", "NONE"}
VALID_LABELS = {"OR-H", "OR-L", "+1", "+2", "+3", "-1", "-2", "-3"}
VALID_EXECUTION_READS = {
    "PAY_FOR_TRADE",
    "WAIT_FOR_CONFIRM",
    "STAND_DOWN",
    "SCRATCH_READY",
}
VALID_THESIS_PREFIXES = (
    "ACCEPTANCE_",
    "REJECTION_",
    "ABSORPTION_",
    "ICEBERG_",
    "STOP_SWEEP_",
    "NONE",
)

REQUIRED_FIELDS = (
    "alias",
    "level",
    "thesis",
    "execution_read",
    "direction",
    "horizon_sec",
    "prob_success",
    "expected_r",
    "invalidation",
    "features_used",
)


def validate_forecast(raw: Any,
                      *,
                      ts_ms: Optional[int] = None,
                      source_turn_id: Optional[int] = None) -> Dict[str, Any]:
    """Validate and normalize one Pax AI forecast.

    Raises ValueError with a stable message on bad input. Callers that need a
    soft failure should use ``coerce_forecast``.
    """
    if not isinstance(raw, dict):
        raise ValueError("forecast must be an object")
    missing = [k for k in REQUIRED_FIELDS if k not in raw]
    if missing:
        raise ValueError("missing required fields: " + ", ".join(missing))

    alias = _non_empty_str(raw["alias"], "alias")
    level = _non_empty_str(raw["level"], "level")
    if level not in VALID_LABELS:
        raise ValueError(f"invalid level: {level}")

    thesis = _non_empty_str(raw["thesis"], "thesis")
    if not any(thesis == p or thesis.startswith(p) for p in VALID_THESIS_PREFIXES):
        raise ValueError(f"invalid thesis: {thesis}")

    execution_read = _non_empty_str(raw["execution_read"], "execution_read")
    if execution_read not in VALID_EXECUTION_READS:
        raise ValueError(f"invalid execution_read: {execution_read}")

    direction = _non_empty_str(raw["direction"], "direction")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"invalid direction: {direction}")
    if execution_read == "PAY_FOR_TRADE" and direction not in {"LONG", "SHORT"}:
        raise ValueError("PAY_FOR_TRADE requires LONG or SHORT direction")
    if execution_read != "PAY_FOR_TRADE" and direction != "NONE":
        raise ValueError(f"{execution_read} requires NONE direction")

    horizon_sec = _int_in_range(raw["horizon_sec"], "horizon_sec", 1, 86_400)
    prob_success = _float_in_range(raw["prob_success"], "prob_success", 0.0, 1.0)
    expected_r = _float_in_range(raw["expected_r"], "expected_r", -10.0, 10.0)
    invalidation = _non_empty_str(raw["invalidation"], "invalidation")
    features_used = _features(raw["features_used"])

    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "forecast_id": str(raw.get("forecast_id") or ""),
        "source_turn_id": source_turn_id if source_turn_id is not None else raw.get("source_turn_id"),
        "ts_ms": ts_ms if ts_ms is not None else raw.get("ts_ms"),
        "alias": alias,
        "level": level,
        "thesis": thesis,
        "execution_read": execution_read,
        "direction": direction,
        "horizon_sec": horizon_sec,
        "prob_success": round(prob_success, 6),
        "expected_r": round(expected_r, 6),
        "invalidation": invalidation,
        "features_used": features_used,
    }
    if not out["forecast_id"]:
        out["forecast_id"] = _forecast_id(out)
    return out


def coerce_forecast(raw: Any,
                    *,
                    ts_ms: Optional[int] = None,
                    source_turn_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Soft wrapper around ``validate_forecast``."""
    try:
        return validate_forecast(raw, ts_ms=ts_ms, source_turn_id=source_turn_id)
    except ValueError:
        return None


def forecast_setup_bucket(forecast: Dict[str, Any]) -> str:
    """Stable bucket key for calibration/replay grouping."""
    return "|".join([
        str(forecast.get("level") or "?"),
        str(forecast.get("thesis") or "?"),
        str(forecast.get("execution_read") or "?"),
        str(forecast.get("direction") or "?"),
        str(forecast.get("horizon_sec") or "?"),
    ])


def probability_bucket(prob_success: Any, width: float = 0.05) -> str:
    """Return a calibration bucket label such as ``0.60-0.65``."""
    p = _float_in_range(prob_success, "prob_success", 0.0, 1.0)
    if width <= 0.0 or width > 1.0:
        raise ValueError("width must be in (0, 1]")
    idx = int(p / width)
    lo = min(1.0, idx * width)
    hi = min(1.0, lo + width)
    if p == 1.0:
        lo = max(0.0, 1.0 - width)
        hi = 1.0
    return f"{lo:.2f}-{hi:.2f}"


def _forecast_id(forecast: Dict[str, Any]) -> str:
    seed = {
        k: forecast.get(k)
        for k in (
            "source_turn_id", "ts_ms", "alias", "level", "thesis",
            "execution_read", "direction", "horizon_sec", "prob_success",
            "expected_r",
        )
    }
    body = json.dumps(seed, sort_keys=True, separators=(",", ":"), default=str)
    return "pax_fcst|" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


def _non_empty_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _float_in_range(value: Any, name: str, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be numeric")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} must be in [{lo}, {hi}]")
    return v


def _int_in_range(value: Any, name: str, lo: int, hi: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be an integer")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} must be in [{lo}, {hi}]")
    return v


def _features(value: Any) -> List[str]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, dict)):
        raise ValueError("features_used must be a list of strings")
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("features_used must be a list of strings")
        s = item.strip()
        if s not in out:
            out.append(s)
    if not out:
        raise ValueError("features_used must not be empty")
    return out
