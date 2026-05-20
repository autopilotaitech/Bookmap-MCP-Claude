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


# ---------------------------------------------------------------------------
# Phase 1: result-event parsing (cost / usage footer)
# ---------------------------------------------------------------------------

def test_extract_result_info_full_payload():
    line = ('{"type":"result","subtype":"success","duration_ms":1234,'
            '"duration_api_ms":1100,"num_turns":1,"total_cost_usd":0.0012,'
            '"usage":{"input_tokens":800,"output_tokens":42,'
            '"cache_creation_input_tokens":0,"cache_read_input_tokens":720}}')
    out = claude_stream._extract_result_info(line)
    assert out is not None
    assert out["total_cost_usd"] == 0.0012
    assert out["input_tokens"] == 800
    assert out["output_tokens"] == 42
    assert out["cache_creation_input_tokens"] == 0
    assert out["cache_read_input_tokens"] == 720
    assert out["duration_ms"] == 1234
    assert out["duration_api_ms"] == 1100
    assert out["num_turns"] == 1


def test_extract_result_info_partial_payload_ok():
    """When the CLI omits total_cost_usd (subscription-mode on some
    versions), we still return whatever usage fields exist."""
    line = ('{"type":"result","subtype":"success",'
            '"usage":{"input_tokens":500,"output_tokens":20}}')
    out = claude_stream._extract_result_info(line)
    assert out is not None
    assert "total_cost_usd" not in out
    assert out["input_tokens"] == 500
    assert out["output_tokens"] == 20


@pytest.mark.parametrize("line", [
    "",
    "   ",
    "not json",
    '{"type":"stream_event"}',
    '{"type":"system","subtype":"init"}',
    '{}',
])
def test_extract_result_info_returns_none_for_non_result_lines(line):
    assert claude_stream._extract_result_info(line) is None


def test_result_event_parsed_into_done_info(tmp_path, patch_argv, monkeypatch):
    """End-to-end via fake_claude.py with FAKE_CLAUDE_RESULT=1: stream-json
    text deltas come through first, then a final result event populates
    the cost + usage fields in on_done."""
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "ok|")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_COST_USD", "0.00345")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_INPUT_TOKENS", "1234")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_OUTPUT_TOKENS", "67")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_CACHE_READ", "900")

    sp = tmp_path / "sp.txt"; sp.write_text("sp", encoding="utf-8")
    done_info: dict = {}
    rc = claude_stream.stream_chat(
        user_message="q", model="claude-haiku-4-5", system_prompt_path=sp,
        on_token=lambda t: None, on_done=lambda i: done_info.update(i),
    )
    assert rc == 0
    assert done_info["total_cost_usd"] == pytest.approx(0.00345)
    assert done_info["input_tokens"] == 1234
    assert done_info["output_tokens"] == 67
    assert done_info["cache_read_input_tokens"] == 900


def test_stream_without_result_event_does_not_populate_cost(tmp_path, patch_argv, monkeypatch):
    """Legacy fake_claude (no FAKE_CLAUDE_RESULT=1) must keep working.
    Cost / usage keys are absent from on_done -- the UI footer renders
    the 'subscription' fallback in that case."""
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "ok|")
    monkeypatch.delenv("FAKE_CLAUDE_RESULT", raising=False)
    sp = tmp_path / "sp.txt"; sp.write_text("sp", encoding="utf-8")
    done_info: dict = {}
    claude_stream.stream_chat(
        user_message="q", model="m", system_prompt_path=sp,
        on_token=lambda t: None, on_done=lambda i: done_info.update(i),
    )
    assert "total_cost_usd" not in done_info
    assert "input_tokens" not in done_info


def test_result_cost_omitted_when_subscription_mode(tmp_path, patch_argv, monkeypatch):
    """FAKE_CLAUDE_RESULT_OMIT_COST=1 simulates subscription-mode CLI:
    result event present, usage present, but no total_cost_usd."""
    monkeypatch.setenv("FAKE_CLAUDE_TEXTS", "ok|")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT", "1")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_OMIT_COST", "1")
    monkeypatch.setenv("FAKE_CLAUDE_RESULT_INPUT_TOKENS", "111")
    sp = tmp_path / "sp.txt"; sp.write_text("sp", encoding="utf-8")
    done_info: dict = {}
    claude_stream.stream_chat(
        user_message="q", model="m", system_prompt_path=sp,
        on_token=lambda t: None, on_done=lambda i: done_info.update(i),
    )
    assert "total_cost_usd" not in done_info
    assert done_info.get("input_tokens") == 111


# ---------------------------------------------------------------------------
# Pre-open hardening fix 3: deep timeout env override
# ---------------------------------------------------------------------------

def test_parse_timeout_default_when_env_absent(monkeypatch):
    monkeypatch.delenv("PAX_AI_DEEP_CHAT_TIMEOUT", raising=False)
    assert claude_stream._parse_timeout("PAX_AI_DEEP_CHAT_TIMEOUT", 60.0) == 60.0


def test_parse_timeout_honours_env_override(monkeypatch):
    monkeypatch.setenv("PAX_AI_DEEP_CHAT_TIMEOUT", "120")
    assert claude_stream._parse_timeout("PAX_AI_DEEP_CHAT_TIMEOUT", 60.0) == 120.0


@pytest.mark.parametrize("bad", ["", "abc", "0", "-5", "1e", "  "])
def test_parse_timeout_falls_back_on_garbage(monkeypatch, bad):
    monkeypatch.setenv("PAX_AI_DEEP_CHAT_TIMEOUT", bad)
    assert claude_stream._parse_timeout("PAX_AI_DEEP_CHAT_TIMEOUT", 60.0) == 60.0


def test_live_timeout_env_independent_of_deep(monkeypatch):
    """Live and deep envs must not cross-pollinate."""
    monkeypatch.setenv("PAX_AI_CHAT_TIMEOUT", "15")
    monkeypatch.delenv("PAX_AI_DEEP_CHAT_TIMEOUT", raising=False)
    assert claude_stream._parse_timeout("PAX_AI_CHAT_TIMEOUT", 30.0) == 15.0
    assert claude_stream._parse_timeout("PAX_AI_DEEP_CHAT_TIMEOUT", 60.0) == 60.0
