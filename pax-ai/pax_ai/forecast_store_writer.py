"""Thin pax-ai-side wrapper that persists validated forecasts.

The actual storage lives in ``bookmap_mcp.pax_forecast_store.PaxForecastStore``
(the mcp-server package). This wrapper is a lazy-import seam so that the
chat path:
- never raises if the mcp-server package is not installed,
- never blocks the SSE thread on disk I/O for more than the SQLite
  transaction it owns,
- can be turned on/off at runtime via ``forecast.enabled`` in
  ``pax_ai_config.json``.

This module intentionally has no module-level side effects. It does not
open the DB at import time; the store is opened per call and closed
immediately so a long-running chat does not pin a file handle.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import config


_LAST_MISSING_LOG = False


def is_enabled() -> bool:
    return bool(config.get("forecast.enabled", False))


def resolved_store_path() -> Path:
    raw = config.get("forecast.store_path",
                     "D:/BookmapLogs/pax-forecast.db") or \
          "D:/BookmapLogs/pax-forecast.db"
    return Path(raw)


def persist_validated(forecast: Dict[str, Any],
                       *,
                       chat_run_id: Optional[str] = None,
                       digest_sha256: Optional[str] = None,
                       snapshot_sha256: Optional[str] = None,
                       store_path: Optional[Path] = None) -> bool:
    """Persist a single validated forecast. Returns True on success.

    Returns False (silently) for any of:
    - ``forecast.enabled`` is False
    - mcp-server not installed (bookmap_mcp.pax_forecast_store missing)
    - the forecast dict is missing required fields
    - any disk / sqlite / other OS error
    """
    global _LAST_MISSING_LOG
    if not is_enabled():
        return False
    if not isinstance(forecast, dict):
        return False
    path = Path(store_path) if store_path else resolved_store_path()
    try:
        from bookmap_mcp.pax_forecast_store import PaxForecastStore  # type: ignore
    except Exception as exc:
        if not _LAST_MISSING_LOG:
            sys.stderr.write(
                f"[forecast_writer] bookmap_mcp.pax_forecast_store not "
                f"importable ({exc}); forecast capture disabled until "
                f"mcp-server is installed.\n"
            )
            _LAST_MISSING_LOG = True
        return False
    try:
        with PaxForecastStore(path) as s:
            s.record_validated(
                forecast,
                chat_run_id=chat_run_id,
                digest_sha256=digest_sha256,
                snapshot_sha256=snapshot_sha256,
            )
        return True
    except Exception as exc:
        sys.stderr.write(f"[forecast_writer] persist failed: {exc}\n")
        return False


__all__ = ["is_enabled", "persist_validated", "resolved_store_path"]
