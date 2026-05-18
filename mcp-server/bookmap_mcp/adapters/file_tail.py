"""FileTailAdapter — consume a continuously-written CSV by tailing the
file. Survives logrotate (inode change) and truncation (file size shrinks
below current read position).

Designed for a setup where another process writes a tick CSV with a header
row plus appended data rows; this adapter feeds the appended rows to the
daemon as snapshots.

Polling model (no asyncio): on each `next_snapshot()` call, attempts to
read one complete line within `poll_timeout_ms`. If no line is ready by
the deadline, returns None — the daemon loop sleeps briefly and tries
again. EOF on the file is not terminal; we keep polling for appends.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .base import AdapterHealth, Snapshot
from .csv_replay import csv_row_to_snapshot


class FileTailAdapter:
    name = "file_tail"

    def __init__(self, path: Path, alias: str = "TAIL",
                  default_tick: float = 0.25,
                  poll_timeout_ms: int = 1000,
                  from_start: bool = False) -> None:
        self.path = Path(path)
        self._alias_default = alias
        self.default_tick = default_tick
        self.poll_timeout_ms = poll_timeout_ms
        self.from_start = from_start
        self._fh = None
        self._inode: Optional[int] = None
        self._header: Optional[List[str]] = None
        self._row_count = 0
        self._last_snapshot_ms = 0
        self._state: Dict[str, Any] = {
            "last_mid": None, "synth_nanos": 0, "last_snapshot_ms": 0,
        }
        self._errors: List[str] = []
        # Per-poll rotation handler counter for tests / debug.
        self._rotations_observed = 0

    # ─── DataAdapter interface ───────────────────────────────────────

    def start(self) -> None:
        if self._fh is not None:
            return
        if not self.path.exists():
            raise FileNotFoundError(f"file not found: {self.path}")
        self._fh = open(self.path, "r", encoding="utf-8", newline="")
        self._header = None
        if not self.from_start:
            # Read the first line as the header so subsequent appended data
            # rows know their column names, then jump to end so we only
            # process NEW writes.
            first = self._fh.readline()
            if first and first.strip():
                self._header = [c.strip() for c in first.strip().split(",")]
            self._fh.seek(0, os.SEEK_END)
        self._inode = self._stat_inode()

    def stop(self) -> None:
        if self._fh is None: return
        try: self._fh.close()
        except Exception: pass
        self._fh = None

    def next_snapshot(self) -> Optional[Snapshot]:
        if self._fh is None:
            return None
        deadline = time.monotonic() + (self.poll_timeout_ms / 1000.0)
        while True:
            # Quickly check for rotation / truncation between reads.
            self._maybe_reopen_on_rotation()
            line = self._fh.readline()
            if line:
                if not line.endswith("\n"):
                    # Partial line — back up so we re-read after the next write.
                    self._fh.seek(self._fh.tell() - len(line.encode("utf-8")))
                else:
                    snap = self._line_to_snapshot(line.rstrip("\r\n"))
                    if snap is not None:
                        self._row_count += 1
                        return snap
                    # Header consumed; loop to read next data line.
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.02)

    def health(self) -> AdapterHealth:
        if self._fh is None:
            return AdapterHealth(status="ok", detail="not started",
                                  snapshots_emitted=self._row_count)
        if self._errors:
            return AdapterHealth(status="error",
                                  detail=f"{len(self._errors)} bad rows",
                                  last_snapshot_ms=self._last_snapshot_ms,
                                  snapshots_emitted=self._row_count,
                                  errors=list(self._errors))
        return AdapterHealth(status="ok",
                              detail=f"rotations={self._rotations_observed}",
                              last_snapshot_ms=self._last_snapshot_ms,
                              snapshots_emitted=self._row_count)

    # ─── internals ──────────────────────────────────────────────────

    def _stat_inode(self) -> Optional[int]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        # On Windows the inode is implemented but may not be unique across
        # rotations; fall back to st_ctime + st_size signature.
        return (st.st_ino, st.st_dev, int(st.st_ctime_ns)) \
            if hasattr(st, "st_ctime_ns") else st.st_ino

    def _maybe_reopen_on_rotation(self) -> None:
        """If the file was rotated (new inode) or truncated (size below our
        read position), close + reopen and reset state."""
        if self._fh is None: return
        try:
            cur_ino = self._stat_inode()
            cur_size = os.stat(self.path).st_size
        except (FileNotFoundError, OSError):
            # File temporarily missing during rotation — leave fh open
            # and try again on next poll.
            return
        try:
            pos = self._fh.tell()
        except (OSError, ValueError):
            return
        rotated = (cur_ino is not None and self._inode is not None
                    and cur_ino != self._inode)
        truncated = (cur_size is not None and cur_size < pos)
        if not (rotated or truncated):
            return
        self._rotations_observed += 1
        try: self._fh.close()
        except Exception: pass
        self._fh = open(self.path, "r", encoding="utf-8", newline="")
        self._fh.seek(0, os.SEEK_SET)
        self._inode = cur_ino
        # Re-read the header from the new file.
        self._header = None

    def _line_to_snapshot(self, line: str) -> Optional[Snapshot]:
        # Skip empty lines.
        line = line.strip()
        if not line: return None
        cells = [c.strip() for c in line.split(",")]
        # First non-empty line of a new file is treated as header.
        if self._header is None:
            self._header = cells
            return None
        row = dict(zip(self._header, cells))
        try:
            snap = csv_row_to_snapshot(
                row, alias_default=self._alias_default,
                default_tick=self.default_tick, state=self._state,
                source_name=self.name)
            self._last_snapshot_ms = self._state.get("last_snapshot_ms", 0)
            return snap
        except Exception as exc:    # pragma: no cover — defensive
            self._errors.append(
                f"row {self._row_count + 1}: {type(exc).__name__}: {exc}")
            return None
