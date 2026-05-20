"""End-to-end tests for claude_stream against a fake_claude.py binary.

We don't spawn the real `claude` CLI in tests. Instead we substitute the
CLAUDE_CLI environment variable + edit the argv to invoke the fake.

Because the real claude_stream module uses subprocess.Popen with a single
argv (the binary + flags), and the fake is a Python script, we monkeypatch
claude_stream._build_argv at the import site to call:

    [sys.executable, str(FAKE_CLAUDE_PY), ...rest of flags...]

instead of `[CLAUDE_BIN, ...]`. That lets us test the JSON parser and
event flow without depending on a real claude install.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from pax_ai import claude_stream


FAKE_CLAUDE_PY = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def patch_argv(monkeypatch):
    """Swap _build_argv so it invokes fake_claude.py via the current python."""
    real_build = claude_stream._build_argv
    def fake_build(user_message, model, system_prompt_path):
        original = real_build(user_message, model, system_prompt_path)
        # Replace original[0] (the binary) with [sys.executable, fake_claude.py];
        # keep all flags afterwards so the parser exercises real flag parsing.
        return [sys.executable, str(FAKE_CLAUDE_PY)] + original[1:]
    monkeypatch.setattr(claude_stream, "_build_argv", fake_build)
    yield


def test_extract_text_delta_happy_path():
    line = ('{"type":"stream_event","event":{"type":"content_block_delta",'
            '"delta":{"type":"text_delta","text":"Hello"}}}')
    assert claude_stream._extract_text_delta(line) == "Hello"


@pytest.mark.parametrize("line", [
    "",
    "   ",
    "not json",
    '{"type":"system"}',
    '{"type":"stream_event","event":{"type":"content_block_start"}}',
    '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"unknown"}}}',
    '{}',
])
def test_extract_text_delta_returns_none_for_non_text_events(line):
    assert claude_stream._extract_text_delta(line) is None


def test_stream_chat_with_fake_binary_emits_tokens(tmp_path, patch_argv, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "Approaching |+1 |FOLLOW long.")
    sp = tmp_path / "system_prompt.txt"
    sp.write_text("system prompt", encoding="utf-8")

    tokens: list = []
    done_info: dict = {}
    def on_token(t): tokens.append(t)
    def on_done(info): done_info.update(info)

    rc = claude_stream.stream_chat(
        user_message="is +1 in play?",
        model="claude-haiku-4-5",
        system_prompt_path=sp,
        on_token=on_token, on_done=on_done,
    )
    assert rc == 0
    assert tokens == ["Approaching ", "+1 ", "FOLLOW long."]
    assert done_info["exit_code"] == 0
    assert done_info["tokens_emitted"] == 3
    assert done_info["aborted"] is False
    assert done_info["elapsed_ms"] >= 0


def test_stream_chat_handles_non_zero_exit(tmp_path, patch_argv, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "partial.")
    monkeypatch.setenv("FAKE_CLAUDE_EXIT_CODE", "2")
    monkeypatch.setenv("FAKE_CLAUDE_STDERR", "fake error blob")
    sp = tmp_path / "system_prompt.txt"; sp.write_text("sp", encoding="utf-8")
    tokens = []; done_info = {}
    claude_stream.stream_chat(
        user_message="q", model="m", system_prompt_path=sp,
        on_token=tokens.append, on_done=lambda i: done_info.update(i),
    )
    assert done_info["exit_code"] == 2
    assert done_info.get("stderr_tail", "").startswith("fake error blob")


def test_stream_chat_abort_terminates_subprocess(tmp_path, patch_argv, monkeypatch):
    # Sleep 200ms between chunks; abort after first chunk arrives.
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "a|b|c|d|e|f")
    monkeypatch.setenv("FAKE_CLAUDE_SLEEP_MS", "200")
    sp = tmp_path / "sp.txt"; sp.write_text("sp", encoding="utf-8")
    abort = threading.Event()
    tokens = []
    def on_token(t):
        tokens.append(t)
        if len(tokens) >= 1:
            abort.set()
    done_info = {}
    t0 = time.monotonic()
    claude_stream.stream_chat(
        user_message="q", model="m", system_prompt_path=sp,
        on_token=on_token, on_done=lambda i: done_info.update(i), abort=abort,
    )
    elapsed = time.monotonic() - t0
    # 6 chunks * 200ms = 1.2s if not aborted. We should be much faster.
    assert elapsed < 1.0, f"abort did not terminate quickly: {elapsed:.2f}s"
    assert done_info["aborted"] is True
    assert len(tokens) <= 6


def test_stream_chat_missing_binary_reports_error(tmp_path, monkeypatch):
    sp = tmp_path / "sp.txt"; sp.write_text("sp", encoding="utf-8")
    # Force claude_stream to invoke a non-existent binary by patching _build_argv.
    monkeypatch.setattr(claude_stream, "_build_argv",
                          lambda u, m, p: ["this_binary_does_not_exist_xyz_12345.exe"])
    done_info = {}
    rc = claude_stream.stream_chat(
        user_message="q", model="m", system_prompt_path=sp,
        on_token=lambda t: None, on_done=lambda i: done_info.update(i),
    )
    assert rc == 127
    assert "not found" in done_info.get("error", "").lower() or done_info.get("error")
