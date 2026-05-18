@echo off
REM Stop the Pax daemon + overview UI launched by pax-start.bat.
REM Uses Ctrl-C-equivalent first; force-kills if a window doesn't comply.

setlocal

echo [pax-stop] stopping daemon and UI windows...
taskkill /FI "WINDOWTITLE eq Pax Daemon*" /T 2>nul
taskkill /FI "WINDOWTITLE eq Pax Overview UI*" /T 2>nul
timeout /t 1 /nobreak >nul
taskkill /F /FI "WINDOWTITLE eq Pax Daemon*" /T 2>nul
taskkill /F /FI "WINDOWTITLE eq Pax Overview UI*" /T 2>nul

echo [pax-stop] done.
endlocal
