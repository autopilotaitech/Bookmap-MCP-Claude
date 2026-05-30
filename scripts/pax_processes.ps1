<#
  PAX managed-process enumeration / stop helper.

  Single authority for "which python processes are the managed PAX stack" so
  paxi.bat can FAIL LOUDLY instead of silently stacking a second autopilot loop
  on top of an existing one. (Symptom of the old silent failure: paired
  armed=false + armed=true heartbeats in agent-loop.jsonl -- two loops writing
  the same log because stop could not enumerate/kill the prior one.)

  -Mode status : list managed PAX processes (human/table). Exit 0, or 3 if the
                 process table cannot be enumerated (e.g. Access Denied) -- the
                 caller must NOT interpret an enumeration failure as "nothing
                 running".
  -Mode stop   : stop managed PAX processes, then VERIFY none remain.
                 Exit 0 clean, 3 if enumeration failed, 4 if a process survived.

  Scope is anchored to bookmap_mcp.<module> / -m pax_ai under python*, so
  Bookmap, OpenRange, the Java bridge, and Ollama are never matched/killed.
#>
param(
    [ValidateSet('status','stop')]
    [string]$Mode = 'status'
)

$ErrorActionPreference = 'Stop'

$Pattern = 'bookmap_mcp\.pax_agent_tick|bookmap_mcp\.pax_autopilot|bookmap_mcp\.pax_daemon|bookmap_mcp\.overview_ui| -m pax_ai'

function Get-PaxProcs {
    # Throws on enumeration failure (Access Denied etc.) -- caught by callers so
    # the failure is surfaced loudly rather than read as an empty result set.
    Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object { ($_.Name -like 'python*') -and ($_.CommandLine -match $Pattern) }
}

function Get-PaxModule {
    param($CommandLine)
    if     ($CommandLine -match 'bookmap_mcp\.pax_agent_tick') { 'pax_agent_tick' }
    elseif ($CommandLine -match 'bookmap_mcp\.pax_autopilot')  { 'pax_autopilot' }
    elseif ($CommandLine -match 'bookmap_mcp\.pax_daemon')     { 'pax_daemon-LEGACY' }
    elseif ($CommandLine -match 'bookmap_mcp\.overview_ui')    { 'overview_ui' }
    elseif ($CommandLine -match 'pax_ai')                      { 'pax_ai' }
    else                                                       { 'other' }
}

if ($Mode -eq 'status') {
    try {
        $procs = @(Get-PaxProcs)
    } catch {
        Write-Error "[pax_processes] ERROR: cannot enumerate processes (Access Denied?): $($_.Exception.Message)"
        exit 3
    }
    if ($procs.Count -eq 0) {
        Write-Host 'PAX processes: none'
    } else {
        $procs |
            Select-Object @{N='PaxModule';E={ Get-PaxModule $_.CommandLine }}, ProcessId, Name, CommandLine |
            Format-Table -AutoSize
    }
    exit 0
}

# Mode = stop
try {
    $procs = @(Get-PaxProcs)
} catch {
    Write-Error "[pax_processes] ERROR: cannot enumerate PAX processes to stop (Access Denied?): $($_.Exception.Message)"
    exit 3
}
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
    } catch {
        Write-Warning "[pax_processes] could not stop PID $($p.ProcessId) ($(Get-PaxModule $p.CommandLine)): $($_.Exception.Message)"
    }
}
Start-Sleep -Milliseconds 400
try {
    $remaining = @(Get-PaxProcs)
} catch {
    Write-Error "[pax_processes] ERROR: cannot re-enumerate PAX processes after stop (Access Denied?): $($_.Exception.Message)"
    exit 3
}
if ($remaining.Count -gt 0) {
    $ids = ($remaining | ForEach-Object { $_.ProcessId }) -join ','
    Write-Error "[pax_processes] ERROR: $($remaining.Count) PAX process(es) still running after stop (PIDs: $ids). Refusing to report success."
    exit 4
}
exit 0
