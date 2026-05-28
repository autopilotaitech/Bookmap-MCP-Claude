"""Per-level crossing audit log.

Every time mid crosses an OR / extension level (OR-H, OR-L, +1..+N, -1..-N),
record the crossing with rich context:

- Direction (UP / DOWN), mid_at_cross, level price.
- Prior crossing on this alias: time_since_prior_sec, pts_per_sec (path
  speed from the last level we crossed to this one).
- State at crossing: regime, weighted_vote, conviction, trend_filter,
  micro_signed (institutional fingerprint score).
- Event counts in the last 60s by kind (iceberg, absorption, stack, pull,
  sweep) -- tells you how institutions handled the journey to this level.

Persistence:
- Append-only CSV at D:/BookmapLogs/or-level-crossings.csv (one row per
  crossing).
- Last 10 crossings exposed on snap["or_level_crossings"] for live
  inspection without parsing the CSV.

Hysteresis: a crossing fires when the per-level "above/below" state
flips between consecutive snapshots. Subtle oscillation right at the
level price doesn't double-fire because each level keeps its own
above/below state machine.
"""

from __future__ import annotations

import csv
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:
    _CT = None


_CSV_PATH = Path(r"D:\BookmapLogs\or-level-crossings.csv")
_MAX_RECENT = 10
_EVENT_COUNT_WINDOW_SEC = 60.0
_TRACKED_EVENT_KINDS = ("ICEBERG", "ABSORPTION", "STACK", "PULL", "STOP_SWEEP", "SWEEP", "SPOOF")


_HEADER: Tuple[str, ...] = (
    "crossing_iso_ct",
    "alias",
    "level_label",
    "level_price",
    "direction",
    "mid_at_cross",
    "prior_level_label",
    "prior_iso_ct",
    "secs_since_prior",
    "pts_per_sec",
    "regime",
    "weighted_vote",
    "conviction",
    "trend_filter",
    "micro_signed",
    "iceberg_count_60s",
    "absorption_count_60s",
    "stack_count_60s",
    "pull_count_60s",
    "stop_sweep_count_60s",
    "spoof_count_60s",
)


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


def _snapshot_ts_ms(snap: Mapping[str, Any]) -> int:
    ts_ms = _safe_float(snap.get("ts_ms"))
    if ts_ms is not None:
        return int(ts_ms)
    ts_str = snap.get("ts")
    if isinstance(ts_str, str):
        try:
            dt = datetime.fromisoformat(ts_str)
            return int(dt.timestamp() * 1000)
        except (ValueError, TypeError):
            pass
    return _now_ms()


def _to_iso_ct(ms: Optional[int]) -> str:
    if ms is None:
        return ""
    if _CT is None:
        return datetime.utcfromtimestamp(ms / 1000.0).isoformat()
    return datetime.fromtimestamp(ms / 1000.0, tz=_CT).isoformat()


def _get_state(alias: str) -> Dict[str, Any]:
    s = _STATE.get(alias)
    if s is None:
        s = {
            "level_sides": {},          # label -> "above" / "below"
            "level_prices": {},         # label -> last seen price
            "last_crossing": None,      # dict
            "recent": [],               # list of public-view dicts
        }
        _STATE[alias] = s
    return s


def _event_counts_last_60s(snap: Mapping[str, Any], now_ms: int) -> Dict[str, int]:
    counts = {k: 0 for k in _TRACKED_EVENT_KINDS}
    events = ((snap.get("micro_events") or {}).get("events") or [])
    cutoff = now_ms - int(_EVENT_COUNT_WINDOW_SEC * 1000)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        ts = _safe_float(ev.get("timeMs") or ev.get("timestampMs"))
        if ts is None or ts < cutoff:
            continue
        kind = (ev.get("kind") or ev.get("type") or "").upper()
        if kind in counts:
            counts[kind] += 1
    return counts


def _micro_signed_from_flow(flow: Mapping[str, Any]) -> float:
    """Pull the micro_events signed value out of institutional_flow.drivers
    if present. Falls back to 0.0 when not available."""
    drivers = flow.get("drivers") or []
    for d in drivers:
        if isinstance(d, dict) and d.get("name") == "micro_events":
            return float(d.get("signed") or 0.0)
    return 0.0


def _append_csv(row: List[str]) -> None:
    path = _CSV_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if needs_header:
                w.writerow(_HEADER)
            w.writerow(row)
    except Exception as exc:
        sys.stderr.write(f"[or_level_crossings] append CSV failed: "
                         f"{type(exc).__name__}: {exc}\n")


def _build_crossing_record(
    alias: str,
    label: str,
    level_price: float,
    direction: str,
    mid_at_cross: float,
    now_ms: int,
    flow: Mapping[str, Any],
    prior: Optional[Mapping[str, Any]],
    event_counts: Mapping[str, int],
) -> Dict[str, Any]:
    secs_since_prior = None
    pts_per_sec = None
    prior_label = ""
    prior_iso = ""
    if prior is not None:
        prior_ms = prior.get("crossing_ms")
        prior_mid = prior.get("mid_at_cross")
        prior_label = prior.get("level_label", "") or ""
        prior_iso = _to_iso_ct(prior_ms)
        if prior_ms is not None and prior_mid is not None:
            secs = max(0.0, (now_ms - int(prior_ms)) / 1000.0)
            secs_since_prior = int(secs)
            if secs > 0:
                pts_per_sec = round(abs(mid_at_cross - float(prior_mid)) / secs, 4)
    return {
        "crossing_ms": now_ms,
        "crossing_iso_ct": _to_iso_ct(now_ms),
        "alias": alias,
        "level_label": label,
        "level_price": level_price,
        "direction": direction,
        "mid_at_cross": mid_at_cross,
        "prior_level_label": prior_label,
        "prior_iso_ct": prior_iso,
        "secs_since_prior": secs_since_prior,
        "pts_per_sec": pts_per_sec,
        "regime": (flow.get("regime") or "") if isinstance(flow, dict) else "",
        "weighted_vote": float(flow.get("weighted_vote") or 0.0) if isinstance(flow, dict) else 0.0,
        "conviction": float(flow.get("conviction") or 0.0) if isinstance(flow, dict) else 0.0,
        "trend_filter": (flow.get("trend_filter") or "") if isinstance(flow, dict) else "",
        "micro_signed": _micro_signed_from_flow(flow) if isinstance(flow, dict) else 0.0,
        "event_counts": dict(event_counts),
    }


def _record_to_row(r: Mapping[str, Any]) -> List[str]:
    ec = r.get("event_counts") or {}
    return [
        r.get("crossing_iso_ct", ""),
        r.get("alias", ""),
        r.get("level_label", ""),
        f"{r.get('level_price', 0.0):.4f}",
        r.get("direction", ""),
        f"{r.get('mid_at_cross', 0.0):.4f}",
        r.get("prior_level_label", ""),
        r.get("prior_iso_ct", ""),
        "" if r.get("secs_since_prior") is None else str(r["secs_since_prior"]),
        "" if r.get("pts_per_sec") is None else f"{r['pts_per_sec']:.4f}",
        r.get("regime", ""),
        f"{r.get('weighted_vote', 0.0):.4f}",
        f"{r.get('conviction', 0.0):.4f}",
        r.get("trend_filter", ""),
        f"{r.get('micro_signed', 0.0):.4f}",
        str(ec.get("ICEBERG", 0)),
        str(ec.get("ABSORPTION", 0)),
        str(ec.get("STACK", 0)),
        str(ec.get("PULL", 0)),
        str(ec.get("STOP_SWEEP", 0) + ec.get("SWEEP", 0)),
        str(ec.get("SPOOF", 0)),
    ]


def _public_view(r: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "iso_ct": r.get("crossing_iso_ct"),
        "level": r.get("level_label"),
        "price": r.get("level_price"),
        "direction": r.get("direction"),
        "mid_at_cross": r.get("mid_at_cross"),
        "secs_since_prior": r.get("secs_since_prior"),
        "pts_per_sec": r.get("pts_per_sec"),
        "regime": r.get("regime"),
        "conviction": r.get("conviction"),
        "micro_signed": r.get("micro_signed"),
        "trend_filter": r.get("trend_filter"),
        "event_counts": r.get("event_counts"),
    }


def update_or_level_crossings(
    snap: Mapping[str, Any], alias_arg: Optional[str] = None
) -> Dict[str, Any]:
    alias = alias_arg or snap.get("alias") or "unknown"
    state = _get_state(alias)
    now_ms = _snapshot_ts_ms(snap)

    or_levels = snap.get("or_levels") or {}
    levels = or_levels.get("levels") or []
    mid = _safe_float((snap.get("book") or {}).get("mid"))

    if mid is None or not levels:
        return {"recent": list(state["recent"])}

    flow = snap.get("institutional_flow") or {}
    event_counts = _event_counts_last_60s(snap, now_ms)

    sides = state["level_sides"]
    new_crossings: List[Dict[str, Any]] = []

    for level in levels:
        if not isinstance(level, dict):
            continue
        label = level.get("label")
        price = _safe_float(level.get("price"))
        if label is None or price is None:
            continue

        new_side = "above" if mid > price else "below"
        old_side = sides.get(label)
        sides[label] = new_side
        state["level_prices"][label] = price

        if old_side is None or old_side == new_side:
            continue

        direction = "UP" if new_side == "above" else "DOWN"
        prior = state.get("last_crossing")
        record = _build_crossing_record(
            alias, label, price, direction, mid, now_ms, flow, prior, event_counts
        )
        new_crossings.append(record)

    # If multiple levels crossed in one tick (gap), order by absolute
    # distance from prior mid so the "first crossed" appears first in the
    # CSV / recent list.
    if len(new_crossings) > 1 and state.get("last_crossing"):
        prior_mid = state["last_crossing"].get("mid_at_cross") or mid
        new_crossings.sort(
            key=lambda r: abs((r["level_price"] or 0.0) - float(prior_mid))
        )

    for record in new_crossings:
        _append_csv(_record_to_row(record))
        state["recent"].append(_public_view(record))
        while len(state["recent"]) > _MAX_RECENT:
            state["recent"].pop(0)
        state["last_crossing"] = record

    return {"recent": list(state["recent"])}


def reset_state(alias: Optional[str] = None) -> None:
    if alias is None:
        _STATE.clear()
    else:
        _STATE.pop(alias, None)


def _override_paths(csv_path: Optional[Path] = None) -> Path:
    global _CSV_PATH
    prior = _CSV_PATH
    if csv_path is not None:
        _CSV_PATH = csv_path
    return prior
