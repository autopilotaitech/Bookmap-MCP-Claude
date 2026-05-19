$ErrorActionPreference = "Stop"

# Build the MCP bridge add-on and install it into Bookmap's add-on folder.
# Run this from PowerShell in the project root:  .\build-and-deploy.ps1

$root = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
Set-Location -LiteralPath $root

# 1) Build with Gradle wrapper.
& "$root\gradlew.bat" clean test jar
if ($LASTEXITCODE -ne 0) { throw "Gradle build failed (exit $LASTEXITCODE)." }

# Gradle emits a versioned jar (bookmap-mcp-bridge-v<N>.jar) so each commit
# leaves a recoverable artifact alongside its predecessor in build/libs.
# Locate the freshest one deterministically; refuse to deploy if none found.
$libsDir = Join-Path $root "build\libs"
if (-not (Test-Path -LiteralPath $libsDir)) {
    throw "build\libs not present after Gradle run: $libsDir"
}
$builtJar = Get-ChildItem -LiteralPath $libsDir -Filter "bookmap-mcp-bridge-v*.jar" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1 |
    ForEach-Object { $_.FullName }
if (-not $builtJar) {
    # Fall back to the legacy unversioned name in case build.gradle is reverted.
    $legacy = Join-Path $libsDir "bookmap-mcp-bridge.jar"
    if (Test-Path -LiteralPath $legacy) { $builtJar = $legacy }
}
if (-not $builtJar -or -not (Test-Path -LiteralPath $builtJar)) {
    throw "No bridge jar found in $libsDir (expected bookmap-mcp-bridge-v<N>.jar). " +
          "Did Gradle's :jar task run? Check 'archiveFileName' in build.gradle."
}
Write-Host "Source jar: $builtJar"

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

# Backup + quarantine target lives OUTSIDE C:\Bookmap\addons so Bookmap's
# recursive addon scan never sees these files. Sibling directory:
#   C:\Bookmap\addons-archive\bookmap-mcp-bridge\
$archive = "C:\Bookmap\addons-archive\bookmap-mcp-bridge"
if (-not (Test-Path -LiteralPath $archive)) {
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
}

if (Test-Path -LiteralPath $target) {
    # Pre-install backup of the about-to-be-overwritten jar. Stored in the
    # safe archive, NOT alongside the live install.
    $backup = Join-Path $archive ("bookmap-mcp-bridge.backup-{0}.jar" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    Copy-Item -LiteralPath $target -Destination $backup -Force
    Write-Host "Backed up existing jar to $backup"
}
Copy-Item -LiteralPath $builtJar -Destination $target -Force
Write-Host "Installed: $target"

# 4) Quarantine EVERY non-canonical bookmap-mcp-bridge*.jar under
#    C:\Bookmap\addons. Bookmap scans the addons folder recursively, and
#    this source repo lives under that tree (C:\Bookmap\addons\MCP\Bookmap),
#    so even build\libs and _phase_backups bridge jars are load candidates.
#    The canonical installed jar is the ONLY exception.
#
#    Files we touch (move to safe archive):
#      - C:\Bookmap\addons\**\bookmap-mcp-bridge*.jar  (any name, any depth)
#        EXCEPT the canonical install we just wrote.
#
#    Files we never touch:
#      - jars already inside the safe archive (would be a no-op move).
#
#    Gradle's build\libs jar is included on purpose: once copied to the
#    canonical name above, the build\libs copy is redundant. Next run of
#    `gradlew jar` recreates build\libs\bookmap-mcp-bridge-v<N>.jar, so this
#    is safe to quarantine after each deploy.
$preserved = @()
$quarantined = @()
$candidates = Get-ChildItem -LiteralPath $addonsRoot -Recurse -Filter "bookmap-mcp-bridge*.jar" `
    -ErrorAction SilentlyContinue
foreach ($s in $candidates) {
    $full = $s.FullName
    # Keep our installed jar.
    if ($full -ieq $targetResolved) {
        $preserved += "installed: $full"
        continue
    }
    # Skip anything already inside the safe archive (would be a no-op move).
    if ($full.StartsWith($archive + [IO.Path]::DirectorySeparatorChar,
                          [StringComparison]::OrdinalIgnoreCase)) {
        $preserved += "archive:   $full"
        continue
    }
    # Everything else — repo build\libs, repo _phase_backups, stray nested
    # repo copies, any *.backup-*.jar — moves to the safe archive.
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $dest = Join-Path $archive ("{0}.{1}" -f $s.Name, $stamp)
    Move-Item -LiteralPath $full -Destination $dest -Force
    $quarantined += "$full -> $dest"
}

# Hard recursive invariant: exactly one bridge jar may remain anywhere
# under C:\Bookmap\addons, and it must be the canonical install path.
$remaining = @(Get-ChildItem -LiteralPath $addonsRoot -Recurse -Filter "bookmap-mcp-bridge*.jar" `
    -ErrorAction SilentlyContinue)
$violations = @($remaining | Where-Object { $_.FullName -ine $targetResolved })

Write-Host ""
Write-Host "--- Deploy summary ---"
Write-Host "Installed jar               : $target"
foreach ($p in $preserved)   { Write-Host "Preserved                   : $p" }
foreach ($q in $quarantined) { Write-Host "Quarantined                 : $q" }
if ($quarantined.Count -eq 0) {
    Write-Host "Quarantined                 : (none)"
}
Write-Host ("Bridge jars under addons    : {0}" -f $remaining.Count)
Write-Host ("Safe archive                : {0}" -f $archive)
Write-Host ""
if ($remaining.Count -ne 1 -or $violations.Count -gt 0) {
    Write-Host "Deploy invariant VIOLATED: addons tree must contain exactly one bridge jar, and it must be the canonical install." -ForegroundColor Red
    Write-Host "Expected exactly one bridge jar at: $target" -ForegroundColor Red
    Write-Host "Found $($remaining.Count) bridge jar(s) under $addonsRoot." -ForegroundColor Red
    foreach ($r in $remaining) { Write-Host ("  found: {0}" -f $r.FullName) -ForegroundColor Red }
    foreach ($v in $violations) { Write-Host ("  offending (not canonical): {0}" -f $v.FullName) -ForegroundColor Red }
    throw "Deploy invariant violated: addons tree must contain exactly one bridge jar."
}

Write-Host "Next: start Bookmap, then attach the 'MCP Bridge' add-on to one or more instruments."
Write-Host "Then start the dashboard:  .\dashboard-start.ps1"
