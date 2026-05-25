"""Extract + validate a structured chart-signal block from a Pax AI response.

The block is hard-fenced and lives OUTSIDE markdown:

    <<PAX_AI_CHART_SIGNAL>>
    {"action":"PAY_FOR_TRADE","direction":"LONG","label":"OR-H",
     "price":20000.0,"confidence":0.72,"reason":"acceptance + WITH"}
    <<END>>

Multiple blocks: only the LAST well-formed one wins.
Missing required field / bad enum / non-finite price / out-of-range conf -> None.

validate_against_snapshot anchors the block against snap['or_levels'] so
hallucinated levels never reach the chart. It is the only function that
emits the chart-event shape consumed downstream.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any, Dict, Optional


_REQUIRED = ("action", "direction", "label", "price", "confidence", "reason")
_ACTIONS = ("PAY_FOR_TRADE", "WAIT_FOR_CONFIRM", "STAND_DOWN", "SCRATCH_READY")
_DIRECTIONS = ("LONG", "SHORT", "NONE")
# Strict (action, direction) combo rules. The Pax AI prompt already
# constrains the model this way; the validator enforces it so a model
# misbehavior or a hand-edited JSONL row can never produce an
# inconsistent chart marker.
#   PAY_FOR_TRADE    -> direction MUST be LONG or SHORT
#   WAIT_FOR_CONFIRM -> direction MUST be NONE
#   STAND_DOWN       -> direction MUST be NONE
#   SCRATCH_READY    -> direction MUST be NONE
_DIRECTIONAL_ACTIONS = {"PAY_FOR_TRADE"}
_NONE_DIRECTION_ACTIONS = {"WAIT_FOR_CONFIRM", "STAND_DOWN", "SCRATCH_READY"}


def _combo_ok(action: str, direction: str) -> bool:
    if action in _DIRECTIONAL_ACTIONS:
        return direction in ("LONG", "SHORT")
    if action in _NONE_DIRECTION_ACTIONS:
        return direction == "NONE"
    return False
_PATTERN = re.compile(
    r"<<PAX_AI_CHART_SIGNAL>>\s*(\{.*?\})\s*<<END>>",
    re.DOTALL,
)

_PRICE_TOLERANCE_TICKS = 5
_TICK_SIZE_NQ = 0.25
_REASON_MAX = 240


def extract_block(pax_text: Optional[str]) -> Optional[Dict[str, Any]]:
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
        if blk["action"] not in _ACTIONS:
            continue
        if blk["direction"] not in _DIRECTIONS:
            continue
        if not _combo_ok(blk["action"], blk["direction"]):
            continue
        try:
            price = float(blk["price"])
            conf = float(blk["confidence"])
        except (ValueError, TypeError):
            continue
        if not (price > 0.0):
            continue
        if not (0.0 <= conf <= 1.0):
            continue
        blk["price"] = price
        blk["confidence"] = conf
        return blk
    return None


def validate_against_snapshot(blk: Any,
                               snap: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(blk, dict) or not isinstance(snap, dict):
        return None
    if snap.get("health") != "ok":
        return None
    # Defense in depth: even if a row bypassed extract_block (e.g. a
    # hand-edited JSONL file), the validator rejects bad combos here.
    action = blk.get("action")
    direction = blk.get("direction")
    if action not in _ACTIONS or direction not in _DIRECTIONS:
        return None
    if not _combo_ok(action, direction):
        return None
    or_levels = snap.get("or_levels") or {}
    if not isinstance(or_levels, dict):
        return None
    levels = or_levels.get("levels") or []
    if not isinstance(levels, list):
        return None
    target_label = blk.get("label")
    match = next((L for L in levels if isinstance(L, dict)
                  and L.get("label") == target_label), None)
    if match is None:
        return None
    lvl_price = match.get("price")
    if not isinstance(lvl_price, (int, float)) or lvl_price <= 0.0:
        return None
    if abs(float(blk["price"]) - float(lvl_price)) > _PRICE_TOLERANCE_TICKS * _TICK_SIZE_NQ:
        return None
    alias = snap.get("alias") or ""
    ts_ms = int(time.time() * 1000)
    id_seed = f"{alias}|{target_label}|{blk['action']}|{blk['direction']}|{ts_ms // 1000}"
    sig_id = "pax_ai|" + hashlib.sha1(id_seed.encode("utf-8")).hexdigest()[:16]
    direction = blk["direction"]
    side = match.get("side") or ("above" if direction == "LONG" else "below")
    reason = str(blk.get("reason") or "")[:_REASON_MAX]
    return {
        "id":           sig_id,
        "alias":        alias,
        "label":        target_label,
        "price":        float(lvl_price),
        "side":         side,
        "action":       blk["action"],
        "direction":    direction,
        "confidence":   float(blk["confidence"]),
        "reason":       reason,
        "timestamp_ms": ts_ms,
        "source":       "pax_ai",
    }
