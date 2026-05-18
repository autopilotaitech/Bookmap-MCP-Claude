# Start the Bookmap MCP dashboard with -B (no bytecode cache) so corrupted
# .pyc files can never block startup. Run this from anywhere.

$ErrorActionPreference = "Stop"
$root  = "C:\Bookmap\addons\MCP\Bookmap\mcp-server"
$py    = Join-Path $root ".venv\Scripts\python.exe"
$cache = Join-Path $root "bookmap_mcp\__pycache__"

if (Test-Path $cache) {
    Write-Host "Clearing __pycache__..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $cache -ErrorAction SilentlyContinue
}

Set-Location $root
Write-Host "Starting Bookmap MCP dashboard on http://localhost:18888 ..." -ForegroundColor Cyan
& $py -B -u -m bookmap_mcp.dashboard
