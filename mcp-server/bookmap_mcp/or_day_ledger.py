"""Per-session OR + extension ledger.

For each (alias, anchor) session, tracks: anchor metadata, OR-H/OR-L/width,
first commit time and direction, time to commit, max rotations above/below
(NQ = 65 pt strides, ES = 15 pt), peak extremes, whipsaw count, observation
count, final status. Finalized rows append to D:/BookmapLogs/or-day-ledger.csv.

A "session" is identified by the OR boundary tuple (orHigh, orLow,
anchor_hhmm, anchor_tz). When that fingerprint changes the prior session is
finalized to CSV and a new session begins.

No execution path. Read-only with respect to snapshot inputs. Side effects:
- Append-only writes to D:/BookmapLogs/or-day-ledger.csv (finalized rows).
- Overwrite-style writes to D:/BookmapLogs/or-day-ledger-current.json (live
  in-progress sessions snapshot, for tooling that wants to peek at today
  without parsing the CSV).
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

try:
    from zoneinfo import ZoneInfo
    _CT = ZoneInfo("America/Chicago")
except Exception:
    _CT = None


_NQ_ROTATION_PTS = 65.0
_ES_ROTATION_PTS = 15.0


_LEDGER_CSV_PATH = Path(r"D:\BookmapLogs\or-day-ledger.csv")
_LIVE_JSON_PATH = Path(r"D:\BookmapLogs\or-day-ledger-current.json")


_HEADER: Tuple[str, ...] = (
    "session_anchor_iso_ct",
    "session_date_ct",
    "session_day_of_week",
    "session_type",
    "alias",
    "anchor_hhmm",
    "anchor_tz",
    "anchor_mode",
    "or_high",
    "or_low",
    "or_width",
    "range_seconds",
    "rotation_unit_pts",
    "session_high",
    "session_high_iso_ct",
    "session_low",
    "session_low_iso_ct",
    "session_range",
    "first_commit_iso_ct",
    "first_commit_direction",
    "time_to_commit_sec",
    "max_rotations_above",
    "max_rotations_below",
    "peak_extreme_above_price",
    "peak_extreme_above_iso_ct",
    "peak_extreme_below_price",
    "peak_extreme_below_iso_ct",
    "whipsaw_count",
    "total_observations",
    "session_first_seen_iso_ct",
    "session_last_seen_iso_ct",
    "final_status",
)


_SESSIONS: Dict[str, Dict[str, Any]] = {}


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f


def _safe_int(value: Any) -> Optional[int]:
    f = _safe_float(value)
    return None if f is None else int(f)


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


def _to_date_ct(ms: Optional[int]) -> str:
    if ms is None or _CT is None:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=_CT).strftime("%Y-%m-%d")


def _to_dow_ct(ms: Optional[int]) -> str:
    if ms is None or _CT is None:
        return ""
    return datetime.fromtimestamp(ms / 1000.0, tz=_CT).strftime("%a")


def _classify_session_type(anchor_hhmm: str, anchor_tz: str) -> str:
    """Map (anchor_hhmm, anchor_tz) to a coarse session label.

    Recognized:
        17:00 CT == 18:00 ET -> ETH (Globex/electronic open)
        08:30 CT == 09:30 ET -> RTH (CME index cash open)
        02:00 CT == 03:00 ET == 08:00 London -> EU (London cash open)
    Any other anchor falls into OTHER.
    """
    if not anchor_hhmm:
        return "OTHER"
    hhmm = anchor_hhmm[:5]
    tz = (anchor_tz or "").strip()

    if tz == "America/Chicago":
        if hhmm == "17:00":
            return "ETH"
        if hhmm == "08:30":
            return "RTH"
        if hhmm == "02:00":
            return "EU"
    elif tz == "America/New_York":
        if hhmm == "18:00":
            return "ETH"
        if hhmm == "09:30":
            return "RTH"
        if hhmm == "03:00":
            return "EU"
    elif tz in ("Europe/London", "Europe/Dublin"):
        if hhmm in ("08:00", "08:00:00"):
            return "EU"
    return "OTHER"


def _alias_from(snap: Mapping[str, Any], alias_arg: Optional[str]) -> str:
    if alias_arg:
        return alias_arg
    a = snap.get("alias")
    return a if isinstance(a, str) and a else "unknown"


def _rotation_unit_for(alias: str) -> float:
    a = (alias or "").upper()
    if a.startswith("NQ") or a.startswith("MNQ"):
        return _NQ_ROTATION_PTS
    if a.startswith("ES") or a.startswith("MES"):
        return _ES_ROTATION_PTS
    return _NQ_ROTATION_PTS  # default; operator can override later


def _resolve_session_anchor_ms(
    anchor_hhmm: str, anchor_tz: str, now_ms: int
) -> int:
    """Resolve anchor_hhmm + anchor_tz to the most recent wall-clock
    instant in that timezone matching that time-of-day.

    Example: anchor 08:30 America/Chicago, now is Thu 09:55 CT
        -> returns Thu 08:30 CT (today's RTH open).
    Example: anchor 17:00 America/Chicago, now is Thu 09:55 CT
        -> returns Wed 17:00 CT (yesterday's ETH open, still active).

    Falls back to now_ms on parse failure or if zoneinfo isn't available.
    """
    if not anchor_hhmm:
        return now_ms
    try:
        tz = ZoneInfo(anchor_tz) if anchor_tz else _CT
    except Exception:
        tz = _CT
    if tz is None:
        return now_ms
    parts = anchor_hhmm.split(":")
    if len(parts) < 2:
        return now_ms
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) > 2 else 0
    except (TypeError, ValueError):
        return now_ms
    now_dt = datetime.fromtimestamp(now_ms / 1000.0, tz=tz)
    today_anchor = now_dt.replace(hour=hh, minute=mm, second=ss, microsecond=0)
    if today_anchor > now_dt:
        today_anchor = today_anchor - timedelta(days=1)
    return int(today_anchor.timestamp() * 1000)


def _session_fingerprint(snap: Mapping[str, Any]) -> Optional[str]:
    or_levels = snap.get("or_levels") or {}
    orh = _safe_float(or_levels.get("orHigh"))
    orl = _safe_float(or_levels.get("orLow"))
    if orh is None or orl is None:
        return None
    session = snap.get("session") or {}
    anchor_hhmm = session.get("anchorHHMM") or ""
    anchor_tz = session.get("anchorTimezone") or ""
    return f"{anchor_hhmm}|{anchor_tz}|{orh:.4f}|{orl:.4f}"


def _init_session(snap: Mapping[str, Any], alias: str, now_ms: int) -> Dict[str, Any]:
    or_levels = snap.get("or_levels") or {}
    orh = _safe_float(or_levels.get("orHigh")) or 0.0
    orl = _safe_float(or_levels.get("orLow")) or 0.0
    session = snap.get("session") or {}
    or_session_config = snap.get("or_session_config") or {}
    config = or_session_config.get("config") or {}
    anchor_hhmm = session.get("anchorHHMM") or ""
    anchor_tz = session.get("anchorTimezone") or ""
    return {
        "alias": alias,
        "anchor_hhmm": anchor_hhmm,
        "anchor_tz": anchor_tz,
        "anchor_mode": session.get("anchorMode") or "",
        "range_seconds": _safe_int(config.get("rangeSeconds")) or 0,
        "or_high": orh,
        "or_low": orl,
        "or_width": round(orh - orl, 4),
        "rotation_unit_pts": _rotation_unit_for(alias),
        "session_anchor_ms": _resolve_session_anchor_ms(anchor_hhmm, anchor_tz, now_ms),
        "session_first_seen_ms": now_ms,
        "session_last_seen_ms": now_ms,
        "first_commit_ms": None,
        "first_commit_direction": "",
        "max_rotations_above": 0,
        "max_rotations_below": 0,
        "peak_extreme_above_price": None,
        "peak_extreme_above_ms": None,
        "peak_extreme_below_price": None,
        "peak_extreme_below_ms": None,
        # Absolute session High/Low (independent of OR boundary). Operator
        # tracks these as support/resistance for the session.
        "session_high_price": None,
        "session_high_ms": None,
        "session_low_price": None,
        "session_low_ms": None,
        "whipsaw_count": 0,
        "total_observations": 0,
        "_last_committed_direction": None,
    }


def _update_session(session: Dict[str, Any], snap: Mapping[str, Any], now_ms: int) -> None:
    session["session_last_seen_ms"] = now_ms
    session["total_observations"] += 1

    mid = _safe_float((snap.get("book") or {}).get("mid"))
    if mid is None:
        return

    # Absolute session High/Low (regardless of OR boundary).
    if session["session_high_price"] is None or mid > session["session_high_price"]:
        session["session_high_price"] = mid
        session["session_high_ms"] = now_ms
    if session["session_low_price"] is None or mid < session["session_low_price"]:
        session["session_low_price"] = mid
        session["session_low_ms"] = now_ms

    orh = session["or_high"]
    orl = session["or_low"]
    unit = session["rotation_unit_pts"]

    direction: Optional[str] = None
    if mid > orh + 0.01:
        direction = "UP"
    elif mid < orl - 0.01:
        direction = "DOWN"

    if direction is not None and session["first_commit_ms"] is None:
        session["first_commit_ms"] = now_ms
        session["first_commit_direction"] = direction

    last = session["_last_committed_direction"]
    if direction is not None:
        if last is not None and last != direction:
            session["whipsaw_count"] += 1
        session["_last_committed_direction"] = direction
    # When mid returns INSIDE the OR after a commit, we keep
    # _last_committed_direction so that a future flip increments whipsaw.

    if direction == "UP":
        distance = mid - orh
        rotations = int(distance // unit)
        if rotations > session["max_rotations_above"]:
            session["max_rotations_above"] = rotations
        if (session["peak_extreme_above_price"] is None
                or mid > session["peak_extreme_above_price"]):
            session["peak_extreme_above_price"] = mid
            session["peak_extreme_above_ms"] = now_ms
    elif direction == "DOWN":
        distance = orl - mid
        rotations = int(distance // unit)
        if rotations > session["max_rotations_below"]:
            session["max_rotations_below"] = rotations
        if (session["peak_extreme_below_price"] is None
                or mid < session["peak_extreme_below_price"]):
            session["peak_extreme_below_price"] = mid
            session["peak_extreme_below_ms"] = now_ms


def _final_status(session: Mapping[str, Any]) -> str:
    above = session["max_rotations_above"]
    below = session["max_rotations_below"]
    whips = session["whipsaw_count"]
    if whips > 0 and above > 0 and below > 0:
        return "WHIPSAW"
    if above > 0 and below == 0:
        return "BROKE_UP"
    if below > 0 and above == 0:
        return "BROKE_DOWN"
    if above > 0 and below > 0:
        return "TWO_SIDED"
    return "HELD"


def _row_from_session(session: Mapping[str, Any]) -> List[str]:
    fcs = session["first_commit_ms"]
    fct = (
        ""
        if fcs is None or session["session_anchor_ms"] is None
        else str(max(0, (fcs - session["session_anchor_ms"]) // 1000))
    )
    anchor_ms = session["session_anchor_ms"]
    sh_p = session.get("session_high_price")
    sl_p = session.get("session_low_price")
    session_range = (
        f"{(sh_p - sl_p):.4f}" if (sh_p is not None and sl_p is not None) else ""
    )
    return [
        _to_iso_ct(anchor_ms),
        _to_date_ct(anchor_ms),
        _to_dow_ct(anchor_ms),
        _classify_session_type(session["anchor_hhmm"], session["anchor_tz"]),
        session["alias"],
        session["anchor_hhmm"],
        session["anchor_tz"],
        session["anchor_mode"],
        f"{session['or_high']:.4f}",
        f"{session['or_low']:.4f}",
        f"{session['or_width']:.4f}",
        str(session["range_seconds"]),
        f"{session['rotation_unit_pts']:.4f}",
        "" if sh_p is None else f"{sh_p:.4f}",
        _to_iso_ct(session.get("session_high_ms")),
        "" if sl_p is None else f"{sl_p:.4f}",
        _to_iso_ct(session.get("session_low_ms")),
        session_range,
        _to_iso_ct(fcs),
        session["first_commit_direction"],
        fct,
        str(session["max_rotations_above"]),
        str(session["max_rotations_below"]),
        ("" if session["peak_extreme_above_price"] is None
         else f"{session['peak_extreme_above_price']:.4f}"),
        _to_iso_ct(session["peak_extreme_above_ms"]),
        ("" if session["peak_extreme_below_price"] is None
         else f"{session['peak_extreme_below_price']:.4f}"),
        _to_iso_ct(session["peak_extreme_below_ms"]),
        str(session["whipsaw_count"]),
        str(session["total_observations"]),
        _to_iso_ct(session["session_first_seen_ms"]),
        _to_iso_ct(session["session_last_seen_ms"]),
        _final_status(session),
    ]


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
        sys.stderr.write(f"[or_day_ledger] append CSV failed: "
                         f"{type(exc).__name__}: {exc}\n")


def _write_live_json() -> None:
    path = _LIVE_JSON_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Build a JSON-safe snapshot of all in-progress sessions.
        out: Dict[str, Any] = {}
        for alias, sess in _SESSIONS.items():
            anchor_ms = sess.get("session_anchor_ms")
            out[alias] = {
                "session_anchor_iso_ct": _to_iso_ct(anchor_ms),
                "session_date_ct": _to_date_ct(anchor_ms),
                "session_day_of_week": _to_dow_ct(anchor_ms),
                "session_type": _classify_session_type(
                    sess.get("anchor_hhmm") or "",
                    sess.get("anchor_tz") or "",
                ),
                "alias": sess.get("alias"),
                "anchor_hhmm": sess.get("anchor_hhmm"),
                "anchor_tz": sess.get("anchor_tz"),
                "anchor_mode": sess.get("anchor_mode"),
                "or_high": sess.get("or_high"),
                "or_low": sess.get("or_low"),
                "or_width": sess.get("or_width"),
                "range_seconds": sess.get("range_seconds"),
                "rotation_unit_pts": sess.get("rotation_unit_pts"),
                "session_high_price": sess.get("session_high_price"),
                "session_high_iso_ct": _to_iso_ct(sess.get("session_high_ms")),
                "session_low_price": sess.get("session_low_price"),
                "session_low_iso_ct": _to_iso_ct(sess.get("session_low_ms")),
                "first_commit_iso_ct": _to_iso_ct(sess.get("first_commit_ms")),
                "first_commit_direction": sess.get("first_commit_direction"),
                "max_rotations_above": sess.get("max_rotations_above"),
                "max_rotations_below": sess.get("max_rotations_below"),
                "peak_extreme_above_price": sess.get("peak_extreme_above_price"),
                "peak_extreme_above_iso_ct": _to_iso_ct(sess.get("peak_extreme_above_ms")),
                "peak_extreme_below_price": sess.get("peak_extreme_below_price"),
                "peak_extreme_below_iso_ct": _to_iso_ct(sess.get("peak_extreme_below_ms")),
                "whipsaw_count": sess.get("whipsaw_count"),
                "total_observations": sess.get("total_observations"),
                "session_first_seen_iso_ct": _to_iso_ct(sess.get("session_first_seen_ms")),
                "session_last_seen_iso_ct": _to_iso_ct(sess.get("session_last_seen_ms")),
                "in_progress_status": _final_status(sess),
            }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(out, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as exc:
        sys.stderr.write(f"[or_day_ledger] write live JSON failed: "
                         f"{type(exc).__name__}: {exc}\n")


def update_or_day_ledger(
    snap: Mapping[str, Any], alias_arg: Optional[str] = None
) -> Dict[str, Any]:
    alias = _alias_from(snap, alias_arg)
    now_ms = _snapshot_ts_ms(snap)
    fp = _session_fingerprint(snap)

    current = _SESSIONS.get(alias)
    if fp is None:
        # OR not yet formed; do not write or update.
        return _public_view(current, fp)

    if current is None or current.get("_fingerprint") != fp:
        # Session boundary: finalize prior, start new.
        if current is not None:
            _append_csv(_row_from_session(current))
        new_session = _init_session(snap, alias, now_ms)
        new_session["_fingerprint"] = fp
        _SESSIONS[alias] = new_session
        current = new_session

    _update_session(current, snap, now_ms)
    _write_live_json()
    return _public_view(current, fp)


def _public_view(
    session: Optional[Mapping[str, Any]], fingerprint: Optional[str]
) -> Dict[str, Any]:
    if session is None:
        return {
            "active": False,
            "fingerprint": fingerprint,
        }
    sh_p = session.get("session_high_price")
    sl_p = session.get("session_low_price")
    return {
        "active": True,
        "fingerprint": fingerprint,
        "alias": session.get("alias"),
        "session_type": _classify_session_type(
            session.get("anchor_hhmm") or "",
            session.get("anchor_tz") or "",
        ),
        "session_date_ct": _to_date_ct(session.get("session_anchor_ms")),
        "session_day_of_week": _to_dow_ct(session.get("session_anchor_ms")),
        "anchor_hhmm": session.get("anchor_hhmm"),
        "or_high": session.get("or_high"),
        "session_high_price": sh_p,
        "session_high_iso_ct": _to_iso_ct(session.get("session_high_ms")),
        "session_low_price": sl_p,
        "session_low_iso_ct": _to_iso_ct(session.get("session_low_ms")),
        "session_range": (None if (sh_p is None or sl_p is None) else round(sh_p - sl_p, 4)),
        "or_low": session.get("or_low"),
        "or_width": session.get("or_width"),
        "rotation_unit_pts": session.get("rotation_unit_pts"),
        "first_commit_iso_ct": _to_iso_ct(session.get("first_commit_ms")),
        "first_commit_direction": session.get("first_commit_direction"),
        "max_rotations_above": session.get("max_rotations_above"),
        "max_rotations_below": session.get("max_rotations_below"),
        "peak_extreme_above_price": session.get("peak_extreme_above_price"),
        "peak_extreme_below_price": session.get("peak_extreme_below_price"),
        "whipsaw_count": session.get("whipsaw_count"),
        "total_observations": session.get("total_observations"),
        "in_progress_status": _final_status(session),
    }


def reset_state(alias: Optional[str] = None) -> None:
    """Test helper. Clears in-memory state for one alias or all."""
    if alias is None:
        _SESSIONS.clear()
    else:
        _SESSIONS.pop(alias, None)


def _override_paths(csv_path: Optional[Path] = None, json_path: Optional[Path] = None) -> Tuple[Path, Path]:
    """Test helper to retarget the disk paths. Returns the prior values."""
    global _LEDGER_CSV_PATH, _LIVE_JSON_PATH
    prior_csv, prior_json = _LEDGER_CSV_PATH, _LIVE_JSON_PATH
    if csv_path is not None:
        _LEDGER_CSV_PATH = csv_path
    if json_path is not None:
        _LIVE_JSON_PATH = json_path
    return prior_csv, prior_json
