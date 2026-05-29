param(
    [string]$TaskName = "PaxAgentCron"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "[pax-agent-cron] task '$TaskName' is not installed"
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[pax-agent-cron] removed task '$TaskName'"
