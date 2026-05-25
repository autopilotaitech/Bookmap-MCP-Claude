"""Dashboard-side reader for Pax AI's structured chart signals.

Reads the JSONL file written by `pax-ai/pax_ai/ai_chart_signal_store.py`
(default `D:\\BookmapLogs\\pax-ai-chart-signals.jsonl`), TTL-filters,
dedupes by id (newest timestamp_ms wins), and converts each row to a
chart-event dict shaped to match `PaxInstitutionalChartEvent` so the
Java parser can ingest it via a sibling `parsePaxAiChartEvents` method.

Action -> event_type / severity mapping is deterministic and matches the
Pax AI prompt contract:

  PAY_FOR_TRADE     -> AI_ACCEPTANCE,    ENTRY
  WAIT_FOR_CONFIRM  -> AI_WATCH,         WATCH
  STAND_DOWN        -> AI_STAND_DOWN,    WARNING
  SCRATCH_READY     -> AI_SCRATCH,       EXIT
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_STORE_PATH = Path(r"D:\BookmapLogs\pax-ai-chart-signals.jsonl")
DEFAULT_TTL_SEC = 300
DEFAULT_MAX_ROWS = 50

_ACTION_TO_EVENT_TYPE = {
    "PAY_FOR_TRADE":    "AI_ACCEPTANCE",
    "WAIT_FOR_CONFIRM": "AI_WATCH",
    "STAND_DOWN":       "AI_STAND_DOWN",
    "SCRATCH_READY":    "AI_SCRATCH",
}
_ACTION_TO_SEVERITY = {
    "PAY_FOR_TRADE":    "ENTRY",
    "WAIT_FOR_CONFIRM": "WATCH",
    "STAND_DOWN":       "WARNING",
    "SCRATCH_READY":    "EXIT",
}

# Reader-side strict (action, direction) combo gate. Mirrors the writer
# check in pax-ai/pax_ai/ai_chart_signal._combo_ok. Defense in depth:
# the dashboard reader is the LAST gate before a marker reaches the
# Java chart, and the JSONL file is operator-editable for ops, so a
# hand-edited bad combo must still be rejected here.
#   PAY_FOR_TRADE                                  -> direction LONG/SHORT
#   WAIT_FOR_CONFIRM, STAND_DOWN, SCRATCH_READY    -> direction NONE
_DIRECTIONAL_ACTIONS = {"PAY_FOR_TRADE"}
_NONE_DIRECTION_ACTIONS = {"WAIT_FOR_CONFIRM", "STAND_DOWN", "SCRATCH_READY"}
_VALID_DIRECTIONS = {"LONG", "SHORT", "NONE"}


def _combo_ok(action: Any, direction: Any) -> bool:
    if direction not in _VALID_DIRECTIONS:
        return False
    if action in _DIRECTIONAL_ACTIONS:
        return direction in ("LONG", "SHORT")
    if action in _NONE_DIRECTION_ACTIONS:
        return direction == "NONE"
    return False

# Marker colors (hex "#RRGGBB"). Distinct from the local-anchored palette
# so AI markers visually pop on the chart. Magenta = bullish AI, cyan =
# bearish AI, purple = neutral/context AI.
AI_BULL_COLOR    = "#FF40D9"   # magenta
AI_BEAR_COLOR    = "#40E0FF"   # cyan
AI_NEUTRAL_COLOR = "#A86DEC"   # purple


def pax_ai_row_to_chart_event(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Map one stored JSONL row to a chart-event dict.

    Returns None when:
      * row is not a dict
      * action is unknown
      * (action, direction) combination violates _combo_ok

    The combo gate runs BEFORE event_type / severity mapping so a
    hand-edited "PAY_FOR_TRADE + NONE" row never reaches the chart.
    """
    if not isinstance(row, dict):
        return None
    action = row.get("action")
    if action not in _ACTION_TO_EVENT_TYPE:
        return None
    direction = row.get("direction") or "NONE"
    if not _combo_ok(action, direction):
        return None
    event_type = _ACTION_TO_EVENT_TYPE[action]
    severity = _ACTION_TO_SEVERITY[action]
    if direction == "LONG":
        color = AI_BULL_COLOR
        arrow = "^"
    elif direction == "SHORT":
        color = AI_BEAR_COLOR
        arrow = "v"
    else:
        color = AI_NEUTRAL_COLOR
        arrow = "."
    try:
        conf = float(row.get("confidence") or 0.0)
    except (ValueError, TypeError):
        conf = 0.0
    conf_int = max(0, min(100, int(round(conf * 100.0))))
    label = row.get("label") or "?"
    try:
        price = float(row.get("price") or 0.0)
    except (ValueError, TypeError):
        price = 0.0
    compact_label = str(label).upper().replace("-", "")[:3] or "?"
    marker_text = f"AI{arrow}{compact_label}{conf_int}"
    side = row.get("side") or ("above" if direction == "LONG" else "below")
    reason = str(row.get("reason") or "")[:240]
    try:
        ts_ms = int(row.get("timestamp_ms") or 0)
    except (ValueError, TypeError):
        ts_ms = 0
    return {
        "id":                row.get("id") or "",
        "alias":             row.get("alias") or "",
        "label":             label,
        "price":             price,
        "side":              side,
        "event_type":        event_type,
        "direction":         direction,
        "execution_read":    action,
        "marker_text":       marker_text,
        "marker_color_hint": color,
        "severity":          severity,
        "timestamp_ms":      ts_ms,
        "source":            "pax_ai",
        "confidence":        conf,
        "reason_codes":      [reason] if reason else [],
        "invalidation_price": None,
        "payline_price":      None,
    }


def read_pax_ai_chart_events(store_path: Optional[Path] = None,
                              now_ms: Optional[int] = None,
                              ttl_sec: int = DEFAULT_TTL_SEC,
                              max_rows: int = DEFAULT_MAX_ROWS,
                              alias: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read TTL-filtered, deduped chart events from the JSONL store.

    When ``alias`` is a non-empty string, ONLY rows whose ``alias`` field
    matches are returned. This is the production-side multi-instrument
    safety: a NQ-anchored AI signal must never appear on an ES chart.

    When ``alias`` is None or empty, all rows pass through. The composer
    always supplies an alias; the unfiltered path is for ad-hoc tooling.
    """
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    if not path.exists():
        return []
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - (ttl_sec * 1000)
    by_id: Dict[str, Dict[str, Any]] = {}
    alias_filter = alias if isinstance(alias, str) and alias else None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # Partial / malformed line. Skip; do NOT raise into
                    # the snapshot path.
                    continue
                if not isinstance(row, dict):
                    continue
                ts = row.get("timestamp_ms")
                if not isinstance(ts, (int, float)) or ts < cutoff:
                    continue
                rid = row.get("id")
                if not rid:
                    continue
                if alias_filter is not None and row.get("alias") != alias_filter:
                    continue
                prev = by_id.get(rid)
                if prev is None or row.get("timestamp_ms", 0) >= prev.get("timestamp_ms", 0):
                    by_id[rid] = row
    except OSError:
        return []
    out: List[Dict[str, Any]] = []
    for row in sorted(by_id.values(), key=lambda r: r.get("timestamp_ms", 0)):
        ev = pax_ai_row_to_chart_event(row)
        if ev is not None:
            out.append(ev)
    return out[-max_rows:]


def compute_pax_ai_chart_events(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Dashboard composer entry point.

    Reads the cross-process JSONL store filtered to the snapshot's alias
    (so multi-instrument operators don't see NQ AI signals on an ES
    chart). When snap['alias'] is missing or empty, falls back to
    unfiltered read -- production code paths always set the alias.
    """
    alias = None
    if isinstance(snap, dict):
        a = snap.get("alias")
        if isinstance(a, str) and a:
            alias = a
    return read_pax_ai_chart_events(now_ms=int(time.time() * 1000),
                                     alias=alias)
