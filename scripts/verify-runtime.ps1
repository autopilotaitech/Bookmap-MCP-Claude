# Bookmap MCP runtime diagnostic.
#
# Walks the full path:
#   Java bridge (8765 by default)
#     -> Python dashboard (18888) /api/snapshot
#       -> OpenRange poll URL (same /api/snapshot)
#
# For each layer, prints PASS/FAIL with a concrete next-step on failure.
# Never prints the bridge token.
#
# Run from anywhere:  .\scripts\verify-runtime.ps1

$ErrorActionPreference = "Continue"

$dashboardPort = 18888
$dashboardUrl  = "http://127.0.0.1:$dashboardPort"
$expectedOpenRangeUrl = "$dashboardUrl/api/snapshot"

$passCount = 0
$failCount = 0
function Check($name, [scriptblock]$body) {
    Write-Host -NoNewline ("  {0,-48} " -f $name)
    try {
        $result = & $body
        if ($null -eq $result -or $result -eq $true) {
            Write-Host "PASS" -ForegroundColor Green
            $script:passCount++
            return $true
        }
        Write-Host "FAIL: $result" -ForegroundColor Red
        $script:failCount++
        return $false
    } catch {
        Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
        $script:failCount++
        return $false
    }
}

# ----- Bridge config loader (mirrors mcp-server\bookmap_mcp\config.py) -----
function Get-BridgeConfig {
    # Env vars take precedence.
    $url = $env:BOOKMAP_BRIDGE_URL
    $token = $env:BOOKMAP_BRIDGE_TOKEN

    # Properties file path:
    #   $env:BOOKMAP_MCP_CONFIG (if set) else ~\.bookmap-mcp\bridge.properties
    $cfgPath = $env:BOOKMAP_MCP_CONFIG
    if (-not $cfgPath) {
        $cfgPath = Join-Path $env:USERPROFILE ".bookmap-mcp\bridge.properties"
    }

    $props = @{}
    if (Test-Path -LiteralPath $cfgPath) {
        foreach ($raw in Get-Content -LiteralPath $cfgPath) {
            $line = $raw.Trim()
            if (-not $line) { continue }
            if ($line.StartsWith("#") -or $line.StartsWith("!")) { continue }
            $eq = $line.IndexOf("=")
            if ($eq -lt 0) { $eq = $line.IndexOf(":") }
            if ($eq -lt 0) { continue }
            $k = $line.Substring(0, $eq).Trim()
            $v = $line.Substring($eq + 1).Trim()
            $props[$k] = $v
        }
    }

    if (-not $url) {
        $port = $props["port"]
        if (-not $port) { $port = "8765" }
        $url = "http://127.0.0.1:$port"
    }
    if (-not $token) { $token = $props["token"] }

    return [PSCustomObject]@{
        Url            = $url.TrimEnd("/")
        TokenSet       = [bool]$token
        Token          = $token       # NEVER print this
        ConfigPath     = $cfgPath
        ConfigExists   = (Test-Path -LiteralPath $cfgPath)
    }
}

# ----- Bridge HTTP probe with structured error classification -----
function Invoke-BridgeProbe([string]$url, [string]$token, [int]$timeoutSec = 3) {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.Method = "GET"
    $req.Timeout = $timeoutSec * 1000
    $req.ReadWriteTimeout = $timeoutSec * 1000
    if ($token) { $req.Headers.Add("X-Bookmap-MCP-Token", $token) }
    try {
        $resp = $req.GetResponse()
        try {
            $sr = New-Object System.IO.StreamReader($resp.GetResponseStream())
            $body = $sr.ReadToEnd()
            $sr.Close()
            return [PSCustomObject]@{
                Ok       = $true
                Status   = [int]$resp.StatusCode
                Body     = $body
                Category = "ok"
                Reason   = $null
            }
        } finally { $resp.Close() }
    } catch [System.Net.WebException] {
        $we = $_.Exception
        $r = $we.Response
        $status = if ($r) { [int]([System.Net.HttpWebResponse]$r).StatusCode } else { 0 }
        $cat = "error"
        $reason = $we.Message
        if ($we.Status -eq [System.Net.WebExceptionStatus]::ConnectFailure) { $cat = "refused" }
        elseif ($we.Status -eq [System.Net.WebExceptionStatus]::Timeout) { $cat = "timeout" }
        elseif ($status -eq 401) { $cat = "unauthorized" }
        elseif ($status -eq 404) { $cat = "notfound" }
        elseif ($status -ge 500) { $cat = "server_error" }
        return [PSCustomObject]@{
            Ok       = $false
            Status   = $status
            Body     = $null
            Category = $cat
            Reason   = $reason
        }
    } catch {
        return [PSCustomObject]@{
            Ok       = $false
            Status   = 0
            Body     = $null
            Category = "error"
            Reason   = $_.Exception.Message
        }
    }
}

Write-Host ""
Write-Host "=== Bookmap MCP Runtime Verification ===" -ForegroundColor Cyan

$bridgeCfg = Get-BridgeConfig
Write-Host "Dashboard URL : $dashboardUrl"
Write-Host "Bridge URL    : $($bridgeCfg.Url)"
Write-Host "Token source  : $(if ($env:BOOKMAP_BRIDGE_TOKEN) { 'env BOOKMAP_BRIDGE_TOKEN' } elseif ($bridgeCfg.ConfigExists) { $bridgeCfg.ConfigPath } else { '(none)' })"
Write-Host "Token set     : $($bridgeCfg.TokenSet)"
Write-Host ""

# ===== Layer -1: Addon jar hygiene =====
# Bookmap loads jars recursively from C:\Bookmap\addons. Any
# bookmap-mcp-bridge*.jar under that tree -- including build artefacts
# inside this repo, _phase_backups, stale copied repos -- is a load
# candidate that can race the canonical jar for the classloader. The
# canonical install is the ONLY permitted match.
$addonsRoot = "C:\Bookmap\addons"
$canonicalJar = Join-Path $addonsRoot "bookmap-mcp-bridge.jar"
Write-Host "[layer -1] Addon jar hygiene (recursive scan)"
$bridgeJars = @(Get-ChildItem -LiteralPath $addonsRoot -Recurse -Filter "bookmap-mcp-bridge*.jar" `
    -ErrorAction SilentlyContinue)
$violations = @($bridgeJars | Where-Object { $_.FullName -ine $canonicalJar })
Check "exactly one bridge jar under C:\Bookmap\addons" {
    if ($bridgeJars.Count -eq 1 -and $violations.Count -eq 0) { return $true }
    if ($bridgeJars.Count -eq 0) {
        return "no bridge jar found anywhere under $addonsRoot - run .\build-and-deploy.ps1"
    }
    return "$($bridgeJars.Count) bridge jar(s) found; only the canonical install is permitted"
} | Out-Null
if ($violations.Count -gt 0) {
    Write-Host "  Offending bridge jars (not the canonical install):" -ForegroundColor Red
    foreach ($v in $violations) {
        Write-Host ("    {0}" -f $v.FullName) -ForegroundColor Red
    }
    Write-Host "  Fix: run .\build-and-deploy.ps1 to quarantine them into" -ForegroundColor Yellow
    Write-Host "       C:\Bookmap\addons-archive\bookmap-mcp-bridge\" -ForegroundColor Yellow
}
Write-Host ""

# ===== Layer 0: Bridge direct probes (the source of truth) =====
Write-Host "[layer 0] Java bridge (direct probe)"
$bridgePingOk = $false
$bridgeInstruments = $null
$bridgeAlias = $null

Check "config has bridge URL + token" {
    if (-not $bridgeCfg.TokenSet) {
        return "no token in env or $($bridgeCfg.ConfigPath) - start Bookmap with MCP Bridge attached once"
    }
    if (-not $bridgeCfg.Url) { return "no bridge URL" }
    return $true
} | Out-Null

if ($bridgeCfg.TokenSet) {
    $ping = Invoke-BridgeProbe "$($bridgeCfg.Url)/ping" $bridgeCfg.Token 3
    Check "GET /ping" {
        if ($ping.Ok) { return $true }
        switch ($ping.Category) {
            "refused"      { return "connection refused - Bookmap not running OR bridge addon failed to start" }
            "timeout"      { return "timeout - bridge port open but not responding" }
            "unauthorized" { return "401 unauthorized - token mismatch; regenerate bridge.properties" }
            "notfound"     { return "404 - /ping endpoint missing; stale jar may be loaded" }
            default        { return "$($ping.Category): $($ping.Reason)" }
        }
    } | Out-Null
    if ($ping.Ok) { $bridgePingOk = $true }
}

if ($bridgePingOk) {
    $inst = Invoke-BridgeProbe "$($bridgeCfg.Url)/instruments" $bridgeCfg.Token 3
    Check "GET /instruments" {
        if ($inst.Ok) { return $true }
        return "$($inst.Category): $($inst.Reason)"
    } | Out-Null
    if ($inst.Ok) {
        try {
            $bridgeInstruments = $inst.Body | ConvertFrom-Json
            $aliases = @()
            if ($bridgeInstruments -and $bridgeInstruments.instruments) {
                $aliases = @($bridgeInstruments.instruments | ForEach-Object { $_.alias })
            }
            Check "at least one instrument attached" {
                if ($aliases.Count -ge 1) { return $true }
                return "0 instruments - attach the MCP Bridge addon to an instrument in Bookmap"
            } | Out-Null
            if ($aliases.Count -ge 1) {
                $bridgeAlias = $aliases[0]
                Write-Host ("  primary alias                                    {0}" -f $bridgeAlias)
                $taUrl = "$($bridgeCfg.Url)/trend_analyzer?alias=$([uri]::EscapeDataString($bridgeAlias))"
                $ta = Invoke-BridgeProbe $taUrl $bridgeCfg.Token 3
                Check "GET /trend_analyzer?alias=$bridgeAlias" {
                    if ($ta.Ok) { return $true }
                    switch ($ta.Category) {
                        "notfound"     { return "404 - endpoint not registered; deployed jar may be pre-v12" }
                        "unauthorized" { return "401 - token rejected on /trend_analyzer" }
                        default        { return "$($ta.Category): $($ta.Reason)" }
                    }
                } | Out-Null
                if ($ta.Ok) {
                    try {
                        $taJson = $ta.Body | ConvertFrom-Json
                        Check "trend_analyzer warmedUp flag present" {
                            if ($null -ne $taJson.warmedUp) { return $true }
                            return "warmedUp field absent - bridge may be running older code"
                        } | Out-Null
                        Write-Host ("  trend_analyzer warmedUp                          {0}" -f $taJson.warmedUp)
                    } catch {}
                }
            }
        } catch {}
    }
}

# ===== Layer 1: Python dashboard =====
Write-Host ""
Write-Host "[layer 1] Python dashboard HTTP server"
$dashOk = Check "TCP port $dashboardPort listening" {
    $tcp = New-Object System.Net.Sockets.TcpClient
    if ($tcp.ConnectAsync("127.0.0.1", $dashboardPort).Wait(750)) {
        $tcp.Close(); return $true
    }
    $tcp.Close()
    return "not listening; start dashboard with .\dashboard-start.ps1"
}

$snap = $null
if ($dashOk) {
    Check "GET /api/snapshot reachable" {
        try {
            $r = Invoke-WebRequest -Uri "$dashboardUrl/api/snapshot" -TimeoutSec 5 -UseBasicParsing
            $script:snap = $r.Content | ConvertFrom-Json
            return $true
        } catch { return "HTTP error: $($_.Exception.Message)" }
    } | Out-Null
}

# ===== Layer 2: Dashboard -> bridge connectivity =====
Write-Host ""
Write-Host "[layer 2] Dashboard -> bridge"
if ($snap) {
    $health = "" + $snap.health
    Check "dashboard reports health=ok" {
        if ($health -eq "ok") { return $true }
        if ($health -eq "offline") { return "health=offline (bridge unreachable FROM dashboard, even if direct /ping works)" }
        return "health=$health (unexpected)"
    } | Out-Null

    if ($health -eq "offline") {
        $bErr = if ($snap.bridgeError) { $snap.bridgeError } else { ("" + $snap.error) }
        $tokenSet = if ($null -ne $snap.tokenConfigured) { $snap.tokenConfigured } else { "?" }
        Write-Host ""
        Write-Host "  BRIDGE OFFLINE (from dashboard's perspective):" -ForegroundColor Yellow
        Write-Host "    bridge URL       : $($snap.bridgeUrl)"
        Write-Host "    bridge error     : $bErr"
        Write-Host "    token configured : $tokenSet"
        Write-Host "    config path      : $($snap.expectedBridgeConfigPath)"
        if ($snap.nextSteps) {
            Write-Host "    next steps:"
            foreach ($s in $snap.nextSteps) { Write-Host "      - $s" }
        }
    } elseif ($health -eq "ok") {
        Check "snap has /trend_analyzer data" {
            if ($snap.trend_analyzer) {
                $ta = $snap.trend_analyzer
                if ($ta._error) { return "trend_analyzer error: $($ta._error)" }
                return $true
            }
            return "snap['trend_analyzer'] absent"
        } | Out-Null
    }
}

# ===== Layer 3: OpenRange poll target match =====
Write-Host ""
Write-Host "[layer 3] OpenRange poll target"
Check "OpenRange default URL matches dashboard" {
    if ($expectedOpenRangeUrl -eq "http://127.0.0.1:18888/api/snapshot") { return $true }
    return "mismatch - OpenRange expects $expectedOpenRangeUrl"
} | Out-Null

# ===== Summary =====
Write-Host ""
$total = $passCount + $failCount
Write-Host "=== Summary: $passCount/$total checks passed ===" -ForegroundColor Cyan
$chainHealthy = ($bridgePingOk -and $snap -and $snap.health -eq "ok")
if ($failCount -eq 0 -and $chainHealthy) {
    Write-Host "FULL CHAIN HEALTHY:" -ForegroundColor Green
    Write-Host "  Bookmap bridge ($($bridgeCfg.Url))  ->  Python dashboard ($dashboardUrl)  ->  OpenRange ($expectedOpenRangeUrl)" -ForegroundColor Green
    exit 0
} elseif (-not $bridgePingOk) {
    Write-Host "Java bridge UNREACHABLE at $($bridgeCfg.Url) - the rest of the chain cannot recover until this is fixed." -ForegroundColor Yellow
    exit 1
} elseif ($snap -and $snap.health -eq "offline") {
    Write-Host "Bridge directly reachable but dashboard sees health=offline." -ForegroundColor Yellow
    Write-Host "Restart the dashboard so it re-reads bridge.properties (.\dashboard-start.ps1)." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "Some checks failed. Follow the next-step hints above." -ForegroundColor Yellow
    exit 1
}
