"""Tests for pax_ai.chat.handle_chat_stream cleanup invariants.

Audit fix 5 (round 1): _CURRENT_ABORT must be cleared on EVERY exit path,
including prompt-write failure and BrokenPipe before the start event.
Prior code had bare `return` statements after setting _CURRENT_ABORT
that leaked the flag, so a subsequent POST /api/pax/chat/abort could
bind to a stale event from a failed chat.

Audit fix 5 (round 2): these tests MUST NOT touch the real chat journal
at D:\\BookmapLogs\\pax-chat.db. The autouse `_isolate_journal` fixture
replaces `chat.journal.record` with a no-op and replaces `journal._connect`
with a tripwire so accidental DB I/O during the test is a loud failure.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from pax_ai import chat
from pax_ai import journal as journal_mod


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


@pytest.fixture(autouse=True)
def _isolate_journal(monkeypatch):
    """Belt-and-braces guard so handle_chat_stream cannot touch the real
    D:\\BookmapLogs\\pax-chat.db while these tests run.

    Layers:
      1. Replace `chat.journal.record` with a no-op spy (the import path
         the chat module actually sees -- monkeypatching `journal.record`
         alone would not affect the reference already bound on import).
      2. Replace `journal._connect` with a tripwire that raises if any
         test path slips through and tries to open a real DB.
      3. Pre-assert: the default chat DB path must not exist BEFORE the
         test (signals a developer-machine touch); we don't delete it,
         just refuse to run.

    The spy is exposed on chat.journal.record.calls for tests that want
    to assert journal interaction without writing anything.
    """
    # 0. Sanity-check the real path was not pre-touched by an earlier
    # leaky test run on this machine. (Doesn't fail if the path simply
    # doesn't exist -- this is a developer-friendly heads-up.)
    default_db = journal_mod.default_db_path()
    pre_existed = default_db.exists()

    # 1. No-op record() with a call counter for assertions.
    calls = []
    def _noop_record(role, text, meta=None):
        calls.append((role, text, meta))
    monkeypatch.setattr(chat.journal, "record", _noop_record)

    # 2. Tripwire: if anything bypasses (1) and reaches _connect, blow up.
    def _tripwire(_path):
        raise AssertionError(
            "test reached journal._connect -- isolation fixture failed; "
            "the test was about to open a real SQLite DB at "
            f"{_path}. Add a journal-aware monkeypatch.")
    monkeypatch.setattr(journal_mod, "_connect", _tripwire)

    # Expose the spy to tests that want to read it.
    chat.journal.record.calls = calls   # type: ignore[attr-defined]

    yield

    # 3. Post-assertion: the default-db file was not created by this test.
    if not pre_existed:
        assert not default_db.exists(), (
            f"test must not create {default_db} -- isolation fixture "
            f"did not cover an I/O path")


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


# ---------------------------------------------------------------------------
# Phase 2: /deep mode model selection
# ---------------------------------------------------------------------------

def test_select_model_live_default():
    """Default config: deep=False resolves to models.live (Haiku)."""
    assert chat._select_model(False).startswith("claude-haiku")


def test_select_model_deep_default_sonnet():
    """opus_opt_in unset (default false): deep=True resolves to Sonnet."""
    assert chat._select_model(True).startswith("claude-sonnet")


def test_select_model_deep_with_opus_opt_in(monkeypatch):
    """opus_opt_in=true: deep=True resolves to deep_when_opus (Opus)."""
    real_get = chat.config.get
    def fake_get(path, default=None):
        if path == "models.opus_opt_in": return True
        return real_get(path, default)
    monkeypatch.setattr(chat.config, "get", fake_get)
    assert chat._select_model(True).startswith("claude-opus")


def _capture_argv(monkeypatch):
    """Helper: monkeypatch claude_stream so we capture the argv passed
    to subprocess.Popen without actually spawning the binary. Returns a
    list that will hold the captured argv after handle_chat_stream runs."""
    captured: list = []
    def fake_build_argv(user_message, model, system_prompt_path):
        argv = ["fake_claude_stub", "--model", model,
                "--tools", "",
                "--max-turns", "1"]
        captured.append({"argv": argv, "model": model})
        # Return a path that does not exist so Popen raises FileNotFoundError
        # and stream_chat bails fast via the missing-binary branch.
        return ["this_binary_does_not_exist_xyz_deep.exe",
                 "--model", model, "--tools", "", "--max-turns", "1"]
    monkeypatch.setattr(chat.claude_stream, "_build_argv", fake_build_argv)
    return captured


def test_handle_chat_stream_deep_false_uses_live_model(monkeypatch):
    with chat._ABORT_LOCK: chat._CURRENT_ABORT = None
    captured = _capture_argv(monkeypatch)
    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "what regime?", deep=False)
    assert captured, "stream_chat should have been called once"
    assert captured[0]["model"].startswith("claude-haiku")


def test_handle_chat_stream_deep_true_uses_sonnet(monkeypatch):
    with chat._ABORT_LOCK: chat._CURRENT_ABORT = None
    captured = _capture_argv(monkeypatch)
    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "explain absorption at -1", deep=True)
    assert captured and captured[0]["model"].startswith("claude-sonnet")


def test_handle_chat_stream_deep_true_with_opus_opt_in(monkeypatch):
    with chat._ABORT_LOCK: chat._CURRENT_ABORT = None
    real_get = chat.config.get
    def fake_get(path, default=None):
        if path == "models.opus_opt_in": return True
        return real_get(path, default)
    monkeypatch.setattr(chat.config, "get", fake_get)
    captured = _capture_argv(monkeypatch)
    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "deep review", deep=True)
    assert captured and captured[0]["model"].startswith("claude-opus")


def test_handle_chat_stream_deep_keeps_tools_empty_and_max_turns_one(monkeypatch):
    """Both invariants must be enforced in /deep mode too."""
    with chat._ABORT_LOCK: chat._CURRENT_ABORT = None
    captured: list = []
    real_build = chat.claude_stream._build_argv
    def spy_build(user_message, model, system_prompt_path):
        argv = real_build(user_message, model, system_prompt_path)
        captured.append(list(argv))
        # Redirect to a missing binary so Popen returns 127 fast.
        return ["this_binary_does_not_exist_xyz_deep.exe"] + list(argv[1:])
    monkeypatch.setattr(chat.claude_stream, "_build_argv", spy_build)
    buf = io.BytesIO()
    chat.handle_chat_stream(buf, "deep query", deep=True)
    assert captured, "_build_argv must have been called"
    argv = captured[0]
    # Find adjacent ('--tools', '') and ('--max-turns', '1') pairs.
    pairs = list(zip(argv, argv[1:]))
    assert ("--tools", "") in pairs, (
        "--tools \"\" must remain in argv under /deep mode")
    assert ("--max-turns", "1") in pairs, (
        "--max-turns 1 must remain in argv under /deep mode")


def test_handle_chat_stream_deep_default_param_is_false():
    """The deep parameter must default to False so legacy callers
    (every existing test) keep getting the live model."""
    import inspect
    sig = inspect.signature(chat.handle_chat_stream)
    assert sig.parameters["deep"].default is False
