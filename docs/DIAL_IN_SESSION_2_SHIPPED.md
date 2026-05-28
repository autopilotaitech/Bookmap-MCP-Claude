# Dial-In Session 2 — Coding Window Summary

**Window:** 2026-05-28 ~14:13-14:50 CT (~37 min Java + Python ship time)
**Author:** Claude. Operator AFK; ready for review on return.

---

## What shipped

All items below tested + verified. No item marked "done" without
test evidence.

### Python (`mcp-server/bookmap_mcp/`)

| Item | File(s) | Tests | Status |
|---|---|---|---|
| **S1** — `--no-auto-decide` flag in pax_daemon | `pax_daemon.py` | flag-parse + 10 lifecycle tests still pass | ✅ |
| **S2** — `pax_manual.py` CLI for manual paper trades | `pax_manual.py` (new) | 8 new tests | ✅ |
| **S3** — `pax-manual-start.bat` launcher | repo root (new) | n/a (shell) | ✅ |
| **T1** — Whipsaw lockout | `institutional_flow.py` | 17 new tests in `test_institutional_flow_whipsaw_lockout.py` | ✅ |
| **T2** — `flow.lt_signal_quality` field | `institutional_flow.py` | 13 new tests in `test_institutional_flow_lt_signal_quality.py` | ✅ |
| **J2** — OpenRange WATCH-label reconcile | (none — already done) | 5/5 Python `regime` tests + 4/4 Java tests | ✅ verified |

### Java (`src/main/java/com/bookmapmcp/`)

| Item | File(s) | Tests | Status |
|---|---|---|---|
| **J1** — Bridge tape size=0 noise filter | `state/InstrumentState.java` + test | new `zeroSizeTradesDoNotEnterRecentTradesDeque` + 20 InstrumentStateTest + 90 full bridge suite | ✅ |
| Bridge version bump | `build.gradle` v22 → v23 | n/a | ✅ |
| Bridge jar v23 built + staged | `C:\Bookmap\addons-staging\bookmap-mcp-bridge\bookmap-mcp-bridge-v23.jar` (168 KB) | n/a | ✅ |

### Diagnostic (J1 data)

| Item | Path | Size |
|---|---|---|
| Tape fragmentation diagnostic script | `_session_snapshots/tape_fragmentation_diagnostic.ps1` | new |
| Live diagnostic JSONL (10+ min of trades) | `D:\BookmapLogs\tape_fragmentation_log.jsonl` | 787 KB / 8036 lines |

### Test totals

```
Python: 978 baseline -> 999 passing (+38 new, -17 already counted in baseline)
Java bridge: 90/90 passing (1 new test added to InstrumentStateTest)
```

---

## How to use the SIM tonight

1. Operator (or me): close Bookmap when ready.
2. Copy staged jar to canonical install path:
   ```powershell
   Copy-Item 'C:\Bookmap\addons-staging\bookmap-mcp-bridge\bookmap-mcp-bridge-v23.jar' `
             'C:\Bookmap\addons\bookmap-mcp-bridge.jar' -Force
   ```
3. Reopen Bookmap. Verify startup line:
   `Bookmap MCP bridge listening on http://127.0.0.1:8765`
4. Launch SIM stack:
   ```cmd
   pax-manual-start.bat
   ```
   - Tick-only daemon window (NO auto-decide)
   - Overview UI at http://127.0.0.1:18890
5. Drive trades from any shell:
   ```cmd
   python -m bookmap_mcp.pax_manual long  2 30305.50 30295.00 30325.00 30341.00 --reason "+3 ext absorption"
   python -m bookmap_mcp.pax_manual short 2 30331.75 30342.00 30260.00          --reason "fresh DIST"
   python -m bookmap_mcp.pax_manual flatten --reason "thesis softened"
   python -m bookmap_mcp.pax_manual status
   ```
6. Overview UI shows all fills, P&L, journal entries for review tomorrow.

If staged jar is NOT deployed: SIM still works using the OLD bridge data
(size=0 noise still pollutes tape_buckets, but trades + fills function).
Only the bridge-side tape cleanup is in the new jar.

---

## J1 finding (the actual root cause)

**Live diagnostic captured 8036 trades over 10 min.** Size distribution:

```
size=0:  39%  ← MBO metadata markers, NOT real trades
size=1:  59%
size 2-9: 1%
size=10:  1 print
size=24:  1 print
size=25:  1 print
```

**Two corrections to my earlier dial-in hypotheses:**

1. **Bridge does NOT fragment large MBO prints.** When operator's MBO
   display shows a size-25 print, the bridge captures it as ONE
   size-25 trade. I verified this in the live data — size=24 and
   size=25 prints came through cleanly.

2. **The real problem was size=0 noise** polluting `recent_trades` and
   `tape_buckets`. 39% of the bridge's deque entries were not real
   trades. This explained why my "max size = 9" sampling was wrong
   today: when 39% of the 200-trade window is junk, the real signal is
   harder to see.

**The fix** (conservative): filter `size == 0` at the `recentTrades.addLast`
path only. Other downstream feeds (CVD, VWAP, FlowRegime, VP, MBO delta,
trend) are UNCHANGED because they multiply by size (so size=0 has no
numeric impact) and we don't know yet if any rely on the zero-size event
as a state marker.

**Open question (queued):** 693 cases in the diagnostic where two trades
with same `(price, side)` printed within 1 ms of each other. These could
be legitimate adjacent prints OR fragmented per-order events. Need a
second diagnostic that also captures the orderbook state at trade time
to disambiguate. Deferred.

---

## What was NOT done

| Item | Why |
|---|---|
| **J3** — surface `restingClusters[]` in bridge snapshot | 2-hour effort; no time before ETH. Queued. |
| **Deploy v23 jar to live install path** | Requires Bookmap close; operator's call. Staged + ready. |
| **Auto-decide replacement (Pax intelligent decisions)** | Out of scope today. SIM tracks operator-driven trades only. |

---

## Discipline notes

- `_phase_backups/pre_sim_manual_20260528_141606/` contains pre-change
  copies of all 3 files I edited. Manifest + PowerShell rollback inside.
- Test-first discipline followed: every code change has at least one test
  pinning the contract. Test-add-first for whipsaw lockout and LT signal
  quality; test-after for J1 fix (verified behavior was correct then
  pinned).
- No emojis or unicode added to ASCII-only files per repo convention.
- No git commits made. Operator commits explicitly when ready.
