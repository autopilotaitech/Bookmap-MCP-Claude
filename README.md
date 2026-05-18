# Bookmap MCP Bridge

Lets Claude (Desktop, Code, or Cowork) read live state out of your running Bookmap instance through the Model Context Protocol, plus a live trading dashboard you can keep open while you trade.

Two pieces:

1. **Java add-on** (`src/main/java/com/bookmapmcp/`) — loads inside Bookmap, runs a localhost-only HTTP server (port 8765) protected by a shared secret. Captures depth, trades, orders, fills, position; session-anchored VWAP with ±1σ/±2σ/±3σ extension bands; per-listener event counters for diagnostics; shadow position reconstructed from executions when Bookmap doesn't fire `PositionListener` (e.g. playback without sim-trading).
2. **Python MCP server + dashboard** (`mcp-server/`) — exposes the bridge as MCP tools for Claude, and serves a live HTML dashboard at `http://localhost:18888`.

## Architecture

```
Claude  ──stdio──►  bookmap-mcp (Python)  ──HTTP──►  bookmap-mcp-bridge.jar (Java, inside Bookmap)
                                          127.0.0.1:8765
                                          X-Bookmap-MCP-Token: <shared secret>

Browser ──HTTP──►   dashboard.py (Python)  ──same HTTP──►  bridge.jar
                    localhost:18888
```

Shared secret generated on first run, stored in `%USERPROFILE%\.bookmap-mcp\bridge.properties`.

## MCP tools shipped

`bookmap_ping`, `bookmap_list_instruments`, `bookmap_orderbook`, `bookmap_recent_trades`, `bookmap_working_orders`, `bookmap_position`, `bookmap_recent_fills`, `bookmap_balance`, `bookmap_vwap`, `bookmap_screenshot`, `bookmap_place_limit_order` (gated), `bookmap_cancel_order` (gated).

Trading tools are gated behind `BOOKMAP_ALLOW_TRADING=1` on the Bookmap-side process. Sim-vs-live is determined by which broker login you used in Bookmap.

## Dashboard cards

- **Decision banner** — server-side composite verdict (ENTER_LONG / ENTER_SHORT / WAIT / STAND_DOWN / EXIT) with full reason chain and gate states. Refreshes every 1.5s.
- **Session** — RTH state (PRE_MARKET, OR_FORMING, ACTIVE, LATE_MORNING, CHOP, AFTERNOON, CLOSE_RISK, POST_MARKET).
- **VWAP / OR Gate** — directional alignment of OR-Strategy vs session VWAP.
- **OR-Strategy Bias** — latest row from OR-Strategy's CSV signal log (`D:\BookmapLogs\openrange-signals-*.csv`).
- **Session VWAP & bands** — vwap, σ, ±1σ/±2σ/±3σ levels, σ-deviation of current price.
- **Position / PnL** — broker-reported when available, shadow-from-fills when not. Source badge shows which.
- **Balance** — broker-reported (empty in sim/replay).
- **Momentum** — 10/50/200-print aggressor imbalance + flag.
- **Orderbook (top 25)**, **Last 20 prints**, **Working orders**, **Recent fills** — straight from the bridge.

## Claude Code skills

Declarative `.claude/skills/` folder:

- `trade-decision` — composite gate skill, mirrors the dashboard banner
- `or-bias` — read & cross-check OR-Strategy's CSV row vs live tape
- `vwap-or-gate` — VWAP/OR alignment with σ-stretch downgrade
- `absorption-watch` — detects iceberg absorption at OR levels
- `session-clock`, `news-blackout` — time/event kill switches
- `risk-check` — position/PnL caps
- `momentum-scan`, `trade-journal` — supporting

## Build & install

JDK 17 required.

```powershell
cd C:\Bookmap\addons\MCP\Bookmap
.\build-and-deploy.ps1
```

Builds, runs tests, copies to `C:\Bookmap\addons\bookmap-mcp-bridge.jar`, and prunes any stray copies under `addons/` (Bookmap loads recursively and would otherwise double-attach).

Start Bookmap → top menu **Strategies** → check **MCP Bridge** for your instrument(s). The strategy is annotated `@Layer1TradingStrategy`, so Bookmap disables it on session load as a safety net — you have to manually re-arm each session. This is fine; the dashboard is empty until you arm it.

## Python install & dashboard

```powershell
cd C:\Bookmap\addons\MCP\Bookmap\mcp-server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m bookmap_mcp.dashboard
```

Open `http://localhost:18888`. Polls the bridge every 1.5s.

## Wire into a Claude client

### Claude Code (CLI)

```powershell
claude mcp add bookmap --command "C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe" --args "-m" --args "bookmap_mcp"
```

### Claude Desktop / Cowork

Edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "bookmap": {
      "command": "C:\\Bookmap\\addons\\MCP\\Bookmap\\mcp-server\\.venv\\Scripts\\python.exe",
      "args": ["-m", "bookmap_mcp"]
    }
  }
}
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Dashboard says `BRIDGE OK · no instrument attached` | Add-on not armed. Strategies dialog → check the box on the instrument. |
| Bookmap throws `BindException` on arm | A previous bridge instance still owns port 8765 (stray duplicate jar under `addons/`). Kill Bookmap, run `Get-ChildItem C:\Bookmap\addons -Recurse -Filter "bookmap-mcp-bridge*.jar"` and delete any that aren't the canonical `C:\Bookmap\addons\bookmap-mcp-bridge.jar`. |
| `BRIDGE OK · no instrument attached` and `Strategy settings ... disabling since it's a trading strategy` in Bookmap log | Expected on session load. Manually arm via Strategies dialog. |
| Position/PnL stuck at 0 in playback | Bookmap doesn't fire `PositionListener` in raw replay. Shadow position is computed from `onExecution` instead — confirm `source: shadow from N fills` badge in the Position card. |
| Tape prices look 4x too high | Old jar before the tick→dollar fix. Rebuild & redeploy. |
| `bookmap_mcp` module not found | Run from `mcp-server/` directory with the venv activated. |

## Notes

- The session VWAP accumulator anchors at 09:30 America/New_York (= 08:30 America/Chicago) and resets daily. Uses Bookmap's `TimeListener` for playback-aware time.
- The shadow position uses FIFO close-or-flip semantics: realized PnL = closed-size × (close - avg) × multiplier. Avg flips to the fill price when the position reverses.
- Bridge prunes its own duplicates via the `BindException` defensive probe in `BridgeServer.probeOurBridge()`, but only if both instances share the same token.

## Layout

```
src/
  main/java/com/bookmapmcp/
    BookmapMcpBridgeModule.java        # the @Layer1TradingStrategy
    BridgeServer.java                  # HTTP server, port 8765
    BridgeAuth.java                    # X-Bookmap-MCP-Token check
    BridgeConfig.java                  # token + port from ~/.bookmap-mcp/bridge.properties
    BridgeRegistry.java                # alias → InstrumentState
    state/                             # InstrumentState, VwapSnapshot, etc.
    handlers/                          # one per HTTP route
mcp-server/
  bookmap_mcp/
    server.py                          # FastMCP — Claude-facing tools
    dashboard.py                       # localhost:18888 HUD
    bridge_client.py                   # http client
    config.py
.claude/skills/                        # declarative Claude Code skills
news-calendar.json                     # macro blackout windows
```
