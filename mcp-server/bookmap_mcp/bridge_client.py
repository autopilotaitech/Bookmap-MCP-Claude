"""Thin HTTP client over the Java add-on's localhost bridge."""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from .config import BridgeConfig


class BridgeError(RuntimeError):
    """Raised when the bridge returns a non-2xx response or is unreachable."""


class BridgeClient:
    """Synchronous HTTP client. The MCP server is low-QPS so sync is fine."""

    def __init__(self, config: BridgeConfig, timeout_s: float = 5.0) -> None:
        self._config = config
        self._client = httpx.Client(
            base_url=config.url,
            headers={"X-Bookmap-MCP-Token": config.token},
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "BridgeClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return self._json("GET", path, params=params)

    def post_json(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        return self._json("POST", path, params=params)

    def get_bytes(self, path: str, params: Optional[Mapping[str, Any]] = None) -> bytes:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise BridgeError(
                f"Bridge unreachable at {self._config.url}{path}: {exc}. "
                "Is Bookmap running with the MCP Bridge add-on attached?"
            ) from exc
        if response.status_code == 401:
            raise BridgeError("Bridge rejected the token (401).")
        if response.status_code >= 400:
            raise BridgeError(f"Bridge returned {response.status_code} for {path}: {response.text[:200]}")
        return response.content

    def _json(self, method: str, path: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        try:
            response = self._client.request(method, path, params=params)
        except httpx.HTTPError as exc:
            raise BridgeError(
                f"Bridge unreachable at {self._config.url}{path}: {exc}. "
                "Is Bookmap running with the MCP Bridge add-on attached?"
            ) from exc
        if response.status_code == 401:
            raise BridgeError("Bridge rejected the token (401). Check ~/.bookmap-mcp/bridge.properties.")
        if response.status_code == 403:
            try:
                msg = response.json().get("message", response.text)
            except ValueError:
                msg = response.text
            raise BridgeError(f"Forbidden: {msg}")
        if response.status_code >= 400:
            try:
                msg = response.json().get("message", response.text[:200])
            except ValueError:
                msg = response.text[:200]
            raise BridgeError(f"Bridge returned {response.status_code} for {path}: {msg}")
        try:
            return response.json()
        except ValueError as exc:
            raise BridgeError(f"Bridge returned non-JSON for {path}: {exc}") from exc
