param(
    [ValidateSet("observe", "armed")]
    [string]$Mode = "observe",
    [int]$EveryMinutes = 1,
    [string]$TaskName = "PaxAgentCron",
    [string]$RepoRoot = "C:\Bookmap\addons\MCP\Bookmap",
    [string]$Model = "",
    [switch]$Visible,
    [switch]$RunNow
)

$ErrorActionPreference = "Stop"

if ($EveryMinutes -lt 1) {
    throw "EveryMinutes must be >= 1. Windows Task Scheduler minute granularity cannot run sub-minute cron ticks."
}

$python = Join-Path $RepoRoot "mcp-server\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python venv not found at $python"
}
$pythonw = Join-Path $RepoRoot "mcp-server\.venv\Scripts\pythonw.exe"
$runner = if ((-not $Visible) -and (Test-Path -LiteralPath $pythonw)) { $pythonw } else { $python }

$moduleArgs = @("-B", "-u", "-m", "bookmap_mcp.pax_agent_tick")
if ($Mode -eq "armed") {
    $moduleArgs += "--armed"
} else {
    $moduleArgs += "--observe"
}
if ($Model) {
    $moduleArgs += @("--model", $Model)
}

$argLine = ($moduleArgs | ForEach-Object {
    if ($_ -match "\s") { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
}) -join " "

$action = New-ScheduledTaskAction `
    -Execute $runner `
    -Argument $argLine `
    -WorkingDirectory (Join-Path $RepoRoot "mcp-server")

$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs one Pax autonomous sim-agent tick every $EveryMinutes minute(s) in $Mode mode." `
    -Force | Out-Null

Write-Host "[pax-agent-cron] installed task '$TaskName' mode=$Mode every=${EveryMinutes}m"
Write-Host "[pax-agent-cron] command: $runner $argLine"
if ($runner -like "*pythonw.exe") {
    Write-Host "[pax-agent-cron] no console window: using pythonw.exe"
} else {
    Write-Host "[pax-agent-cron] console window enabled: using python.exe"
}
Write-Host "[pax-agent-cron] log: D:\BookmapLogs\pax-agent\agent-loop.jsonl"

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "[pax-agent-cron] started '$TaskName'"
}
