"""Entry point: ``python -m bookmap_mcp`` or the ``bookmap-mcp`` console script."""

from __future__ import annotations

from .server import build_server


def main() -> None:
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
