# OpenRange User And API Guide

## Artifact

Current build artifact:

```text
build\libs\openrange-release.jar
```

Bookmap strategy name:

```text
OpenRange
```

Main package:

```text
com.openrange
```

The Java class names still use the historical `PaxOpeningRange...` prefix internally. User-facing names, CSV output names, and the built jar use `OpenRange`.

## What OpenRange Does

OpenRange marks the opening-range high, low, optional midpoint, and dynamic extension levels. After the opening range completes, it evaluates breakout context using:

- price location relative to ORH/ORL
- breakout distance in ticks
- CVD delta
- bid depth delta
- ask depth delta
- net pulling/stacking delta
- rolling CVD percentile
- rolling pulling/stacking percentile
- OR range quality
- ES/NQ cross-market confirmation or divergence

It does not place trades. It produces chart telemetry and CSV logs for review, filtering, and external analysis.

## Install

1. Build or use the jar:

   ```text
   build\libs\openrange-release.jar
   ```

2. In Bookmap, load it as a Layer 1 add-on.

3. Add the `OpenRange` screen-space painter to the chart.

4. Configure the settings panel before the session starts or before the target opening-range window.

## Settings

| Setting | Default | Meaning |
|---|---:|---|
| Start hour | `9` | Exchange-time OR start hour. |
| Start minute | `30` | Exchange-time OR start minute. |
| Start second | `0` | Exchange-time OR start second. |
| Range seconds | `30` | Opening-range duration. |
| Line end hour | `17` | Hour where OR lines stop drawing. |
| Line end minute | `0` | Minute where OR lines stop drawing. |
| Days to display | `8` | Number of historical OR days shown. |
| Signal CVD threshold | `1` | Raw CVD minimum used by the base signal score. |
| Signal depth threshold | `1` | Raw depth delta minimum used by the base signal score. |
| Signal depth levels | `10` | Number of near-price levels used for pulling/stacking deltas. |
| Min breakout ticks | `0` | If above zero, waits until breakout is at least this far outside OR. |
| Max breakout ticks | `0` | If above zero, blocks breakouts beyond this distance. |
| Min signal score | `3` | Minimum base score out of 4 required before quality gates. |
| Min CVD percentile | `70` | Longs require percentile >= this value; shorts require percentile <= `100 - value`. |
| Min PS percentile | `70` | Longs require percentile >= this value; shorts require percentile <= `100 - value`. |
| Norm window seconds | `120` | Rolling window for z-scores and percentiles. |
| CSV log directory | `build\logs` | Directory where signal CSV files are written. |
| Label prefix | `OpenRange` | Prefix used on OR level labels. |
| Block ES/NQ divergence | `true` | Blocks allowed signals when cross-market state diverges. |
| Show mid line | `false` | Draws OR midpoint. |

## Badge

Example:

```text
ORH +8t RNG 9.3 OK AGE 2m | X CONF
CVD p82 UP  PS p91 UP
```

| Field | Meaning |
|---|---|
| `ORH` | Price is above the opening-range high. |
| `ORL` | Price is below the opening-range low. |
| `INSIDE` | Price is inside the opening range. |
| `+8t` / `-8t` | Distance in ticks from ORH or ORL. |
| `RNG 9.3` | Opening-range width in instrument price units. |
| `TIGHT` | OR width is in the lower tail of recent OR widths. |
| `OK` | OR width is normal versus recent OR widths. |
| `WIDE` | OR width is in the upper tail of recent OR widths. |
| `AGE 2m` | Time since the OR completed. |
| `CVD p82 UP` | CVD is at the 82nd percentile and positive/aligned upward. |
| `PS p91 UP` | Pulling/stacking is at the 91st percentile and positive/aligned upward. |
| `X CONF` | Cross-market state confirms. |
| `X DIV` | Cross-market state diverges. |

## Signal Model

Signals use `PaxOpeningRangeSignal`.

```java
public record PaxOpeningRangeSignal(
    PaxOpeningRangeSignalAction action,
    PaxOpeningRangeSignalBias bias,
    PaxOpeningRangeSignalConfidence confidence,
    String reason,
    int score,
    int maxScore,
    String evidence,
    String location,
    int distanceTicks,
    double rangeWidth,
    long ageSeconds,
    double cvdDelta,
    double pullingStackingDelta,
    double cvdZScore,
    double pullingStackingZScore,
    int cvdPercentile,
    int pullingStackingPercentile,
    String rangeQuality
)
```

### Actions

| Enum | Meaning |
|---|---|
| `WAIT` | Not enough context or price is not in a valid breakout state. |
| `ALLOW_SIGNAL` | Base logic and quality gate allow the setup. |
| `BLOCK_SIGNAL` | Price may be outside OR, but logic blocks the setup. |

### Bias

| Enum | Meaning |
|---|---|
| `LONG` | Price is above ORH. |
| `SHORT` | Price is below ORL. |
| `NEUTRAL` | Price is inside OR or breakout distance is not valid. |

### Confidence

| Enum | Meaning |
|---|---|
| `HIGH` | Strong base order-flow confirmation. |
| `MEDIUM` | Acceptable base order-flow confirmation. |
| `LOW` | Blocked or weak. |
| `NONE` | Waiting or neutral. |

## Base Signal Score

The base signal engine is `PaxOpeningRangeSignalEngine`.

Constructor:

```java
new PaxOpeningRangeSignalEngine(PaxOpeningRangeSignalSettings settings, double tickSize)
```

Evaluation:

```java
PaxOpeningRangeSignal signal = engine.evaluate(day, market);
```

Long breakout scoring:

| Condition | Score |
|---|---:|
| `cvdDelta >= minCvdConfirmation` | `+1` |
| `bidDepthDelta >= minDepthConfirmation` | `+1` |
| `askDepthDelta <= -minDepthConfirmation` | `+1` |
| `netDepthDelta >= minDepthConfirmation` | `+1` |

Short breakout scoring:

| Condition | Score |
|---|---:|
| `cvdDelta <= -minCvdConfirmation` | `+1` |
| `bidDepthDelta <= -minDepthConfirmation` | `+1` |
| `askDepthDelta >= minDepthConfirmation` | `+1` |
| `netDepthDelta <= -minDepthConfirmation` | `+1` |

The base engine blocks when `score < minScore`.

## Quality Gate

After the base signal is built and percentiles are attached, `PaxOpeningRangeSignalQualityGate` applies stricter filtering.

```java
PaxOpeningRangeSignalQualityGate gate =
    new PaxOpeningRangeSignalQualityGate(settings);

PaxOpeningRangeSignal gated =
    gate.apply(signal, crossMarketStatus);
```

Defaults:

```java
new PaxOpeningRangeSignalQualitySettings(70, 70, true)
```

Rules:

- Non-`ALLOW_SIGNAL` signals pass through unchanged.
- If ES/NQ cross-market status is `DIVERGE` and divergence blocking is enabled, the signal becomes `BLOCK_SIGNAL`.
- For longs, CVD percentile must be at or above the configured CVD gate.
- For longs, pulling/stacking percentile must be at or above the configured PS gate.
- For shorts, CVD percentile must be at or below `100 - CVD gate`.
- For shorts, pulling/stacking percentile must be at or below `100 - PS gate`.

Example with default `70` gate:

| Bias | Strong CVD Percentile | Strong PS Percentile |
|---|---:|---:|
| Long | `>= 70` | `>= 70` |
| Short | `<= 30` | `<= 30` |

## Market State API

Market state uses `PaxOpeningRangeMarketState`.

```java
public record PaxOpeningRangeMarketState(
    double lastPrice,
    double cvdDelta,
    double bidDepthDelta,
    double askDepthDelta,
    double netDepthDelta
)
```

`netDepthDelta` is bid-side delta minus ask-side delta.

## Order Flow Tracker

`PaxOpeningRangeOrderFlowTracker` ingests trades and depth changes.

```java
PaxOpeningRangeOrderFlowTracker tracker =
    new PaxOpeningRangeOrderFlowTracker(tickSize, depthLevels);

tracker.onTrade(price, size, isBidAggressor);
tracker.onDepth(isBid, price, size);

PaxOpeningRangeMarketState market = tracker.snapshot();
```

Notes:

- Trade size is added to CVD for ask aggressors.
- Trade size is subtracted from CVD for bid aggressors.
- Depth changes are only tracked near the last traded price.
- The near-price window is `tickSize * depthLevels`.

## Feature Cache API

The low-latency cache is `PaxOpeningRangeFeatureCache`.

```java
PaxOpeningRangeFeatureCache cache =
    new PaxOpeningRangeFeatureCache(1024);

PaxOpeningRangeFeatureSnapshot snapshot =
    cache.update(timeNanos, market, signal);

PaxOpeningRangeFeatureSnapshot latest = cache.latest();
List<PaxOpeningRangeFeatureSnapshot> history = cache.history();
```

Snapshot:

```java
public record PaxOpeningRangeFeatureSnapshot(
    long timeNanos,
    PaxOpeningRangeMarketState market,
    PaxOpeningRangeSignal signal,
    String badgeText,
    PaxOpeningRangeSignalColorState colorState
)
```

Behavior:

- `latest()` is a volatile read of the newest snapshot.
- `history()` returns an immutable copy.
- History is bounded by constructor capacity.
- The painter reads cached badge text and cached color state instead of recalculating them during paint.

## Diagnostics API

Diagnostics are formatted by `PaxOpeningRangeDiagnostics`.

```java
String text = PaxOpeningRangeDiagnostics.format(cache, nowNanos, csvPath);
```

Example output:

```text
Snapshots: 42
Last update: 1s ago
CVD: p82
PS: p91
Range: OK
CSV: build\logs\openrange-signals-ESM6.csv
```

Diagnostics appear in the Bookmap settings panel.

## CSV Logging

CSV files are written by `PaxOpeningRangeSignalCsvLogger`.

Default directory:

```text
build\logs
```

Default file pattern:

```text
openrange-signals-<symbol>.csv
```

Path helper:

```java
Path path = PaxOpeningRangeLogPath.signalLogPath(directory, symbol);
```

Logger:

```java
PaxOpeningRangeSignalCsvLogger logger =
    new PaxOpeningRangeSignalCsvLogger(path);

logger.logIfChanged(symbol, time, orHigh, orLow, market, signal);
```

The logger skips duplicate signal states. A row is written when the key changes:

```text
symbol|bias|action|confidence|score|evidence
```

### CSV Schema

| Column | Meaning |
|---|---|
| `time` | Local signal timestamp. |
| `symbol` | Bookmap instrument symbol. |
| `price` | Latest price. |
| `orHigh` | Opening-range high. |
| `orLow` | Opening-range low. |
| `cvdDelta` | Raw CVD delta. |
| `bidDepthDelta` | Bid-side depth delta. |
| `askDepthDelta` | Ask-side depth delta. |
| `netDepthDelta` | Bid depth delta minus ask depth delta. |
| `cvdZ` | Rolling CVD z-score. |
| `psZ` | Rolling pulling/stacking z-score. |
| `cvdPercentile` | Rolling CVD percentile. |
| `psPercentile` | Rolling pulling/stacking percentile. |
| `location` | `ORH`, `ORL`, or `INSIDE`. |
| `distanceTicks` | Distance from OR boundary in ticks. |
| `rangeWidth` | OR width. |
| `rangeQuality` | `TIGHT`, `OK`, `WIDE`, or blank. |
| `ageSeconds` | Seconds since OR completion. |
| `bias` | `LONG`, `SHORT`, or `NEUTRAL`. |
| `action` | `WAIT`, `ALLOW_SIGNAL`, or `BLOCK_SIGNAL`. |
| `confidence` | `HIGH`, `MEDIUM`, `LOW`, or `NONE`. |
| `score` | Base order-flow score. |
| `maxScore` | Maximum base score, currently `4`. |
| `evidence` | Compact order-flow evidence string. |
| `reason` | Human-readable reason. |

## Range Quality

`PaxOpeningRangeRangeQuality.fromPercentile(percentile)` maps OR width percentile to a label:

| Percentile | Label |
|---:|---|
| `<= 0` | blank |
| `1..20` | `TIGHT` |
| `21..79` | `OK` |
| `80..100` | `WIDE` |

## Rolling Percentiles

`PaxOpeningRangeRollingPercentile` keeps a bounded time window of samples.

```java
PaxOpeningRangeRollingPercentile percentile =
    new PaxOpeningRangeRollingPercentile(120);

percentile.add(timeNanos, value);
int p = percentile.percentile(value);
```

Behavior:

- Returns `0` when empty.
- Percentile is calculated as percent of samples less than or equal to the value.
- Samples older than the configured window are evicted on `add`.

## Rolling Z-Scores

`PaxOpeningRangeRollingStats` tracks rolling samples for z-score normalization.

```java
PaxOpeningRangeRollingStats stats =
    new PaxOpeningRangeRollingStats(120);

double z = stats.zScore(value);
stats.add(timeNanos, value);
```

The live path computes z-score before adding the current sample, then adds the sample after signal evaluation.

## Cross-Market State

Cross-market status is tracked by `PaxOpeningRangeCrossMarketState`.

```java
crossMarketState.update(symbol, signal);
PaxOpeningRangeCrossMarketStatus status =
    crossMarketState.statusFor(symbol);
```

Statuses:

| Status | Meaning |
|---|---|
| `NONE` | Not enough related market state. |
| `CONFIRM` | Related market bias confirms. |
| `DIVERGE` | Related market bias diverges. |

The chart badge appends `X CONF` or `X DIV` on the first line.

## Live Data Flow

```text
Bookmap trade/depth event
  -> PaxOpeningRangeOrderFlowTracker
  -> PaxOpeningRangeCalculator
  -> PaxOpeningRangeSignalEngine
  -> attach z-scores, percentiles, range quality, age
  -> PaxOpeningRangeSignalQualityGate
  -> PaxOpeningRangeFeatureCache
  -> Cross-market state
  -> CSV logger when signal state changes
  -> Painter reads latest cached snapshot
```

## Extension Points

### Add A New Signal Filter

Best location:

```text
PaxOpeningRangeSignalQualityGate
```

Reason: this keeps base OR/order-flow scoring separate from final trade-quality filtering.

### Add A New Badge Field

Best locations:

```text
PaxOpeningRangeSignal
PaxOpeningRangeSignalFormatter
PaxOpeningRangeFeatureCache
```

Add the value to the signal, format it once, and let the painter read cached text.

### Add A New CSV Field

Best location:

```text
PaxOpeningRangeSignalCsvLogger
```

Update both `HEADER` and `row(...)`, then add or update the CSV logger test.

### Add A New Setting

Best locations:

```text
PaxOpeningRangeUiSettings
PaxOpeningRangeModule#getCustomGuiFor
```

Add the field to settings, clamp it in a converter if needed, expose it in the panel, and include it in the `apply` runnable.

## Tests

Build script:

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\build.ps1
```

Current test coverage includes:

- opening-range calculation
- signal engine behavior
- order-flow tracking
- badge formatting
- CSV logging
- rolling stats
- signal color state
- cross-market state
- log path generation
- rolling percentiles
- range quality
- feature cache
- signal quality gate
- diagnostics formatting

## Safety Notes

OpenRange is an indicator and logging tool. It does not submit, modify, or cancel orders. Treat every signal as telemetry that must be validated against market context, risk, liquidity, and execution constraints.
