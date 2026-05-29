$logPath = 'D:\BookmapLogs\watcher_ps_log.jsonl'
$max = 2500  # ~20 hours at 30s for overnight ETH session
for ($i = 0; $i -lt $max; $i++) {
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:18888/api/snapshot' -TimeoutSec 5
        $f = $r.institutional_flow
        $rot = $f.rotation_state
        $microDrv = $f.drivers | Where-Object { $_.name -eq 'micro_events' } | Select-Object -First 1
        $micro = if ($microDrv) { $microDrv.signed } else { 0 }
        $top = ($f.drivers | Select-Object -First 3 | ForEach-Object { "$($_.name):$([Math]::Round($_.signed,2))" }) -join '|'
        $proxLevels = @($r.or_levels.levels) | Where-Object { $_.proximity -eq $true }
        $proxStr = ($proxLevels | ForEach-Object { "$($_.label)@$($_.price)" }) -join ','
        $ct = ([System.TimeZoneInfo]::ConvertTimeBySystemTimeZoneId((Get-Date), 'Central Standard Time')).ToString('HH:mm:ss')
        $buckets = $r.tape_buckets.buckets
        $bigBuy30s = ($buckets[2].buyVol30s + $buckets[3].buyVol30s + $buckets[4].buyVol30s)
        $bigSell30s = ($buckets[2].sellVol30s + $buckets[3].sellVol30s + $buckets[4].sellVol30s)
        $bigBuy5m = ($buckets[2].buyVol5m + $buckets[3].buyVol5m + $buckets[4].buyVol5m)
        $bigSell5m = ($buckets[2].sellVol5m + $buckets[3].sellVol5m + $buckets[4].sellVol5m)
        $totalVol30s = ($buckets | Measure-Object buyVol30s -Sum).Sum + ($buckets | Measure-Object sellVol30s -Sum).Sum
        $maxPrint30s = ($r.trades | Where-Object { $_.size -gt 0 } | Select-Object -First 60 | Measure-Object -Property size -Maximum).Maximum
        if ($null -eq $maxPrint30s) { $maxPrint30s = 0 }
        $tapeRecent = $r.trades | Select-Object -First 30
        $tapeBuyCt = ($tapeRecent | Where-Object { $_.side -eq 'buy' } | Measure-Object).Count
        $tapeSellCt = ($tapeRecent | Where-Object { $_.side -eq 'sell' } | Measure-Object).Count
        $tapeBuySz = ($tapeRecent | Where-Object { $_.side -eq 'buy' } | Measure-Object size -Sum).Sum
        $tapeSellSz = ($tapeRecent | Where-Object { $_.side -eq 'sell' } | Measure-Object size -Sum).Sum
        $obj = [ordered]@{
            ts = $ct
            iter = $i
            mid = $r.book.mid
            regime = $f.regime
            vote = [Math]::Round($f.weighted_vote, 3)
            raw_vote = [Math]::Round($f.raw_vote_pre_trend_filter, 3)
            conv = [Math]::Round($f.conviction, 3)
            trend_filter = $f.trend_filter
            trend_sign = $f.trend_sign
            micro_signed = $micro
            top_drivers = $top
            commit_level = $rot.commit_level
            commit_price = $rot.commit_price
            rotations = $rot.rotations_completed
            extreme = $rot.current_extreme_price
            next_target = $rot.next_rotation_target
            chop_window = $f.chop_window
            prox_levels = $proxStr
            rich_emit = $r.ifl_rich_signals.emitted
            lt_bid = $r.lt_liquidity.ltBidSize
            lt_ask = $r.lt_liquidity.ltAskSize
            lt_ratio = [Math]::Round($r.lt_liquidity.ratio, 3)
            session_h = $r.or_day_ledger.session_high_price
            session_l = $r.or_day_ledger.session_low_price
            session_range = $r.or_day_ledger.session_range
            ifl_active = if ($r.ifl_outcomes.active) { "$($r.ifl_outcomes.active.regime):peak=$($r.ifl_outcomes.active.peak_favorable_pts):adv=$($r.ifl_outcomes.active.max_adverse_pts):rungs=$($r.ifl_outcomes.active.rungs_advanced_so_far)" } else { '' }
            ifl_last_closed = if ($r.ifl_outcomes.last_closed) { "$($r.ifl_outcomes.last_closed.regime):$($r.ifl_outcomes.last_closed.verdict):rungs=$($r.ifl_outcomes.last_closed.rungs_advanced)" } else { '' }
            big_buy_30s = $bigBuy30s
            big_sell_30s = $bigSell30s
            big_buy_5m = $bigBuy5m
            big_sell_5m = $bigSell5m
            total_vol_30s = $totalVol30s
            max_print_30s = $maxPrint30s
            tape_buy_ct_30 = $tapeBuyCt
            tape_sell_ct_30 = $tapeSellCt
            tape_buy_sz_30 = $tapeBuySz
            tape_sell_sz_30 = $tapeSellSz
        }
        $line = $obj | ConvertTo-Json -Compress
        Add-Content -Encoding utf8 -Path $logPath -Value $line
    } catch {
        $errLine = '{"err":"' + ($_.Exception.Message -replace '"', "'") + '","iter":' + $i + '}'
        Add-Content -Encoding utf8 -Path $logPath -Value $errLine
    }
    Start-Sleep -Seconds 60
}
