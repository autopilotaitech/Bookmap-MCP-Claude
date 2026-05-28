"""Institutional-flow outcome tracker.

For each ACCUMULATION / DISTRIBUTION episode emitted by the
`institutional_flow` tracker, observes the subsequent rotation_state and
regime trail to determine whether the call was right:

- HIT  : during the episode, rotation_state.rotations_completed advanced
        at least one more rung in the regime's direction (i.e. price
        covered the next 65pt block for NQ).
- MISS : episode closed by an opposite-direction regime AND no rotation
        advance happened.
- STALE: episode closed by BALANCED/TRANSITION timeout (no flip, no
        advance) -- indeterminate.

Episodes close when:
- regime transitions to the opposite directional regime, OR
- regime transitions to BALANCED/TRANSITION and stays there longer than
  CLOSE_AFTER_NONDIRECTIONAL_SEC, OR
- session boundary (commit_price changes, indicating a new OR commit).

Persistence:
- D:/BookmapLogs/ifl-outcomes.csv  -- append-only, one row per closed
  episode.
- snap["ifl_outcomes"]              -- live state: open episode + last
  closed verdict.
"""

from __future__ import annotations

import csv
import math
import os
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


CLOSE_AFTER_NONDIRECTIONAL_SEC = 180.0
_NQ_ROTATION_PTS = 65.0
_ES_ROTATION_PTS = 15.0


def _rotation_unit_for(alias: str) -> float:
    a = (alias or "").upper()
    if a.startswith("ES") or a.startswith("MES"):
        return _ES_ROTATION_PTS
    return _NQ_ROTATION_PTS


_LEDGER_CSV_PATH = Path(r"D:\BookmapLogs\ifl-outcomes.csv")


_HEADER: Tuple[str, ...] = (
    "start_iso_ct",
    "end_iso_ct",
    "duration_sec",
    "alias",
    "regime",
    "commit_level",
    "commit_price",
    "start_mid",
    "peak_favorable_pts",
    "max_adverse_pts",
    "rungs_advanced",
    "rotation_unit_pts",
    "start_conviction",
    "end_regime",
    "end_conviction",
    "verdict",
)


_STATE: Dict[str, Dict[str, Any]] = {}


def _now_ms() -> int:
    return int(time.time() * 1000)


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


def _safe_int(v: Any) -> Optional[int]:
    f = _safe_float(v)
    return None if f is None else int(f)


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
        s = {"open": None, "last_closed": None, "nondirectional_since_ms": None}
        _STATE[alias] = s
    return s


def _append_csv(row: List[str]) -> None:
    path = _LEDGER_CSV_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        needs_header = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if needs_header:
                w.writerow(_HEADER)
            w.writerow(row)
    except Exception as exc:
        sys.stderr.write(f"[ifl_outcomes] append CSV failed: "
                         f"{type(exc).__name__}: {exc}\n")


def _episode_to_row(ep: Mapping[str, Any]) -> List[str]:
    start_ms = ep["start_ms"]
    end_ms = ep["end_ms"]
    duration_sec = max(0, (end_ms - start_ms) // 1000) if end_ms else 0
    return [
        _to_iso_ct(start_ms),
        _to_iso_ct(end_ms),
        str(duration_sec),
        ep.get("alias") or "",
        ep.get("start_regime") or "",
        ep.get("commit_level") or "",
        ("" if ep.get("commit_price") is None
         else f"{ep['commit_price']:.4f}"),
        ("" if ep.get("start_mid") is None
         else f"{ep['start_mid']:.4f}"),
        f"{ep.get('peak_favorable_pts', 0.0):.4f}",
        f"{ep.get('max_adverse_pts', 0.0):.4f}",
        str(ep.get("rungs_advanced", 0)),
        f"{ep.get('rotation_unit_pts', 0.0):.4f}",
        f"{ep.get('start_conviction', 0.0):.4f}",
        ep.get("end_regime") or "",
        f"{ep.get('end_conviction', 0.0):.4f}",
        ep.get("verdict") or "",
    ]


def _compute_verdict(ep: Mapping[str, Any], end_regime: str) -> str:
    """Five-tier verdict matching operator's trade management:

    - SUSTAINED_HIT : 2+ rungs advanced. Runner profitable, trail stop
                      above first rung. Operator may have added on
                      continuation.
    - HIT           : 1 rung advanced. Operator's TP1 reached; first
                      contract banked, runner now active.
    - PARTIAL_HIT   : 0 rungs but peak_favorable >= rotation_unit/2.
                      Stop-coverage worth of favorable -- contract #1
                      protected even though rotation didn't complete.
    - MISS          : 0 rungs, regime flipped to opposite.
    - STALE         : 0 rungs, regime drifted to BALANCED/TRANSITION
                      without enough favorable to score PARTIAL_HIT.

    See [[operator_trade_management]] memory for the trade-structure
    rationale.
    """
    rungs = ep.get("rungs_advanced", 0)
    peak_fav = float(ep.get("peak_favorable_pts", 0.0))
    rotation_unit = float(ep.get("rotation_unit_pts") or _NQ_ROTATION_PTS)
    half_rotation = rotation_unit / 2.0
    start_regime = ep.get("start_regime")
    opposite = {
        "ACCUMULATION": "DISTRIBUTION",
        "DISTRIBUTION": "ACCUMULATION",
    }.get(start_regime)
    if rungs >= 2:
        return "SUSTAINED_HIT"
    if rungs >= 1:
        return "HIT"
    if peak_fav >= half_rotation:
        return "PARTIAL_HIT"
    if opposite is not None and end_regime == opposite:
        return "MISS"
    return "STALE"


def _mid_from_snap(snap: Mapping[str, Any]) -> Optional[float]:
    book = snap.get("book") if isinstance(snap, Mapping) else None
    if isinstance(book, Mapping):
        return _safe_float(book.get("mid"))
    return None


def _open_new_episode(state: Dict[str, Any], snap_alias: str, flow: Mapping[str, Any],
                       snap: Mapping[str, Any], now_ms: int) -> None:
    rot = flow.get("rotation_state") or {}
    commit_price = _safe_float(rot.get("commit_price"))
    start_mid = _mid_from_snap(snap)
    state["open"] = {
        "alias": snap_alias,
        "start_ms": now_ms,
        "start_regime": flow.get("regime"),
        "start_conviction": float(flow.get("conviction") or 0.0),
        "commit_level": rot.get("commit_level"),
        "commit_price": commit_price,
        "start_mid": start_mid,
        "rotation_unit_pts": _rotation_unit_for(snap_alias),
        "peak_favorable_pts": 0.0,   # max favorable mid delta seen
        "max_adverse_pts": 0.0,      # most negative favorable delta (i.e. adverse move)
    }
    state["nondirectional_since_ms"] = None


def _update_open_episode(state: Dict[str, Any], flow: Mapping[str, Any],
                          snap: Mapping[str, Any], now_ms: int) -> None:
    ep = state["open"]
    if ep is None:
        return
    start_mid = ep.get("start_mid")
    if start_mid is None:
        # Recover: episode opened before mid was available; capture now.
        m = _mid_from_snap(snap)
        if m is not None:
            ep["start_mid"] = m
        return

    current_mid = _mid_from_snap(snap)
    if current_mid is None:
        return

    sign = 1 if ep["start_regime"] == "ACCUMULATION" else -1
    favorable_delta = (current_mid - start_mid) * sign
    if favorable_delta > ep["peak_favorable_pts"]:
        ep["peak_favorable_pts"] = favorable_delta
    if favorable_delta < ep["max_adverse_pts"]:
        ep["max_adverse_pts"] = favorable_delta


def _close_open_episode(state: Dict[str, Any], flow: Mapping[str, Any],
                         snap: Mapping[str, Any],
                         now_ms: int, end_regime: str) -> Dict[str, Any]:
    # Final price-delta sample at close so the row reflects what mid did
    # on the closing tick.
    _update_open_episode(state, flow, snap, now_ms)
    ep = state["open"]
    ep["end_ms"] = now_ms
    ep["end_regime"] = end_regime
    ep["end_conviction"] = float(flow.get("conviction") or 0.0)
    unit = ep.get("rotation_unit_pts") or _NQ_ROTATION_PTS
    ep["rungs_advanced"] = int(max(0.0, ep.get("peak_favorable_pts", 0.0)) // unit)
    ep["verdict"] = _compute_verdict(ep, end_regime)
    _append_csv(_episode_to_row(ep))
    state["last_closed"] = dict(ep)
    state["open"] = None
    state["nondirectional_since_ms"] = None
    return state["last_closed"]


def update_ifl_outcomes(snap: Mapping[str, Any], alias_arg: Optional[str] = None) -> Dict[str, Any]:
    flow = snap.get("institutional_flow") or {}
    if not isinstance(flow, dict) or flow.get("_error") or not flow.get("regime"):
        return {"active": None, "last_closed": None}

    alias = alias_arg or flow.get("alias") or snap.get("alias") or "unknown"
    state = _get_state(alias)
    now_ms = _snapshot_ts_ms(snap)
    regime = flow.get("regime")

    open_ep = state["open"]

    if regime in ("ACCUMULATION", "DISTRIBUTION"):
        if open_ep is None:
            _open_new_episode(state, alias, flow, snap, now_ms)
        elif open_ep["start_regime"] != regime:
            # Opposite-direction flip closes the prior episode immediately.
            _close_open_episode(state, flow, snap, now_ms, regime)
            _open_new_episode(state, alias, flow, snap, now_ms)
        else:
            _update_open_episode(state, flow, snap, now_ms)
        state["nondirectional_since_ms"] = None

    else:
        # BALANCED or TRANSITION
        if open_ep is not None:
            if state["nondirectional_since_ms"] is None:
                state["nondirectional_since_ms"] = now_ms
            elif now_ms - state["nondirectional_since_ms"] >= int(CLOSE_AFTER_NONDIRECTIONAL_SEC * 1000):
                _close_open_episode(state, flow, snap, now_ms, regime)

    return _public_view(state)


def _public_view(state: Mapping[str, Any]) -> Dict[str, Any]:
    open_ep = state.get("open")
    last_closed = state.get("last_closed")
    out: Dict[str, Any] = {
        "active": None,
        "last_closed": None,
    }
    if open_ep is not None:
        unit = open_ep.get("rotation_unit_pts") or _NQ_ROTATION_PTS
        peak = open_ep.get("peak_favorable_pts", 0.0)
        out["active"] = {
            "regime": open_ep.get("start_regime"),
            "start_iso_ct": _to_iso_ct(open_ep.get("start_ms")),
            "commit_level": open_ep.get("commit_level"),
            "commit_price": open_ep.get("commit_price"),
            "start_mid": open_ep.get("start_mid"),
            "peak_favorable_pts": round(peak, 4),
            "max_adverse_pts": round(open_ep.get("max_adverse_pts", 0.0), 4),
            "rungs_advanced_so_far": int(max(0.0, peak) // unit),
        }
    if last_closed is not None:
        out["last_closed"] = {
            "regime": last_closed.get("start_regime"),
            "start_iso_ct": _to_iso_ct(last_closed.get("start_ms")),
            "end_iso_ct": _to_iso_ct(last_closed.get("end_ms")),
            "verdict": last_closed.get("verdict"),
            "rungs_advanced": last_closed.get("rungs_advanced"),
            "peak_favorable_pts": round(last_closed.get("peak_favorable_pts", 0.0), 4),
            "max_adverse_pts": round(last_closed.get("max_adverse_pts", 0.0), 4),
            "end_regime": last_closed.get("end_regime"),
        }
    return out


def reset_state(alias: Optional[str] = None) -> None:
    if alias is None:
        _STATE.clear()
    else:
        _STATE.pop(alias, None)


def _override_paths(csv_path: Optional[Path] = None) -> Path:
    """Test helper to retarget the CSV path. Returns the prior value."""
    global _LEDGER_CSV_PATH
    prior = _LEDGER_CSV_PATH
    if csv_path is not None:
        _LEDGER_CSV_PATH = csv_path
    return prior
