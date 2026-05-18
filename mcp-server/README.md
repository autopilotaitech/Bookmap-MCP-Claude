# bookmap-mcp (Python)

The MCP server half of the Bookmap MCP Bridge. See the top-level [README](../README.md) for the full picture.

## Run

```bash
python -m bookmap_mcp
```

Reads `~/.bookmap-mcp/bridge.properties` for the URL + token by default. Override with env vars:

- `BOOKMAP_BRIDGE_URL` — default `http://127.0.0.1:8765`
- `BOOKMAP_BRIDGE_TOKEN` — required if no properties file
- `BOOKMAP_MCP_CONFIG` — alternate path to `bridge.properties`

## Tests

```bash
pip install pytest
pytest --basetemp=/tmp/pytest-bookmap   # any path off the project tree avoids Windows-mount cleanup quirks
```
