"""Async Claude CLI streaming wrapper.

Spawns `claude` per chat turn with the exact flag set from spec section 7.1:

  claude --bare -p "<user msg>"
         --model <models.live>
         --output-format stream-json --verbose --include-partial-messages
         --tools ""
         --max-turns 1
         --append-system-prompt-file <frozen prompt path>

The CLI emits one JSON event per line. We filter for
`{"type":"stream_event","event":{"type":"content_block_delta",
"delta":{"type":"text_delta","text":"..."}}}` and forward the text deltas
to the SSE stream.

Public surface:
  stream_chat(user_message, snapshot_digest_text, system_prompt_path,
              model, on_token, on_done, abort_flag) -> exit_code

`abort_flag` is a threading.Event. If set, the subprocess is terminated.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional


# Hard ceiling so a runaway CLI call cannot hang the server forever.
CHAT_TIMEOUT_SEC = float(os.environ.get("PAX_AI_CHAT_TIMEOUT", "30"))

CLAUDE_BIN = os.environ.get("CLAUDE_CLI", "claude")


def _use_bare() -> bool:
    """Decide whether to pass --bare.

    Per the Claude CLI docs, --bare skips OAuth + keychain reads. That
    means subscription-only logins ("claude auth login") fail with
    "Not logged in" if --bare is used. We therefore only pass --bare when
    an ANTHROPIC_API_KEY is set in the environment (or when the operator
    explicitly opts in via PAX_AI_CLAUDE_BARE=1). Without --bare we lose
    deterministic environment (CLAUDE.md, hooks, skills auto-discovery)
    but gain working auth for OAuth subscribers.
    """
    if os.environ.get("PAX_AI_CLAUDE_BARE") == "1":
        return True
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _build_argv(user_message: str, model: str, system_prompt_path: Path) -> list:
    argv: list = [CLAUDE_BIN]
    if _use_bare():
        argv.append("--bare")
    argv += [
        "-p", user_message,
        "--model", model,
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--tools", "",
        "--max-turns", "1",
        "--append-system-prompt-file", str(system_prompt_path),
    ]
    return argv


def _extract_text_delta(line: str) -> Optional[str]:
    """Pull the .event.delta.text out of a single stream-json line.

    Returns the text fragment if this line is a content_block_delta of
    type text_delta. Returns None for any other event type or unparseable
    lines (which we silently skip).
    """
    line = line.strip()
    if not line:
        return None
    if line[0] != "{":
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("type") != "stream_event":
        return None
    ev = obj.get("event")
    if not isinstance(ev, dict):
        return None
    if ev.get("type") != "content_block_delta":
        return None
    delta = ev.get("delta")
    if not isinstance(delta, dict):
        return None
    if delta.get("type") != "text_delta":
        return None
    text = delta.get("text")
    return text if isinstance(text, str) else None


def stream_chat(
    user_message: str,
    model: str,
    system_prompt_path: Path,
    on_token: Callable[[str], None],
    on_done: Callable[[dict], None],
    abort: Optional[threading.Event] = None,
) -> int:
    """Run one Claude CLI call. Returns the process exit code.

    on_token(text)   - invoked for every text_delta chunk
    on_done(info)    - invoked exactly once at end with metadata dict
    abort            - if set during the call, the process is terminated

    Errors (FileNotFoundError for missing claude binary, OS errors, JSON
    parse errors) are converted to a final on_done({"error": "..."}) and
    a non-zero return code rather than raising.
    """
    abort = abort or threading.Event()
    argv = _build_argv(user_message, model, system_prompt_path)
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,         # line-buffered
            encoding="utf-8",
        )
    except FileNotFoundError as exc:
        on_done({"error": f"claude CLI not found: {exc}",
                  "exit_code": 127, "elapsed_ms": 0})
        return 127
    except OSError as exc:
        on_done({"error": f"subprocess spawn failed: {exc}",
                  "exit_code": 1, "elapsed_ms": 0})
        return 1

    # Watchdog: abort thread terminates the process on timeout or abort flag.
    def _watchdog() -> None:
        deadline = time.monotonic() + CHAT_TIMEOUT_SEC
        while True:
            if proc.poll() is not None:
                return
            if abort.is_set() or time.monotonic() > deadline:
                try:
                    proc.terminate()
                    if not _proc_wait(proc, 0.5):
                        proc.kill()
                except Exception:
                    pass
                return
            time.sleep(0.05)

    wd = threading.Thread(target=_watchdog, name="pax-ai-chat-watchdog", daemon=True)
    wd.start()

    tokens_emitted = 0
    try:
        if proc.stdout is not None:
            for line in proc.stdout:
                if abort.is_set():
                    break
                text = _extract_text_delta(line)
                if text:
                    on_token(text)
                    tokens_emitted += 1
    except (BrokenPipeError, ConnectionResetError):
        pass
    except Exception as exc:
        sys.stderr.write(f"[chat] stdout read crashed: {exc}\n")

    exit_code = _proc_wait_exit(proc, 2.0)
    elapsed_ms = int((time.monotonic() - start) * 1000)
    info = {
        "exit_code":      exit_code,
        "elapsed_ms":     elapsed_ms,
        "tokens_emitted": tokens_emitted,
        "aborted":        abort.is_set(),
    }
    if exit_code != 0:
        # Try to surface stderr for diagnostics (capped length).
        try:
            err_out = proc.stderr.read() if proc.stderr else ""
            if err_out:
                info["stderr_tail"] = err_out[-500:]
        except Exception:
            pass
    on_done(info)
    return exit_code


def _proc_wait(proc: subprocess.Popen, seconds: float) -> bool:
    try:
        proc.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def _proc_wait_exit(proc: subprocess.Popen, seconds: float) -> int:
    if _proc_wait(proc, seconds):
        rc = proc.returncode
        return rc if rc is not None else -1
    try:
        proc.kill()
    except Exception:
        pass
    return -9


def claude_available() -> bool:
    """Quick check: is `claude` on PATH? Cached at first call."""
    global _CLAUDE_AVAIL
    try:
        return _CLAUDE_AVAIL
    except NameError:
        pass
    try:
        r = subprocess.run([CLAUDE_BIN, "--version"],
                            capture_output=True, text=True, timeout=3.0)
        ok = r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        ok = False
    globals()["_CLAUDE_AVAIL"] = ok
    return ok
