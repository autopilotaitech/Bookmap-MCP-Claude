# OpenRange

Bookmap add-on for opening-range levels, order-flow confirmation, and compact signal telemetry.

## Install

Build output:

```text
build\libs\openrange-release.jar
```

Load the jar in Bookmap as a Layer 1 add-on.

## Badge

The badge is intentionally compact:

```text
ORH +8t RNG 9.3 OK AGE 2m
CVD p82 UP  PS p91 UP
```

- `ORH` / `ORL` / `INSIDE`: price location relative to the opening range.
- `+8t`: distance in ticks from the range boundary.
- `RNG`: opening-range width.
- `TIGHT` / `OK` / `WIDE`: range quality versus recent range widths.
- `AGE`: time since the opening range completed.
- `CVD p82`: CVD percentile in the rolling normalization window.
- `PS p91`: pulling/stacking percentile in the rolling normalization window.
- `X CONF` / `X DIV`: ES/NQ cross-market confirmation or divergence.

## Signal Gate

OpenRange can block otherwise valid breakout signals when quality is weak:

- CVD percentile is below the configured gate for longs.
- Pulling/stacking percentile is below the configured gate for longs.
- For shorts, lower-tail percentiles are treated as strong negative flow.
- ES/NQ divergence is blocked when `Block ES/NQ divergence` is enabled.

The badge remains visible when blocked so the reason can be audited.

## Diagnostics

The settings panel includes diagnostics:

- feature snapshot count
- last update age
- current CVD percentile
- current pulling/stacking percentile
- current range quality
- CSV path

## CSV

Default directory:

```text
build\logs
```

File names use:

```text
openrange-signals-<symbol>.csv
```

The CSV includes raw order-flow values, z-scores, percentiles, range quality, signal state, and reason.
