@echo off
REM Pax AI launcher
REM Matches the style of pax-start.bat / dashboard-start.ps1.
REM
REM Usage:
REM   pax-ai-start.bat                     -> floating Pax AI window (default), :18891
REM   pax-ai-start.bat 18895                -> floating window, port 18895
REM   pax-ai-start.bat server               -> server-only mode (no window), :18891
REM   pax-ai-start.bat server 18895         -> server-only mode, port 18895
REM
REM Server-only mode is useful for headless smoke tests, CI, and
REM curl-driven debugging. The Bookmap PaxAILauncher addon also starts
REM Pax AI in --shell mode internally (see indicators/PaxAILauncher/),
REM so this launcher is for manual / standalone runs.

setlocal

set "MODE=shell"
set "PORT=18891"

if /i "%~1"=="server" (
  set "MODE=server"
  if not "%~2"=="" set "PORT=%~2"
) else (
  if not "%~1"=="" set "PORT=%~1"
)

set "PY=C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [pax-ai] python venv not found at %PY%
  echo [pax-ai] run: cd mcp-server ^&^& python -m pip install -e .
  exit /b 1
)

if /i "%MODE%"=="shell" (
  echo [pax-ai] launching floating window on http://127.0.0.1:%PORT%
  start "Pax AI :%PORT%" "%PY%" -B -u -m pax_ai --shell --port %PORT%
) else (
  echo [pax-ai] launching server-only on http://127.0.0.1:%PORT%
  start "Pax AI :%PORT% (server-only)" "%PY%" -B -u -m pax_ai --port %PORT%
)

REM Give the server a moment, then probe.
timeout /t 2 /nobreak >nul
"%PY%" -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:%PORT%/api/snapshot',timeout=3); sys.stderr.write('[pax-ai] proxy probe OK status=%%d\n' %% r.status)" 2>nul
if errorlevel 1 (
  echo [pax-ai] probe failed -- check the new window for errors
) else (
  if /i "%MODE%"=="shell" (
    echo [pax-ai] proxy probe OK; floating window should be visible
  ) else (
    echo [pax-ai] proxy probe OK; open http://127.0.0.1:%PORT%/ in your browser
  )
)

endlocal
