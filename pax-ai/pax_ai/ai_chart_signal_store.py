"""Cross-process JSONL store for validated Pax AI chart signals.

Pax AI's chat handler appends one validated signal per accepted block.
The dashboard process reads the file on every snapshot poll, TTL-filters,
dedupes by id (newest timestamp_ms wins), and emits the result as
snap["pax_ai_chart_events"].

Atomic-write contract (load-bearing):
- append_signal MUST land the full JSON object + trailing newline in a
  single os.write() syscall and fsync. Concurrent readers must never see
  a partial row mid-line. On Windows, O_APPEND atomicity for single
  writes <= PIPE_BUF is honored by NTFS; we still fsync so the bytes are
  on disk before the function returns.
- read_active gracefully skips a partial / malformed final line. A reader
  that sees the file mid-append must NOT raise into the snapshot path.

File is append-only; the reader applies the TTL, the file itself is not
trimmed. A daily prune job is out of scope for v1.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_STORE_PATH = Path(r"D:\BookmapLogs\pax-ai-chart-signals.jsonl")
DEFAULT_TTL_SEC = 300       # 5 min
DEFAULT_MAX_ROWS = 200

# Process-wide writer lock. Pax AI is single-process, so a threading.Lock
# is sufficient to guarantee one writer at a time: O_APPEND on Windows is
# not atomic across separate file descriptors within the same process, so
# absent this lock 50 racing threads will produce interleaved bytes.
# Cross-process write coordination is not needed today (dashboard is
# reader-only).
_WRITER_LOCK = threading.Lock()


def append_signal(sig: Dict[str, Any],
                   store_path: Optional[Path] = None) -> None:
    """Atomic single-syscall append, then fsync.

    Never raises into the caller -- chat plumbing is not allowed to break
    on a disk error here. Failures are silent at this layer; the dashboard
    polls the file and a missing/stale write surfaces as "no AI signal"
    which is the correct degraded state.
    """
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(sig, separators=(",", ":"), ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        flags = os.O_APPEND | os.O_WRONLY | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        with _WRITER_LOCK:
            fd = os.open(str(path), flags, 0o644)
            try:
                os.write(fd, data)
                try:
                    os.fsync(fd)
                except OSError:
                    pass
            finally:
                os.close(fd)
    except OSError:
        return


def read_active(store_path: Optional[Path] = None,
                 now_ms: Optional[int] = None,
                 ttl_sec: int = DEFAULT_TTL_SEC,
                 max_rows: int = DEFAULT_MAX_ROWS) -> List[Dict[str, Any]]:
    path = Path(store_path) if store_path else DEFAULT_STORE_PATH
    if not path.exists():
        return []
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff = now - (ttl_sec * 1000)
    by_id: Dict[str, Dict[str, Any]] = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    # Partial / malformed line (concurrent append in flight,
                    # or a developer-edited row). Skip; do not raise.
                    continue
                if not isinstance(row, dict):
                    continue
                ts = row.get("timestamp_ms")
                if not isinstance(ts, (int, float)) or ts < cutoff:
                    continue
                rid = row.get("id")
                if not rid:
                    continue
                prev = by_id.get(rid)
                if prev is None or row.get("timestamp_ms", 0) >= prev.get("timestamp_ms", 0):
                    by_id[rid] = row
    except OSError:
        return []
    rows = sorted(by_id.values(), key=lambda r: r.get("timestamp_ms", 0))
    return rows[-max_rows:]
