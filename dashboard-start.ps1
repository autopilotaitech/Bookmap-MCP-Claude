$ErrorActionPreference = "Stop"

# Bookmap MCP dashboard launcher.
#
# Starts the dashboard HTTP server at http://127.0.0.1:18888 and probes
# /api/snapshot so the operator immediately sees whether:
#   - the dashboard is up
#   - the bridge addon inside Bookmap is reachable
#   - real instruments are attached
#
# It does NOT start Bookmap itself - Bookmap must be running with the
# MCP Bridge add-on attached for the bridge probe to succeed.

$root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$mcpServer = Join-Path $root "mcp-server"
$venvPy = Join-Path $mcpServer ".venv\Scripts\python.exe"
$dashboardPort = 18888

if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "ERROR: dashboard venv python not found: $venvPy" -ForegroundColor Red
    Write-Host "Setup: cd mcp-server && python -m venv .venv && .\.venv\Scripts\pip install -e ."
    exit 1
}

# 1) Clear stale __pycache__ so a stale dashboard.pyc doesn't shadow a
#    fresh edit. Idempotent.
Write-Host "[dashboard-start] clearing __pycache__..."
$pycount = 0
Get-ChildItem -LiteralPath $mcpServer -Recurse -Directory -Filter "__pycache__" `
    -ErrorAction SilentlyContinue | ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
        $script:pycount++
    }
Write-Host "[dashboard-start] removed $pycount __pycache__ dir(s)."

# 2) If port 18888 is already listening, skip launch - assume it's our
#    dashboard (or warn if some other process holds it).
function Test-Port18888 {
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $task = $tcp.ConnectAsync("127.0.0.1", $dashboardPort)
        if ($task.Wait(500)) { $tcp.Close(); return $true }
        $tcp.Close(); return $false
    } catch { return $false }
}

if (Test-Port18888) {
    # Port is in use - VERIFY it is actually our dashboard before assuming.
    # If something else owns the port, the operator must be told so they
    # don't think the dashboard is running when it isn't.
    $isOurs = $false
    try {
        $probe = Invoke-WebRequest -Uri "http://127.0.0.1:$dashboardPort/api/snapshot" `
            -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        $probeJson = $probe.Content | ConvertFrom-Json -ErrorAction Stop
        # A real dashboard response always has a 'health' field (ok / offline / error).
        if ($null -ne $probeJson -and $null -ne $probeJson.health) {
            $isOurs = $true
        }
    } catch {
        $isOurs = $false
    }
    if ($isOurs) {
        Write-Host "[dashboard-start] port $dashboardPort already serving our dashboard; not relaunching." -ForegroundColor Yellow
    } else {
        $owner = $null
        try {
            $conn = Get-NetTCPConnection -LocalPort $dashboardPort -State Listen -ErrorAction Stop
            if ($conn) { $owner = "pid=$($conn.OwningProcess)" }
        } catch {}
        Write-Host "ERROR: port $dashboardPort is in use by a process that is NOT the Bookmap MCP dashboard." -ForegroundColor Red
        if ($owner) { Write-Host "       owning process: $owner" -ForegroundColor Red }
        Write-Host "       /api/snapshot probe did not return a valid dashboard JSON payload." -ForegroundColor Red
        Write-Host "       Stop that process (or pick a different --port) before retrying." -ForegroundColor Red
        exit 4
    }
} else {
    Write-Host "[dashboard-start] launching dashboard window..."
    $args = @("-B", "-u", "-m", "bookmap_mcp.dashboard", "--port", "$dashboardPort")
    # Spawn detached in a new window so the operator can see logs.
    Start-Process -FilePath $venvPy -ArgumentList $args -WorkingDirectory $mcpServer `
        -WindowStyle Normal
    # Wait up to 30s for the port to start listening.
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if (Test-Port18888) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not (Test-Port18888)) {
        Write-Host "ERROR: dashboard did not start listening on $dashboardPort within 30s." -ForegroundColor Red
        Write-Host "Check the spawned dashboard window for stack traces."
        exit 2
    }
}

# 3) Probe /api/snapshot.
Write-Host ""
Write-Host "[dashboard-start] probing http://127.0.0.1:$dashboardPort/api/snapshot ..."
try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$dashboardPort/api/snapshot" `
        -TimeoutSec 5 -UseBasicParsing
    $snap = $resp.Content | ConvertFrom-Json
} catch {
    Write-Host "ERROR: /api/snapshot did not respond: $($_.Exception.Message)" -ForegroundColor Red
    exit 3
}

$health = "" + $snap.health
Write-Host "[dashboard-start] /api/snapshot health = $health"
switch ($health) {
    "ok" {
        $instCount = 0
        if ($snap.instruments -and $snap.instruments.instruments) {
            $instCount = @($snap.instruments.instruments).Count
        }
        $alias = if ($snap.alias) { $snap.alias } else { "(none)" }
        Write-Host ""
        Write-Host "DASHBOARD ONLINE - bridge reachable, instruments attached: $instCount" -ForegroundColor Green
        Write-Host "  primary alias    : $alias"
        Write-Host "  view in browser  : http://127.0.0.1:$dashboardPort/"
        Write-Host "  API snapshot     : http://127.0.0.1:$dashboardPort/api/snapshot"
    }
    "offline" {
        $bridgeUrl  = if ($snap.bridgeUrl)  { $snap.bridgeUrl }  else { "(unknown)" }
        $bridgeErr  = if ($snap.bridgeError){ $snap.bridgeError }else { ("" + $snap.error) }
        $tokenSet   = if ($snap.tokenConfigured -ne $null) { $snap.tokenConfigured } else { "?" }
        Write-Host ""
        Write-Host "DASHBOARD ONLINE - BRIDGE OFFLINE" -ForegroundColor Yellow
        Write-Host "  bridge URL       : $bridgeUrl"
        Write-Host "  bridge error     : $bridgeErr"
        Write-Host "  token configured : $tokenSet"
        Write-Host ""
        Write-Host "Next steps:"
        if ($snap.nextSteps) {
            foreach ($step in $snap.nextSteps) { Write-Host "  - $step" }
        } else {
            Write-Host "  - Open Bookmap and attach the 'MCP Bridge' addon to an instrument"
            Write-Host "  - Confirm Bookmap is loading bookmap-mcp-bridge.jar from C:\Bookmap\addons\"
            Write-Host "  - Run scripts\verify-runtime.ps1 for full diagnostics"
        }
    }
    default {
        Write-Host ""
        Write-Host "DASHBOARD ONLINE - snapshot health=$health (unexpected)" -ForegroundColor Yellow
        if ($snap.error) { Write-Host "  error: $($snap.error)" }
    }
}
