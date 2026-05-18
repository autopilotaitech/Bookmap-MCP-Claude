"""Smoke tests for the HTTP client against a fake bridge."""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse

import pytest

from bookmap_mcp.bridge_client import BridgeClient, BridgeError
from bookmap_mcp.config import BridgeConfig


class _FakeBridge(BaseHTTPRequestHandler):
    expected_token = "good"
    response_body: dict = {"ok": True}
    response_status: int = 200
    last_query: dict = {}

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        provided = self.headers.get("X-Bookmap-MCP-Token")
        if provided != self.expected_token:
            self.send_response(401)
            self.end_headers()
            return
        type(self).last_query = parse_qs(urlparse(self.path).query)
        body = json.dumps(self.response_body).encode("utf-8")
        self.send_response(self.response_status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:  # silence stderr noise in tests
        return


@pytest.fixture
def fake_bridge():
    server = HTTPServer(("127.0.0.1", 0), _FakeBridge)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_get_json_happy_path(fake_bridge: str) -> None:
    _FakeBridge.response_body = {"ok": True, "version": "0.1.0"}
    with BridgeClient(BridgeConfig(url=fake_bridge, token="good")) as client:
        result = client.get_json("/ping")
    assert result == {"ok": True, "version": "0.1.0"}


def test_get_json_passes_query_params(fake_bridge: str) -> None:
    _FakeBridge.response_body = {"alias": "ES", "depth": 10}
    with BridgeClient(BridgeConfig(url=fake_bridge, token="good")) as client:
        client.get_json("/orderbook", params={"alias": "ES-CME", "depth": 5})
    assert _FakeBridge.last_query == {"alias": ["ES-CME"], "depth": ["5"]}


def test_wrong_token_raises_clear_error(fake_bridge: str) -> None:
    with BridgeClient(BridgeConfig(url=fake_bridge, token="wrong")) as client:
        with pytest.raises(BridgeError) as info:
            client.get_json("/ping")
    assert "401" in str(info.value)


def test_unreachable_raises(fake_bridge: str) -> None:
    # Point at a closed port to simulate Bookmap not running.
    with BridgeClient(BridgeConfig(url="http://127.0.0.1:1", token="x"), timeout_s=0.5) as client:
        with pytest.raises(BridgeError) as info:
            client.get_json("/ping")
    assert "unreachable" in str(info.value).lower() or "Bridge" in str(info.value)
