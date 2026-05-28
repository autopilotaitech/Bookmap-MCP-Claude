"""Deterministic rich chart-signal emitter.

Operator directive 2026-05-28: the rich chart popup style (down-arrow,
confidence, R-multiple, FADE/FOLLOW setup, components text, STOP price)
should be populated from MY deterministic system rather than LLM
emissions that hallucinate. Operator's words: "you are now in control
and only you."

This module appends rich chart-signal rows to
`D:/BookmapLogs/pax-ai-chart-signals.jsonl` whenever the
institutional_flow regime is firing at a level in proximity. The Java
OpenRange addon already reads that JSONL and renders the popup. Same
visual style, deterministic content.

A row is emitted at most once per 30-second bucket per
(alias, level, regime, direction) tuple so the JSONL doesn't churn.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

_JSONL_PATH = Path(r"D:\BookmapLogs\pax-ai-chart-signals.jsonl")
_EMIT_BUCKET_SEC = 30   # one rich row per 30s per (alias, level, regime, dir)
_PROXIMITY_REQUIRED = False   # 2026-05-28: relaxed -- when regime fires
                              # but no level is in strict proximity, emit
                              # at the NEAREST level so the rich popup
                              # still appears. Operator caught this gap
                              # at 11:59 CT (ACCUMULATION fired between
                              # +3 and +4, no rich popup because both
                              # levels were just past proximity threshold).
_NEAREST_LEVEL_MAX_DIST_PTS = 65.0  # within 1 NQ rotation of nearest


_STATE: Dict[str, Dict[str, Any]] = {}


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _now_ms() -> int:
    return int(time.time() * 1000)


def _get_state(alias: str) -> Dict[str, Any]:
    s = _STATE.get(alias)
    if s is None:
        s = {"last_emit_keys": {}}
        _STATE[alias] = s
    return s


def _setup_type(regime: str, side: str, mid: float, level_price: float) -> str:
    """Classify the setup as FADE / FOLLOW + direction.

    Decision matrix (regime x level side):
        LONG  + above-OR (resistance side): going with up-break  -> FOLLOW LONG
        LONG  + below-OR (support side):    defend the support   -> FADE LONG
        SHORT + above-OR (resistance side): fade at resistance   -> FADE SHORT
        SHORT + below-OR (support side):    going with down-break-> FOLLOW SHORT

    Approach direction (mid vs level_price) is captured for stop placement
    elsewhere but does not change the FADE/FOLLOW classification.
    """
    if regime == "ACCUMULATION":
        direction = "LONG"
    elif regime == "DISTRIBUTION":
        direction = "SHORT"
    else:
        return "WAIT"
    is_above = (side == "above")
    if direction == "LONG":
        return "FOLLOW LONG" if is_above else "FADE LONG"
    # SHORT
    return "FADE SHORT" if is_above else "FOLLOW SHORT"


def _compute_stop_price(
    regime: str, side: str, level_price: float, rotation_unit: float
) -> float:
    """Stop placement (Pax mechanics, operator-adjusted):

    FADE SHORT at +N ext: stop = level + 1 rung (next ext above)
    FADE LONG at -N ext:  stop = level - 1 rung (next ext below)
    FOLLOW LONG breaks above: stop = level (entry-scratch stop on OR-H)
    FOLLOW SHORT breaks below: stop = level (scratch stop on OR-L)
    """
    if regime == "DISTRIBUTION" and side == "above":
        # SHORT fading a high ext
        return level_price + rotation_unit
    if regime == "ACCUMULATION" and side == "below":
        # LONG fading a low ext
        return level_price - rotation_unit
    # FOLLOW: scratch at the entry level
    return level_price


def _format_reason(
    setup_type: str,
    regime: str,
    flow: Mapping[str, Any],
    stop_price: float,
    level_label: str,
    level_price: float,
    mid: float,
) -> str:
    """Multi-line rich text for the JSONL row. The Java painter
    renders the popup using this reason field."""
    drivers = flow.get("drivers") or []
    top_signed = []
    for d in drivers[:3]:
        if not isinstance(d, dict):
            continue
        nm = d.get("name") or ""
        sg = d.get("signed", 0.0)
        top_signed.append(f"{nm}{sg:+.2f}")
    drv_text = " + ".join(top_signed) if top_signed else "no_drivers"

    conv = float(flow.get("conviction") or 0.0)
    vote = float(flow.get("weighted_vote") or 0.0)
    trend = flow.get("trend_filter") or "NEUTRAL"
    rot_state = flow.get("rotation_state") or {}
    rotations = rot_state.get("rotations_completed") or 0

    parts = [
        f"{setup_type}",
        f"{drv_text}",
        f"STOP {stop_price:.2f}",
        f"conv={conv:.2f} vote={vote:+.2f} trend={trend} rot={rotations}",
        f"@ {level_label} {level_price:.2f}  mid {mid:.2f}",
    ]
    return " | ".join(parts)[:240]


def _emit_key(alias: str, level_label: str, regime: str, direction: str, bucket_ms: int) -> str:
    return f"{alias}|{level_label}|{regime}|{direction}|{bucket_ms}"


def _signal_id(alias: str, level_label: str, regime: str, bucket_ms: int) -> str:
    h = hashlib.sha1(f"{alias}:{level_label}:{regime}:{bucket_ms}".encode("utf-8")).hexdigest()[:16]
    return f"ifl|{h}"


def _append_jsonl(row: Mapping[str, Any]) -> None:
    path = _JSONL_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, separators=(",", ":"))
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        sys.stderr.write(f"[ifl_rich_signals] append failed: "
                         f"{type(exc).__name__}: {exc}\n")


def emit_rich_signals(snap: Mapping[str, Any], alias_arg: Optional[str] = None) -> Dict[str, Any]:
    """Per-tick check. When institutional_flow regime is directional AND a
    level is in proximity, emit a rich JSONL row. Returns a small status
    dict for tooling.
    """
    flow = snap.get("institutional_flow") or {}
    if not isinstance(flow, dict) or flow.get("_error"):
        return {"emitted": 0, "reason": "no_flow"}
    regime = (flow.get("regime") or "").upper()
    if regime not in ("ACCUMULATION", "DISTRIBUTION"):
        return {"emitted": 0, "reason": "non_directional"}

    alias = alias_arg or flow.get("alias") or snap.get("alias") or "unknown"
    state = _get_state(alias)
    now_ms = int(flow.get("asOfMs") or _now_ms())
    bucket_ms = (now_ms // (_EMIT_BUCKET_SEC * 1000)) * (_EMIT_BUCKET_SEC * 1000)

    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    mid = _safe_float((snap.get("book") or {}).get("mid"))
    if mid is None:
        return {"emitted": 0, "reason": "no_mid"}

    rot_state = flow.get("rotation_state") or {}
    rotation_unit = 65.0
    if alias and alias.upper().startswith("ES"):
        rotation_unit = 15.0

    direction = "LONG" if regime == "ACCUMULATION" else "SHORT"
    emitted_rows = 0

    # Determine candidate levels: prefer proximity=True; if NONE in
    # proximity, fall back to the SINGLE nearest level within
    # _NEAREST_LEVEL_MAX_DIST_PTS so the rich popup still emits.
    proximate_levels = [
        l for l in levels
        if isinstance(l, dict)
        and l.get("proximity") is True
        and l.get("label")
        and _safe_float(l.get("price")) is not None
    ]
    if not proximate_levels:
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for l in levels:
            if not isinstance(l, dict):
                continue
            lp = _safe_float(l.get("price"))
            if lp is None or not l.get("label"):
                continue
            scored.append((abs(lp - mid), l))
        scored.sort(key=lambda t: t[0])
        if scored and scored[0][0] <= _NEAREST_LEVEL_MAX_DIST_PTS:
            proximate_levels = [scored[0][1]]

    for level in proximate_levels:
        label = level.get("label") or ""
        price = _safe_float(level.get("price"))
        side = level.get("side")
        if not label or price is None or not side:
            continue

        key = _emit_key(alias, label, regime, direction, bucket_ms)
        if state["last_emit_keys"].get(key):
            continue   # already emitted for this bucket
        state["last_emit_keys"][key] = bucket_ms

        setup_type = _setup_type(regime, side, mid, price)
        stop_price = _compute_stop_price(regime, side, price, rotation_unit)
        reason = _format_reason(setup_type, regime, flow, stop_price, label, price, mid)
        conv = float(flow.get("conviction") or 0.0)

        row = {
            "id": _signal_id(alias, label, regime, bucket_ms),
            "alias": alias,
            "label": label,
            "price": price,
            "side": side,
            "action": "BIAS_SIGNAL",
            "direction": direction,
            "confidence": round(conv, 4),
            "reason": reason,
            "timestamp_ms": now_ms,
            "source": "institutional_flow_rich",
        }
        _append_jsonl(row)
        emitted_rows += 1

    # Prune stale bucket keys so the dict doesn't grow unbounded.
    if len(state["last_emit_keys"]) > 256:
        cutoff = bucket_ms - 8 * _EMIT_BUCKET_SEC * 1000
        state["last_emit_keys"] = {
            k: v for k, v in state["last_emit_keys"].items() if v >= cutoff
        }

    return {"emitted": emitted_rows, "regime": regime, "alias": alias}


def reset_state(alias: Optional[str] = None) -> None:
    if alias is None:
        _STATE.clear()
    else:
        _STATE.pop(alias, None)


def _override_paths(jsonl_path: Optional[Path] = None) -> Path:
    global _JSONL_PATH
    prior = _JSONL_PATH
    if jsonl_path is not None:
        _JSONL_PATH = jsonl_path
    return prior
