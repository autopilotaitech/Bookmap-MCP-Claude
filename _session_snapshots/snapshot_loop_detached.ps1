while ($true) {
    $ts = Get-Date -Format "yyyy-MM-ddTHH-mm-ss"
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:18888/api/snapshot' -TimeoutSec 10 -OutFile "C:\Bookmap\addons\MCP\Bookmap\_session_snapshots\snap-d-$ts.json" -UseBasicParsing | Out-Null
        Add-Content -Path "C:\Bookmap\addons\MCP\Bookmap\_session_snapshots\fetch.log" -Value "OK-D $ts"
    } catch {
        Add-Content -Path "C:\Bookmap\addons\MCP\Bookmap\_session_snapshots\fetch.log" -Value "ERR-D $ts $($_.Exception.Message)"
    }
    Start-Sleep 300
}
