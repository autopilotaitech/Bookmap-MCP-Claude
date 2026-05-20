"""Background snapshot poller.

Runs in a daemon thread. Fetches dashboard /api/snapshot every `poll_ms`
milliseconds (clamped [500, 3000]). Stores the latest body + receive-time +
status in module-local state under a single lock. HTTP handlers read from
this without ever calling out to the dashboard.

Design follows the OpenRange PaxHeatwaveFetcher pattern: never block on a
foreign-process callback. Failures retry with bounded backoff so a brief
dashboard restart doesn't permanently shut down the strip.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

from . import DASHBOARD_URL, config


_LOCK = threading.Lock()
_LATEST: Optional[Dict[str, Any]] = None
_LATEST_AT_MS: int = 0
_CONSECUTIVE_FAILS: int = 0
_LAST_ERROR: Optional[str] = None
_HTTP_TIMEOUT_S = 3.0
_BACKOFF_THRESHOLD = 3
_MAX_BACKOFF_MS = 5000
_STOP_EVT = threading.Event()
_THREAD: Optional[threading.Thread] = None


def _fetch_once() -> Tuple[bool, Dict[str, Any], Optional[str]]:
    try:
        with urllib.request.urlopen(DASHBOARD_URL, timeout=_HTTP_TIMEOUT_S) as r:
            raw = r.read()
        body = json.loads(raw.decode("utf-8"))
        return True, body, None
    except urllib.error.URLError as exc:
        return False, {}, str(getattr(exc, "reason", exc))
    except json.JSONDecodeError as exc:
        return False, {}, f"non-JSON body: {exc}"
    except Exception as exc:
        return False, {}, f"{type(exc).__name__}: {exc}"


def _loop() -> None:
    global _LATEST, _LATEST_AT_MS, _CONSECUTIVE_FAILS, _LAST_ERROR
    sys.stderr.write("[poller] started\n")
    while not _STOP_EVT.is_set():
        poll_ms = int(config.get("poll_ms", 1000))
        poll_ms = max(500, min(3000, poll_ms))
        ok, body, err = _fetch_once()
        now_ms = int(time.time() * 1000)
        with _LOCK:
            if ok:
                _LATEST = body
                _LATEST_AT_MS = now_ms
                _CONSECUTIVE_FAILS = 0
                _LAST_ERROR = None
            else:
                _CONSECUTIVE_FAILS += 1
                _LAST_ERROR = err
        # Backoff after sustained failures
        wait_ms = poll_ms
        if _CONSECUTIVE_FAILS >= _BACKOFF_THRESHOLD:
            wait_ms = min(_MAX_BACKOFF_MS, poll_ms * (2 ** (_CONSECUTIVE_FAILS - _BACKOFF_THRESHOLD + 1)))
        _STOP_EVT.wait(wait_ms / 1000.0)
    sys.stderr.write("[poller] stopped\n")


def start() -> None:
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _STOP_EVT.clear()
        _THREAD = threading.Thread(target=_loop, name="pax-ai-poller", daemon=True)
        _THREAD.start()


def stop() -> None:
    _STOP_EVT.set()


def latest() -> Tuple[Optional[Dict[str, Any]], int, int, int, Optional[str]]:
    """Return (snapshot, as_of_ms, age_ms, consecutive_fails, last_error).

    snapshot may be None if the poller has never had a successful fetch yet.
    """
    with _LOCK:
        snap = _LATEST
        ts = _LATEST_AT_MS
        fails = _CONSECUTIVE_FAILS
        err = _LAST_ERROR
    age = (int(time.time() * 1000) - ts) if ts > 0 else -1
    return snap, ts, age, fails, err
