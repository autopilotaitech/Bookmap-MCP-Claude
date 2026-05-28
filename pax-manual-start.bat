@echo off
REM ==================================================================
REM  Pax MANUAL paper-trading launcher.
REM
REM  Starts the same daemon + overview UI as pax-start.bat, BUT with
REM    --no-auto-decide
REM  so the daemon ticks SimEngine (fills + journals) WITHOUT running
REM  decide_and_act(). Operator drives trades manually via:
REM    python -m bookmap_mcp.pax_manual long  2 30305 30295 30325 30341
REM    python -m bookmap_mcp.pax_manual short 2 30331 30342 30260
REM    python -m bookmap_mcp.pax_manual flatten
REM    python -m bookmap_mcp.pax_manual status
REM
REM  Usage:
REM    pax-manual-start.bat                       (alias NQM6.CME@RITHMIC)
REM    pax-manual-start.bat ESM6.CME@RITHMIC      (override alias)
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

echo Pax MANUAL launcher
echo   repo    : %REPO%
echo   alias   : %ALIAS%
echo   journal : %JOURNAL%
echo   sim DB  : %SIMDB%
echo   poll ms : %POLL_MS%
echo.

echo [pax-manual-start] ensuring bookmap_mcp is installed editably...
pushd "%REPO%\mcp-server"
python -m pip install -e . --quiet
if errorlevel 1 (
  echo [pax-manual-start] pip install failed; not launching.
  popd
  endlocal
  exit /b 1
)
popd

REM Tick-only daemon window (no decisions; operator drives entries).
echo [pax-manual-start] launching tick-only daemon (--no-auto-decide)...
start "Pax Manual Daemon (%ALIAS%)" cmd /k ^
  python -m bookmap_mcp.pax_daemon ^
    --source bookmap ^
    --alias "%ALIAS%" ^
    --journal "%JOURNAL%" ^
    --sim-db "%SIMDB%" ^
    --poll-ms %POLL_MS% ^
    --no-auto-decide

timeout /t 2 /nobreak >nul

echo [pax-manual-start] launching overview UI window...
start "Pax Overview UI" cmd /k ^
  python -m bookmap_mcp.overview_ui ^
    --journal "%JOURNAL%" ^
    --sim-db "%SIMDB%" ^
    --port %UI_PORT%

timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:%UI_PORT%"

echo.
echo Two windows launched:
echo   1. Pax Manual Daemon (%ALIAS%)
echo   2. Pax Overview UI       http://127.0.0.1:%UI_PORT%
echo.
echo Drive trades from any shell:
echo   python -m bookmap_mcp.pax_manual long  2 30305 30295 30325 30341
echo   python -m bookmap_mcp.pax_manual short 2 30331 30342 30260
echo   python -m bookmap_mcp.pax_manual flatten
echo   python -m bookmap_mcp.pax_manual status
echo.
echo Ctrl+C inside the daemon window to stop, or run pax-stop.bat.
echo.
endlocal
