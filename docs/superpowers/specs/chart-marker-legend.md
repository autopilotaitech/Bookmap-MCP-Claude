# Bookmap Chart Marker Legend

This is the operator-facing legend for OpenRange / Pax AI chart markers.

Current chart rendering rule: markers on the Bookmap chart are glyph-first. They should be tiny triangles or dots, not large printed labels over candles. Text codes below describe the source payload and diagnostics; they are not meant to be painted as large chart labels.

## Glyph Shape

| Glyph | Meaning |
|---|---|
| Up triangle | Bullish / long-side read |
| Down triangle | Bearish / short-side read |
| Dot | Neutral context, warning, or non-directional evidence |

## Source Families

| Source | Meaning | Chart behavior |
|---|---|---|
| `PAX_AI` | AI-originated signal from Pax AI popup trigger or Claude chart block | Plots from `snap["pax_ai_chart_events"]` |
| `LOCAL_ANCHORED` | Dashboard-local institutional thesis or level condition | Plots from `snap["institutional_chart_events"]` |
| `CONTEXT` | Microstructure evidence such as stack, pull, sweep, iceberg, absorption | Plots as context only; not an entry by itself |

## Pax AI Colors

| Color | Hex | Meaning |
|---|---:|---|
| Magenta | `#FF40D9` | Pax AI bullish / long-side signal |
| Cyan | `#40E0FF` | Pax AI bearish / short-side signal |
| Purple | `#A86DEC` | Pax AI neutral / non-directional signal |

## Local / Context Colors

| Color | Hex | Marker types |
|---|---:|---|
| Yellow | `#E5C100` | `WATCH_LEVEL`, `TOUCHED_LEVEL` |
| Orange | `#FF8C00` | `LIQUIDITY_SWEEP` |
| Blue | `#00BFFF` | `ABSORPTION` |
| Purple | `#A86DEC` | `ICEBERG_DEFENSE` |
| Red | `#FF4D4D` | `SPOOF_RISK`, bearish rejection |
| Green | `#2BD25B` | Bullish acceptance / rejection |
| Gray | `#B0B0B0` | `PULLING`, `STACKING`, `SCRATCH`, fallback |

## Direction Rules

| Direction | Meaning |
|---|---|
| `LONG` | Bullish read. Can only be an actionable direction for `PAY_FOR_TRADE` or Pax AI `BIAS_SIGNAL`. |
| `SHORT` | Bearish read. Can only be an actionable direction for `PAY_FOR_TRADE` or Pax AI `BIAS_SIGNAL`. |
| `NONE` | Context / wait / warning / scratch. Not a trade direction. |

Hard rule: local `WATCH`, `TOUCHED`, `SWP`, `ABS`, `ICE`, `SPD`, `PULL`, and `STACK` are evidence, not entries.

## Abbreviations

| Code | Full Name | Meaning |
|---|---|---|
| `AI` | Pax AI | AI-originated chart signal. |
| `L` / `LOC` | Local anchored | Dashboard-local institutional condition. |
| `C` / `CTX` | Context | Microstructure context. |
| `TRD` | Trend | Pax AI trend trigger, such as `WEAK_BEAR` or `STRONG_BULL`. |
| `REG` | Regime | Pax AI regime trigger, such as exhaustion or absorption regime change. |
| `CNV` | Conviction | Pax AI conviction flip. |
| `MIC` | Micro event | Pax AI microstructure trigger. |
| `WCH` / `WATCH` | Watch | Level is near or setup needs confirmation. |
| `TCH` | Touched | Price touched a tracked level. |
| `SWP` | Sweep | Stop/liquidity sweep near a level. |
| `ABS` | Absorption | Aggressor flow is being absorbed by passive liquidity. |
| `ICE` | Iceberg | Iceberg-style defending liquidity near a level. |
| `SPD` | Spoof risk | Flash/cancel or spoof-risk style depth behavior. |
| `PUL` / `PULL` | Pulling | Book liquidity is pulling away from the level. |
| `STK` / `STACK` | Stacking | Book liquidity is stacking toward the level. |
| `ACC` | Acceptance | Accepted through a level. Can be actionable only when paired with `PAY_FOR_TRADE`. |
| `REJ` | Rejection | Rejected from a level. Can be actionable only when paired with `PAY_FOR_TRADE`. |
| `SCR` | Scratch | Prior thesis invalidated; scratch/exit context. |
| `ORH` / `OR-H` | Opening range high | Upper opening-range level. |
| `ORL` / `OR-L` | Opening range low | Lower opening-range level. |

## Action Types

| Action | Direction allowed | Meaning |
|---|---|---|
| `PAY_FOR_TRADE` | `LONG` or `SHORT` | Entry-quality acceptance/rejection. |
| `BIAS_SIGNAL` | `LONG` or `SHORT` | Pax AI popup/trigger bias marker. It is a chart signal, not a local entry order. |
| `WAIT_FOR_CONFIRM` | `NONE` | Watch only. Needs more confirmation. |
| `STAND_DOWN` | `NONE` | Do not act on this condition. |
| `SCRATCH_READY` | `NONE` | Exit/scratch context for a prior thesis. |

## Severity / Priority

| Severity | Priority | Meaning |
|---|---:|---|
| `ENTRY` | 0 | Highest priority. Acceptance/rejection with `PAY_FOR_TRADE`. |
| `EXIT` | 1 | Scratch/exit context. |
| `WARNING` | 2 | Spoof, iceberg, sweep, stand-down risk. |
| `WATCH` | 3 | Watch/confirmation or Pax AI bias marker. |
| `INFO` | 4 | Background context such as pull/stack. |

When a cluster is dense, higher-priority items win. AI is prioritized before local anchored, and local anchored before generic context inside the same collision bucket.

## Examples

| Diagnostic code | Reads as |
|---|---|
| `AIvTRD55` | Pax AI bearish trend bias, confidence 55. |
| `AI^REG75` | Pax AI bullish regime signal, confidence 75. |
| `L^ACC68` | Local anchored bullish acceptance, confidence 68. |
| `LvREJ71` | Local anchored bearish rejection, confidence 71. |
| `C.STK44` | Context: stack, no direction, confidence 44. |
| `C.ICE60` | Context: iceberg defense, no direction, confidence 60. |

On the Bookmap chart, the intended production view is the glyph and color, not the full diagnostic text.

