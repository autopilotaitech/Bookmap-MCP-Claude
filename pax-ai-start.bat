@echo off
REM Pax AI launcher (Phase 0 -- HTTP server on :18891)
REM Matches the style of pax-start.bat / dashboard-start.ps1.
REM
REM Usage:
REM   pax-ai-start.bat            (defaults to port 18891)
REM   pax-ai-start.bat 18895       (override port)

set "PORT=%1"
if "%PORT%"=="" set "PORT=18891"

set "PY=C:\Bookmap\addons\MCP\Bookmap\mcp-server\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [pax-ai] python venv not found at %PY%
  echo [pax-ai] run: cd mcp-server ^&^& python -m pip install -e .
  exit /b 1
)

echo [pax-ai] launching on http://127.0.0.1:%PORT%
start "Pax AI :%PORT%" "%PY%" -B -u -m pax_ai --port %PORT%

REM Give the server a moment, then probe.
timeout /t 2 /nobreak >nul
"%PY%" -c "import urllib.request,sys; r=urllib.request.urlopen('http://127.0.0.1:%PORT%/api/snapshot',timeout=3); sys.stderr.write('[pax-ai] proxy probe OK status=%%d\n' %% r.status)" 2>nul
if errorlevel 1 (
  echo [pax-ai] probe failed -- check the new window for errors
) else (
  echo [pax-ai] proxy probe OK; open http://127.0.0.1:%PORT%/ in your browser
)
