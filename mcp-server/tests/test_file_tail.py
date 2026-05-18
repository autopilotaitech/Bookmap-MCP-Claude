"""Phase 6: FileTailAdapter tests.

Exercises the live-tail behavior: header detection, line-buffered reads
across writes, logrotate handling (inode change), and truncation.
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bookmap_mcp.adapters import FileTailAdapter   # noqa: E402
from bookmap_mcp.adapters.base import DataAdapter  # noqa: E402


def _append(path: Path, line: str) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


HEADER = "ts_iso,alias,mid,or_high,or_low,vwap,regime,bias_score"


def _row(ts: str, mid: float) -> str:
    return f"{ts},NQM6,{mid},20100,20000,20040,TRENDING_UP,0.5"


def test_adapter_satisfies_protocol(tmp_path):
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n", encoding="utf-8")
    a = FileTailAdapter(p)
    assert isinstance(a, DataAdapter)
    assert a.name == "file_tail"


def test_tail_reads_from_end_by_default(tmp_path):
    """from_start=False: rows written BEFORE start() are skipped."""
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n" + _row("2026-05-18T13:30:00+00:00", 100.0) + "\n",
                  encoding="utf-8")
    a = FileTailAdapter(p, alias="NQM6", poll_timeout_ms=50)
    a.start()
    # Pre-existing row at end is skipped; only NEW appends are read.
    _append(p, _row("2026-05-18T13:30:01+00:00", 100.5))
    snap = a.next_snapshot()
    a.stop()
    assert snap is not None
    assert snap["book"]["mid"] == 100.5


def test_tail_from_start_reads_existing_rows(tmp_path):
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n" + _row("2026-05-18T13:30:00+00:00", 100.0) + "\n",
                  encoding="utf-8")
    a = FileTailAdapter(p, alias="NQM6", poll_timeout_ms=50,
                          from_start=True)
    a.start()
    snap = a.next_snapshot()
    a.stop()
    assert snap is not None
    assert snap["book"]["mid"] == 100.0


def test_tail_returns_none_on_timeout(tmp_path):
    """When nothing's been appended, next_snapshot returns None after
    poll_timeout_ms — daemon loop can check shutdown signals."""
    p = tmp_path / "quiet.csv"
    p.write_text(HEADER + "\n", encoding="utf-8")
    a = FileTailAdapter(p, poll_timeout_ms=100)
    a.start()
    t0 = time.monotonic()
    snap = a.next_snapshot()
    elapsed = time.monotonic() - t0
    a.stop()
    assert snap is None
    # Should respect the timeout roughly (allow generous slack on Windows).
    assert elapsed < 0.5


def test_tail_buffered_appends_read_sequentially(tmp_path):
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n", encoding="utf-8")
    a = FileTailAdapter(p, poll_timeout_ms=200, from_start=True)
    a.start()
    # Append three rows. Each next_snapshot returns one.
    for i in range(3):
        _append(p, _row(f"2026-05-18T13:30:{i:02d}+00:00", 100.0 + i))
    seen = [a.next_snapshot() for _ in range(3)]
    a.stop()
    assert [s["book"]["mid"] for s in seen] == [100.0, 101.0, 102.0]


def test_tail_handles_truncation(tmp_path):
    """Truncate the file mid-stream. Adapter notices size < position and
    re-reads from the start of the truncated file."""
    p = tmp_path / "rotated.csv"
    p.write_text(HEADER + "\n" + _row("2026-05-18T13:30:00+00:00", 100.0) + "\n",
                  encoding="utf-8")
    a = FileTailAdapter(p, poll_timeout_ms=200, from_start=False)
    a.start()
    # Append a row so we have data after start().
    _append(p, _row("2026-05-18T13:30:01+00:00", 100.5))
    s1 = a.next_snapshot()
    assert s1["book"]["mid"] == 100.5
    # Truncate: rewrite the file from scratch with new content.
    p.write_text(HEADER + "\n" + _row("2026-05-18T13:30:02+00:00", 200.0) + "\n",
                  encoding="utf-8")
    s2 = a.next_snapshot()
    a.stop()
    assert s2 is not None, "adapter should pick up the truncated file's new row"
    assert s2["book"]["mid"] == 200.0
    assert a._rotations_observed >= 1


def test_tail_handles_logrotate_via_copy_and_truncate(tmp_path):
    """Real-world Windows logrotate pattern: copy aside, then truncate the
    original in place. (True inode-rename rotation is a Linux-only thing
    on shared-write filesystems; on Windows real rotators do copy-truncate
    because rename-while-open fails.) The adapter must detect the size
    drop and re-read from the start of the truncated file."""
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n" + _row("2026-05-18T13:30:00+00:00", 100.0) + "\n",
                  encoding="utf-8")
    a = FileTailAdapter(p, poll_timeout_ms=200, from_start=False)
    a.start()
    _append(p, _row("2026-05-18T13:30:01+00:00", 100.5))
    s1 = a.next_snapshot()
    assert s1["book"]["mid"] == 100.5

    # Logrotate: copy aside, then truncate-and-rewrite the original.
    shutil.copy(str(p), str(tmp_path / "live.csv.1"))
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(HEADER + "\n")
        fh.write(_row("2026-05-18T13:30:02+00:00", 300.0) + "\n")
    s2 = a.next_snapshot()
    a.stop()
    assert s2 is not None, "adapter should recover from copy-truncate logrotate"
    assert s2["book"]["mid"] == 300.0
    assert a._rotations_observed >= 1


def test_tail_start_on_missing_file_raises(tmp_path):
    a = FileTailAdapter(tmp_path / "nope.csv")
    with pytest.raises(FileNotFoundError):
        a.start()


def test_tail_snapshot_is_schema_valid(tmp_path):
    from bookmap_mcp.snapshot import is_valid
    p = tmp_path / "live.csv"
    p.write_text(HEADER + "\n", encoding="utf-8")
    a = FileTailAdapter(p, poll_timeout_ms=100, from_start=False)
    a.start()
    _append(p, _row("2026-05-18T13:30:00+00:00", 100.0))
    snap = a.next_snapshot()
    a.stop()
    assert is_valid(snap)
