"""Append-only JSONL log of attack-response states + forward mids.

Stage 5 of the chart-first overhaul (plan
reports/pax-ai-attack-response-plan-2026-05-27.md). Sibling of
level_edge_log but with these differences:

  * Logs WATCH states too. The point of this log is to capture the
    evidence chain BEFORE statistical edge is proven, so the report
    layer can later compute hit-rates per (state, bias, level, OR
    width, etc.) and decide which buckets earn proven_edge=true.
  * Does NOT require a stop_price. Realized_R is only filled when
    one is available (currently never -- the attack_response payload
    does not emit stops). Realized POINTS at +15s / +60s / +300s
    horizons is always computed.
  * Same hard invariants: append-only, safe wrapper, NEVER breaks
    the endpoint, malformed rows are skipped on read.

Files:
  %LOCALAPPDATA%\\pax-ai\\attack-response-log\\YYYY-MM-DD.open.jsonl
  %LOCALAPPDATA%\\pax-ai\\attack-response-log\\YYYY-MM-DD.closed.jsonl
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_LOCK = threading.Lock()

# Per-(alias, location, state, bias) "currently true" cache. Tracks
# rising-edge transitions (false -> true). NONE / NEUTRAL / NO_EDGE
# does not log.
_STATE_KEY = Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]
_STATE: Dict[_STATE_KEY, bool] = {}

# Open events still awaiting backfill.
_OPEN_EVENTS: List[Dict[str, Any]] = []

_HYDRATED = False

_HORIZONS_MS = (15_000, 60_000, 300_000)
_MID_KEYS    = ("mid_at_15s", "mid_at_60s", "mid_at_300s")
_PTS_KEYS    = ("realized_pts_15s", "realized_pts_60s", "realized_pts_300s")
_R_KEYS      = ("realized_R_15s", "realized_R_60s", "realized_R_300s")
_DIR_KEYS    = ("dir_sign_15s", "dir_sign_60s", "dir_sign_300s")


def _reset_state_for_tests() -> None:
    global _HYDRATED
    with _LOCK:
        _STATE.clear()
        _OPEN_EVENTS.clear()
        _HYDRATED = False


# --- paths ---------------------------------------------------------------

def default_root() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / ".local" / "share")
    return Path(local) / "pax-ai" / "attack-response-log"


def _utc_date_str(ts_ms: int) -> str:
    return (_dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=_dt.timezone.utc)
            .strftime("%Y-%m-%d"))


def _open_path(date_str: str, root: Optional[Path]) -> Path:
    base = root if root is not None else default_root()
    return Path(base) / f"{date_str}.open.jsonl"


def _closed_path(date_str: str, root: Optional[Path]) -> Path:
    base = root if root is not None else default_root()
    return Path(base) / f"{date_str}.closed.jsonl"


# --- pure helpers --------------------------------------------------------

def state_key(state: Dict[str, Any]) -> _STATE_KEY:
    if not isinstance(state, dict):
        return (None, None, None, None)
    return (state.get("_alias"), state.get("location"),
            state.get("state"), state.get("bias"))


def should_log_transition(prev: bool, state: Dict[str, Any]) -> bool:
    """True iff the state crosses false -> true as a logged WATCH row.

    NO_EDGE / NEUTRAL never log: they describe absence of evidence.
    Repeats of an already-true key do not re-log.
    """
    if not isinstance(state, dict):
        return False
    if state.get("state") == "NO_EDGE":
        return False
    if state.get("bias") == "NEUTRAL":
        return False
    return not prev


def build_open_event(payload: Dict[str, Any], state: Dict[str, Any],
                      snap: Dict[str, Any], now_ms: int) -> Optional[Dict[str, Any]]:
    """Construct the open-row dict from one attack-response state row.

    Returns None when mid is unknown -- realized points cannot be
    computed without mid_at_signal. Stop is optional and stays null
    when missing.
    """
    book = snap.get("book") if isinstance(snap, dict) else None
    if not isinstance(book, dict):
        book = {}
    mid_raw = book.get("mid")
    try:
        mid = float(mid_raw) if mid_raw is not None else None
    except (TypeError, ValueError):
        mid = None
    if mid is None:
        return None

    or_levels = snap.get("or_levels") if isinstance(snap, dict) else {}
    if not isinstance(or_levels, dict):
        or_levels = {}
    try:
        or_width_pts = float(or_levels.get("orWidthPts")) \
                if or_levels.get("orWidthPts") is not None else None
    except (TypeError, ValueError):
        or_width_pts = None

    session = snap.get("session") if isinstance(snap, dict) else {}
    if not isinstance(session, dict):
        session = {}
    vwap_bias = snap.get("vwap_bias") if isinstance(snap, dict) else {}
    if not isinstance(vwap_bias, dict):
        vwap_bias = {}
    flow = snap.get("flow") if isinstance(snap, dict) else {}
    if not isinstance(flow, dict):
        flow = {}

    drivers = state.get("drivers") or []
    if not isinstance(drivers, list):
        drivers = []
    drivers = [str(d) for d in drivers if isinstance(d, (str, int, float))]

    return {
        "signal_id":     state.get("id"),
        "ts_ms":         int(now_ms),
        "alias":         payload.get("alias"),
        "location":      state.get("location"),
        "level_price":   state.get("level_price"),
        "mid_at_signal": mid,
        "state":         state.get("state"),
        "bias":          state.get("bias"),
        "attack":        state.get("attack"),
        "response":      state.get("response"),
        "drivers":       drivers,
        "confidence":    state.get("confidence"),
        "or_width_pts":  or_width_pts,
        "vwap_regime":   vwap_bias.get("regime"),
        "vwap_sigma_z":  vwap_bias.get("sigma_z"),
        "flow_regime":   flow.get("regime"),
        "session_code":  session.get("code"),
        "anchor_mode":   session.get("anchorMode"),
        "stop_price":    state.get("stop_price"),   # always None today
        "proven_edge":   bool(state.get("proven_edge")),
        "sample_n":      state.get("sample_n"),
        "edge_R_60s":    state.get("edge_R_60s"),
        "source":        "attack_response",
    }


def realized_points(bias: Optional[str], mid0: Optional[float],
                     future_mid: Optional[float]) -> Optional[float]:
    """Direction-normalized point move at a horizon.

      BULL_WATCH: future_mid - mid0
      BEAR_WATCH: mid0 - future_mid
      otherwise: None
    """
    if mid0 is None or future_mid is None:
        return None
    try:
        m0 = float(mid0); fm = float(future_mid)
    except (TypeError, ValueError):
        return None
    if bias == "BULL_WATCH":
        return fm - m0
    if bias == "BEAR_WATCH":
        return m0 - fm
    return None


def realized_r_from_stop(bias: Optional[str], mid0: Optional[float],
                          future_mid: Optional[float],
                          stop_price: Optional[float]) -> Optional[float]:
    if stop_price is None or mid0 is None or future_mid is None:
        return None
    if bias not in ("BULL_WATCH", "BEAR_WATCH"):
        return None
    try:
        m0 = float(mid0); fm = float(future_mid); sp = float(stop_price)
    except (TypeError, ValueError):
        return None
    distance = abs(m0 - sp)
    if distance == 0.0:
        return None
    pts = realized_points(bias, m0, fm)
    if pts is None:
        return None
    return pts / distance


def backfill_open_events(open_events: List[Dict[str, Any]],
                          snap: Dict[str, Any], now_ms: int
                          ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Walk open list. Mature mids at horizons; close rows whose three
    horizons are all populated. Pure-ish: mutates entries in place to
    record matured fields."""
    book = snap.get("book") if isinstance(snap, dict) else None
    if not isinstance(book, dict):
        book = {}
    mid_raw = book.get("mid")
    try:
        mid = float(mid_raw) if mid_raw is not None else None
    except (TypeError, ValueError):
        mid = None

    still_open: List[Dict[str, Any]] = []
    newly_closed: List[Dict[str, Any]] = []
    for ev in open_events:
        if not isinstance(ev, dict):
            continue
        try:
            ts_ms = int(ev.get("ts_ms"))
        except (TypeError, ValueError):
            continue
        elapsed = int(now_ms) - ts_ms
        if mid is not None:
            for horizon_ms, mkey in zip(_HORIZONS_MS, _MID_KEYS):
                if ev.get(mkey) is None and elapsed >= horizon_ms:
                    ev[mkey] = mid
        if all(ev.get(k) is not None for k in _MID_KEYS):
            closed = dict(ev)
            for mkey, pkey, rkey, dkey in zip(_MID_KEYS, _PTS_KEYS, _R_KEYS, _DIR_KEYS):
                pts = realized_points(closed.get("bias"),
                                       closed.get("mid_at_signal"),
                                       closed.get(mkey))
                closed[pkey] = pts
                closed[dkey] = None if pts is None else (1 if pts > 0 else
                                                          (-1 if pts < 0 else 0))
                closed[rkey] = realized_r_from_stop(
                        closed.get("bias"),
                        closed.get("mid_at_signal"),
                        closed.get(mkey),
                        closed.get("stop_price"))
            newly_closed.append(closed)
        else:
            still_open.append(ev)
    return still_open, newly_closed


# --- disk I/O ------------------------------------------------------------

def _append_jsonl(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, separators=(",", ":")) + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
    except OSError:
        return []
    return out


def _hydrate_if_needed(now_ms: int, root: Optional[Path]) -> None:
    global _HYDRATED
    if _HYDRATED:
        return
    today = _utc_date_str(now_ms)
    closed = _read_jsonl(_closed_path(today, root))
    closed_ids = {ev.get("signal_id") for ev in closed if ev.get("signal_id")}
    for ev in _read_jsonl(_open_path(today, root)):
        sid = ev.get("signal_id")
        key: _STATE_KEY = (ev.get("alias"), ev.get("location"),
                           ev.get("state"), ev.get("bias"))
        _STATE[key] = True
        if sid and sid not in closed_ids:
            _OPEN_EVENTS.append(ev)
    _HYDRATED = True


# --- public recorder -----------------------------------------------------

def record_payload(payload: Dict[str, Any], snap: Dict[str, Any],
                    now_ms: Optional[int] = None,
                    root: Optional[Path] = None) -> Dict[str, Any]:
    """Record one attack-response payload + run backfill.

    `payload` is the dict returned by `compute_attack_response`.
    """
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if root is not None and not isinstance(root, Path):
        root = Path(root)
    if not isinstance(payload, dict):
        payload = {}
    if not isinstance(snap, dict):
        snap = {}

    states = payload.get("states") or []
    if not isinstance(states, list):
        states = []
    alias = payload.get("alias")

    opened = 0
    closed_count = 0
    today = _utc_date_str(int(now_ms))
    open_p = _open_path(today, root)
    closed_p = _closed_path(today, root)

    with _LOCK:
        _hydrate_if_needed(int(now_ms), root)

        for s in states:
            if not isinstance(s, dict):
                continue
            # Stamp alias on state for key derivation; mirrors classifier's
            # _alias hidden field.
            s_with_alias = dict(s)
            s_with_alias["_alias"] = alias
            key = state_key(s_with_alias)
            prev = _STATE.get(key, False)
            if should_log_transition(prev, s_with_alias):
                ev = build_open_event(payload, s_with_alias, snap, int(now_ms))
                if ev is None:
                    # Mid unknown -> hold off, retry on next call.
                    continue
                ev_day = _utc_date_str(int(ev["ts_ms"]))
                _append_jsonl(_open_path(ev_day, root), ev)
                _OPEN_EVENTS.append(ev)
                _STATE[key] = True
                opened += 1
            else:
                _STATE[key] = (s.get("state") not in (None, "NO_EDGE")
                               and s.get("bias") != "NEUTRAL")

        # Rearm state for keys NOT in this poll's states - if a previously
        # logged key is absent, decay it so a re-emergence next poll counts
        # as a fresh rising edge.
        seen_keys = {state_key({"_alias": alias, **s}) for s in states
                     if isinstance(s, dict)}
        for k in list(_STATE.keys()):
            if k[0] == alias and k not in seen_keys:
                _STATE[k] = False

        still_open, newly_closed = backfill_open_events(_OPEN_EVENTS, snap, int(now_ms))
        _OPEN_EVENTS[:] = still_open
        for ev in newly_closed:
            ev_day = _utc_date_str(int(ev["ts_ms"]))
            _append_jsonl(_closed_path(ev_day, root), ev)
            closed_count += 1

    return {
        "opened":      opened,
        "closed":      closed_count,
        "open_path":   str(open_p),
        "closed_path": str(closed_p),
    }


def record_payload_safe(payload: Dict[str, Any], snap: Dict[str, Any],
                         now_ms: Optional[int] = None,
                         root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """`record_payload` wrapper that NEVER raises. Logs one stderr line on
    failure and returns None. Used from the HTTP handler so a disk hiccup
    can never 500 the attack-response endpoint."""
    try:
        return record_payload(payload, snap, now_ms=now_ms, root=root)
    except Exception as exc:  # noqa: BLE001
        try:
            sys.stderr.write(
                f"[attack_response_log] record_payload failed: {exc!r}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return None
