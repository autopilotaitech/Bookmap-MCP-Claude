$ErrorActionPreference = "Stop"

# Build the MCP bridge add-on and install it into Bookmap's add-on folder.
# Run this from PowerShell in the project root:  .\build-and-deploy.ps1

$root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $root

# 1) Build with Gradle wrapper.
& "$root\gradlew.bat" clean test jar
if ($LASTEXITCODE -ne 0) { throw "Gradle build failed (exit $LASTEXITCODE)." }

$builtJar = Join-Path $root "build\libs\bookmap-mcp-bridge.jar"
if (-not (Test-Path -LiteralPath $builtJar)) {
    throw "Expected jar not found: $builtJar"
}

# 2) Wait for Bookmap to close so the jar isn't locked.
while (Get-Process -Name Bookmap -ErrorAction SilentlyContinue) {
    Write-Host "Waiting for Bookmap to close..."
    Start-Sleep -Seconds 2
}

# 3) Copy to the standard add-on install root. Bookmap loads any jar dropped
#    into C:\Bookmap\addons\. We back up any existing copy first.
$addonsRoot = "C:\Bookmap\addons"
$target = Join-Path $addonsRoot "bookmap-mcp-bridge.jar"
$targetResolved = if (Test-Path -LiteralPath $target) {
    (Resolve-Path -LiteralPath $target).Path
} else { $target }

if (Test-Path -LiteralPath $target) {
    $backup = Join-Path $addonsRoot ("bookmap-mcp-bridge.backup-{0}.jar" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Write-Host "Backed up existing jar to $backup"
}
Copy-Item -LiteralPath $builtJar -Destination $target -Force
Write-Host "Installed: $target"

# 4) Purge stray copies of our jar inside C:\Bookmap\addons\ ONLY when they
#    sit at the addons-root level (Bookmap recurses one folder; identical
#    jars deeper down would double-load and fail to bind the port).
#
#    CRITICAL: do NOT touch jars inside this source repo. Build artifacts,
#    _phase_backups, deploy logs, and any developer-managed copies under
#    $root must be preserved.
$rootResolved = (Resolve-Path -LiteralPath $root).Path
$strays = Get-ChildItem -LiteralPath $addonsRoot -Recurse -Filter "bookmap-mcp-bridge*.jar" `
    -ErrorAction SilentlyContinue |
    Where-Object {
        $full = $_.FullName
        # Keep our installed jar.
        if ($full -ieq $targetResolved) { return $false }
        # Skip anything inside the source repo (build output, backups, etc).
        if ($full.StartsWith($rootResolved + [IO.Path]::DirectorySeparatorChar,
                              [StringComparison]::OrdinalIgnoreCase)) {
            Write-Host "Preserving repo jar: $full"
            return $false
        }
        return $true
    }
foreach ($s in $strays) {
    Remove-Item -LiteralPath $s.FullName -Force
    Write-Host "Removed external duplicate: $($s.FullName)"
}

Write-Host "Next: start Bookmap, then attach the 'MCP Bridge' add-on to one or more instruments."
