# Pax AI self-training edge workflow.
#
# Runs the offline research chain end-to-end:
#   bus replay  ->  outcomes backfill  ->  calibration
#               ->  candidate lessons  ->  policy replay
#
# Every step is read-only relative to pax_ai_config.json / pax_weights.json /
# production prompts. Candidate artifacts land under .\reports\.
#
# Usage:
#   .\scripts\pax-edge-workflow.ps1 -Date 2026-05-25
#   .\scripts\pax-edge-workflow.ps1 -Date 2026-05-25 -ForecastDb D:\BookmapLogs\pax-forecast.db `
#                                   -BusDb D:\BookmapLogs\pax-bus.db
#
# Set -SkipBusReplay if the bus replay step is not relevant (no chat
# history for the date). Set -SkipJournalOutcomes when running off a
# pre-populated bus DB.

param(
    [Parameter(Mandatory=$true)] [string] $Date,
    [string] $ForecastDb = "D:\BookmapLogs\pax-forecast.db",
    [string] $BusDb = "D:\BookmapLogs\pax-bus.db",
    [string] $ReportsDir = ".\reports",
    [int]    $MinSamples = 5,
    [int]    $ReplayMinSamples = 30,
    [switch] $SkipBusReplay,
    [switch] $SkipJournalOutcomes
)

$ErrorActionPreference = "Stop"

# Resolve the MCP-server venv if present; otherwise fall back to the system
# python. Test both candidate venv paths to keep this script portable.
$venvCandidates = @(
    ".\mcp-server\.venv\Scripts\python.exe",
    ".\.venv\Scripts\python.exe"
)
$Python = "python"
foreach ($cand in $venvCandidates) {
    if (Test-Path $cand) { $Python = $cand; break }
}
Write-Host "[pax-edge] using python: $Python"

if (-not (Test-Path $ReportsDir)) {
    New-Item -ItemType Directory -Path $ReportsDir -Force | Out-Null
}

function Invoke-Step {
    param([string] $Title, [string[]] $Args)
    Write-Host ""
    Write-Host "[pax-edge] >>> $Title"
    Write-Host "[pax-edge]     $Python $($Args -join ' ')"
    & $Python @Args
    if ($LASTEXITCODE -ne 0) {
        throw "[pax-edge] step failed: $Title (exit $LASTEXITCODE)"
    }
}

# 1. Bus replay (digest-replay sanity check).
if (-not $SkipBusReplay) {
    Invoke-Step "bus replay (verify digests reproduce byte-for-byte)" `
        @("-m", "bookmap_mcp.pax_bus_replay", "--date", $Date)
}

# 2. Outcomes backfill -- only meaningful if a journal exists at the
# default path; the script delegates the read of the configured path to
# the module itself.
if (-not $SkipJournalOutcomes) {
    Invoke-Step "journal outcomes backfill" `
        @("-m", "bookmap_mcp.journal_outcomes")
}

# 3. Calibration.
$calibrationPath = Join-Path $ReportsDir "calibration-$Date.json"
Invoke-Step "calibration" @(
    "-m", "bookmap_mcp.pax_calibration",
    "--date", $Date,
    "--forecasts", $ForecastDb,
    "--bus-db", $BusDb,
    "--report", $calibrationPath,
    "--min-samples", $MinSamples
)

# 4. Candidate lessons (dry-run; never invokes Claude).
Invoke-Step "candidate lessons" @(
    "-m", "bookmap_mcp.pax_research_claude",
    "--date", $Date,
    "--calibration", $calibrationPath,
    "--out-dir", $ReportsDir,
    "--min-samples", $MinSamples,
    "--dry-run"
)

# 5. Policy replay against the candidate lessons.
$candidatesPath = Join-Path $ReportsDir "policy-candidates-$Date.json"
$replayReportPath = Join-Path $ReportsDir "replay-$Date.json"
Invoke-Step "policy replay" @(
    "-m", "bookmap_mcp.pax_policy_replay",
    "--forecasts", $ForecastDb,
    "--candidates", $candidatesPath,
    "--bus-db", $BusDb,
    "--report", $replayReportPath,
    "--min-samples", $ReplayMinSamples,
    "--date", $Date
)

Write-Host ""
Write-Host "[pax-edge] OK -- artifacts under $ReportsDir"
Write-Host "[pax-edge]   calibration : $calibrationPath"
Write-Host "[pax-edge]   candidates  : $candidatesPath"
Write-Host "[pax-edge]   replay      : $replayReportPath"
Write-Host "[pax-edge] none of the active config / weights / prompts were touched."
