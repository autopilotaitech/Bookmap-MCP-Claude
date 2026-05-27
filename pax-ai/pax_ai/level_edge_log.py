"""Append-only JSONL log of plotted level-edge signals + forward outcomes.

Slice 3 of the live edge loop (reports/pax-ai-full-project-audit-2026-05-26.md).
Every false -> true `actionable` transition in `/api/pax/levels/edge` becomes
one row in `YYYY-MM-DD.open.jsonl`; once +15s / +60s / +300s have passed,
the matured row is rewritten into `YYYY-MM-DD.closed.jsonl` with realized R
multiples and an `invalidated` flag.

Hard contracts:
  * Append-only on both files. The open file is NEVER rewritten.
  * Non-actionable rows do not log. WAIT rows do not log.
  * Signals with missing stop_price or zero stop distance do not log.
  * Logging errors must not break the level-edge endpoint -- the server
    swallows exceptions and writes one stderr line on failure.

No SQLite. No feature_bus. No research framework.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Module-level state. Lazy hydration on first record_payload call.
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()

# Signal key -> last seen actionable state (True iff the most recent payload
# observed this key as actionable=true). Rising-edge transitions trigger a
# log. Persists across day boundaries within a single process.
_STATE: Dict[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]],
              bool] = {}

# Open events (cross-day): each is the dict that was appended to the open
# JSONL. Backfill mutates these in place; once all three horizons are
# present we move them to the closed JSONL and drop them.
_OPEN_EVENTS: List[Dict[str, Any]] = []

# Set once we have hydrated today's state from disk. Hydration walks the
# current UTC day's open + closed JSONL.
_HYDRATED = False


def _reset_state_for_tests() -> None:
    """Clear module-level state. Tests must call this between cases to
    isolate per-test fixtures. Not for production callsites."""
    global _HYDRATED
    with _LOCK:
        _STATE.clear()
        _OPEN_EVENTS.clear()
        _HYDRATED = False


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_HORIZONS_MS = (15_000, 60_000, 300_000)
_HORIZON_KEYS = ("mid_at_15s", "mid_at_60s", "mid_at_300s")
_REALIZED_KEYS = ("realized_R_15s", "realized_R_60s", "realized_R_300s")


def default_root() -> Path:
    """`%LOCALAPPDATA%\\pax-ai\\level-edge-log` on Windows, with safe
    Unix fallback."""
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        local = str(Path.home() / ".local" / "share")
    return Path(local) / "pax-ai" / "level-edge-log"


def _utc_date_str(ts_ms: int) -> str:
    return (_dt.datetime.fromtimestamp(ts_ms / 1000.0, tz=_dt.timezone.utc)
            .strftime("%Y-%m-%d"))


def _open_path(date_str: str, root: Optional[Path]) -> Path:
    base = root if root is not None else default_root()
    return Path(base) / f"{date_str}.open.jsonl"


def _closed_path(date_str: str, root: Optional[Path]) -> Path:
    base = root if root is not None else default_root()
    return Path(base) / f"{date_str}.closed.jsonl"


# ---------------------------------------------------------------------------
# Pure helpers (testable in isolation)
# ---------------------------------------------------------------------------

def signal_key(row: Dict[str, Any], alias: Optional[str]) -> Tuple[
        Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Stable identity for a level-edge signal across calls.

    Key tuple is (alias, level_label, direction, setup). `direction` here
    is the row's `raw_direction` (the upstream composite classification)
    when it is LONG/SHORT, falling back to the chart-facing `direction`
    otherwise. Using raw_direction means a level that flips
    actionable->non-actionable (e.g. proximity drops, thesis gate trips)
    keeps the same key across calls, so the rearm transition is detected
    correctly when it later becomes actionable again.
    """
    if not isinstance(row, dict):
        return (alias, None, None, None)
    raw = row.get("raw_direction")
    direction = raw if raw in ("LONG", "SHORT") else row.get("direction")
    return (alias, row.get("label"), direction, row.get("setup"))


def should_log_transition(prev_state: bool, row: Dict[str, Any]) -> bool:
    """True iff the row represents a false -> true actionable rising edge.

    A non-actionable row, a WAIT direction, or any non-LONG/SHORT direction
    never logs. A row whose key was already True does not re-log.
    """
    if not isinstance(row, dict):
        return False
    if not row.get("actionable"):
        return False
    direction = row.get("direction")
    if direction not in ("LONG", "SHORT"):
        return False
    return not prev_state


def build_open_event(payload: Dict[str, Any], row: Dict[str, Any],
                      now_ms: int) -> Optional[Dict[str, Any]]:
    """Construct the open-row dict, or None if the row is unloggable.

    Returns None when:
      * stop_price is missing
      * mid_at_signal is missing
      * stop distance == 0 (cannot compute R)
    """
    stop_price = row.get("stop_price")
    mid_at_signal = row.get("mid_at_signal")
    direction = row.get("direction")
    if stop_price is None or mid_at_signal is None:
        return None
    try:
        stop_distance = abs(float(mid_at_signal) - float(stop_price))
    except (TypeError, ValueError):
        return None
    if stop_distance == 0.0:
        return None
    if direction not in ("LONG", "SHORT"):
        return None
    alias = payload.get("alias") if isinstance(payload, dict) else None
    snapshot_ts_ms = row.get("snapshot_ts_ms")
    if snapshot_ts_ms is None and isinstance(payload, dict):
        snapshot_ts_ms = payload.get("asOfMs")
    top_drivers = row.get("top_drivers")
    if not isinstance(top_drivers, list):
        top_drivers = []
    else:
        top_drivers = [str(x) for x in top_drivers if isinstance(x, str)]
    return {
        "signal_id":      uuid.uuid4().hex,
        "ts_ms":          int(now_ms),
        "alias":          alias,
        "level_label":    row.get("label"),
        "level_price":    row.get("price"),
        "mid_at_signal":  float(mid_at_signal),
        "direction":      direction,
        "setup":          row.get("setup"),
        "score_R":        row.get("score_R"),
        "confidence":     row.get("confidence"),
        "size_tier":      row.get("size_tier"),
        "stop_price":     float(stop_price),
        "top_drivers":    top_drivers,
        "snapshot_ts_ms": snapshot_ts_ms,
        "source":         "levels_edge",
    }


def realized_r(direction: Optional[str], mid0: Optional[float],
                future_mid: Optional[float],
                stop_price: Optional[float]) -> Optional[float]:
    """R-multiple realized between entry mid and a future mid.

      LONG  : (future_mid - mid0) / stop_distance
      SHORT : (mid0 - future_mid) / stop_distance

    Returns None when any input is missing or stop distance is zero.
    """
    if direction not in ("LONG", "SHORT"):
        return None
    if mid0 is None or future_mid is None or stop_price is None:
        return None
    try:
        m0 = float(mid0)
        fm = float(future_mid)
        sp = float(stop_price)
    except (TypeError, ValueError):
        return None
    stop_distance = abs(m0 - sp)
    if stop_distance == 0.0:
        return None
    if direction == "LONG":
        return (fm - m0) / stop_distance
    return (m0 - fm) / stop_distance


def backfill_open_events(open_events: List[Dict[str, Any]],
                          snap: Dict[str, Any], now_ms: int) -> Tuple[
                              List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Walk the open list and (a) fill matured horizon mids, (b) emit
    closed rows for events that have all three horizons populated.

    Returns (still_open, newly_closed). Mutates entries of open_events in
    place to record matured mids; newly-closed events are removed from the
    returned still_open list.
    """
    book = snap.get("book") if isinstance(snap, dict) else None
    if not isinstance(book, dict):
        book = {}
    mid_raw = book.get("mid")
    mid = None
    if mid_raw is not None:
        try:
            mid = float(mid_raw)
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
            for horizon_ms, key in zip(_HORIZONS_MS, _HORIZON_KEYS):
                if ev.get(key) is None and elapsed >= horizon_ms:
                    ev[key] = mid
        if all(ev.get(k) is not None for k in _HORIZON_KEYS):
            closed = dict(ev)
            for h_key, r_key in zip(_HORIZON_KEYS, _REALIZED_KEYS):
                closed[r_key] = realized_r(closed.get("direction"),
                                            closed.get("mid_at_signal"),
                                            closed.get(h_key),
                                            closed.get("stop_price"))
            r_values = [closed[k] for k in _REALIZED_KEYS
                        if closed.get(k) is not None]
            closed["invalidated"] = any(r <= -1.0 for r in r_values)
            newly_closed.append(closed)
        else:
            still_open.append(ev)
    return still_open, newly_closed


# ---------------------------------------------------------------------------
# Disk I/O
# ---------------------------------------------------------------------------

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
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out


def _hydrate_if_needed(now_ms: int, root: Optional[Path]) -> None:
    """Load today's open + closed JSONL on first call. Sets _STATE so a
    still-actionable key after process restart doesn't re-log."""
    global _HYDRATED
    if _HYDRATED:
        return
    today = _utc_date_str(now_ms)
    closed = _read_jsonl(_closed_path(today, root))
    closed_ids = {ev.get("signal_id") for ev in closed if ev.get("signal_id")}
    for ev in _read_jsonl(_open_path(today, root)):
        sid = ev.get("signal_id")
        key = signal_key({"label": ev.get("level_label"),
                            "direction": ev.get("direction"),
                            "setup": ev.get("setup")},
                           ev.get("alias"))
        _STATE[key] = True
        if sid and sid not in closed_ids:
            _OPEN_EVENTS.append(ev)
    _HYDRATED = True


# ---------------------------------------------------------------------------
# Public recorder
# ---------------------------------------------------------------------------

def record_payload(payload: Dict[str, Any], snap: Dict[str, Any],
                    now_ms: Optional[int] = None,
                    root: Optional[Path] = None) -> Dict[str, Any]:
    """Walk one /api/pax/levels/edge payload + the source snapshot, append
    new rising-edge opens, run backfill, append matured closes. Returns
    a small summary dict {opened, closed, open_path, closed_path}."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if root is not None and not isinstance(root, Path):
        root = Path(root)
    if not isinstance(payload, dict):
        payload = {}
    if not isinstance(snap, dict):
        snap = {}
    levels = payload.get("levels") or []
    if not isinstance(levels, list):
        levels = []

    alias = payload.get("alias")
    today = _utc_date_str(now_ms)
    open_path = _open_path(today, root)
    closed_path = _closed_path(today, root)

    opened = 0
    closed_count = 0
    with _LOCK:
        _hydrate_if_needed(now_ms, root)

        # Rising-edge opens. Walk all levels (actionable or not) so we can
        # rearm keys whose actionability fell to false.
        for row in levels:
            if not isinstance(row, dict):
                continue
            key = signal_key(row, alias)
            prev = _STATE.get(key, False)
            if should_log_transition(prev, row):
                ev = build_open_event(payload, row, now_ms)
                if ev is None:
                    # Missing stop / zero stop distance -- do NOT log this
                    # signal, and do NOT mark state True. We want to log it
                    # on the next call if the stop becomes available.
                    continue
                # Append to the event's own ts_ms day file so daily reports
                # always read a coherent open+closed pair per UTC date.
                ev_day = _utc_date_str(ev["ts_ms"])
                _append_jsonl(_open_path(ev_day, root), ev)
                _OPEN_EVENTS.append(ev)
                _STATE[key] = True
                opened += 1
            else:
                # Update state to reflect current observation. Non-actionable
                # / WAIT rows reset the key so a later actionable transition
                # is logged.
                if row.get("actionable") and row.get("direction") in ("LONG", "SHORT"):
                    _STATE[key] = True
                else:
                    _STATE[key] = False

        # Backfill: fill matured horizon mids, emit closed rows.
        still_open, newly_closed = backfill_open_events(_OPEN_EVENTS, snap, now_ms)
        # Replace _OPEN_EVENTS contents in place (preserve identity).
        _OPEN_EVENTS[:] = still_open
        for ev in newly_closed:
            ev_day = _utc_date_str(ev["ts_ms"])
            _append_jsonl(_closed_path(ev_day, root), ev)
            closed_count += 1

    return {
        "opened":      opened,
        "closed":      closed_count,
        "open_path":   str(open_path),
        "closed_path": str(closed_path),
    }


def record_payload_safe(payload: Dict[str, Any], snap: Dict[str, Any],
                         now_ms: Optional[int] = None,
                         root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """`record_payload` wrapper that NEVER raises. Logs one stderr line on
    failure and returns None. Use from the HTTP handler so a disk hiccup
    can never 500 the level-edge endpoint."""
    try:
        return record_payload(payload, snap, now_ms=now_ms, root=root)
    except Exception as exc:  # noqa: BLE001
        try:
            sys.stderr.write(
                f"[level_edge_log] record_payload failed: {exc!r}\n")
            sys.stderr.flush()
        except Exception:
            pass
        return None
