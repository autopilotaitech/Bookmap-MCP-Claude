@echo off
REM Phase 4B operator launcher for pax_bus_replay / pax_bus_eod / pax_bus_prune.
REM Sets PYTHONPATH so the CLIs can import both bookmap_mcp and pax_ai
REM regardless of operator CWD.
REM
REM Usage:
REM   pax-bus-tools.bat replay --date YYYY-MM-DD [--alias ALIAS]
REM   pax-bus-tools.bat eod    --date YYYY-MM-DD [--verify-replay]
REM   pax-bus-tools.bat prune  --days 30 --yes
REM
REM This script never modifies config or starts Pax AI; it only invokes
REM read-only / batch CLIs.
setlocal EnableDelayedExpansion

set "REPO_ROOT=%~dp0"
REM Strip trailing backslash for readability in error messages.
if "!REPO_ROOT:~-1!"=="\" set "REPO_ROOT=!REPO_ROOT:~0,-1!"

set "MCP_DIR=!REPO_ROOT!\mcp-server"
set "PAX_DIR=!REPO_ROOT!\pax-ai"

if not exist "!MCP_DIR!\bookmap_mcp" (
    echo [pax-bus-tools] cannot find !MCP_DIR!\bookmap_mcp
    exit /b 2
)
if not exist "!PAX_DIR!\pax_ai" (
    echo [pax-bus-tools] cannot find !PAX_DIR!\pax_ai
    exit /b 2
)

if defined PYTHONPATH (
    set "PYTHONPATH=!MCP_DIR!;!PAX_DIR!;!PYTHONPATH!"
) else (
    set "PYTHONPATH=!MCP_DIR!;!PAX_DIR!"
)

set "SUB=%~1"
if "!SUB!"=="" goto :usage
if /I "!SUB!"=="replay" goto :replay
if /I "!SUB!"=="eod"    goto :eod
if /I "!SUB!"=="prune"  goto :prune
goto :unknown

:replay
shift
python -m bookmap_mcp.pax_bus_replay %*
exit /b !ERRORLEVEL!

:eod
shift
python -m bookmap_mcp.pax_bus_eod %*
exit /b !ERRORLEVEL!

:prune
shift
python -m bookmap_mcp.pax_bus_prune %*
exit /b !ERRORLEVEL!

:unknown
echo [pax-bus-tools] unknown subcommand: !SUB!
goto :usage

:usage
echo Usage:
echo   pax-bus-tools.bat replay --date YYYY-MM-DD [--alias ALIAS]
echo   pax-bus-tools.bat eod    --date YYYY-MM-DD [--verify-replay]
echo   pax-bus-tools.bat prune  --days 30 --yes
exit /b 1
