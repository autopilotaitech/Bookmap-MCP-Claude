"""DataAdapter Protocol — every adapter (CSV replay, file tail, future
provider, optional Bookmap live) must implement this surface so the Pax
daemon is source-agnostic."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


# A snapshot is any dict; schema validation lives in `snapshot.py`. Adapters
# build dicts that the signal engine and sim engine consume.
Snapshot = Dict[str, Any]


@dataclass
class AdapterHealth:
    """Adapter status for the daemon's heartbeat journal.

    Status values:
      ok     — operating normally, snapshots flowing
      stale  — no snapshot in the last health-check window
      error  — adapter raised; consult `detail`
      eof    — source is finished (replay adapters at EOF)
    """
    status: str = "ok"
    detail: str = ""
    last_snapshot_ms: int = 0
    snapshots_emitted: int = 0
    errors: List[str] = field(default_factory=list)


@runtime_checkable
class DataAdapter(Protocol):
    """The contract every data source must satisfy.

    Implementations are typically constructed with source-specific config
    (paths, URLs, credentials) and exposed to the daemon via this interface.
    """

    name: str

    def start(self) -> None:
        """Open the source. Called once before the first next_snapshot()."""
        ...

    def stop(self) -> None:
        """Close the source. Idempotent; called on graceful shutdown."""
        ...

    def next_snapshot(self) -> Optional[Snapshot]:
        """Return the next snapshot or None on EOF / shutdown.

        Implementations may block briefly (e.g. file-tail polling) but
        must return None rather than hang indefinitely so the daemon can
        check shutdown signals.
        """
        ...

    def health(self) -> AdapterHealth:
        """Current adapter state for the daemon's heartbeat row."""
        ...
