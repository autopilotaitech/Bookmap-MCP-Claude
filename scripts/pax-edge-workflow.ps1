# Pax AI self-training edge workflow.
#
# Runs the offline research chain end-to-end:
#   optional bus replay  ->  calibration  ->  candidate lessons
#                        ->  policy replay
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
# history for the date). Outcomes must already be present in the feature-bus
# DB's trade_outcomes table; journal_outcomes writes to the daemon journal
# and does not feed this calibration path.

param(
    [Parameter(Mandatory=$true)] [string] $Date,
    [string] $ForecastDb = "D:\BookmapLogs\pax-forecast.db",
    [string] $BusDb = "D:\BookmapLogs\pax-bus.db",
    [string] $ReportsDir = ".\reports",
    [int]    $MinSamples = 5,
    [int]    $ReplayMinSamples = 30,
    [switch] $SkipBusReplay
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
    # $Args is a PowerShell automatic variable; declaring a param of the
    # same name yields ambiguous splat behavior depending on edition. Use
    # $Argv instead so & $Python @Argv expands deterministically.
    param([string] $Title, [string[]] $Argv)
    Write-Host ""
    Write-Host "[pax-edge] >>> $Title"
    Write-Host "[pax-edge]     $Python $($Argv -join ' ')"
    # Windows PowerShell 5.1 wraps every line a native EXE writes to
    # stderr as an ErrorRecord (NativeCommandError). Our CLIs emit
    # progress lines like "calibration report written: ..." to stderr, so
    # we capture stderr to a temp file and surface it ONLY on a non-zero
    # exit. The exit code is the authoritative success signal -- PS
    # error-record semantics on native stderr are not.
    $stderrFile = [System.IO.Path]::GetTempFileName()
    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $proc = Start-Process -FilePath $Python -ArgumentList $Argv `
                              -NoNewWindow -Wait -PassThru `
                              -RedirectStandardError $stderrFile
        $ec = $proc.ExitCode
    } finally {
        $ErrorActionPreference = $previousEAP
    }
    if ($ec -ne 0) {
        $stderrText = (Get-Content -LiteralPath $stderrFile -Raw `
                        -ErrorAction SilentlyContinue)
        Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
        if ($stderrText) {
            Write-Host "[pax-edge] stderr:"
            Write-Host $stderrText
        }
        throw "[pax-edge] step failed: $Title (exit $ec)"
    }
    Remove-Item -LiteralPath $stderrFile -Force -ErrorAction SilentlyContinue
}

# 1. Bus replay (digest-replay sanity check).
if (-not $SkipBusReplay) {
    Invoke-Step "bus replay (verify digests reproduce byte-for-byte)" `
        @("-m", "bookmap_mcp.pax_bus_replay", "--date", $Date)
}

# 2. Calibration. This reads outcomes from the feature-bus DB
# trade_outcomes table via --bus-db. It does not read the daemon journal.
$calibrationPath = Join-Path $ReportsDir "calibration-$Date.json"
Invoke-Step "calibration" @(
    "-m", "bookmap_mcp.pax_calibration",
    "--date", $Date,
    "--forecasts", $ForecastDb,
    "--bus-db", $BusDb,
    "--report", $calibrationPath,
    "--min-samples", $MinSamples
)

# 3. Candidate lessons (dry-run; never invokes Claude).
Invoke-Step "candidate lessons" @(
    "-m", "bookmap_mcp.pax_research_claude",
    "--date", $Date,
    "--calibration", $calibrationPath,
    "--out-dir", $ReportsDir,
    "--min-samples", $MinSamples,
    "--dry-run"
)

# 4. Policy replay against the candidate lessons.
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
