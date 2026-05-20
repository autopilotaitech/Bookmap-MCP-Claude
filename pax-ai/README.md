# Pax AI

Floating dark-glass quant chat over the Bookmap MCP dashboard.

Separate process from `mcp-server`, `indicators/OpenRange`, and the Java bridge. Polls `127.0.0.1:18888/api/snapshot`. Never touches Bookmap.

See `docs/superpowers/specs/2026-05-19-pax-ai-design.md` for the design.

## Run (Phase 0 scaffold)

```powershell
.\pax-ai-start.bat
```

Or directly:

```powershell
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pax_ai
```

Listens on `http://127.0.0.1:18891`. Endpoints in Phase 0:

- `GET /`              -- placeholder index (will become the chat UI)
- `GET /api/snapshot`  -- transparent proxy to dashboard `:18888/api/snapshot`

## Tests

```powershell
cd pax-ai
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pytest
```

## Capture snapshot fixtures

Dashboard must be running. Run:

```powershell
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe \
  C:\Bookmap\addons\MCP\Bookmap\pax-ai\fixtures\capture_snapshot.py
```

Saves to `pax-ai/fixtures/snapshot_<ISO>.json`. Schema test reads these.

## Layout

```
pax-ai/
  pax_ai/             -- package
    __main__.py       -- entry: HTTP server
    server.py         -- ThreadingHTTPServer
    static/           -- HTML/CSS/JS
  spikes/             -- throwaway visual spikes (phase 0a artifact)
  fixtures/           -- captured /api/snapshot JSON
  tests/              -- pytest
  pyproject.toml
  README.md
```
