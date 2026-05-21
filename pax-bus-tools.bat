@echo off
REM Operator launcher for pax_bus_replay / pax_bus_eod / pax_bus_prune / pax_bus_tune.
REM Sets PYTHONPATH so the CLIs can import both bookmap_mcp and pax_ai
REM regardless of operator CWD.
REM
REM Usage:
REM   pax-bus-tools.bat replay --date YYYY-MM-DD [--alias ALIAS]
REM   pax-bus-tools.bat eod    --date YYYY-MM-DD [--verify-replay]
REM   pax-bus-tools.bat prune  --days 30 --yes
REM   pax-bus-tools.bat tune   [--date YYYY-MM-DD] [--days N] [--min-samples N]
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
if /I "!SUB!"=="tune"   goto :tune
goto :unknown

:replay
call :tail_args %*
python -m bookmap_mcp.pax_bus_replay !TAIL_ARGS!
exit /b !ERRORLEVEL!

:eod
call :tail_args %*
python -m bookmap_mcp.pax_bus_eod !TAIL_ARGS!
exit /b !ERRORLEVEL!

:prune
call :tail_args %*
python -m bookmap_mcp.pax_bus_prune !TAIL_ARGS!
exit /b !ERRORLEVEL!

:tune
call :tail_args %*
python -m bookmap_mcp.pax_bus_tune !TAIL_ARGS!
exit /b !ERRORLEVEL!

:tail_args
set "TAIL_ARGS="
:tail_loop
if "%~2"=="" exit /b 0
if defined TAIL_ARGS (
    set "TAIL_ARGS=!TAIL_ARGS! "%~2""
) else (
    set "TAIL_ARGS="%~2""
)
shift
goto :tail_loop

:unknown
echo [pax-bus-tools] unknown subcommand: !SUB!
goto :usage

:usage
echo Usage:
echo   pax-bus-tools.bat replay --date YYYY-MM-DD [--alias ALIAS]
echo   pax-bus-tools.bat eod    --date YYYY-MM-DD [--verify-replay]
echo   pax-bus-tools.bat prune  --days 30 --yes
echo   pax-bus-tools.bat tune   [--date YYYY-MM-DD] [--days N] [--min-samples N]
exit /b 1
