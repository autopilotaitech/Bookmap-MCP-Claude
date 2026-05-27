# Pax AI Pre-Market Signal Audit (2026-05-27)

Read-only audit of the first 16 closed `/api/pax/levels/edge` signals
captured by the Slice 3 JSONL log. The goal is to decide whether the
plotted signals should influence morning entries, and to identify whether
any of the 8 candidate failure modes (direction, setup, proximity, stop,
confidence, thesis, drivers, sample) is the dominant cause.

**Bottom line.** Sample is too small and biased to one low-quality overnight
window. Aggregate edge is negative at -0.27R mean over 60s, hit rate 0.31.
Plot for visibility; do not let the signal influence entries today. One
tiny, report-only patch is proposed (sort driver bundles for analytical
grouping); no live behaviour change.

---

## 1. Current Performance

Source: `%LOCALAPPDATA%\pax-ai\level-edge-log\2026-05-27.closed.jsonl`

```
n_signals          16
n_skipped          0
hit_rate_15s     0.31  mean_R_15s  -0.08   median_R_15s  -0.02
hit_rate_60s     0.31  mean_R_60s  -0.27   median_R_60s  -0.06
hit_rate_300s    0.44  mean_R_300s -0.40   median_R_300s -0.00
invalidated      5 / 16  (0.31)
```

All 16 signals fired between 01:46 and 07:59 UTC (20:46-02:59 CT) — the
overnight pre-market window, not RTH. None of the morning session has been
sampled yet.

---

## 2. Closed Signal Table

`stop_dist` = `abs(mid_at_signal - stop_price)` in points. `inv` =
invalidated (any horizon R ≤ -1.0).

```
utc_time    lbl   dir   setup              conf  tier  mid_sig    stop_dist  R_15   R_60   R_300  inv
01:46:49    +1    SHORT LEVEL_FADE_SHORT   0.483 HALF  30132.50   64.00      +0.07  +0.04  -0.07  no
01:47:04    +1    SHORT LEVEL_FADE_SHORT   0.403 HALF  30128.12   59.62      -0.04  -0.06  -0.09  no
02:09:41    +1    SHORT LEVEL_FADE_SHORT   0.428 HALF  30141.88   73.38      +0.03  +0.02  +0.08  no
03:15:14    +1    SHORT LEVEL_FADE_SHORT   0.375 HALF  30134.75   66.25      -0.03  -0.05  +0.02  no
03:17:18    +1    SHORT LEVEL_FADE_SHORT   0.389 HALF  30132.88   64.38      -0.02  -0.03  +0.05  no
06:35:09    OR-H  LONG  OR_BREAK_FOLLOW    0.359 HALF  30072.88    9.63      -0.30  -1.09  -0.47  YES
06:36:07    OR-L  SHORT OR_BREAK_FOLLOW    0.433 HALF  30060.88    7.62      -0.26  -0.52  -1.64  YES
06:36:14    OR-L  SHORT OR_BREAK_FOLLOW    0.439 HALF  30061.62    6.88      -0.07  -0.36  -1.64  YES
06:36:26    OR-L  SHORT OR_BREAK_FOLLOW    0.351 HALF  30061.62    6.88      -0.29  -1.02  -1.53  YES
06:36:29    OR-L  SHORT OR_BREAK_FOLLOW    0.376 HALF  30062.12    6.38      -0.39  -0.86  -1.57  YES
06:48:31    OR-H  LONG  OR_BREAK_FOLLOW    0.371 HALF  30080.38   17.13      +0.02  -0.32  +0.13  no
06:50:23    OR-H  LONG  OR_BREAK_FOLLOW    0.500 FULL  30079.88   16.63      +0.04  +0.24  +0.04  no
06:50:45    OR-H  SHORT LEVEL_FADE_SHORT   0.447 HALF  30080.12   11.62      -0.11  -0.28  +0.25  no
07:57:44    +1    LONG  OR_BREAK_FOLLOW    0.420 HALF  30141.62   78.37       0.00   0.00   0.00  no
07:57:58    +1    LONG  OR_BREAK_FOLLOW    0.412 HALF  30141.38   78.13      +0.01  +0.01  -0.01  no
07:59:20    +1    LONG  OR_BREAK_FOLLOW    0.361 HALF  30141.62   78.37       0.00  -0.06   0.00  no
```

Driver-bundle distribution (after sorting member names — see §6):

```
n=9   orderbook+pull_stack+tape    (raw was 5x p+o+t, 3x p+t+o, 1x t+p+o)
n=4   micro+orderbook+tape         (raw was 3x m+o+t, 1x m+t+o)
n=2   micro+orderbook+pull_stack   (raw was 1x m+p+o, 1x p+o+m)
n=1   micro+pull_stack+tape        (raw was 1x m+t+p)
```

---

## 3. Failure Clusters

### Cluster A: OR-L SHORT BREAK (n=4, all invalidated)

All four signals fired in a 22-second window (06:36:07-06:36:29 UTC) at
OR-L = 30063.50, mid 30060.88-30062.12, after price had **already broken
~2-3 points below OR-L**. Direction was a continuation breakout SHORT.

- Stop = `or_high + tick = 30068.50`; stop distance 6.4-7.6 pts.
- Price reversed up through OR-L and into a 12+pt rally; all four hit
  `R_300 ≤ -1.5`, all invalidated.
- Driver bundle (sorted): `micro+orderbook+tape` on all four.
- Conf 0.35-0.44; size_tier HALF for all.

OR width = `30068.25 - 30063.50 = 4.75 pts`. The stop is anchored at the
opposite OR boundary, so a *full OR-width adverse move plus a tick* is
exactly what invalidates. With OR this narrow, the stop is mechanically
fragile: ordinary post-break noise is enough to trigger -1R.

The dashboard-side rearm fired four separate opens in 22 seconds, which is
correct per Slice 3 dedup semantics (false→true rising-edge per state
flip) but means a single failed setup contributes 4 of the 5 invalidations.

### Cluster B: OR-H LONG BREAK (n=3)

06:35 (invalidated -1.09 at 60s), 06:48 (-0.32 at 60s, recovered +0.13 at
300s), 06:50 FULL (+0.24 at 60s, the only +60s winner). Stop distance
9.6-17.1 pts. Mid was 5-12 pts above OR-H — i.e. already past the break.
Mean_R_60s ≈ -0.39, but one row each at strong loss / mild loss / mild win.

### Cluster C: OR-H FADE SHORT (n=1)

Mid 30080.12, 11.6 pts above OR-H. The dashboard proximity gate is 12.5
pts (`PROX_TICKS = 50` × `NQ_TICK = 0.25` in dashboard.py:392), so this
just barely passed proximity. R_60 = -0.28, R_300 = +0.25 — the fade
recovered after 5 minutes. Single sample.

### Cluster D: +1 FADE SHORT (n=5)

Overnight 01:46-03:17 UTC. All five outcomes are essentially flat
(`|R_60| ≤ 0.06`). Stop distance 60-73 pts, because the stop is anchored
at OR-H + tick regardless of which level the FADE is at. With such a wide
stop, even a normal ~3-tick move registers as ~0.05R — outcomes look
nothing-burgers, but that is a *measurement artefact* of the stop, not
proof the signal worked or didn't work.

### Cluster E: +1 LONG BREAK (n=3)

Three signals at 07:57-07:59 UTC after price had broken through +1.
Stops anchored at OR-L − tick = 30063.25, 78+ pts away. All three are
flat for the same reason as Cluster D: the stop is far enough that 60s of
price action cannot move the meter.

---

## 4. Suspected Root Causes (ranked)

1. **Sample is too small and overnight-biased.** 16 signals from a single
   pre-RTH session, with 4 of them being the same OR-L breakout retrigger
   in 22 seconds. There is no statistical case for or against the signal
   on this corpus. Audit weight: dominant.

2. **OR width was anomalously narrow during the OR-L cluster** (4.75 pts).
   Stops anchored at the opposite OR boundary are mechanically tight when
   OR is narrow. This explains *every* invalidation in Cluster A but is
   not a logic bug — it is the consequence of an unusually-tight session
   OR meeting the documented `edge_calculus.invalidation_price` rule.

3. **Stop placement is OR-anchored for all levels**, including
   `+1/+2/+3/-1/-2/-3` extensions. For OR-side signals (Clusters A and B)
   the math is consistent with spec. For extension signals (Clusters D and
   E) the stop sits 60-78 pts away, making realized-R essentially zero
   for any normal 60s move. Outcomes from extension levels do not measure
   what we want them to measure. This is by spec (`edge_calculus.py:192`
   docstring), not a bug.

4. **Driver bundle ordering inflates apparent variance in the report.**
   The same driver triplet appears under multiple raw orderings
   (`pull_stack+orderbook+tape` vs `pull_stack+tape+orderbook` vs
   `tape+pull_stack+orderbook`) because the live `top_drivers` list is
   ranked by `|score × weight|`, which jitters. Live signal/log is
   correct; the report's per-bundle grouping fragments. This is
   analytical-only.

5. **Driver mix does NOT explain wins vs losses on this sample.** The
   FULL +0.24R winner used `pull_stack+orderbook+tape` (sorted:
   `orderbook+pull_stack+tape`), the same sorted bundle as 8 other
   signals with mixed outcomes. The 4 OR-L losers all used
   `micro+orderbook+tape`. With n ≤ 4 per bundle, this is not a
   conclusion, only a hypothesis.

6. **The single FULL winner does not justify "FULL-only" gating.** n=1,
   confidence 0.500 is exactly the FULL threshold, fired during the OR-H
   reclaim two minutes after a same-side loss. Looks like a "regime
   confirmed" signal, but one example.

Things that are **not** root causes on this sample:

- Direction mapping. Every closed row's `direction`/`setup` pair matches
  the spec mapping (LONG at OR-H or +N → OR_BREAK_FOLLOW; SHORT at OR-L
  or -N → OR_BREAK_FOLLOW; SHORT at OR-H → LEVEL_FADE_SHORT; SHORT at +1
  → LEVEL_FADE_SHORT).
- Setup mislabelling. Each row's setup is consistent with `(direction,
  label-side)` per the table in §3 above.
- Confidence threshold. 15 of 16 signals sit in `[0.35, 0.50)`; the only
  one at 0.50 was the only +60s winner. Raising the threshold to 0.50
  would have left 1 trade total on the day. Not actionable from n=1.
- Thesis gating. Every closed signal had `actionable=true` at log time,
  which means `thesis_gated_size_tier ∈ {HALF, FULL}` was satisfied.
  There is no closed-row evidence of a thesis-gating bug.

---

## 5. Morning Safety Recommendation

The operator's read is correct: **treat the signal as an instrument under
test.** Plot for visibility, do not enter on it.

Concrete recommendation for today (2026-05-27, RTH open):

- **Chart**: allow OpenRange overlay to plot actionable rows as-is
  (visibility only, no behaviour change). The new compact reason glyph
  (`LONG conf R~score / setup / drivers / STOP`) is the operator-readable
  artifact.
- **Trading**: do not let the chart signal trigger any entry. The first
  16-sample slice is overnight, n=16, mean -0.27R at 60s, dominated by
  one tight-OR cluster. There is no edge proof.
- **Re-evaluate after RTH close**: rerun the daily report at end-of-day,
  see what RTH adds. The plotted signal should produce different
  statistics during normal liquidity hours; that is the first measurement
  worth taking.

Explicitly **not** recommended right now:

- Raising confidence threshold. n=1 at conf=0.50 cannot support that.
- Restricting to FULL only. Same n=1 problem.
- Restricting to +1 only. The +1 sample is flat, not winning; it just
  has wide stops that hide outcomes.
- Suppressing OR-L SHORT BREAK or OR_BREAK_FOLLOW from the chart. The
  failure was tight OR width, not the setup label; the next session with
  a normal OR could behave differently.

---

## 6. Allowed Patch (proposed, not applied)

**Only one** patch is justified by the audit. It is report-only, three
lines, fully reversible, and does **not** touch live signal, log, chart,
or any decision path:

- **In `pax-ai/pax_ai/level_edge_report.py::_GROUP_BY`**, change the
  `top_drivers` extractor to sort the bundle members alphabetically
  before joining:

  ```python
  ("top_drivers", lambda r: _top_drivers_bundle(r.get("top_drivers"))),
  ```

  becomes:

  ```python
  ("top_drivers", lambda r:
       _top_drivers_bundle(sorted(r.get("top_drivers") or []))),
  ```

  Effect: today's `orderbook+pull_stack+tape (n=9)` consolidates the
  three raw orderings (`p+o+t`, `p+t+o`, `t+p+o`) into one analytical
  group. Same for the smaller buckets. No change to live `top_drivers`,
  no change to JSONL contents, no change to chart, no change to gates.

This is the only patch. It belongs to Slice 4 hygiene, not Slice 5
behaviour. **Awaiting operator approval before applying.**

---

## 7. Do Not Touch

- `pax-ai/pax_ai/level_edge.py` — direction, setup, top_drivers, gates.
- `pax-ai/pax_ai/edge_calculus.py` — stop logic, size tiers, R math.
- `pax-ai/pax_ai/level_edge_log.py` — log schema, dedup, backfill.
- `pax-ai/pax_ai_config.json` — thresholds, weights, `prox_ticks`.
- `pax-ai/pax_ai/dashboard.py` — `PROX_TICKS`, composite, drivers.
- `indicators/OpenRange/...` — Java overlay, jar, deploy.
- Any prompt, skill, or Claude call.
- `feature_bus`, research, preflight, promotion gate, calibration.

If anything in this list looks tempting after a session of more data,
that is a Slice 6+ conversation and needs its own audit, not an in-flight
patch.

---

## 8. Go / No-Go For Morning Chart Signals

- **Plot on chart**: **GO**. Visibility-only. The new glyph (setup +
  drivers + R~ + STOP) is exactly what the operator needs to see whether
  the system *would* have called the trade.
- **Influence entries (manual or automated)**: **NO-GO**. First measured
  sample is -0.27R/60s, hit rate 0.31, dominated by an overnight
  tight-OR cluster. No edge proof.
- **Daily report after RTH close**: **GO**. Run `python -m
  pax_ai.level_edge_report --date 2026-05-27` after the close; the
  morning session will roughly double the sample and reveal whether RTH
  produces a different signal profile.

Stop here. No code changes. Await approval to apply the §6 report patch.
