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
    FAKE_CLAUDE_RESULT  = "1" to emit a final stream-json `result` event
                          (Phase 1 cost-footer support). Default off so
                          existing tests that assume the old shape still
                          pass unchanged.
    FAKE_CLAUDE_RESULT_COST_USD          = total_cost_usd (default 0.0012)
    FAKE_CLAUDE_RESULT_INPUT_TOKENS      = usage.input_tokens (default 800)
    FAKE_CLAUDE_RESULT_OUTPUT_TOKENS     = usage.output_tokens (default 42)
    FAKE_CLAUDE_RESULT_CACHE_CREATION    = usage.cache_creation_input_tokens (0)
    FAKE_CLAUDE_RESULT_CACHE_READ        = usage.cache_read_input_tokens (720)
    FAKE_CLAUDE_RESULT_DURATION_MS       = duration_ms (default 1234)
    FAKE_CLAUDE_RESULT_DURATION_API_MS   = duration_api_ms (default 1100)
    FAKE_CLAUDE_RESULT_OMIT_COST = "1" to drop total_cost_usd from the
                          result event, simulating subscription-mode CLI
                          output where cost may be absent.
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

    # Optional final `result` event for cost-footer testing. The real CLI
    # emits one of these at end-of-turn for non-error runs. We only emit
    # if explicitly requested so legacy tests keep their old shape.
    if os.environ.get("FAKE_CLAUDE_RESULT") == "1":
        usage = {
            "input_tokens":                int(os.environ.get("FAKE_CLAUDE_RESULT_INPUT_TOKENS", "800")),
            "output_tokens":               int(os.environ.get("FAKE_CLAUDE_RESULT_OUTPUT_TOKENS", "42")),
            "cache_creation_input_tokens": int(os.environ.get("FAKE_CLAUDE_RESULT_CACHE_CREATION", "0")),
            "cache_read_input_tokens":     int(os.environ.get("FAKE_CLAUDE_RESULT_CACHE_READ", "720")),
        }
        result = {
            "type":            "result",
            "subtype":         "success",
            "duration_ms":     int(os.environ.get("FAKE_CLAUDE_RESULT_DURATION_MS", "1234")),
            "duration_api_ms": int(os.environ.get("FAKE_CLAUDE_RESULT_DURATION_API_MS", "1100")),
            "num_turns":       1,
            "result":          "".join(texts),
            "session_id":      "fake-session-1",
            "usage":           usage,
        }
        if os.environ.get("FAKE_CLAUDE_RESULT_OMIT_COST") != "1":
            try:
                result["total_cost_usd"] = float(
                    os.environ.get("FAKE_CLAUDE_RESULT_COST_USD", "0.0012"))
            except ValueError:
                pass
        _emit(result)

    if stderr_extra:
        sys.stderr.write(stderr_extra)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
