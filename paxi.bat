@echo off
REM Unified hidden manager for the Pax AI SIM stack.
REM
REM Usage:
REM   paxi.bat start          hidden observe-mode autopilot + overview UI
REM   paxi.bat armed          hidden armed SIM autopilot + overview UI
REM   paxi.bat stop           stop autopilot/overview/old tick cron
REM   paxi.bat restart        stop then start observe mode
REM   paxi.bat status         print matching processes and PaxAgentCron state

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
echo [paxi] starting overview UI hidden on :%PORT%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.overview_ui','--journal','%JOURNAL%','--sim-db','%SIMDB%','--port','%PORT%') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\overview-ui.out.log' -RedirectStandardError '%LOGDIR%\overview-ui.err.log'" >nul
echo [paxi] starting autopilot OBSERVE hidden
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.pax_autopilot','--observe','--interval-sec','15','--llm-every','999','--status-every-sec','30') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\autopilot-observe.out.log' -RedirectStandardError '%LOGDIR%\autopilot-observe.err.log'" >nul
echo [paxi] observe mode up: http://127.0.0.1:%PORT%
exit /b 0

:start_armed
call :common_start || exit /b 1
call :stop_processes_only
echo [paxi] starting overview UI hidden on :%PORT%
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.overview_ui','--journal','%JOURNAL%','--sim-db','%SIMDB%','--port','%PORT%') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\overview-ui.out.log' -RedirectStandardError '%LOGDIR%\overview-ui.err.log'" >nul
echo [paxi] starting autopilot ARMED hidden
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%PYW%' -ArgumentList @('-B','-u','-m','bookmap_mcp.pax_autopilot','--armed','--interval-sec','15','--llm-every','6','--status-every-sec','30') -WorkingDirectory '%SRV%' -WindowStyle Hidden -RedirectStandardOutput '%LOGDIR%\autopilot-armed.out.log' -RedirectStandardError '%LOGDIR%\autopilot-armed.err.log'" >nul
echo [paxi] armed SIM mode up: http://127.0.0.1:%PORT%
exit /b 0

:stop
REM Write a session report BEFORE killing processes. Read-only (tails the agent
REM log + reads the SIM/journal DBs read-only), runs synchronously in this
REM console (no new/persistent terminal), and a failure here must NOT prevent
REM the stop. --archive also keeps a timestamped copy under sessions\.
if exist "%PY%" (
  echo [paxi] writing session report (best-effort)...
  "%PY%" -B -m bookmap_mcp.pax_session_report --archive --learn-dir "%LOGDIR%" --journal "%JOURNAL%" --sim-db "%SIMDB%" >> "%LOGDIR%\session-report.log" 2>&1
  if errorlevel 1 echo [paxi] session report failed (continuing stop).
)
call :stop_processes_only
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=Get-ScheduledTask -TaskName 'PaxAgentCron' -ErrorAction SilentlyContinue; if($t){Disable-ScheduledTask -TaskName 'PaxAgentCron' | Out-Null}" >nul 2>nul
echo [paxi] stopped PAX AI/autopilot/overview and disabled PaxAgentCron.
exit /b 0

:restart
call :stop
call "%~f0" start
exit /b %ERRORLEVEL%

:status
powershell -NoProfile -ExecutionPolicy Bypass -Command "$task=Get-ScheduledTask -TaskName 'PaxAgentCron' -ErrorAction SilentlyContinue; if($task){Write-Host ('PaxAgentCron=' + $task.State)} else {Write-Host 'PaxAgentCron=not_installed'}; Get-CimInstance Win32_Process | Where-Object {($_.Name -like 'python*') -and ($_.CommandLine -match 'bookmap_mcp\.pax_agent_tick|bookmap_mcp\.pax_autopilot|bookmap_mcp\.overview_ui|pax_ai')} | Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize"
exit /b 0

:stop_processes_only
REM Stop old visible window launchers by title, then force-kill known PAX modules.
taskkill /FI "WINDOWTITLE eq Pax Daemon*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax Manual Daemon*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax Overview UI*" /T >nul 2>nul
taskkill /FI "WINDOWTITLE eq Pax AI*" /T >nul 2>nul
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object {($_.Name -like 'python*') -and ($_.CommandLine -match 'bookmap_mcp\.pax_agent_tick|bookmap_mcp\.pax_autopilot|bookmap_mcp\.overview_ui| -m pax_ai')} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue}" >nul 2>nul
exit /b 0
