@echo off
REM Unified hidden manager for the Pax AI SIM stack.
REM
REM Preferred production runtime is `paxi.bat start|armed`, which launches
REM `pax_autopilot` (the new pax_autopilot + pax_sim_agent + pax_risk_gate
REM acceptance stack). The older `bookmap_mcp.pax_daemon` (pax_trader.decide_and_act
REM path) is LEGACY and is NOT the production PAX path; stop/status still include
REM it so a stale legacy process stays visible and stoppable.
REM
REM Usage:
REM   paxi.bat start          hidden observe-mode autopilot + overview UI
REM   paxi.bat armed          hidden armed SIM autopilot + overview UI
REM   paxi.bat stop           stop autopilot/overview/legacy pax_daemon/old tick cron
REM   paxi.bat restart        stop then start observe mode
REM   paxi.bat status         print matching PAX processes (incl. legacy pax_daemon)

setlocal EnableExtensions

set "REPO=C:\Bookmap\addons\MCP\Bookmap"
set "SRV=%REPO%\mcp-server"
set "PYW=%SRV%\.venv\Scripts\pythonw.exe"
set "PY=%SRV%\.venv\Scripts\python.exe"
set "LOGDIR=D:\BookmapLogs\pax-agent"
set "JOURNAL=D:\BookmapLogs\pax-journal.db"
set "SIMDB=D:\BookmapLogs\pax-daemon-trades.db"
set "PORT=18890"
set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

if /i "%CMD%"=="start" goto start_observe
if /i "%CMD%"=="observe" goto start_observe
if /i "%CMD%"=="armed" goto start_armed
if /i "%CMD%"=="stop" goto stop
if /i "%CMD%"=="restart" goto restart
if /i "%CMD%"=="status" goto status

echo Usage: paxi.bat start^|observe^|armed^|stop^|restart^|status
exit /b 2

:common_start
if not exist "%PYW%" (
  echo [paxi] missing venv pythonw: %PYW%
  exit /b 1
)
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>nul
set "BOOKMAP_ALLOW_TRADING="
REM The old cron respawns pax_agent_tick. Disable it every start.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=Get-ScheduledTask -TaskName 'PaxAgentCron' -ErrorAction SilentlyContinue; if($t){Disable-ScheduledTask -TaskName 'PaxAgentCron' | Out-Null}" >nul 2>nul
goto :eof

:start_observe
call :common_start || exit /b 1
call :stop_processes_only
if errorlevel 1 goto start_aborted
echo [paxi] starting overview UI hidden on :%PORT%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.overview_ui','--journal','%JOURNAL%','--sim-db','%SIMDB%','--port','%PORT%') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\overview-ui.out.log' -RedirectStandardError '%LOGDIR%\overview-ui.err.log'" >nul
echo [paxi] starting autopilot OBSERVE hidden
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.pax_autopilot','--observe','--interval-sec','15','--llm-every','999','--status-every-sec','30') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\autopilot-observe.out.log' -RedirectStandardError '%LOGDIR%\autopilot-observe.err.log'" >nul
echo [paxi] observe mode up: http://127.0.0.1:%PORT%
exit /b 0

:start_armed
call :common_start || exit /b 1
call :stop_processes_only
if errorlevel 1 goto start_aborted
echo [paxi] starting overview UI hidden on :%PORT%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.overview_ui','--journal','%JOURNAL%','--sim-db','%SIMDB%','--port','%PORT%') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\overview-ui.out.log' -RedirectStandardError '%LOGDIR%\overview-ui.err.log'" >nul
echo [paxi] starting autopilot ARMED hidden
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.pax_autopilot','--armed','--interval-sec','15','--llm-every','6','--status-every-sec','30') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\autopilot-armed.out.log' -RedirectStandardError '%LOGDIR%\autopilot-armed.err.log'" >nul
echo [paxi] armed SIM mode up: http://127.0.0.1:%PORT%
exit /b 0

:start_aborted
echo [paxi] ABORTED start: could not enumerate/stop existing PAX processes.
echo [paxi] Refusing to launch a new loop so a DUPLICATE autopilot is not stacked
echo [paxi] (duplicate loops show as paired armed/observe heartbeats in agent-loop.jsonl).
echo [paxi] Likely cause: Get-CimInstance Access Denied. Run as the SAME user, NOT
echo [paxi] elevated, or stop the stale process manually, then retry.
exit /b 1

:stop
REM Write a session report BEFORE killing processes. Read-only (tails the agent
REM log + reads the SIM/journal DBs read-only), runs synchronously in this
REM console (no new/persistent terminal), and a failure here must NOT prevent
REM the stop. --archive also keeps a timestamped copy under sessions\.
REM NOTE: no literal ( ) inside these parenthesized blocks -- cmd miscounts
REM them and dies with "... was unexpected at this time".
if exist "%PY%" (
  echo [paxi] writing session report best-effort...
  pushd "%SRV%"
  "%PY%" -B -m bookmap_mcp.pax_session_report --archive --learn-dir "%LOGDIR%" --journal "%JOURNAL%" --sim-db "%SIMDB%" >> "%LOGDIR%\session-report.log" 2>&1
  if errorlevel 1 echo [paxi] session report failed - continuing stop.
  popd
)
call :stop_processes_only
set "STOPRC=%ERRORLEVEL%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=Get-ScheduledTask -TaskName 'PaxAgentCron' -ErrorAction SilentlyContinue; if($t){Disable-ScheduledTask -TaskName 'PaxAgentCron' | Out-Null}" >nul 2>nul
if not "%STOPRC%"=="0" (
  echo [paxi] ERROR: stop could not confirm all PAX processes were stopped rc=%STOPRC%.
  echo [paxi] A stale loop may still be running. Investigate before starting again.
  exit /b %STOPRC%
)
echo [paxi] stopped PAX AI/autopilot/overview and disabled PaxAgentCron.
exit /b 0

:restart
call :stop
if errorlevel 1 (
  echo [paxi] ABORTED restart: stop failed; not starting to avoid stacking a duplicate loop.
  exit /b 1
)
call "%~f0" start
exit /b %ERRORLEVEL%

:status
REM Match ONLY managed PAX python processes (incl. the LEGACY pax_daemon so a
REM stale legacy process is visible). Enumeration runs via pax_processes.ps1,
REM which FAILS LOUDLY (rc=3) if the process table cannot be read -- so an
REM Access-Denied is never misread as "nothing running".
powershell -NoProfile -ExecutionPolicy Bypass -Command "$task=Get-ScheduledTask -TaskName 'PaxAgentCron' -ErrorAction SilentlyContinue; if($task){Write-Host ('PaxAgentCron=' + $task.State)} else {Write-Host 'PaxAgentCron=not_installed'}" 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\pax_processes.ps1" -Mode status
if errorlevel 1 echo [paxi] WARNING: process enumeration FAILED (Access Denied?); status above may be incomplete.
exit /b %ERRORLEVEL%

:stop_processes_only
REM Stop old visible window launchers by title (best-effort), then stop the
REM managed PAX modules via pax_processes.ps1 which VERIFIES none remain and
REM FAILS LOUDLY (rc=3 enumerate / rc=4 survivor) instead of silently
REM succeeding. A silent failure here is exactly what let start/armed stack a
REM SECOND autopilot loop onto the first.
taskkill /FI "WINDOWTITLE eq Pax Daemon*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax Manual Daemon*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax Overview UI*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax AI*" /T >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO%\scripts\pax_processes.ps1" -Mode stop
exit /b %ERRORLEVEL%
