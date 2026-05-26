"""Auto-fire Pax AI on snapshot trigger events.

Closes the "I shouldn't have to type at a chat box while NQ is moving"
gap: a small daemon polls the same trigger detector the UI uses
(``triggers.compute_triggers``), and for each NEW actionable trigger
runs one background Claude call via ``chat.fire_triggered_turn``. The
captured ``pax_text`` flows through the existing chart-signal +
forecast persistence paths, so a fired trigger surfaces on the Bookmap
chart as an AI marker AND lands in ``pax-forecast.db`` for the
self-training research loop -- no user typing required.

Hard rules:
- Default OFF (``trigger_engine.enabled = false``).
- Per-kind cooldown + global rate limit are HARD caps. Cost cannot run
  away if a single trigger keeps re-firing.
- Failures are silent. The engine can never break chat or the dashboard.
- Never touches live orders; never edits active config / weights / prompts.
- Reads the snapshot via ``poller.latest()`` (same source the chat path
  uses). Does not re-derive any market state.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import config, poller, triggers


_DEFAULT_FIREABLE_KINDS = (
    "LEVEL_APPROACH",
    "TREND_SIGNAL_FIRE",
    "CONVICTION_FLIP",
    "REGIME_CHANGE",
    "MICRO_EVENT",
)

_DEFAULT_MIN_GLOBAL_INTERVAL_SEC = 30
_DEFAULT_COOLDOWN_PER_KIND_SEC = 120
_DEFAULT_TICK_INTERVAL_SEC = 1.0
_DEFAULT_QUEUE_MAX = 8


def is_enabled() -> bool:
    return bool(config.get("trigger_engine.enabled", False))


def _fireable_kinds() -> Tuple[str, ...]:
    val = config.get("trigger_engine.fireable_kinds", _DEFAULT_FIREABLE_KINDS)
    if isinstance(val, (list, tuple)):
        return tuple(str(k) for k in val if isinstance(k, str) and k.strip())
    return _DEFAULT_FIREABLE_KINDS


def _min_global_interval_sec() -> float:
    try:
        v = float(config.get("trigger_engine.min_global_interval_sec",
                              _DEFAULT_MIN_GLOBAL_INTERVAL_SEC))
    except (TypeError, ValueError):
        return float(_DEFAULT_MIN_GLOBAL_INTERVAL_SEC)
    return max(5.0, v)


def _cooldown_per_kind_sec() -> float:
    try:
        v = float(config.get("trigger_engine.cooldown_per_kind_sec",
                              _DEFAULT_COOLDOWN_PER_KIND_SEC))
    except (TypeError, ValueError):
        return float(_DEFAULT_COOLDOWN_PER_KIND_SEC)
    return max(10.0, v)


def _tick_interval_sec() -> float:
    try:
        v = float(config.get("trigger_engine.tick_interval_sec",
                              _DEFAULT_TICK_INTERVAL_SEC))
    except (TypeError, ValueError):
        return _DEFAULT_TICK_INTERVAL_SEC
    return max(0.25, min(10.0, v))


def _deep() -> bool:
    return bool(config.get("trigger_engine.deep", False))


def _trigger_key(trig: Dict[str, Any], alias: Optional[str]) -> Tuple[str, str, str]:
    return (
        (alias or "__default__"),
        str(trig.get("kind") or "?"),
        str(trig.get("label") or "-"),
    )


def _format_user_text(trig: Dict[str, Any]) -> str:
    kind = trig.get("kind") or "?"
    sev = trig.get("severity") or "MED"
    lbl = trig.get("label") or "-"
    head = trig.get("headline") or ""
    details = trig.get("details") or ""
    return (
        f"TRIGGER FIRED: {kind} ({sev})\n"
        f"  label    : {lbl}\n"
        f"  headline : {head}\n"
        f"  details  : {details}\n\n"
        "Give me a one- to two-sentence read on this trigger in the current "
        "snapshot context. If every PAX_FORECAST field can be grounded in the "
        "snapshot, emit a forecast block. If a chart marker at the related "
        "level is warranted, emit a PAX_AI_CHART_SIGNAL block too."
    )


class TriggerEngine:
    """Single-instance daemon. Start/stop is idempotent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop_evt = threading.Event()
        self._tick_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._queue: "queue.Queue[Dict[str, Any]]" = queue.Queue(
            maxsize=_DEFAULT_QUEUE_MAX)
        self._last_fire_by_key: Dict[Tuple[str, str, str], float] = {}
        self._last_global_fire: float = 0.0
        self._fires_total: int = 0
        self._skips_total: int = 0
        # Test hook: when set, fired turns go to this callable instead
        # of chat.fire_triggered_turn (avoids spawning the real CLI).
        self._fire_callable = None

    # -------------------------------------------------------- lifecycle

    def start(self) -> bool:
        with self._lock:
            if self._tick_thread is not None and self._tick_thread.is_alive():
                return False
            self._stop_evt.clear()
            self._tick_thread = threading.Thread(
                target=self._tick_loop,
                name="pax-trigger-engine-tick",
                daemon=True,
            )
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="pax-trigger-engine-worker",
                daemon=True,
            )
            self._tick_thread.start()
            self._worker_thread.start()
            sys.stderr.write("[trigger_engine] started\n")
            return True

    def stop(self, timeout_s: float = 3.0) -> bool:
        with self._lock:
            tt = self._tick_thread
            wt = self._worker_thread
        self._stop_evt.set()
        # Unblock the worker's queue.get with a sentinel.
        try:
            self._queue.put_nowait({"__stop__": True})
        except queue.Full:
            pass
        ok = True
        if tt is not None:
            tt.join(timeout=timeout_s)
            ok = ok and not tt.is_alive()
        if wt is not None:
            wt.join(timeout=timeout_s)
            ok = ok and not wt.is_alive()
        with self._lock:
            self._tick_thread = None
            self._worker_thread = None
        return ok

    def is_running(self) -> bool:
        with self._lock:
            return (self._tick_thread is not None
                    and self._tick_thread.is_alive())

    # --------------------------------------------------------- accounting

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            running = (self._tick_thread is not None
                       and self._tick_thread.is_alive())
            return {
                "enabled":            is_enabled(),
                "running":            running,
                "queue_depth":        self._queue.qsize(),
                "fires_total":        self._fires_total,
                "skips_total":        self._skips_total,
                "last_global_fire_ms": int(self._last_global_fire * 1000),
                "dedup_keys_tracked": len(self._last_fire_by_key),
            }

    # ----------------------------------------------------- detect + enqueue

    def consider_triggers(self,
                           snap: Optional[Dict[str, Any]],
                           trigs: List[Dict[str, Any]],
                           now: float) -> List[Dict[str, Any]]:
        """Filter trigger list to actionable + outside-cooldown entries.

        Pure function over ``self._last_fire_by_key``; mutates the
        dedup map for entries that ARE enqueued. Returns the list of
        triggers selected for firing (in the order produced).
        """
        if not is_enabled():
            return []
        fireable = set(_fireable_kinds())
        cooldown = _cooldown_per_kind_sec()
        alias = (snap or {}).get("alias") if isinstance(snap, dict) else None
        selected: List[Dict[str, Any]] = []
        for t in trigs or []:
            kind = t.get("kind")
            if kind not in fireable:
                continue
            key = _trigger_key(t, alias)
            last = self._last_fire_by_key.get(key, 0.0)
            if (now - last) < cooldown:
                self._skips_total += 1
                continue
            self._last_fire_by_key[key] = now
            payload = dict(t)
            payload["_alias"] = alias
            payload["_enqueued_ms"] = int(now * 1000)
            selected.append(payload)
        return selected

    # ------------------------------------------------------ tick + worker

    def _tick_loop(self) -> None:
        interval = _tick_interval_sec()
        while not self._stop_evt.is_set():
            try:
                if not is_enabled():
                    # Engine disabled mid-run -> sleep until next config
                    # check; don't enqueue anything.
                    self._stop_evt.wait(timeout=interval)
                    continue
                snap, _as_of_ms, age_ms, _fails, _err = poller.latest()
                trigs = triggers.compute_triggers(
                    snap, age_ms if snap is not None else 10**9)
                now = time.time()
                selected = self.consider_triggers(snap, trigs, now)
                for s in selected:
                    try:
                        self._queue.put_nowait(s)
                    except queue.Full:
                        # Queue saturated -> drop (skip accounting); the
                        # cooldown map prevents the same trigger from
                        # immediately re-enqueuing.
                        with self._lock:
                            self._skips_total += 1
                        break
            except Exception as exc:
                sys.stderr.write(
                    f"[trigger_engine] tick loop error: {exc}\n")
            self._stop_evt.wait(timeout=interval)

    def _worker_loop(self) -> None:
        while not self._stop_evt.is_set():
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if not isinstance(item, dict) or item.get("__stop__"):
                continue
            try:
                self._fire_one(item)
            except Exception as exc:
                sys.stderr.write(
                    f"[trigger_engine] fire failed: {exc}\n")

    def _fire_one(self, trig: Dict[str, Any]) -> None:
        # Global rate limit: enforce min interval between any two fires.
        min_interval = _min_global_interval_sec()
        wait_s = (self._last_global_fire + min_interval) - time.time()
        if wait_s > 0:
            # Bounded sleep so stop() still wakes us in <= 1s.
            self._stop_evt.wait(timeout=min(wait_s, 5.0))
            if self._stop_evt.is_set():
                return
            if (self._last_global_fire + min_interval) > time.time():
                return  # still rate-limited; drop this fire to avoid pileup
        self._last_global_fire = time.time()
        with self._lock:
            self._fires_total += 1

        user_text = _format_user_text(trig)
        fire = self._fire_callable
        try:
            if fire is not None:
                fire(user_text, deep=_deep())
            else:
                # Real path: import lazily to break the chat <-> engine
                # import cycle (chat imports do not pull trigger_engine).
                from . import chat as _chat
                _chat.fire_triggered_turn(user_text, deep=_deep())
        except Exception as exc:
            sys.stderr.write(
                f"[trigger_engine] fire_triggered_turn failed: {exc}\n")


# ---------------------------------------------------------- module facade

_ENGINE = TriggerEngine()


def start() -> bool:
    """Start the engine if forecast capture infrastructure is enabled.

    Returns True if a new engine was started, False if already running or
    disabled.
    """
    if not is_enabled():
        return False
    return _ENGINE.start()


def stop(timeout_s: float = 3.0) -> bool:
    return _ENGINE.stop(timeout_s=timeout_s)


def is_running() -> bool:
    return _ENGINE.is_running()


def stats() -> Dict[str, Any]:
    return _ENGINE.stats()


def _reset_for_tests() -> TriggerEngine:
    """Replace the module engine with a fresh instance. Test-only."""
    global _ENGINE
    try:
        _ENGINE.stop(timeout_s=0.5)
    except Exception:
        pass
    _ENGINE = TriggerEngine()
    return _ENGINE


__all__ = [
    "TriggerEngine",
    "is_enabled",
    "is_running",
    "start",
    "stats",
    "stop",
]
