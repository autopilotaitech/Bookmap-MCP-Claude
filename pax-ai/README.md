# Pax AI

Floating dark-glass quant chat over the Bookmap MCP dashboard.

Separate process from `mcp-server`, `indicators/OpenRange`, and the Java bridge. Polls `127.0.0.1:18888/api/snapshot`. Never touches Bookmap.

See `docs/superpowers/specs/2026-05-19-pax-ai-design.md` for the design.

## Run

The launcher defaults to **floating-window mode** (pywebview frameless on_top):

```powershell
.\pax-ai-start.bat                  # floating window on :18891 (default)
.\pax-ai-start.bat 18895             # floating window on :18895
```

Server-only mode (no window — useful for headless smoke tests, CI, curl-driven debugging):

```powershell
.\pax-ai-start.bat server            # server-only :18891
.\pax-ai-start.bat server 18895      # server-only :18895
```

Direct invocation (skips the .bat probe):

```powershell
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pax_ai --shell           # floating window
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pax_ai                    # server-only
C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe -m pax_ai --shell --port 18895
```

When toggled inside Bookmap via the **Pax AI** addon (`indicators/PaxAILauncher/`), the launcher spawns the Python process with `--shell` already set. You do not normally need to run `pax-ai-start.bat` if you use the addon.

## Endpoints (`:18891`)

- `GET /`                        -- chat UI (index.html)
- `GET /api/snapshot`            -- transparent proxy to dashboard `:18888/api/snapshot`
- `GET /api/pax/context`         -- quantized strip context (alias / mid / nearest / conv / regime / session / age)
- `GET /api/pax/whynow`          -- active WHY-NOW triggers (LEVEL_APPROACH, TREND_SIGNAL_FIRE, etc.)
- `GET /api/pax/level/<label>`   -- per-level Jane-Street edge calculus
- `GET /api/pax/playbook`        -- scenario tree
- `GET /api/pax/health`          -- process + dashboard + claude CLI presence
- `GET /api/pax/skills`          -- skill registry
- `GET /api/pax/chat/history`    -- SQLite chat history (`?limit=1..500` clamped, `?scope=run|all`)
- `POST /api/pax/chat/stream`    -- SSE: spawns `claude` CLI, streams tokens
- `POST /api/pax/chat/abort`     -- abort the in-flight chat
- `POST /api/pax/chat/forget`    -- delete current run's chat history (or `{"run_id": "*"}` to wipe everything)

## Claude CLI auth — `--bare` is conditional

`pax_ai/claude_stream.py` invokes the Claude CLI with a flag set chosen at runtime:

- **API-key mode** -- when `ANTHROPIC_API_KEY` is set in env (or `PAX_AI_CLAUDE_BARE=1` is set to opt in explicitly), Pax AI adds `--bare` so the CLI starts faster and ignores any local CLAUDE.md / hooks / MCP / skill auto-discovery on this machine. This is the deterministic scripted-call shape recommended in the Claude Code docs.
- **OAuth subscription mode (default for individual users)** -- when neither env var is set, Pax AI **omits** `--bare` so the CLI can use the subscriber's OAuth keychain login (without `--bare`, the CLI returns "Not logged in" for subscription auth). The trade-off is a slower cold start because the CLI reads CLAUDE.md / hooks / skills from `~/.claude` and the cwd.

In **both** modes Pax AI passes `--tools ""` and `--max-turns 1`, so the CLI cannot edit files or run shell commands and exits after a single response. Pax AI live chat is conversation over a snapshot digest, never an agentic loop.

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
  pax_ai/                 -- package
    __main__.py           -- entry: HTTP server (+ --shell for pywebview)
    server.py             -- ThreadingHTTPServer, REST + SSE
    shell.py              -- pywebview frameless / on_top / draggable
    poller.py             -- 1Hz background snapshot poller
    triggers.py           -- alias-scoped WHY-NOW trigger engine
    chat.py               -- SSE chat handler
    claude_stream.py      -- async wrapper for `claude` CLI
    prompts.py            -- frozen system prompt + skill router
    edge_calculus.py      -- pure-function Jane-Street edge per level
    playbook.py           -- scenario tree
    context.py            -- quantized strip digest
    journal.py            -- SQLite chat journal
    voice.py              -- server-side jargon normalizer
    config.py             -- pax_ai_config.json hot-reload
    static/               -- HTML/CSS/JS (frontend)
  spikes/                 -- throwaway visual spikes (Phase 0a artifact)
  fixtures/               -- captured /api/snapshot JSON
  tests/                  -- pytest
  pyproject.toml
  README.md
```
