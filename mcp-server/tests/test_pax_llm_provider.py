import json

import pytest

from bookmap_mcp import pax_llm_provider as P


def test_claude_cli_provider_pipes_prompt_via_stdin(monkeypatch):
    captured = {}

    class _R:
        returncode = 0
        stdout = json.dumps({"result": '{"action":"WAIT"}'})
        stderr = ""

    def fake_run(args, **kw):
        captured["args"] = args
        captured["input"] = kw.get("input")
        captured["env"] = kw.get("env")
        return _R()

    monkeypatch.setattr(P.subprocess, "run", fake_run)
    out = P.call_agent_json("BIG PROMPT", model="m", timeout_sec=3)
    assert out == '{"action":"WAIT"}'
    assert "BIG PROMPT" not in " ".join(captured["args"])
    assert captured["input"] == "BIG PROMPT"
    assert captured["env"]["BOOKMAP_ALLOW_TRADING"] == ""
    assert "--tools" in captured["args"]
    assert "--max-turns" in captured["args"]


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        P.call_agent_json("x", provider="bad")


def test_ollama_provider_posts_generate_json(monkeypatch):
    captured = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"response": '{"action":"WAIT"}'}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp()

    monkeypatch.setattr(P.urllib.request, "urlopen", fake_urlopen)
    out = P.call_agent_json(
        "prompt", provider="ollama", model="llama3.1",
        endpoint="http://localhost:11434", timeout_sec=5,
        keep_alive="1h")
    assert out == '{"action":"WAIT"}'
    assert captured["url"] == "http://localhost:11434/api/generate"
    assert captured["body"]["stream"] is False
    assert captured["body"]["format"] == "json"
    assert captured["body"]["keep_alive"] == "1h"
