"""Stub Claude CLI for testing claude_stream.

Accepts the same flag surface as real `claude`, then writes a hand-crafted
sequence of stream-json events to stdout matching the official
--output-format stream-json --verbose --include-partial-messages shape.

Usage from a test:
    argv = [sys.executable, str(fake_claude_py), "-p", "...", "--model", "...", ...]
    proc = subprocess.Popen(argv, ...)

Env vars to drive behavior:
    FAKE_CLAUDE_TEXTS   = pipe-separated list of text fragments to emit
                          (default "Hello |from |Pax.")
    FAKE_CLAUDE_SLEEP_MS = delay between fragments (default 0)
    FAKE_CLAUDE_EXIT_CODE = process exit code (default 0)
    FAKE_CLAUDE_STDERR  = extra text to write to stderr (default empty)
"""

from __future__ import annotations

import json
import os
import sys
import time


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    texts = (os.environ.get("FAKE_CLAUDE_TEXTS") or "Hello |from |Pax.").split("|")
    sleep_ms = int(os.environ.get("FAKE_CLAUDE_SLEEP_MS", "0"))
    exit_code = int(os.environ.get("FAKE_CLAUDE_EXIT_CODE", "0"))
    stderr_extra = os.environ.get("FAKE_CLAUDE_STDERR", "")

    _emit({"type": "system", "subtype": "init",
            "session_id": "fake-session-1", "model": "fake-haiku"})
    _emit({"type": "stream_event",
            "event": {"type": "content_block_start",
                       "content_block": {"type": "text", "text": ""}}})
    for chunk in texts:
        _emit({"type": "stream_event",
                "event": {"type": "content_block_delta",
                           "delta": {"type": "text_delta", "text": chunk}}})
        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)
    _emit({"type": "stream_event",
            "event": {"type": "content_block_stop"}})

    if stderr_extra:
        sys.stderr.write(stderr_extra)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
