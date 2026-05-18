---
name: or-bias
description: Reads the latest signal row from OR-Strategy's CSV output and cross-checks it against the current live orderbook so Claude can translate the strategy's opinion into a current-moment recommendation. Use when the user asks "what's the OR bias", "what's open range saying", "is OR-Strategy aligned with the tape", or "should I follow OR-Strategy right now". Read-only; does NOT modify any OR-Strategy code.
---

# OR-Bias Read

Reads OR-Strategy's own emitted signal (its CSV log) and pairs it with the live orderbook so the user can see whether the strategy's call is still valid right now.

## Where the CSV lives

OR-Strategy's `PaxOpeningRangeSignalCsvLogger` writes `openrange-signals-<symbol>.csv` to a configurable directory. On this machine the strategy writes to `D:\BookmapLogs`. Search these paths in order — use the most recently modified match:

1. `D:\BookmapLogs\openrange-signals-*.csv`  ← actual location on this box
2. `C:\Bookmap\addons\OR-Strategy\Reference-Indicators\OpenRange\build\logs\openrange-signals-*.csv`
3. `C:\Bookmap\build\logs\openrange-signals-*.csv`
4. Globbed: `Get-ChildItem -Path D:\,C:\ -Recurse -Filter "openrange-signals-*.csv" -ErrorAction SilentlyContinue` (last-resort search; ask user to point you if multiple hits)

The symbol in the filename is sanitized (non-alphanumerics replaced with `_`), e.g. NQM6.CME → `NQM6.CME`.

## CSV columns (in order)

```
time, symbol, price, orHigh, orLow,
cvdDelta, bidDepthDelta, askDepthDelta, netDepthDelta,
cvdZ, psZ, cvdPercentile, psPercentile,
location, distanceTicks, rangeWidth, rangeQuality,
ageSeconds,
bias, action, confidence, score, maxScore,
evidence, reason
```

Key fields for this skill:
- `bias` — directional read (e.g. LONG_BIAS, SHORT_BIAS, NEUTRAL).
- `action` — what the strategy thinks you should do (e.g. ENTER_LONG, ENTER_SHORT, STAND_DOWN, WAIT).
- `confidence` — categorical (HIGH / MEDIUM / LOW).
- `score` / `maxScore` — numeric strength.
- `location` — where price is relative to the OR (e.g. ABOVE_RANGE, INSIDE, BELOW_RANGE).
- `evidence` — comma-separated reasons (quoted, may contain commas).
- `reason` — human explanation of the call.

## Procedure

1. **Locate the CSV.** Use the search order above. If multiple candidates, take the one with the most recent `LastWriteTime`. If none found, output "no OR-Strategy CSV found — is OpenRange attached and running?" and stop.
2. **Read the last row.** Tail one line beyond the header. Be careful: `evidence` and `reason` are quoted and may contain commas. Use a CSV-aware reader; if shell-scripting, split on quoted boundaries.
3. **Check staleness.** Parse `time`. If older than 5 minutes, flag the read as `STALE` — the OR-Strategy may be paused, the instrument unattached, or the day's session over.
4. **Pull live orderbook.** Call `bookmap_orderbook(alias, depth=10)` for the same instrument.
5. **Cross-check alignment.** Compare CSV `price` and `location` to live `bestBid`/`bestAsk`:
   - If CSV says `LOCATION=ABOVE_RANGE` but live mid is now below `orHigh` → flag as **DISLOCATED** (signal context has changed since CSV was written).
   - If CSV `bias` is `LONG_BIAS` but the last 10 trades are heavily sell-aggressed (call `bookmap_recent_trades(alias, count=10)` and check), flag as **TAPE_DIVERGENT**.
6. **Emit summary.**

## Output format

```
OR-Strategy on {alias} @ {csvTime} ({ageSeconds}s old){ — STALE if applicable}
  bias: {bias}    action: {action}    confidence: {confidence}
  score: {score} / {maxScore}
  range: {orLow} ↔ {orHigh}   price-at-signal: {price}  ({location}, {distanceTicks} ticks)
  rangeQuality: {rangeQuality}    cvdZ: {cvdZ}   psZ: {psZ}
  evidence: {evidence}
  reason: {reason}

LIVE cross-check ({timestamp}):
  bestBid/bestAsk: {bestBid} / {bestAsk}   mid: {mid}
  alignment: {ALIGNED | DISLOCATED | TAPE_DIVERGENT}
  reason-for-alignment-flag: {one sentence}

Bottom line: {one-sentence translation of CSV bias + live context into a "follow / wait / fade" call}
```

## Hard rules

- This skill never modifies OR-Strategy source, CSVs, or any of the user's strategy files.
- If the CSV is stale or the alignment is DISLOCATED, the bottom line MUST be "WAIT for fresh signal" — never recommend following a stale read.
- If `risk-check` hasn't been run in this conversation and the user is about to act on this bias, prompt them to run `risk-check` first.
