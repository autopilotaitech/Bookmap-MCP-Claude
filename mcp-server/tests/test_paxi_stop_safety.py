"""paxi.bat must FAIL LOUDLY when it cannot enumerate/stop PAX processes.

Regression guard for the duplicate-loop bug: the old `:stop_processes_only`
piped Get-CimInstance into Stop-Process with -ErrorAction SilentlyContinue under
`>nul 2>nul` and never checked the exit code, so an Access-Denied enumeration
silently "succeeded" and start/armed stacked a SECOND autopilot (paired
armed/observe heartbeats). These are text-contract assertions (the .bat/.ps1 are
Windows runtime scripts, not importable), pinning the safety wiring.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PAXI = REPO / "paxi.bat"
PS1 = REPO / "scripts" / "pax_processes.ps1"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def test_files_exist():
    assert PAXI.is_file(), "paxi.bat missing"
    assert PS1.is_file(), "scripts/pax_processes.ps1 missing"


def test_start_paths_abort_when_stop_fails():
    txt = _read(PAXI)
    # Both start entry points must guard the stop result and abort.
    assert txt.count("call :stop_processes_only") >= 2
    assert "if errorlevel 1 goto start_aborted" in txt
    assert ":start_aborted" in txt
    # The abort path must exit nonzero (refuse to stack a duplicate loop).
    aborted = txt.split(":start_aborted", 1)[1].split(":stop", 1)[0]
    assert "exit /b 1" in aborted
    assert "DUPLICATE" in aborted.upper() or "duplicate" in aborted


def test_stop_processes_only_delegates_and_propagates_rc():
    txt = _read(PAXI)
    body = txt.split(":stop_processes_only", 1)[1]
    assert "pax_processes.ps1" in body
    assert "-Mode stop" in body
    assert "exit /b %ERRORLEVEL%" in body


def test_status_fails_loudly_on_enumeration_error():
    txt = _read(PAXI)
    status = txt.split(":status", 1)[1].split(":stop_processes_only", 1)[0]
    assert "pax_processes.ps1" in status
    assert "-Mode status" in status
    assert "errorlevel 1" in status  # warns when enumeration failed


def test_no_silent_cim_stop_swallow_remains():
    # The old swallow pattern (Stop-Process ... SilentlyContinue piped from
    # Get-CimInstance, redirected to nul) must be gone from paxi.bat.
    txt = _read(PAXI)
    assert "Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue" not in txt


def test_restart_aborts_when_stop_fails():
    txt = _read(PAXI)
    restart = txt.split(":restart", 1)[1].split(":status", 1)[0]
    assert "if errorlevel 1" in restart
    assert "ABORTED restart" in restart


def test_ps1_enumeration_is_fail_closed():
    txt = _read(PS1)
    # Enumeration must throw (Stop), not silently continue, so Access-Denied is
    # surfaced rather than read as an empty set.
    assert "Get-CimInstance Win32_Process -ErrorAction Stop" in txt
    # Distinct exit codes: 3 = cannot enumerate, 4 = a process survived stop.
    assert "exit 3" in txt
    assert "exit 4" in txt
    # Must re-verify after stopping (not trust the stop blindly).
    assert "re-enumerate" in txt or "still running after stop" in txt
    assert "ValidateSet('status','stop')" in txt
