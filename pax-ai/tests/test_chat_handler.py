"""Tests for pax_ai.chat.handle_chat_stream cleanup invariants.

Audit fix 5: _CURRENT_ABORT must be cleared on EVERY exit path, including
prompt-write failure and BrokenPipe before the start event. Prior code
had bare `return` statements after setting _CURRENT_ABORT that leaked
the flag, so a subsequent POST /api/pax/chat/abort could bind to a
stale event from a failed chat.
"""

from __future__ import annotations

import io

import pytest

from pax_ai import chat


class _WfileRaisingOnWrite:
    """Stand-in for an SSE response wfile that fails on first write."""
    def __init__(self, exc):
        self._exc = exc

    def write(self, _data):
        raise self._exc

    def flush(self):
        pass


def _current_abort_cleared() -> bool:
    """Helper: True if no chat is currently holding the abort flag."""
    with chat._ABORT_LOCK:
        return chat._CURRENT_ABORT is None


def test_handle_chat_stream_clears_abort_after_normal_exit(monkeypatch):
    """Happy-ish path: claude binary missing, stream_chat returns 127.
    The abort flag must be cleared on exit."""
    # Ensure a clean baseline.
    with chat._ABORT_LOCK:
        chat._CURRENT_ABORT = None
    # Force claude_stream to act as if the binary is missing so we don't
    # actually spawn anything in the test process.
    monkeypatch.setattr(chat.claude_stream, "_build_argv",
                          lambda u, m, p: ["this_binary_does_not_exist_xyz.exe"])
    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "test message")
    assert _current_abort_cleared(), (
        "abort flag must be cleared after the chat finishes")


def test_handle_chat_stream_clears_abort_on_prompt_write_failure(monkeypatch):
    """Prompt-write failure must NOT leak _CURRENT_ABORT."""
    with chat._ABORT_LOCK:
        chat._CURRENT_ABORT = None

    def _boom() -> None:
        raise RuntimeError("simulated prompt write failure")
    monkeypatch.setattr(chat.prompts, "write_frozen_prompt", _boom)

    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "x")
    assert _current_abort_cleared(), (
        "prompt-write failure must clear _CURRENT_ABORT before return")
    # The handler should have written an error event before bailing.
    payload = buf.getvalue().decode("utf-8", errors="replace")
    assert "event: error" in payload
    assert "prompts.write failed" in payload


def test_handle_chat_stream_clears_abort_on_broken_pipe_before_start(monkeypatch):
    """If the client disconnects between the journal write and the
    start-event write, BrokenPipe surfaces; _CURRENT_ABORT must still
    be cleared on the return path."""
    with chat._ABORT_LOCK:
        chat._CURRENT_ABORT = None

    wfile = _WfileRaisingOnWrite(BrokenPipeError("simulated"))
    # Prompt write succeeds normally; first wfile.write is the start event,
    # which will raise BrokenPipe and trigger the early return.
    chat.handle_chat_stream(wfile, "x")
    assert _current_abort_cleared(), (
        "BrokenPipe before start must clear _CURRENT_ABORT via try/finally")


def test_request_abort_is_false_when_no_chat_in_flight(monkeypatch):
    with chat._ABORT_LOCK:
        chat._CURRENT_ABORT = None
    assert chat.request_abort() is False
