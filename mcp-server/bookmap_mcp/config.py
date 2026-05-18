"""Resolve the bridge URL + shared-secret token.

Resolution order:

1. ``BOOKMAP_BRIDGE_URL`` and ``BOOKMAP_BRIDGE_TOKEN`` environment variables.
2. The ``bridge.properties`` file the Java add-on writes on first run
   (``~/.bookmap-mcp/bridge.properties`` by default; override with
   ``BOOKMAP_MCP_CONFIG``).
3. Hard defaults: ``http://127.0.0.1:8765`` and a token read from the file.

If we still have no token after step 2, ``BridgeConfig.load`` raises
``MissingTokenError`` — start Bookmap with the bridge add-on attached at least
once so the properties file gets created, or pass the token via env var.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict


class MissingTokenError(RuntimeError):
    """The bridge token couldn't be resolved from env or properties file."""


@dataclass(frozen=True)
class BridgeConfig:
    url: str
    token: str

    @classmethod
    def load(cls) -> "BridgeConfig":
        url = os.environ.get("BOOKMAP_BRIDGE_URL")
        token = os.environ.get("BOOKMAP_BRIDGE_TOKEN")

        props: Dict[str, str] = {}
        if not url or not token:
            props = _load_properties_file()

        if not url:
            port = props.get("port", "8765")
            url = f"http://127.0.0.1:{port}"

        if not token:
            token = props.get("token", "")

        if not token:
            raise MissingTokenError(
                "No bridge token found. Either set BOOKMAP_BRIDGE_TOKEN, or "
                "start Bookmap with the MCP Bridge add-on attached so the "
                "config file is created."
            )

        return cls(url=url.rstrip("/"), token=token)


def _config_path() -> Path:
    override = os.environ.get("BOOKMAP_MCP_CONFIG")
    if override:
        return Path(override)
    return Path.home() / ".bookmap-mcp" / "bridge.properties"


def _load_properties_file() -> Dict[str, str]:
    path = _config_path()
    if not path.exists():
        return {}
    out: Dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        out[key.strip()] = value.strip()
    return out
