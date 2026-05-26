"""Extract + validate a structured PAX_FORECAST block from a Pax AI response.

Mirrors ``ai_chart_signal.py`` in shape. The two blocks coexist in one
response: the chart signal is hard-fenced by ``<<PAX_AI_CHART_SIGNAL>>``
... ``<<END>>`` and the forecast by ``<<PAX_FORECAST>>`` ... ``<<END_FORECAST>>``.
Distinct end markers keep the regexes from overlapping.

Failure modes are silent. ``extract_block`` returns ``None`` on every
"this is not a usable forecast" condition; the chat path is forbidden
from raising on a malformed forecast.

``validate_against_snapshot`` additionally anchors the block against the
snapshot the digest was built from (alias must match, ``level`` must
exist in ``snap['or_levels']['levels']``). When the mcp-server side is
installed, the result is also funneled through
``bookmap_mcp.pax_forecast_schema.validate_forecast`` so the structured
record persisted by the chat path is byte-identical to what offline
calibration / replay expects.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, Iterable, List, Optional


_PATTERN = re.compile(
    r"<<PAX_FORECAST>>\s*(\{.*?\})\s*<<END_FORECAST>>",
    re.DOTALL,
)


_REQUIRED = (
    "alias", "level", "thesis", "execution_read", "direction",
    "horizon_sec", "prob_success", "expected_r", "invalidation",
    "features_used",
)
_LEVELS = {"OR-H", "OR-L", "+1", "+2", "+3", "-1", "-2", "-3"}
_EXECUTION_READS = {"PAY_FOR_TRADE", "WAIT_FOR_CONFIRM", "STAND_DOWN",
                    "SCRATCH_READY"}
_DIRECTIONS = {"LONG", "SHORT", "NONE"}
_THESIS_PREFIXES = ("ACCEPTANCE_", "REJECTION_", "ABSORPTION_", "ICEBERG_",
                    "STOP_SWEEP_", "NONE")

_HORIZON_MIN = 1
_HORIZON_MAX = 86_400
_PROB_MIN = 0.0
_PROB_MAX = 1.0
_EXPECTED_R_MIN = -10.0
_EXPECTED_R_MAX = 10.0


def _combo_ok(execution_read: Any, direction: Any) -> bool:
    if execution_read == "PAY_FOR_TRADE":
        return direction in ("LONG", "SHORT")
    if execution_read in {"WAIT_FOR_CONFIRM", "STAND_DOWN", "SCRATCH_READY"}:
        return direction == "NONE"
    return False


def _features_ok(value: Any) -> Optional[List[str]]:
    if not isinstance(value, list) or not value:
        return None
    out: List[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        s = item.strip()
        if s not in out:
            out.append(s)
    if not out:
        return None
    return out


def _thesis_ok(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if not any(s == p or s.startswith(p) for p in _THESIS_PREFIXES):
        return None
    return s


def extract_block(pax_text: Optional[str]) -> Optional[Dict[str, Any]]:
    """Return the last well-formed forecast block in ``pax_text``, or None.

    No exception is propagated; everything that fails parse / type /
    range / combo / enum returns ``None``.
    """
    if not pax_text or not isinstance(pax_text, str):
        return None
    matches = list(_PATTERN.finditer(pax_text))
    for m in reversed(matches):
        try:
            blk = json.loads(m.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(blk, dict):
            continue
        if not all(k in blk for k in _REQUIRED):
            continue

        alias = blk.get("alias")
        if not isinstance(alias, str) or not alias.strip():
            continue
        level = blk.get("level")
        if level not in _LEVELS:
            continue
        thesis = _thesis_ok(blk.get("thesis"))
        if thesis is None:
            continue
        execution_read = blk.get("execution_read")
        if execution_read not in _EXECUTION_READS:
            continue
        direction = blk.get("direction")
        if direction not in _DIRECTIONS:
            continue
        if not _combo_ok(execution_read, direction):
            continue

        try:
            horizon = int(blk["horizon_sec"])
            prob = float(blk["prob_success"])
            expected_r = float(blk["expected_r"])
        except (ValueError, TypeError):
            continue
        if not (_HORIZON_MIN <= horizon <= _HORIZON_MAX):
            continue
        if not (_PROB_MIN <= prob <= _PROB_MAX):
            continue
        if not (_EXPECTED_R_MIN <= expected_r <= _EXPECTED_R_MAX):
            continue

        invalidation = blk.get("invalidation")
        if not isinstance(invalidation, str) or not invalidation.strip():
            continue
        features = _features_ok(blk.get("features_used"))
        if features is None:
            continue

        return {
            "alias":          alias.strip(),
            "level":          level,
            "thesis":         thesis,
            "execution_read": execution_read,
            "direction":      direction,
            "horizon_sec":    horizon,
            "prob_success":   prob,
            "expected_r":     expected_r,
            "invalidation":   invalidation.strip(),
            "features_used": features,
        }
    return None


def validate_against_snapshot(
    blk: Any,
    snap: Optional[Dict[str, Any]],
    *,
    ts_ms: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Anchor an extracted forecast against the snapshot and normalize it.

    Snapshot grounding:
    - snap['health'] must be 'ok'
    - blk['alias'] must equal snap['alias']
    - blk['level'] must appear in snap['or_levels']['levels'][*]['label']
    - combo rules must hold (defense in depth)

    When ``bookmap_mcp.pax_forecast_schema`` is importable the result is
    a ``schema.validate_forecast`` dict (carries a deterministic
    ``forecast_id`` and ``schema_version``). Otherwise we return a local
    fallback record so the chat path keeps a usable forecast in memory
    even when the mcp-server side is not installed.
    """
    if not isinstance(blk, dict) or not isinstance(snap, dict):
        return None
    if snap.get("health") != "ok":
        return None
    if blk.get("alias") != snap.get("alias"):
        return None

    or_levels = snap.get("or_levels") or {}
    if not isinstance(or_levels, dict):
        return None
    levels = or_levels.get("levels") or []
    if not isinstance(levels, list):
        return None
    target_label = blk.get("level")
    found = any(isinstance(L, dict) and L.get("label") == target_label
                for L in levels)
    if not found:
        return None

    if not _combo_ok(blk.get("execution_read"), blk.get("direction")):
        return None

    captured_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
    try:
        from bookmap_mcp import pax_forecast_schema as schema  # type: ignore
        validated = schema.validate_forecast(blk, ts_ms=captured_ms)
        return validated
    except Exception:
        # No mcp-server install: fall back to a local minimal record.
        out = dict(blk)
        out["schema_version"] = 1
        out["ts_ms"] = captured_ms
        out["forecast_id"] = _fallback_forecast_id(out)
        return out


def _fallback_forecast_id(forecast: Dict[str, Any]) -> str:
    seed = {
        k: forecast.get(k)
        for k in (
            "ts_ms", "alias", "level", "thesis", "execution_read",
            "direction", "horizon_sec", "prob_success", "expected_r",
        )
    }
    body = json.dumps(seed, sort_keys=True, separators=(",", ":"),
                      default=str)
    return "pax_fcst|" + hashlib.sha1(body.encode("utf-8")).hexdigest()[:16]


__all__ = ["extract_block", "validate_against_snapshot"]
