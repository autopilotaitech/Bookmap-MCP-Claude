"""Provider boundary for Pax agent model calls.

The trading loop depends on this narrow interface instead of depending on a
specific CLI forever. Today only the Claude CLI provider is implemented.
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class ProviderConfig:
    provider: str = "claude_cli"
    model: str = "claude-haiku-4-5"
    timeout_sec: float = 120.0


def _use_bare() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY")) or \
        os.environ.get("PAX_AI_CLAUDE_BARE") == "1"


def call_claude_cli_json(prompt: str, model: str,
                         timeout_sec: float) -> str:
    args = ["claude"]
    if _use_bare():
        args.append("--bare")
    # Prompt goes to STDIN, not argv, to avoid Windows command-line limits.
    args += ["-p", "--model", model,
             "--output-format", "json", "--tools", "", "--max-turns", "1"]
    env = {**os.environ, "BOOKMAP_ALLOW_TRADING": ""}
    popen_kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    r = subprocess.run(args, input=prompt, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout_sec,
                       env=env, **popen_kwargs)
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI exit {r.returncode}: {(r.stderr or '')[:200]}")
    try:
        outer = json.loads(r.stdout)
        return outer.get("result", r.stdout)
    except json.JSONDecodeError:
        return r.stdout


def call_agent_json(prompt: str, *,
                    provider: str = "claude_cli",
                    model: str = "claude-haiku-4-5",
                    timeout_sec: float = 120.0,
                    endpoint: Optional[str] = None,
                    keep_alive: str = "30m") -> str:
    if provider == "claude_cli":
        return call_claude_cli_json(prompt, model, timeout_sec)
    if provider == "local_llm":
        return call_ollama_json(
            prompt, model=model, timeout_sec=timeout_sec,
            endpoint=endpoint, keep_alive=keep_alive)
    if provider == "ollama":
        return call_ollama_json(
            prompt, model=model, timeout_sec=timeout_sec,
            endpoint=endpoint, keep_alive=keep_alive)
    raise ValueError(f"unknown LLM provider {provider!r}")


def call_ollama_json(prompt: str, *,
                     model: str,
                     timeout_sec: float,
                     endpoint: Optional[str] = None,
                     keep_alive: str = "30m") -> str:
    base = (endpoint or os.environ.get("PAX_OLLAMA_ENDPOINT")
            or "http://127.0.0.1:11434").rstrip("/")
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "keep_alive": keep_alive,
    }).encode("utf-8")
    req = urllib.request.Request(
        base + "/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as r:
            body = r.read().decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"ollama provider failed: {exc}") from exc
    try:
        outer = json.loads(body)
    except json.JSONDecodeError:
        return body
    if outer.get("error"):
        raise RuntimeError(f"ollama provider error: {outer['error']}")
    return outer.get("response", body)
