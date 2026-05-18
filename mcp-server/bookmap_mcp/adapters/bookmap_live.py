"""BookmapLiveAdapter — wrap the existing fetch_snapshot / BridgeClient
path as a DataAdapter. Read-only; never imports or calls the live-order
MCP tools (test_bookmap_live verifies this by grepping the module source).

This adapter exists so the same `pax_daemon` loop can drive against a live
Bookmap session when desired. It is optional — every other phase works
without it. Keep this fact intact: do NOT import this adapter from
non-optional code paths.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import AdapterHealth, Snapshot

# Lazy import of fetch_snapshot so test environments without a configured
# bridge can still import this module for the lint test below.
def _fetch_snapshot():
    from ..dashboard import fetch_snapshot
    return fetch_snapshot()


class BookmapLiveAdapter:
    name = "bookmap_live"

    def __init__(self, alias: str = "NQ",
                  poll_timeout_ms: int = 0) -> None:
        self.alias = alias
        self.poll_timeout_ms = poll_timeout_ms
        self._snapshots = 0
        self._last_snapshot_ms = 0
        self._errors: List[str] = []
        self._stopped = False

    def start(self) -> None:
        self._stopped = False

    def stop(self) -> None:
        self._stopped = True

    def next_snapshot(self) -> Optional[Snapshot]:
        if self._stopped:
            return None
        try:
            snap = _fetch_snapshot()
        except Exception as exc:
            self._errors.append(f"{type(exc).__name__}: {exc}")
            return None
        if not isinstance(snap, dict):
            return None
        # Tag and ensure the schema fields the daemon needs.
        snap["_source"] = self.name
        snap.setdefault("_synthetic", [])
        snap.setdefault("trades", snap.get("trades") or [])
        snap.setdefault("alias", self.alias)
        snap.setdefault("health", "ok")
        if "book" not in snap:
            snap["book"] = {}
        self._snapshots += 1
        self._last_snapshot_ms = int(time.time() * 1000)
        return snap

    def health(self) -> AdapterHealth:
        status = "error" if self._errors else "ok"
        detail = f"{len(self._errors)} errors" if self._errors else "live"
        return AdapterHealth(status=status, detail=detail,
                              last_snapshot_ms=self._last_snapshot_ms,
                              snapshots_emitted=self._snapshots,
                              errors=list(self._errors[-10:]))
