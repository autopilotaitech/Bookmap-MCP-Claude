"""SQLite chat journal.

One table (`chats`) at D:\\BookmapLogs\\pax-chat.db (overridable via the
PAX_LOG_DIR env var, same convention as mcp-server/bookmap_mcp/dashboard.py
and pax_daemon).

WAL mode so multiple readers can coexist with the writer. Connection per
call -- the journal table is small and per-call open is far cheaper than
juggling sqlite3's per-thread connection rule.

Schema (created lazily on first connect):

  CREATE TABLE chats (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT    NOT NULL,    -- groups rows from one process lifetime
    ts_ms     INTEGER NOT NULL,    -- epoch ms
    role      TEXT    NOT NULL,    -- 'YOU' | 'PAX'
    text      TEXT    NOT NULL,
    meta_json TEXT                 -- JSON: model, router, elapsed_ms, etc.
  );
  CREATE INDEX idx_chats_ts  ON chats(ts_ms);
  CREATE INDEX idx_chats_run ON chats(run_id);

Public surface:
  init(db_path=None) -> str    # returns the run_id for this process
  record(role, text, meta=None)
  recent(limit=50, run_id=None) -> list[dict]
  forget(run_id=None) -> int   # delete rows for run_id (current if None)
  current_run_id() -> str
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional


_LOCK = threading.Lock()
_DB_PATH: Optional[Path] = None
_RUN_ID: Optional[str] = None


def default_db_path() -> Path:
    base = Path(os.environ.get("PAX_LOG_DIR") or r"D:\BookmapLogs")
    return base / "pax-chat.db"


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db_path.as_posix(), timeout=2.0, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL;")
    c.execute("PRAGMA synchronous=NORMAL;")
    c.row_factory = sqlite3.Row
    return c


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id    TEXT    NOT NULL,
            ts_ms     INTEGER NOT NULL,
            role      TEXT    NOT NULL,
            text      TEXT    NOT NULL,
            meta_json TEXT
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_ts  ON chats(ts_ms);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_run ON chats(run_id);")


def init(db_path: Optional[Path] = None, run_id: Optional[str] = None) -> str:
    """Open / create the journal. Returns the run_id for this process.

    Safe to call once at __main__ boot. Subsequent calls return the
    existing run_id (re-using the cached state).
    """
    global _DB_PATH, _RUN_ID
    with _LOCK:
        if _RUN_ID is not None:
            return _RUN_ID
        _DB_PATH = Path(db_path) if db_path else default_db_path()
        try:
            with _connect(_DB_PATH) as conn:
                _ensure_schema(conn)
        except sqlite3.OperationalError as exc:
            sys.stderr.write(f"[journal] init failed at {_DB_PATH}: {exc}; "
                              f"running in no-op mode\n")
            _DB_PATH = None
            _RUN_ID = ""
            return _RUN_ID
        _RUN_ID = run_id or uuid.uuid4().hex
        sys.stderr.write(f"[journal] {_DB_PATH} run_id={_RUN_ID}\n")
        return _RUN_ID


def current_run_id() -> str:
    return _RUN_ID or ""


def record(role: str, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
    """Insert a chat row. Silently no-ops if the journal failed to init.

    role: 'YOU' or 'PAX' (free-form string; convention is uppercase tag)
    text: full message body
    meta: arbitrary JSON-serializable dict (model, router_primary, etc.)
    """
    if not _DB_PATH or not _RUN_ID:
        return
    ts_ms = int(time.time() * 1000)
    meta_json = json.dumps(meta, default=str) if meta else None
    try:
        with _connect(_DB_PATH) as conn:
            conn.execute(
                "INSERT INTO chats(run_id, ts_ms, role, text, meta_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (_RUN_ID, ts_ms, role, text, meta_json),
            )
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[journal] record failed: {exc}\n")


def recent(limit: int = 50, run_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return the most recent rows in ascending ts_ms order.

    `run_id` filter: pass None for all runs (cross-session history),
    pass _RUN_ID for current run only, pass a specific id for a past run.
    """
    if not _DB_PATH:
        return []
    limit = max(1, min(500, int(limit)))
    try:
        with _connect(_DB_PATH) as conn:
            if run_id is None:
                cur = conn.execute(
                    "SELECT id, run_id, ts_ms, role, text, meta_json "
                    "FROM chats ORDER BY ts_ms DESC LIMIT ?",
                    (limit,),
                )
            else:
                cur = conn.execute(
                    "SELECT id, run_id, ts_ms, role, text, meta_json "
                    "FROM chats WHERE run_id = ? ORDER BY ts_ms DESC LIMIT ?",
                    (run_id, limit),
                )
            rows = [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[journal] recent failed: {exc}\n")
        return []
    rows.reverse()                   # oldest first for chronological display
    for r in rows:
        if r.get("meta_json"):
            try: r["meta"] = json.loads(r["meta_json"])
            except json.JSONDecodeError: r["meta"] = None
        else:
            r["meta"] = None
        r.pop("meta_json", None)
    return rows


def forget(run_id: Optional[str] = None) -> int:
    """Delete rows for the given run (current run if None).

    Returns the number of rows deleted. Pass run_id="*" to wipe everything.
    """
    if not _DB_PATH:
        return 0
    target = run_id if run_id is not None else _RUN_ID
    if not target:
        return 0
    try:
        with _connect(_DB_PATH) as conn:
            if target == "*":
                cur = conn.execute("DELETE FROM chats")
            else:
                cur = conn.execute("DELETE FROM chats WHERE run_id = ?", (target,))
            return cur.rowcount or 0
    except sqlite3.OperationalError as exc:
        sys.stderr.write(f"[journal] forget failed: {exc}\n")
        return 0


def _reset_for_tests() -> None:
    """Test-only: wipe the module-local cached state so init() can re-run."""
    global _DB_PATH, _RUN_ID
    with _LOCK:
        _DB_PATH = None
        _RUN_ID = None
