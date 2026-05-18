$ErrorActionPreference = "Stop"

# One-shot setup for the Bookmap MCP Bridge.
# Run from PowerShell in the project root:  .\setup-mcp.ps1
#
# What it does, in order:
#   1) Pings the Java bridge to prove Bookmap + the add-on are running.
#   2) Lists attached instruments.
#   3) Creates a Python venv and installs the MCP server.
#   4) Smoke-tests the Python server against the bridge.
#   5) Writes/merges a "bookmap" entry into Claude Desktop's MCP config.
#
# Re-runnable. Each step prints PASS / FAIL with a clear next-action.

function Write-Section($title) {
    Write-Host ""
    Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Pass($msg) { Write-Host "[PASS] $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }
function Info($msg) { Write-Host "       $msg" -ForegroundColor DarkGray }

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$mcpServer = Join-Path $root "mcp-server"
$venv = Join-Path $mcpServer ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$bridgeProps = Join-Path $env:USERPROFILE ".bookmap-mcp\bridge.properties"

# -------- Step 1: Bridge config + ping --------
Write-Section "1. Verify Bookmap MCP Bridge is running"

if (-not (Test-Path -LiteralPath $bridgeProps)) {
    Fail "Bridge config not found at $bridgeProps"
    Info "Attach the 'MCP Bridge' add-on to an instrument in Bookmap, then re-run this script."
    exit 1
}
Pass "Found bridge config: $bridgeProps"

$token = (Get-Content $bridgeProps | Where-Object { $_ -like 'token=*' } | Select-Object -First 1) -replace '^token=', ''
$port  = (Get-Content $bridgeProps | Where-Object { $_ -like 'port=*'  } | Select-Object -First 1) -replace '^port=',  ''
if (-not $port) { $port = "8765" }
if (-not $token) {
    Fail "No token= line in $bridgeProps"
    exit 1
}
Info "Port: $port"
Info "Token: $($token.Substring(0, [Math]::Min(8, $token.Length)))..."

try {
    $ping = Invoke-RestMethod -Uri "http://127.0.0.1:$port/ping" -Headers @{ "X-Bookmap-MCP-Token" = $token } -TimeoutSec 5
    Pass "Bridge /ping responded: ok=$($ping.ok) bridgeVersion=$($ping.bridgeVersion) attached=$($ping.attachedInstruments)"
} catch {
    Fail "Bridge /ping failed: $($_.Exception.Message)"
    Info "Is Bookmap running with 'MCP Bridge' attached to at least one instrument?"
    exit 1
}

# -------- Step 2: List instruments --------
Write-Section "2. List attached instruments"
try {
    $instruments = Invoke-RestMethod -Uri "http://127.0.0.1:$port/instruments" -Headers @{ "X-Bookmap-MCP-Token" = $token } -TimeoutSec 5
    Pass "Found $($instruments.count) attached instrument(s)"
    foreach ($i in $instruments.instruments) {
        Info ("  {0,-30}  pips={1}  multiplier={2}" -f $i.alias, $i.pips, $i.multiplier)
    }
} catch {
    Fail "/instruments failed: $($_.Exception.Message)"
    exit 1
}

# -------- Step 3: Python venv + install --------
Write-Section "3. Python MCP server install"

$pythonCmd = $null
foreach ($candidate in @("py -3", "python", "python3")) {
    try {
        $output = & cmd /c "$candidate --version 2>&1"
        if ($LASTEXITCODE -eq 0) {
            $pythonCmd = $candidate
            Info "Using Python: $candidate ($output)"
            break
        }
    } catch { }
}
if (-not $pythonCmd) {
    Fail "No Python found. Install Python 3.10+ from python.org and re-run."
    exit 1
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    Info "Creating venv at $venv ..."
    & cmd /c "$pythonCmd -m venv `"$venv`""
    if ($LASTEXITCODE -ne 0) { Fail "venv creation failed"; exit 1 }
}
Pass "venv ready: $venv"

Info "Installing bookmap-mcp (this can take ~30s on first run)..."
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e $mcpServer
if ($LASTEXITCODE -ne 0) { Fail "pip install failed"; exit 1 }
Pass "Installed bookmap-mcp into venv"

# -------- Step 4: Python smoke test against the bridge --------
Write-Section "4. Python -> bridge smoke test"
$smoke = & $venvPython -c "from bookmap_mcp.server import _call; import json; print(json.dumps(_call('/ping')))" 2>&1
if ($LASTEXITCODE -ne 0) {
    Fail "Python ping failed: $smoke"
    exit 1
}
Pass "Python reached the bridge: $smoke"

# -------- Step 5: Claude Desktop config --------
Write-Section "5. Wire into Claude Desktop"
$cdConfig = Join-Path $env:APPDATA "Claude\claude_desktop_config.json"

$entry = [ordered]@{
    command = $venvPython
    args    = @("-m", "bookmap_mcp")
}

if (Test-Path -LiteralPath $cdConfig) {
    $existing = Get-Content $cdConfig -Raw | ConvertFrom-Json
    if (-not $existing.mcpServers) {
        $existing | Add-Member -NotePropertyName mcpServers -NotePropertyValue (New-Object psobject)
    }
    if ($existing.mcpServers.PSObject.Properties.Match("bookmap").Count -gt 0) {
        $existing.mcpServers.bookmap = $entry
        Info "Updated existing 'bookmap' entry in Claude Desktop config"
    } else {
        $existing.mcpServers | Add-Member -NotePropertyName bookmap -NotePropertyValue $entry
        Info "Added 'bookmap' entry to Claude Desktop config"
    }
    $backup = "$cdConfig.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
    Copy-Item -LiteralPath $cdConfig -Destination $backup -Force
    Info "Backup written to $backup"
    $existing | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $cdConfig -Encoding UTF8
    Pass "Updated $cdConfig"
} else {
    $skel = [ordered]@{ mcpServers = [ordered]@{ bookmap = $entry } }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $cdConfig) | Out-Null
    $skel | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $cdConfig -Encoding UTF8
    Pass "Created $cdConfig"
}

Write-Host ""
Write-Host "All five steps passed." -ForegroundColor Green
Write-Host "Next: fully quit Claude Desktop (right-click tray icon -> Quit), reopen it, then ask Claude:" -ForegroundColor Yellow
Write-Host '  "Ping the Bookmap bridge and list the instruments you can see."' -ForegroundColor Yellow
