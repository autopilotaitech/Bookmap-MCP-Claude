$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $root "build\libs\openrange-release-fixed.jar"
$target = Join-Path $root "build\libs\openrange-release.jar"
$backup = Join-Path $root ("build\libs\openrange-release.backup-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".jar")

if (-not (Test-Path -LiteralPath $source)) {
    throw "Fixed jar does not exist: $source"
}

while (Get-Process -Name Bookmap -ErrorAction SilentlyContinue) {
    Start-Sleep -Seconds 2
}

if (Test-Path -LiteralPath $target) {
    Copy-Item -LiteralPath $target -Destination $backup -Force
}

Copy-Item -LiteralPath $source -Destination $target -Force
Write-Host "Installed fixed OpenRange jar: $target"
if (Test-Path -LiteralPath $backup) {
    Write-Host "Backup saved: $backup"
}
