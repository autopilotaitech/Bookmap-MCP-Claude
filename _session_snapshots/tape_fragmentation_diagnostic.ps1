# Tape fragmentation diagnostic for J1.
#
# Polls dashboard /api/snapshot for trades, deduplicates against last-seen
# nanos, and appends each unique trade as JSON to a log file.
#
# Output:
#   D:\BookmapLogs\tape_fragmentation_log.jsonl
#     - one line per trade (price, size, side, nanos, poll_ms_ct)
#
# Analysis questions this answers (post-hoc):
#   1. What is the actual max-size print observed?
#   2. How often do multiple trades share the same nanos value (definite
#      fragments of one MBO event)?
#   3. How often are consecutive trades at the same (price, side) within
#      e.g. 5ms / 50ms / 500ms (candidate fragments vs separate prints)?
#
# Compare to operator's MBO display (Bookmap Min size=10 setting):
#   - operator-visible 10-25+ size prints
#   - bridge max observed sizes today were 4-9

$logPath = 'D:\BookmapLogs\tape_fragmentation_log.jsonl'
$maxIter = 1800   # ~30 min at 1s polling
$lastSeenNanos = 0

# Write a header line so the log self-documents.
$header = @{
    schema = "j1_diagnostic_v1"
    started_ct = ([System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
        (Get-Date), 'Central Standard Time')).ToString('yyyy-MM-dd HH:mm:ss')
    fields = @('poll_ms_ct', 'price', 'size', 'side', 'nanos')
    note = 'Each subsequent line is a single trade as JSON. Same nanos -> same MBO event (definite fragment). Same (price, side) within tight window -> candidate fragment.'
} | ConvertTo-Json -Compress
Add-Content -Encoding utf8 -Path $logPath -Value $header

for ($i = 0; $i -lt $maxIter; $i++) {
    try {
        $snap = Invoke-RestMethod -Uri 'http://127.0.0.1:18888/api/snapshot' -TimeoutSec 5
        $trades = $snap.trades
        if ($null -eq $trades -or $trades.Count -eq 0) {
            Start-Sleep -Seconds 1
            continue
        }
        # /recent_trades returns NEWEST first. Reverse so we walk old -> new
        # and can deduplicate via last_seen_nanos cleanly.
        $sorted = $trades | Sort-Object -Property nanos
        $pollMs = ([System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
            (Get-Date), 'Central Standard Time')).ToString('HH:mm:ss.fff')
        $newCount = 0
        foreach ($t in $sorted) {
            $n = [long]$t.nanos
            if ($n -le $lastSeenNanos) { continue }
            $lastSeenNanos = $n
            $row = @{
                poll_ms_ct = $pollMs
                price = $t.price
                size = $t.size
                side = $t.side
                nanos = $n
            } | ConvertTo-Json -Compress
            Add-Content -Encoding utf8 -Path $logPath -Value $row
            $newCount++
        }
    } catch {
        $err = @{
            poll_ms_ct = ([System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId(
                (Get-Date), 'Central Standard Time')).ToString('HH:mm:ss.fff')
            err = ($_.Exception.Message -replace '"', "'")
        } | ConvertTo-Json -Compress
        Add-Content -Encoding utf8 -Path $logPath -Value $err
    }
    Start-Sleep -Seconds 1
}
