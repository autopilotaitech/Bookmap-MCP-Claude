"""Tests for the env/properties-file config resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from bookmap_mcp.config import BridgeConfig, MissingTokenError


def test_env_vars_take_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKMAP_BRIDGE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("BOOKMAP_BRIDGE_TOKEN", "from-env")
    cfg = BridgeConfig.load()
    assert cfg.url == "http://127.0.0.1:9999"
    assert cfg.token == "from-env"


def test_falls_back_to_properties_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    props = tmp_path / "bridge.properties"
    props.write_text("port=8123\ntoken=from-file\n", encoding="utf-8")
    monkeypatch.delenv("BOOKMAP_BRIDGE_URL", raising=False)
    monkeypatch.delenv("BOOKMAP_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("BOOKMAP_MCP_CONFIG", str(props))
    cfg = BridgeConfig.load()
    assert cfg.url == "http://127.0.0.1:8123"
    assert cfg.token == "from-file"


def test_missing_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOOKMAP_BRIDGE_URL", raising=False)
    monkeypatch.delenv("BOOKMAP_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("BOOKMAP_MCP_CONFIG", str(tmp_path / "nope.properties"))
    with pytest.raises(MissingTokenError):
        BridgeConfig.load()


def test_url_trailing_slash_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKMAP_BRIDGE_URL", "http://127.0.0.1:8765/")
    monkeypatch.setenv("BOOKMAP_BRIDGE_TOKEN", "t")
    assert BridgeConfig.load().url == "http://127.0.0.1:8765"


def test_handles_colon_separator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    props = tmp_path / "bridge.properties"
    props.write_text("port: 7000\ntoken: with-colon\n", encoding="utf-8")
    monkeypatch.delenv("BOOKMAP_BRIDGE_URL", raising=False)
    monkeypatch.delenv("BOOKMAP_BRIDGE_TOKEN", raising=False)
    monkeypatch.setenv("BOOKMAP_MCP_CONFIG", str(props))
    cfg = BridgeConfig.load()
    assert cfg.url == "http://127.0.0.1:7000"
    assert cfg.token == "with-colon"
