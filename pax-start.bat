@echo off
REM ==================================================================
REM  Pax paper-trading launcher.
REM
REM  Starts:
REM    1) pax_daemon   - pulls live snapshots from the Bookmap MCP
REM                      bridge, runs Pax decisions, places paper
REM                      brackets on the local sim engine, journals
REM                      everything to SQLite.
REM    2) overview_ui  - read-only dashboard at port 18890.
REM  Opens the browser to the dashboard.
REM
REM  Usage:
REM    pax-start.bat                       (default alias NQM6.CME@RITHMIC)
REM    pax-start.bat ESM6.CME@RITHMIC      (override alias)
REM
REM  Phase 0 safety: this script CLEARS BOOKMAP_ALLOW_TRADING for the
REM  spawned processes so the daemon's startup check passes. The system
REM  env var (if you have one set globally) is NOT touched.
REM ==================================================================

setlocal

set "BOOKMAP_ALLOW_TRADING="

set "REPO=C:\Bookmap\addons\MCP\Bookmap"
set "JOURNAL=D:\BookmapLogs\pax-journal.db"
set "SIMDB=D:\BookmapLogs\pax-daemon-trades.db"
set "POLL_MS=500"
set "UI_PORT=18890"

set "ALIAS=%~1"
if "%ALIAS%"=="" set "ALIAS=NQM6.CME@RITHMIC"

echo Pax launcher
echo   repo    : %REPO%
echo   alias   : %ALIAS%
echo   journal : %JOURNAL%
echo   sim DB  : %SIMDB%
echo   poll ms : %POLL_MS%
echo.

REM Ensure the package is importable from system Python. Idempotent.
echo [pax-start] ensuring bookmap_mcp is installed editably...
pushd "%REPO%\mcp-server"
python -m pip install -e . --quiet
if errorlevel 1 (
  echo [pax-start] pip install failed; not launching.
  popd
  endlocal
  exit /b 1
)
popd

REM Daemon window.
echo [pax-start] launching daemon window...
start "Pax Daemon (%ALIAS%)" cmd /k ^
  python -m bookmap_mcp.pax_daemon ^
    --source bookmap ^
    --alias "%ALIAS%" ^
    --journal "%JOURNAL%" ^
    --sim-db "%SIMDB%" ^
    --poll-ms %POLL_MS%

REM Brief pause so the daemon opens the journal before the UI tries to read it.
timeout /t 2 /nobreak >nul

REM Overview UI window.
echo [pax-start] launching overview UI window...
start "Pax Overview UI" cmd /k ^
  python -m bookmap_mcp.overview_ui ^
    --journal "%JOURNAL%" ^
    --sim-db "%SIMDB%" ^
    --port %UI_PORT%

REM Open browser to the dashboard.
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%UI_PORT%"

echo.
echo Two windows launched:
echo   1. Pax Daemon (%ALIAS%)
echo   2. Pax Overview UI       http://127.0.0.1:%UI_PORT%
echo.
echo Ctrl+C inside either window to stop, or run pax-stop.bat.
echo.
endlocal
