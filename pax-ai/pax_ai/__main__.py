"""Pax AI entry point.

Two modes:

  python -m pax_ai                  # headless: HTTP server only (curl-friendly)
  python -m pax_ai --shell          # full: HTTP server + pywebview floating window
  python -m pax_ai --shell --port 18891

The PaxAILauncher Bookmap addon spawns this with `--shell`. The HTTP server
runs in a daemon thread; the pywebview event loop owns the main thread.
"""

from __future__ import annotations

import argparse
import sys
import threading

from . import DEFAULT_PORT
from .server import run as run_server
from . import poller


def _start_server_thread(port: int) -> threading.Thread:
    t = threading.Thread(target=run_server, kwargs={"port": port},
                         name="pax-ai-http", daemon=True)
    t.start()
    return t


def main() -> int:
    ap = argparse.ArgumentParser(prog="pax_ai")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT,
                    help=f"HTTP port (default {DEFAULT_PORT})")
    ap.add_argument("--shell", action="store_true",
                    help="Open the pywebview floating window. Without this "
                          "flag the process runs headless (HTTP only).")
    args = ap.parse_args()

    # Snapshot poller starts in both modes - the API endpoints need it.
    poller.start()

    # Phase-1 feature bus: passive capture, guarded by config flag (default
    # false). The call is idempotent; when disabled, start() is a no-op.
    from . import feature_bus
    feature_bus.start()

    # Phase 4A outcomes daemon: passive labeling, guarded by config flag
    # (default false). The call is idempotent; when disabled, start() is a no-op.
    from . import outcomes
    outcomes.start()

    # Chat journal init (SQLite at D:\BookmapLogs\pax-chat.db by default).
    # No-ops if PAX_LOG_DIR is unwritable -- chat still works, history is
    # just not preserved for that session.
    from . import journal
    journal.init()

    if args.shell:
        _start_server_thread(args.port)
        # Give the server a moment to bind before the WebView2 page loads.
        import time
        time.sleep(0.4)
        from . import shell
        return shell.run(port=args.port)

    try:
        run_server(port=args.port)
    except KeyboardInterrupt:
        sys.stderr.write("\n[pax_ai] interrupted\n")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
