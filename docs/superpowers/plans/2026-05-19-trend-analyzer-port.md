# TrendAnalyzer → Dashboard Conviction + Chart Triangles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the existing `trendanalyzer-mvp` stable-trend engine into the MCP bridge as a normalized feature inside the dashboard's anchored multi-source conviction model, then render weak/strong bull/bear triangles on the Bookmap chart from the composite conviction score (not from TrendAnalyzer alone).

**Architecture:** A minimal subset of TrendAnalyzer's core Java classes is copied into a new `com.bookmapmcp.trend` package and driven from the bridge's per-alias `InstrumentState` trade stream via a new bridge-specific `TimeBucketTrendAccumulator` (the upstream `BarAggregator` is count-based and unsuitable for irregular `onTrade` events; we replace it). A new `/trend_analyzer` HTTP endpoint exposes the per-alias stable-trend snapshot with **distinct `eventMs` (event/playback time, used for chart anchoring) and `updatedAtMs` (wall-clock, used for staleness)** — never use one in place of the other. The dashboard reads it as `snap["trend_analyzer"]`, normalizes it through a new `_source_trend_analyzer` helper, and feeds it into `compute_session_conviction` exactly like every other source, with a new **per-source share cap** (`conviction_source_share_caps`) enforcing that `trend_analyzer` cannot account for more than 10% of `Σ|effective|`. Cluster caps alone do NOT achieve this guarantee. A small `compute_trend_signal` projects the resulting composite conviction onto one of `{STRONG_BULL, WEAK_BULL, STRONG_BEAR, WEAK_BEAR, NONE}` and exposes it as `snap["trend_signal"]`. A new `PaxTrendSignalFetcher` + `PaxTrendTrianglePainter` in the existing **OpenRange** addon picks up `snap["trend_signal"]` from `/api/snapshot` and renders triangles on the chart, with `eventMs` converted to chart data nanos via the existing `LocalDateTime → toNanos` path.

**Tech Stack:** Java 17 (bridge + OpenRange), Python 3.11 (dashboard), `com.sun.net.httpserver` (bridge HTTP), Bookmap Layer1 API (ScreenSpaceCanvas), JUnit 5 (Java tests), pytest (Python tests).

---

## 1. Architecture plan

### 1.1 Current data flow (research findings)

```
Bookmap market data
   │ onTrade / onDepth callbacks (Layer1Attachable)
   ▼
BookmapMcpBridgeModule  ──→  BridgeRegistry.INSTANCE.attach(InstrumentState)
                                    │
                                    │ per-alias accumulators:
                                    │  FlowRegime, MomentumSnapshot,
                                    │  VolumeProfile, AnchoredVwap,
                                    │  PullStack, Microstructure, …
                                    ▼
BridgeServer (com.sun HTTP @ 127.0.0.1:<port>)
   │ /momentum /vwap /orderbook /tape_buckets /pull_stack …  (~20 endpoints)
   │
   ▼
dashboard.fetch_snapshot()              [Python, polls ~1 Hz]
   │ BridgeClient.get_json("/momentum", {"alias": …}) etc.
   │ Builds `snap` dict with raw blocks + derived blocks
   │ snap["flow"], snap["vwap_obj"], snap["pull_stack"], …
   ▼
compute_session_conviction(snap)        [dashboard.py:2207]
   │ For each name in _CONVICTION_SOURCES:
   │   _source_<name>(snap)  →  {score, reliability, raw, reason}
   │ Per-source rolling SMA (short/medium/session), reliability gating,
   │ cluster cap normalization, composite ∈ [-1,+1].
   ▼
snap["conviction"] = {score, trajectory, trend, sourceScores, …}
   │
   ▼
overview_ui.py @ :18890   AND   /api/snapshot endpoint
   │
   ▼
OpenRange.PaxHeatwaveFetcher                    [Java, polls /api/snapshot]
   │ Sets heatwaveDirty AtomicBoolean on success
   │ NEVER mutates canvas
   ▼
OpenRange.InstrumentState.shouldRepaint(eventTime)
   │ consumes heatwaveDirty via compareAndSet, bypasses 1s throttle
   ▼
OpenRange.PaxPainter.update()           [runs on Bookmap onTrade/onDepth/onMoveEnd]
   │ clear() existing shapes, redraw lines/labels/heatwave-box
```

Key surfaces touched by this plan:

| File | Role |
|---|---|
| `mcp-server/bookmap_mcp/dashboard.py` | Conviction engine, snap composition |
| `mcp-server/bookmap_mcp/pax_weights.json` | Source weights, clusters, caps, thresholds |
| `mcp-server/bookmap_mcp/bridge_client.py` | Thin `get_json(path, params)` wrapper (no changes needed) |
| `src/main/java/com/bookmapmcp/BridgeServer.java` | HTTP endpoint registration |
| `src/main/java/com/bookmapmcp/handlers/*` | Per-endpoint JSON serializer pattern |
| `src/main/java/com/bookmapmcp/state/InstrumentState.java` | Per-alias accumulators + tradeStream fanout |
| `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java` | ScreenSpacePainter factory, fetcher orchestration, dirty-bit threading |
| `indicators/OpenRange/.../PaxHeatwaveFetcher.java` + `PaxHeatwavePainter.java` | Reference fetcher/painter pattern to mirror |

### 1.2 Where TrendAnalyzer should be ported

**Decision: copy core engine classes into the MCP bridge** (`com.bookmapmcp.trend` package), driven from `InstrumentState`'s existing trade stream. Expose via a new `/trend_analyzer` bridge endpoint.

The original `indicators/trendanalyzer-mvp` Bookmap addon (`TrendAnalyzerProV3Module`) is left untouched. It can continue to run standalone for users who want the bottom-panel candle/confidence lines.

### 1.3 Why bridge-resident, not separate-addon

| Criterion | Bridge-resident (chosen) | Separate addon polled by dashboard |
|---|---|---|
| Latency | Single in-process call from InstrumentState into engine, exposed via the same `/api/...` chain the dashboard already polls. No extra HTTP hop. | Bookmap → TrendAnalyzer addon → file/HTTP → dashboard. Two extra hops per poll. |
| Failure modes | If the bridge addon is loaded, TrendAnalyzer is available. Same lifecycle. | TrendAnalyzer addon must be loaded *and* its endpoint must be reachable. New silent-failure mode: addon detaches, dashboard never knows. |
| Test surface | JUnit tests inside the bridge build (where the source lives). Same gradle module. | Cross-process integration test; harder. |
| Audit | Single trade stream feeds every conviction source. Same timestamps, same instrument lifecycle. | Two-process race conditions are now part of the audit story. |
| Code reuse | Mechanical class copy from `trendanalyzer-mvp/src/main/java/com/trendanalyzer/core/*`. Existing tests port too. | Same. |
| Cost | One new bridge package + one HTTP handler + one accumulator field on `InstrumentState`. | One new HTTP server in the addon + a deploy/lifecycle story for it. |

The decisive factor is **fragility**: the brief explicitly says "lowest-fragility, least-fragile option." A second always-on HTTP source that can silently drop is a worse failure mode than an integrated package with one lifecycle.

The chart-rendering side stays in **OpenRange**, not a new addon, per the memory `feedback_heatwave_lives_in_openrange.md` and the existing precedent of the Heatwave Quant Box.

---

## 2. Data contract

### 2.1 Bridge endpoint: `GET /trend_analyzer?alias=...`

**Request:** Same auth pattern as every other endpoint (`BridgeAuth.guard`, query param `alias` required).

**Response (200, `application/json`):**

```json
{
  "alias": "NQM6.CME@RITHMIC",
  "asOfNanos": 1747680123456789012,
  "eventMs": 1747680123456,
  "updatedAtMs": 1747680123999,
  "lastClose": 21800.25,
  "warmedUp": true,
  "score": 0.42,
  "reliabilityHint": 0.8,
  "fast": {
    "direction": "UP",
    "directionSign": 1,
    "confidence": 72,
    "switched": false,
    "chop": false,
    "trendLine": 21785.00,
    "candleIntervalMillis": 15000,
    "candleCount": 1432
  },
  "slow": {
    "direction": "UP",
    "directionSign": 1,
    "confidence": 58,
    "switched": false,
    "chop": true,
    "trendLine": 21750.00,
    "candleIntervalMillis": 60000,
    "candleCount": 358
  }
}
```

**Error paths:**
- 400 `missing_alias` if no alias.
- 404 `unknown_alias` if `BridgeRegistry.INSTANCE.get(alias)` returns null.
- 200 with `"warmedUp": false`, `fast.candleCount < requiredBars`, `confidence=0` if engine hasn't warmed up yet.

**Timestamp semantics (CRITICAL — do not conflate):**
- `asOfNanos`: raw Bookmap event nanos if available, diagnostic only. Bridge may set 0 if not known.
- `eventMs`: epoch ms of the underlying market event (last trade in the most recently completed bucket). Used by chart anchoring (`eventMs → LocalDateTime → toNanos`). Survives playback and historical data correctly.
- `updatedAtMs`: epoch ms of when the bridge produced this snapshot (wall-clock `System.currentTimeMillis()`). Used for dashboard staleness gating only.

The dashboard MUST NOT compare its own `time.time()` to `eventMs` — those can diverge by hours under playback. Staleness uses `updatedAtMs` only.

**Field semantics:**
- `directionSign`: +1 = UP, -1 = DOWN, 0 = NEUTRAL. Signed by `TrendDirection.sign()`.
- `confidence`: integer in [0, 100], from `StableTrendSnapshot.confidence()` (already smoothed).
- `switched`: `true` exactly on the candle where the stable direction flipped this tick.
- `chop`: `true` if `regimeFilter.score() < antiChopThreshold` — engine in noise mode.
- `score`: bridge-side blended `0.4·(fast.sign·fast.conf/100) + 0.6·(slow.sign·slow.conf/100)`, clipped to `[-1,+1]`. Diagnostic mirror — the dashboard recomputes the same value in `_source_trend_analyzer` (single source of truth for conviction math is the dashboard).
- `reliabilityHint`: bridge-side reliability suggestion in `[0,1]` derived from chop/disagreement/warmup. Diagnostic; dashboard recomputes.
- `trendLine`, `lastClose`: diagnostic only, not used by dashboard math.

### 2.2 Dashboard snapshot field: `snap["trend_analyzer"]`

Identical to the bridge response body. Populated in `fetch_snapshot()` by:

```python
trend_obj = safe(lambda: c.get_json("/trend_analyzer", {"alias": alias}))
# … in snap dict literal …
"trend_analyzer": trend_obj if isinstance(trend_obj, dict) else None,
```

On bridge error, the `safe()` wrapper stamps `{"_error": "..."}` and the source helper falls back to reliability=0.

### 2.3 Final signal field for chart plotting: `snap["trend_signal"]`

Derived from the composite `snap["conviction"]`, **not** directly from `snap["trend_analyzer"]`. This enforces the architectural rule that TrendAnalyzer is one weighted feature, not a decision engine.

```json
{
  "kind": "STRONG_BULL",
  "alias": "NQM6.CME@RITHMIC",
  "asOfMs": 1747680123999,
  "eventMs": 1747680123456,
  "mid": 21800.25,
  "convictionScore": 0.42,
  "convictionTrend": "BULLISH_TREND",
  "convictionTrajectory": "RISING",
  "changedSinceLastTick": true,
  "bucketEnteredMs": 1747680113000
}
```

- `asOfMs`: dashboard wall-clock at projection time. Used by the painter for staleness gating.
- `eventMs`: best event time available — taken from `snap["trend_analyzer"].eventMs` when present, else from the snapshot wall clock as a fallback. Used by the painter for chart anchoring (see §5.3).
- `bucketEnteredMs`: dashboard wall-clock when this `kind` was first entered. Used for dedup.

`kind` ∈ `{STRONG_BULL, WEAK_BULL, STRONG_BEAR, WEAK_BEAR, NONE}`. Mapping in §4.4.

---

## 3. Dashboard registry integration

### 3.1 Helper: `_source_trend_analyzer(snap)`

New helper in `dashboard.py`, placed alongside the other `_source_*` functions (immediately after `_source_ib_context`, before `_CONVICTION_SOURCES`).

```python
def _source_trend_analyzer(snap: Dict[str, Any]) -> Dict[str, Any]:
    """TrendAnalyzer stable-trend contribution.

    Blends the fast and slow StableTrendEngine outputs. Score is the
    confidence-weighted, signed direction across both engines; reliability
    is reduced by chop, disagreement, warmup, and staleness.
    """
    ta = snap.get("trend_analyzer")
    if not isinstance(ta, dict) or "_error" in ta:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"_present": False}, "reason": "no trend_analyzer"}
    if not ta.get("warmedUp"):
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"warmedUp": False}, "reason": "warmup"}
    # Staleness uses updatedAtMs (bridge wall-clock), NOT eventMs.
    # eventMs can be hours behind under playback.
    updated_at_ms = ta.get("updatedAtMs")
    if updated_at_ms is None:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"updatedAtMs": None}, "reason": "no updatedAtMs"}
    age_sec = (int(time.time() * 1000) - int(updated_at_ms)) / 1000.0
    if age_sec > 30.0:
        return {"score": 0.0, "reliability": 0.0,
                "raw": {"ageSec": round(age_sec, 1)}, "reason": "stale"}

    fast = ta.get("fast") or {}
    slow = ta.get("slow") or {}
    f_sign, _  = _as_float(fast.get("directionSign"))
    s_sign, _  = _as_float(slow.get("directionSign"))
    f_conf, _  = _as_float(fast.get("confidence"))
    s_conf, _  = _as_float(slow.get("confidence"))
    f_chop = bool(fast.get("chop"))
    s_chop = bool(slow.get("chop"))

    score = _clip(0.4 * (f_sign * f_conf / 100.0)
                + 0.6 * (s_sign * s_conf / 100.0))

    if f_chop and s_chop:
        reliability = 0.25
    elif f_chop or s_chop:
        reliability = 0.5
    elif f_sign != 0 and s_sign != 0 and (f_sign * s_sign) < 0:
        reliability = 0.6           # engines disagree
    else:
        reliability = 1.0

    return {
        "score": score,
        "reliability": reliability,
        "raw": {"fastSign": f_sign, "fastConf": f_conf,
                "slowSign": s_sign, "slowConf": s_conf,
                "fastChop": f_chop, "slowChop": s_chop,
                "ageSec":   round(age_sec, 1)},
        "reason": (f"fast {fast.get('direction')} c={int(f_conf)} "
                   f"slow {slow.get('direction')} c={int(s_conf)}"
                   + (" CHOP" if (f_chop or s_chop) else "")),
    }
```

Notes:
- **Score is signed by direction × confidence/100** — already in `[-1,+1]` per the brief.
- **Slow engine carries 60% weight** because the slow timeframe is structurally less noisy; reduces whipsaw contribution from the fast engine.
- **Disagreement cuts reliability to 0.6** — the engines disagreeing is informative (regime transition) but the score itself is unreliable.
- **`switched=true` is exposed in raw but does NOT bump reliability** — the trend-change moment is captured by the conviction engine's own trajectory derivative; double-counting here would create a transient asymmetry.

### 3.2 Registry: `_CONVICTION_SOURCES`

Add one line:

```python
_CONVICTION_SOURCES: Dict[str, Any] = {
    # … existing 17 sources …
    "trend_analyzer":              _source_trend_analyzer,
}
```

`compute_session_conviction` iterates this dict — no other dashboard code changes needed. Per-source state is auto-initialized on first poll by the existing `for name in _CONVICTION_SOURCES: if name not in st["sources"]: …` defensive loop at `dashboard.py:2247`.

### 3.3 Weight + share cap in `pax_weights.json`

```json
{
  "conviction_source_weights": {
    "...":                    "...",
    "trend_analyzer":         0.06
  },
  "conviction_source_share_caps": {
    "_comment": "Per-source ceiling on |effective_w| / Σ|effective_w|. Applied after cluster caps. cap=0.10 means this source can never account for more than 10% of the normalized weighted sum.",
    "trend_analyzer":         0.10
  }
}
```

**Why NOT a cluster cap (audit fix):**

The composite is `Σ(w_i · s_i) / Σ|w_i|`. Cluster caps scale a *group* of effective weights down proportionally, but they do NOT control any one source's *share* of the normalizer. If every other source has reliability 0 and `trend_analyzer` has `reliability=1, w=0.06`:

```
composite = (0.06 · s_trend) / (0.06)  =  s_trend   ← cap did nothing
```

The composite reduces to the trend score directly. A cluster cap is the wrong tool for "this source cannot exceed N% of the composite."

**The correct safeguard: source-share cap applied after cluster caps.**

```python
# After existing cluster-cap pass:
for name, cap in share_caps.items():
    if name not in effective:
        continue
    other = sum(abs(w) for n, w in effective.items() if n != name)
    if other <= 0.0:
        # No other source has any weight. Trend cannot dominate
        # by definition because it cannot contribute to a zero composite.
        effective[name] = 0.0
        continue
    # Solve for max |w_name| such that |w_name| / (|w_name| + other) <= cap:
    #   |w_name| (1 - cap) <= cap · other
    #   |w_name| <= cap · other / (1 - cap)
    max_abs = cap * other / (1.0 - cap)
    w = effective[name]
    if abs(w) > max_abs:
        effective[name] = math.copysign(max_abs, w)   # sign preserved
```

**Three independent safeguards now stack:**

1. **Base weight** `0.06` → small slice of the source space.
2. **Reliability gating** → real-world contribution typically below 0.06 (chop / disagreement / staleness all cut reliability).
3. **Source-share cap** `trend_analyzer: 0.10` → mathematically bounded share of the normalized composite, no matter what the other sources do.

Bound: `|composite_contribution_from_trend| ≤ 0.10 · |score_trend| ≤ 0.10`.

When every other source is silent (`other == 0`), `trend_analyzer.effective` is zeroed — TrendAnalyzer cannot single-handedly produce a composite signal.

**No new thresholds**: the existing `trend / lean / chop` composite-score thresholds already define weak/strong; we do not introduce new ones.

---

## 4. Quant model

### 4.1 Feature normalization

Every input to the source helper is normalized to `[-1,+1]` before mixing:

| Raw input | Normalization | Range |
|---|---|---|
| `fast.directionSign` × `fast.confidence/100` | direct | `[-1,+1]` |
| `slow.directionSign` × `slow.confidence/100` | direct | `[-1,+1]` |
| Blended source score | `0.4·fast + 0.6·slow`, then `_clip` | `[-1,+1]` |

### 4.2 Engine math (unchanged from upstream)

The dashboard conviction engine math is **unmodified** by this plan. We are adding one new source to the existing pipeline. The math at `dashboard.py:2261-2314`:

```
For each source:
  inst_score, availability = source_helper(snap)
  ring.append((now_ms, inst_score)) iff availability > 0
  sma_short  = mean(ring over last 30s)
  sma_medium = mean(ring over last 120s)
  sma_session = sessionSum / sessionCount
  source_score = 0.50·sma_medium + 0.30·sma_short + 0.20·sma_session

  reliability = availability × sample_conf × freshness
  sample_conf = min(1, n_medium / required_samples)
  freshness   = exp(-age_sec / freshness_halflife_sec)

For composite:
  effective[name] = base_weight[name] × reliability[name]
  effective = apply_cluster_caps(effective, clusters, caps)
  composite = clip( Σ(effective·source_score) / Σ|effective| )
  trajectory = SMA-slope-of-composite(last 30s) - SMA-slope-of-composite(30-90s)
  trend = label(composite, trajectory, thresholds)
```

### 4.3 Reliability + freshness rules (specific to trend_analyzer)

| Condition | Reliability multiplier |
|---|---|
| `snap["trend_analyzer"]` is None / `_error` | 0.0 (skip entirely) |
| `warmedUp == false` | 0.0 |
| `asOfMs == null` | 0.0 |
| `age = now - asOfMs > 30 s` | 0.0 (stale) |
| `fast.chop AND slow.chop` | 0.25 |
| `fast.chop XOR slow.chop` | 0.5 |
| Engines disagree (`fast.sign × slow.sign < 0`) | 0.6 |
| Otherwise | 1.0 |

The conviction engine *additionally* applies `freshness = exp(-age_sec / 8)` and `sample_conf = min(1, n_medium/10)`, so the realized reliability decays smoothly even below the hard 30s cliff.

### 4.4 Weak / strong / change classification (for `snap["trend_signal"]`)

Driven by `snap["conviction"]` only — TrendAnalyzer's raw output is irrelevant here.

| `conviction.trend` | `trend_signal.kind` |
|---|---|
| `BULLISH_TREND` | `STRONG_BULL` |
| `BULL_LEAN` | `WEAK_BULL` |
| `BEARISH_TREND` | `STRONG_BEAR` |
| `BEAR_LEAN` | `WEAK_BEAR` |
| `CHOP`, `MIXED`, `BULL_FADING`, `BEAR_FADING` | `NONE` |
| (`conviction` missing or `_error`) | `NONE` |

Why fade-states are `NONE`: a fading trend means the composite is still elevated but trajectory is sharply against it — that is a *don't-add-to-position* state, not a fresh weak/strong entry signal.

### 4.5 Trend-change detection + disagreement / chop handling

Trend-change is detected at the **trend_signal bucket** level, not at the TrendAnalyzer engine level:

```python
_LAST_TREND_SIGNAL: Dict[str, Dict[str, Any]] = {}  # per-alias

def compute_trend_signal(snap):
    alias = snap.get("alias")
    if not alias:
        return None
    conv = snap.get("conviction") or {}
    kind = _kind_from_trend_label(conv.get("trend"))
    now_ms = int(time.time() * 1000)
    prev = _LAST_TREND_SIGNAL.get(alias) or {}
    changed = (kind != prev.get("kind"))
    bucket_entered_ms = now_ms if changed else prev.get("bucketEnteredMs", now_ms)
    book = snap.get("book") or {}
    mid, _ = _as_float(book.get("mid"))
    # eventMs: prefer the trend_analyzer's event time when available; fall back
    # to wall-clock so the field is always populated.
    ta = snap.get("trend_analyzer")
    if isinstance(ta, dict) and isinstance(ta.get("eventMs"), (int, float)) and ta.get("eventMs") > 0:
        event_ms = int(ta["eventMs"])
    else:
        event_ms = now_ms
    signal = {
        "kind": kind,
        "alias": alias,
        "asOfMs": now_ms,
        "eventMs": event_ms,
        "mid": mid,
        "convictionScore": conv.get("score"),
        "convictionTrend": conv.get("trend"),
        "convictionTrajectory": conv.get("trajectory"),
        "changedSinceLastTick": changed,
        "bucketEnteredMs": bucket_entered_ms,
    }
    _LAST_TREND_SIGNAL[alias] = signal
    return signal
```

Chop is already handled twice in the pipeline:
1. TrendAnalyzer's own `chop` flag → reliability cut in the source helper.
2. Composite `conviction.trend == "CHOP"` → `trend_signal.kind = NONE`.

Engine disagreement is handled in the source helper (`reliability = 0.6`), then naturally damped by the rolling SMA in the conviction engine.

### 4.6 Bridge-side aggregation: `TimeBucketTrendAccumulator` (audit fix)

The upstream `BarAggregator` in `trendanalyzer-mvp/core/` is **count-based**: it consumes already-completed Bookmap bars (`onBar` callback) and groups N of them into a higher-timeframe bucket. The bridge does NOT receive bars; it receives irregular trade events via `InstrumentState.onTrade(price, size, aggressor, eventMs)`. Copying `BarAggregator` would silently produce broken candles. We replace it with a bridge-specific accumulator.

**Class:** `com.bookmapmcp.trend.TimeBucketTrendAccumulator`

**Inputs per call:** `onTrade(eventMs, price, size, isBuyAggressor)`
- `eventMs`: Bookmap event time (epoch ms). If 0 or negative, falls back to `System.currentTimeMillis()`.
- `price`: trade price.
- `size`: trade size (long).
- `isBuyAggressor`: true = buy aggressor (delta += size), false = sell aggressor (delta -= size).

**Configuration:**
- `bucketMillis`: base bucket duration (15_000 for the fast engine; the slow engine is fed every 4th completed fast bucket → effective 60_000).
- Two `StableTrendEngine` instances (`fast`, `slow`) sharing the same config but driven on different cadences.

**State (per alias):**
```
private long  bucketStartMs        // start of the currently-open bucket
private double bucketOpen, bucketHigh, bucketLow, bucketClose
private long  bucketVolume
private double bucketDelta         // signed: + buy, - sell
private long  lastTradeMs          // for diagnostics
private int   slowBucketCounter    // closes the slow bucket every 4th fast close
private final StableTrendEngine fast
private final StableTrendEngine slow
private volatile StableTrendSnapshot latestFast, latestSlow
private final RollingAverage fastVol = new RollingAverage(20)
private final RollingAverage slowVol = new RollingAverage(20)
```

**Per-trade behavior (O(1)):**
```
ts = eventMs > 0 ? eventMs : System.currentTimeMillis()
if bucketStartMs == 0:                     // first trade ever
    open new bucket starting at floor(ts / bucketMillis) * bucketMillis
while ts >= bucketStartMs + bucketMillis:  // crosses zero or more bucket boundaries
    closeBucket()                          // emits a Candle, advances bucketStartMs
update bucketHigh/Low/Close
bucketVolume += size
bucketDelta  += signed(size, isBuyAggressor)
lastTradeMs = ts
```

**`closeBucket()`:**
- If `bucketVolume == 0`: emit a *carry-forward* candle (open=high=low=close=lastClose, vol=0, delta=0) so the engine sees time progressing during quiet periods but does NOT see synthetic price action. If no `lastClose` exists yet (very first bucket has zero trades), skip emission entirely.
- Build `Candle(bucketStartMs, open, high, low, close, volume, delta)`.
- Build `OrderflowSnapshot(volume, delta, fastVol.average(), 0.0, 0.0)` — no BBO in the bridge accumulator (kept as 0; the upstream `breaksDown/Up` paths using BBO are not used because the bridge uses the default `CLOSE_CROSS` switch condition).
- `latestFast = fast.onCandle(candle, ofs); fastVol.add(volume);`
- `slowBucketCounter++; if slowBucketCounter % 4 == 0:` build a slow Candle by merging the last 4 fast candles in a private sliding deque (`Deque<Candle> recentFast`, capacity 4) → `latestSlow = slow.onCandle(slowCandle, slowOfs); slowVol.add(slowVolume);`
- Advance `bucketStartMs += bucketMillis`. Reset bucket fields to start a fresh bucket.

**Critical safety: gap handling.**

A long no-trade gap (e.g., overnight, ~16 h on NQ) would naively close ~3,840 buckets in a tight `while` loop on the next trade — which would block the Bookmap callback and risk dropping events. Mitigation:

```
MAX_CARRY_FORWARD_BUCKETS = 240    // 1 hour at 15s buckets

while (ts >= bucketStartMs + bucketMillis) {
    if (++carriedThisBatch > MAX_CARRY_FORWARD_BUCKETS) {
        // Snap forward without emitting individual buckets.
        long skipBucketsAhead = (ts - bucketStartMs) / bucketMillis;
        bucketStartMs += skipBucketsAhead * bucketMillis;
        // Reset engine state via fast.reset() / slow.reset() if exposed, OR
        // simply leave engines untouched — they'll re-warm on the next live trade.
        // Either way we MUST NOT emit thousands of synthetic candles.
        break;
    }
    closeBucket();
}
```

The accumulator stays O(1) per trade in the steady-state and bounded-work on resumption from a long gap. **No blocking, no allocation per trade beyond the existing per-bucket scratch fields.**

**`snapshot()` (called by the HTTP handler, runs on the bridge HTTP thread, not on a Bookmap callback):**
- Read the volatile `latestFast` / `latestSlow`. Compute `score`, `reliabilityHint`, `warmedUp` per the same formulas the dashboard uses (as a diagnostic mirror only — the dashboard recomputes the canonical values).
- Build the JSON DTO with `eventMs = lastTradeMs`, `updatedAtMs = System.currentTimeMillis()`.

### 4.7 No black-box ML

Every step is a closed-form arithmetic operation over auditable scalar inputs. No model files, no training artifacts, no statefulness beyond the existing ring buffers. Replay validation (Task 11) is a deterministic pass over recorded snapshots.

---

## 5. Bookmap chart rendering plan

### 5.1 Pattern: mirror PaxHeatwaveFetcher / PaxHeatwavePainter

All chart mutations stay inside `indicators/OpenRange`. No new Bookmap addon is created.

```
┌─────────────────────────────────────────────────────────────┐
│ PaxTrendSignalFetcher  (daemon thread)                       │
│   polls http://127.0.0.1:<port>/api/snapshot   every 1 s     │
│   parses snap["trend_signal"]                                │
│   stores volatile PaxTrendSignalModel latest                 │
│   sets AtomicBoolean trendSignalDirty = true   on success    │
│   NEVER touches canvas                                       │
└──────────────────────────────────────────────┬───────────────┘
                                               │
                                               ▼
┌─────────────────────────────────────────────────────────────┐
│ PaxOpeningRangeModule.InstrumentState.shouldRepaint(t)        │
│   if trendSignalDirty.compareAndSet(true,false):              │
│      bypass 1s throttle, return true                          │
└──────────────────────────────────────────────┬───────────────┘
                                               │ runs on Bookmap callback (onTrade/onDepth/onMoveEnd)
                                               ▼
┌─────────────────────────────────────────────────────────────┐
│ PaxPainter.update()  (synchronized, canvas-safe)              │
│   reads fetcher.snapshot()                                    │
│   maybeEmitTriangle(currentBucket, prevBucket, now)           │
│   draws all live triangles via PaxTrendTrianglePainter        │
└─────────────────────────────────────────────────────────────┘
```

**Threading rule (load-bearing):** the fetcher worker MUST NOT call `canvas.addShape` / `canvas.removeShape` / `PaxPainter.update()`. Same rule as Heatwave. Enforced by giving the fetcher only a `Runnable` repaint callback that sets the dirty bit. Documented in CLAUDE.md and pinned by `feedback_heatwave_lives_in_openrange.md`.

### 5.2 Triangle visual spec

| `kind` | Color | Size (px h × w) | Anchor side | Pointing |
|---|---|---|---|---|
| `STRONG_BULL` | `PaxHeatwaveColors.BULL` (bright green) | 18 × 18 | Below mid | UP |
| `WEAK_BULL` | `PaxHeatwaveColors.BULL` dimmed 50% alpha | 10 × 10 | Below mid | UP |
| `STRONG_BEAR` | `PaxHeatwaveColors.BEAR` (bright red) | 18 × 18 | Above mid | DOWN |
| `WEAK_BEAR` | `PaxHeatwaveColors.BEAR` dimmed 50% alpha | 10 × 10 | Above mid | DOWN |
| `NONE` | (no render) | — | — | — |

Vertical offset from mid: `± 4 × tick` for STRONG, `± 2 × tick` for WEAK, where tick = `state.pips`. Renders below/above price without overlapping standard candle bodies.

### 5.3 Anchoring (audit fix — coordinate conversion)

Triangle is a `CanvasIcon` with `DATA_ZERO`-based composite coordinates so it tracks chart pan/zoom (same as OpenRange's price lines, unlike the screen-space Heatwave box which uses `PIXEL_ZERO`).

**DATA_ZERO x is NOT epoch ms — it is chart data nanos in the same convention used by OpenRange's existing line drawing.** Passing `eventMs` raw produces shapes anchored at January 1970. Conversion path:

```java
// in PaxOpeningRangeModule (or a new ChartTimeCoords helper)
private static final ZoneId EXCHANGE_ZONE = ZoneId.of("America/Chicago");
private static long eventMsToChartNanos(long eventMs) {
    if (eventMs <= 0L) return 0L;
    LocalDateTime ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(eventMs), EXCHANGE_ZONE);
    return toNanos(ldt);   // existing helper in PaxOpeningRangeModule
}
```

The `toNanos(LocalDateTime)` helper already exists in the module and is used by `drawDay` / `addLabel` for OR line anchoring (see `PaxOpeningRangeModule.java:928`). Reuse it.

Triangle coordinates:
```java
long  xNanos = eventMsToChartNanos(signal.eventMs)
double price = signal.mid    // bullish: mid - (4 * pips); bearish: mid + (4 * pips)
int   h      = signal.kind.height()
int   w      = signal.kind.width()

new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, -w/2, xNanos)
new CompositeVerticalCoordinate  (CompositeCoordinateBase.DATA_ZERO, -h,   price)
new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, +w/2, xNanos)
new CompositeVerticalCoordinate  (CompositeCoordinateBase.DATA_ZERO,  0,   price)
```

Note: x pixel offsets center the triangle horizontally on the event time; y pixel offsets place the triangle *above* `price` for bears (rises into screen-y `-h..0`) and *below* `price` for bulls (mirror the box). Two helper methods on the painter encapsulate this — tested in `PaxTrendTrianglePainterTest`.

Where:
- `signal.eventMs` = best event ms from `trend_signal.eventMs` (which inherits from `trend_analyzer.eventMs` when available, else falls back to `snap["asOfMs"]`-style wall clock).
- `signal.mid` = last known book mid for the alias.

### 5.4 De-duplication rules

Maintained in `InstrumentState`, written by `PaxPainter.update()` only (canvas-safe thread):

```
Deque<TrendTriangleEvent> liveTriangles    // capacity 8, oldest first
String lastEmittedKind                     // last `kind` we drew, "" if none
long   lastEmittedBucketEnteredMs          // monotonic anchor of the last emit
```

Emit rule on each painter tick:
```
signal = fetcher.snapshot()
if signal == null || signal.kind == "NONE":
    nothing to emit, but keep redrawing existing liveTriangles
elif signal.bucketEnteredMs != lastEmittedBucketEnteredMs
     OR signal.kind != lastEmittedKind:
    push new TrendTriangleEvent onto liveTriangles
    if liveTriangles.size > 8: pollFirst()
    lastEmittedKind = signal.kind
    lastEmittedBucketEnteredMs = signal.bucketEnteredMs
```

Then redraw every entry of `liveTriangles` (since `PaxPainter.update()` does a full `clear()` each tick). Triangles age out naturally by the deque cap (max 8 visible at once).

**Why this prevents spam:**
- Same bucket across multiple polls → same `bucketEnteredMs` → no new emit.
- A flip from `WEAK_BULL` to `STRONG_BULL` is a new bucket → `bucketEnteredMs` updates → emit one new triangle.
- A flip back from `STRONG_BULL` to `WEAK_BULL` → emit a new triangle (intentional: the trader sees the trend cooling).
- A flicker `STRONG_BULL` → `NONE` → `STRONG_BULL` within one poll is suppressed at the dashboard level (the conviction-engine SMA already damps single-poll flips).

### 5.5 Stale + missing data

`PaxTrendSignalFetcher.snapshot()` returns the last successful model. Staleness is computed in `PaxPainter.update()` from `model.fetchedAtMs` (HTTP response receive time, same convention as Heatwave):

```
age = now - model.fetchedAtMs
if model is null:                  do not render any triangle
elif age > 30_000:                 keep existing liveTriangles but do not emit new ones
elif signal.kind == NONE:          keep existing liveTriangles but do not emit new ones
else:                              emit-if-bucket-changed (§5.4)
```

---

## 6. Test plan

### 6.1 Java unit tests (gradle, JUnit 5)

| Test class | What it pins |
|---|---|
| `com.bookmapmcp.trend.TrendAccumulatorTest` | Feed synthetic trades → expect candles aggregated → expect StableTrendSnapshot direction flips at known prices |
| `com.bookmapmcp.trend.TrendAnalyzerEngineTest` | Re-port of `StableTrendEngineTest` from trendanalyzer-mvp (already exists upstream) |
| `com.bookmapmcp.handlers.TrendAnalyzerHandlerTest` | GET /trend_analyzer: 200 happy path, 400 missing_alias, 404 unknown_alias |
| `com.bookmapmcp.handlers.TrendAnalyzerHandlerWarmupTest` | Engine hasn't received `numberOfCandles` candles → response has `warmedUp:false`, `fast.candleCount` < threshold |

Already-passing tests in `trendanalyzer-mvp` that are copied across with the source:
- `BarAggregatorTest`, `OrderflowAccumulatorTest`, `RollingAverageTest`, `TrendEngineTest`, `StableTrendEngineTest`, `OrderflowConfirmationsTest`, `TrendConfigTest`, `PriceVolumeSwitchTest`.

### 6.2 Python unit tests (pytest)

| Test file / test | What it pins |
|---|---|
| `tests/test_trend_analyzer.py::test_normalization_up_strong` | `direction=UP, confidence=100` → score ≈ +1.0 |
| `…::test_normalization_down_strong` | `direction=DOWN, confidence=100` → score ≈ -1.0 |
| `…::test_normalization_blended_weights` | fast=+0.5, slow=+1.0 → score = 0.4·0.5 + 0.6·1.0 = 0.80 |
| `…::test_missing_data_returns_reliability_zero` | `snap["trend_analyzer"] == None` → `reliability == 0` |
| `…::test_error_payload_returns_reliability_zero` | `snap["trend_analyzer"] == {"_error":"…"}` → `reliability == 0` |
| `…::test_stale_data_returns_reliability_zero` | `asOfMs = now - 31_000` → `reliability == 0`, `reason contains "stale"` |
| `…::test_warmup_returns_reliability_zero` | `warmedUp == False` → `reliability == 0` |
| `…::test_chop_both_cuts_reliability` | `fast.chop=True, slow.chop=True` → `reliability == 0.25` |
| `…::test_chop_one_cuts_reliability` | `fast.chop=True, slow.chop=False` → `reliability == 0.5` |
| `…::test_engines_disagree_cuts_reliability` | fast UP +60, slow DOWN -80 → `reliability == 0.6` |
| `tests/test_pax_weights_load.py::test_trend_analyzer_present` | `_load_pax_weights()` returns a dict with `trend_analyzer` in `conviction_source_weights` and `trend` cluster with cap 0.10 |
| `tests/test_session_conviction.py::test_includes_trend_analyzer_source` | `compute_session_conviction(...)["sourceScores"]` has key `trend_analyzer`; `effectiveWeights["trend_analyzer"]` ≤ 0.10 |
| `tests/test_session_conviction.py::test_trend_analyzer_cannot_dominate` | Fixture: every other source returns reliability 0, trend_analyzer returns reliability 1, score 1.0 → composite ≤ 0.10 (cluster cap holds) |
| `tests/test_trend_signal.py::test_strong_bull_classification` | `conviction.trend == "BULLISH_TREND"` → `kind == "STRONG_BULL"` |
| `…::test_weak_bull_classification` | `BULL_LEAN` → `WEAK_BULL` |
| `…::test_chop_to_none` | `CHOP` → `NONE` |
| `…::test_fading_to_none` | `BULL_FADING` → `NONE` |
| `…::test_bucket_change_sets_changed_flag` | Two consecutive calls in different buckets → second returns `changedSinceLastTick=True` |
| `…::test_same_bucket_clears_changed_flag` | Two consecutive calls in same bucket → second returns `changedSinceLastTick=False` |
| `…::test_missing_conviction_returns_none` | `snap["conviction"] == None` → `kind == "NONE"` |

### 6.3 Java integration tests (OpenRange)

| Test class | What it pins |
|---|---|
| `com.openrange.PaxTrendSignalSnapshotParserTest` | Parses sample JSON, returns expected model; handles missing `trend_signal`, null `mid`, unknown `kind` |
| `com.openrange.PaxTrendSignalFetcherTest` | `tickOnce()` with a stub HTTP server → model updated, dirty bit set, callback fired; consecutive failures escalate backoff |
| `com.openrange.PaxTrendTrianglePainterTest` | `render(STRONG_BULL, mid)` produces a non-empty `PreparedImage` with expected dimensions and a green triangle pixel at the apex |
| `com.openrange.PaxPainterTrendTriangleDedupTest` | Drive two `update()` calls with the same `bucketEnteredMs` → only one CanvasIcon added; change bucket → second icon added; cap at 8 live triangles |
| `com.openrange.PaxPainterStaleSignalTest` | Fetcher returns model with `fetchedAtMs = now - 31_000` → painter keeps existing triangles but emits nothing new |

### 6.4 Replay / backtest validation

| Test | What it pins |
|---|---|
| `tests/test_trend_analyzer_replay.py::test_replay_produces_signal_ledger` | Replay a recorded `D:/BookmapLogs/pax-recordings/*.jsonl` through `compute_session_conviction` + `compute_trend_signal`; verify the ledger has the expected schema, no exceptions, and a non-zero count of bucket transitions on a known-trending session |
| Same file `::test_with_and_without_trend_analyzer` | Run the same replay twice — once with `pax_weights.json::conviction_source_weights.trend_analyzer = 0.0` and once at `0.06`; expect Δcomposite distribution mean ≈ 0 (TrendAnalyzer cannot bias the composite up or down systemically) and |Δcomposite| ≤ 0.10 at the p99 (cluster cap holds in practice, not just in a unit test) |

### 6.5 Existing tests that MUST keep passing

`mcp-server/tests/test_session_conviction.py` already pins the conviction engine's output schema (legacy keys `score / trajectory / trend / components / instantaneous / weights`). The new source MUST NOT break these. Specifically:

- `compute_session_conviction(...)["components"]` still has exactly the 7 legacy keys `regime/bias/vwap/vp/slope/level/ib`.
- `sourceScores` and `sourceReliability` grow by one key (`trend_analyzer`), other keys unchanged.

---

## 7. Implementation plan (commits)

Each commit is a self-contained, reviewable, reversible unit. TDD throughout: write the failing test first, then the minimum code to pass it, then commit.

### Task 1: Port MINIMAL TrendAnalyzer core into the bridge (audit fix — narrowed scope)

**Files to create (ONLY these — copying more bloats the bridge with UI/sim code unused at runtime):**

- Create: `src/main/java/com/bookmapmcp/trend/Candle.java`
- Create: `src/main/java/com/bookmapmcp/trend/OrderflowSnapshot.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendDirection.java`
- Create: `src/main/java/com/bookmapmcp/trend/SwitchCondition.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendConfig.java`
- Create: `src/main/java/com/bookmapmcp/trend/RollingAverage.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendSnapshot.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendEngine.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendRegimeFilter.java`
- Create: `src/main/java/com/bookmapmcp/trend/StableTrendSnapshot.java`
- Create: `src/main/java/com/bookmapmcp/trend/StableTrendEngine.java`

**Explicitly NOT copied (no bridge runtime use; will be added only if a compile fails):**

- `BarAggregator` — REPLACED by the new `TimeBucketTrendAccumulator` (audit fix; see §4.6).
- `OrderflowAccumulator`, `OrderflowConfirmations` — used only by the upstream addon's `processEngines` and `ConfidenceModel`. The bridge accumulator inlines its own delta tracking; `ConfidenceModel` is used inside `StableTrendEngine` only via `OrderflowConfirmations.clampToHundred` (a 3-line static helper). If the compile fails on that single helper, copy ONLY `OrderflowConfirmations.clampToHundred` as a private static in `StableTrendEngine` or copy the full `OrderflowConfirmations.java`. Track this and document in commit message.
- `ConfidenceModel`, `HftCache`, `HistorySimulator`, `HistoryResult`, `DualTrendModel`, `CombinedTrendState`, `StableTrendSnapshot`-deps beyond what's listed, all `bookmap/` and `settings/` sub-packages.

**Tests (ported with package rename — these all already pass upstream):**
- Test: `src/test/java/com/bookmapmcp/trend/StableTrendEngineTest.java` (port)
- Test: `src/test/java/com/bookmapmcp/trend/TrendEngineTest.java` (port)
- Test: `src/test/java/com/bookmapmcp/trend/RollingAverageTest.java` (port — if standalone enough)

`BarAggregatorTest` is NOT ported (BarAggregator is not copied).

- [ ] Step 1: Copy the 11 source files with mechanical package rename `com.trendanalyzer.core` → `com.bookmapmcp.trend`. NO logic changes.
- [ ] Step 2: Run `gradlew compileJava` to confirm the package compiles standalone.
- [ ] Step 3: Port the matching test files with same package rename.
- [ ] Step 4: Run `gradlew test --tests "com.bookmapmcp.trend.*"` → expect green.
- [ ] Step 5: Commit `bridge: port minimal trend core into com.bookmapmcp.trend (no logic changes)`.

### Task 1b: TimeBucketTrendAccumulator (audit fix — replaces BarAggregator)

**Files:**
- Create: `src/main/java/com/bookmapmcp/trend/TimeBucketTrendAccumulator.java`
- Create: `src/main/java/com/bookmapmcp/trend/TrendAnalyzerSnapshot.java` (DTO for JSON serialization)
- Test:   `src/test/java/com/bookmapmcp/trend/TimeBucketTrendAccumulatorTest.java`

- [ ] Step 1: Write `TimeBucketTrendAccumulatorTest::irregularTradesBuildCorrectCandles` — feed 50 trades at irregular ms offsets within a 15s window; expect exactly one closed candle with `open=first`, `high=max`, `low=min`, `close=last`, `volume=sum`, `delta=signed sum`. Run → FAIL.
- [ ] Step 2: Implement `TimeBucketTrendAccumulator` per §4.6.
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Write `…::sparseTradesDoNotFakeTrend` — one trade per minute for 5 minutes, all at the same price; expect engine never to claim a direction stronger than NEUTRAL with confidence < 30. Run → PASS.
- [ ] Step 5: Write `…::longNoTradeGapDoesNotExplodeBuckets` — feed one trade at t=0, then one at t=24h. Expect: total work bounded by `MAX_CARRY_FORWARD_BUCKETS`; no `OutOfMemoryError`; no `StackOverflowError`; resumed engine state is consistent (no thousands of carry-forward candles). Use a `Stopwatch` to assert the single `onTrade` returns in < 50ms.
- [ ] Step 6: Write `…::uptrendDrivesPositiveScore` — feed a clean monotonic uptrend over 200 buckets; after warmup, `latestFast.direction == UP` and `confidence > 50`.
- [ ] Step 7: Write `…::downtrendDrivesNegativeScore` — mirror of step 6.
- [ ] Step 8: Run all → PASS.
- [ ] Step 9: Commit `bridge: TimeBucketTrendAccumulator (time-based, gap-safe, O(1) per trade)`.

### Task 2: InstrumentState wiring

**Files:**
- Modify: `src/main/java/com/bookmapmcp/state/InstrumentState.java` (add `TimeBucketTrendAccumulator trend` field; call into it from existing onTrade path)
- Test: `src/test/java/com/bookmapmcp/state/InstrumentStateTrendTest.java`

- [ ] Step 1: Write `InstrumentStateTrendTest::onTradeFeedsAccumulator` — call `state.onTrade(...)` directly, expect `state.trendAnalyzerSnapshot()` to reflect the trade in `eventMs` and `lastClose`. Run → FAIL.
- [ ] Step 2: Add `private final TimeBucketTrendAccumulator trend = new TimeBucketTrendAccumulator(...);` and add a forward call from the trade-ingest method (whichever method `BookmapMcpBridgeModule` calls — likely `onTrade` or `acceptExecution`).
- [ ] Step 3: Expose `trendAnalyzerSnapshot()` returning a `TrendAnalyzerSnapshot` DTO.
- [ ] Step 4: Run → PASS.
- [ ] Step 5: Write `…::snapshotIncludesEventMsAndUpdatedAtMs` — assert both fields populated and distinct (eventMs from last trade, updatedAtMs from System.currentTimeMillis).
- [ ] Step 6: Run → PASS.
- [ ] Step 7: Commit `bridge: wire TimeBucketTrendAccumulator into InstrumentState.onTrade`.

### Task 3: `/trend_analyzer` HTTP endpoint

**Files:**
- Create: `src/main/java/com/bookmapmcp/handlers/TrendAnalyzerHandler.java`
- Modify: `src/main/java/com/bookmapmcp/BridgeServer.java` (register handler)
- Test: `src/test/java/com/bookmapmcp/handlers/TrendAnalyzerHandlerTest.java`

- [ ] Step 1: Write `TrendAnalyzerHandlerTest::missingAliasReturns400`. Run → FAIL.
- [ ] Step 2: Implement handler skeleton mirroring `MomentumHandler`: `Http.requireGet`, alias parsing, `BridgeRegistry.INSTANCE.get(alias)`, 404 on null.
- [ ] Step 3: Write `…::unknownAliasReturns404`. Run → FAIL.
- [ ] Step 4: Wire alias lookup + 404 path.
- [ ] Step 5: Write `…::happyPathReturnsExpectedJsonShape` — attach a `InstrumentState`, pump synthetic trades, GET endpoint, parse JSON, assert all 18 fields per §2.1 present and typed correctly.
- [ ] Step 6: Implement JSON serialization via `JsonWriter` mirroring MomentumHandler's pattern.
- [ ] Step 7: Modify `BridgeServer::ensureStarted` to add `http.createContext("/trend_analyzer", auth.guard(new TrendAnalyzerHandler()));`.
- [ ] Step 8: Bump `version` in `build.gradle` and `archiveFileName` to `bookmap-mcp-bridge-v<N+1>.jar` per CLAUDE.md's addon-jar-versioning rule.
- [ ] Step 9: `gradlew test build` → PASS, new versioned jar in `build/libs/`.
- [ ] Step 10: Commit `bridge: /trend_analyzer endpoint + bump v<N+1>`.

### Task 4: Dashboard fetches `/trend_analyzer`

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (one block in `fetch_snapshot`)
- Test: `mcp-server/tests/test_trend_analyzer_fetch.py`

- [ ] Step 1: Write `test_trend_analyzer_fetch::test_snap_has_trend_analyzer_key` — mock `BridgeClient.get_json` to return a fixture, call `fetch_snapshot()`, assert `snap["trend_analyzer"]` matches fixture. Run → FAIL (no such key yet).
- [ ] Step 2: Add to `fetch_snapshot` (after `me_obj`):
  ```python
  trend_obj = safe(lambda: c.get_json("/trend_analyzer", {"alias": alias}))
  ```
  and add to snap dict literal:
  ```python
  "trend_analyzer": trend_obj if isinstance(trend_obj, dict) else None,
  ```
- [ ] Step 3: Run test → PASS.
- [ ] Step 4: Write `…::test_bridge_error_yields_none_or_error_payload` — mock `get_json` to raise `BridgeError`, assert `snap["trend_analyzer"]` is either `None` or has `_error` (matching the `safe()` wrapper behavior).
- [ ] Step 5: Run test → PASS (works via existing `safe()` wrapper).
- [ ] Step 6: Commit `dashboard: fetch /trend_analyzer into snap`.

### Task 5: `_source_trend_analyzer` + registry + pax_weights

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (add helper, add to `_CONVICTION_SOURCES`, add to `CONVICTION_SOURCE_WEIGHTS`/`CONVICTION_CLUSTERS`/`CONVICTION_CLUSTER_CAPS` defaults)
- Modify: `mcp-server/bookmap_mcp/pax_weights.json` (add weight + cluster + cap)
- Test: `mcp-server/tests/test_trend_analyzer.py` (new file, hosts §6.2 tests)
- Test: `mcp-server/tests/test_pax_weights_load.py` (new file, single-purpose)
- Modify: `mcp-server/tests/test_session_conviction.py` (add the two new assertions from §6.2)

- [ ] Step 1: Write `test_trend_analyzer::test_normalization_up_strong`. Run → FAIL.
- [ ] Step 2: Implement `_source_trend_analyzer` per §3.1.
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Write the remaining 9 source-helper tests from §6.2. Run → 1 FAIL at a time, fix, PASS each, then move on.
- [ ] Step 5: Add `"trend_analyzer": _source_trend_analyzer` to `_CONVICTION_SOURCES`.
- [ ] Step 6: Add `"trend_analyzer": 0.06` to `CONVICTION_SOURCE_WEIGHTS`, `"trend": ["trend_analyzer"]` to `CONVICTION_CLUSTERS`, `"trend": 0.10` to `CONVICTION_CLUSTER_CAPS` (the module-constant defaults, before `pax_weights.json` overrides).
- [ ] Step 7: Edit `pax_weights.json` (additive — same three entries).
- [ ] Step 8: Write `test_pax_weights_load::test_trend_analyzer_present` and `test_session_conviction::test_includes_trend_analyzer_source` and `…::test_trend_analyzer_cannot_dominate`. Run → PASS.
- [ ] Step 9: Run `python -m pytest` from `mcp-server/`. Full suite green.
- [ ] Step 10: Commit `dashboard: trend_analyzer source + weight=0.06 + new trend cluster cap=0.10`.

### Task 6: `snap["trend_signal"]` producer

**Files:**
- Modify: `mcp-server/bookmap_mcp/dashboard.py` (add `compute_trend_signal`, wire into `fetch_snapshot`)
- Test: `mcp-server/tests/test_trend_signal.py`

- [ ] Step 1: Write `test_trend_signal::test_strong_bull_classification`. Run → FAIL.
- [ ] Step 2: Implement `compute_trend_signal(snap)` per §4.5 with module-level `_LAST_TREND_SIGNAL` cache.
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Write the remaining 6 classification + state tests from §6.2 (`test_trend_signal.py` group). Run iteratively → all PASS.
- [ ] Step 5: Wire into `fetch_snapshot`:
  ```python
  snap["trend_signal"] = _safe_call(compute_trend_signal, "compute_trend_signal")
  ```
  immediately after `snap["conviction"] = …` line.
- [ ] Step 6: Run full pytest. PASS.
- [ ] Step 7: Commit `dashboard: compute_trend_signal projects composite conviction to {STRONG,WEAK}_{BULL,BEAR}/NONE`.

### Task 7: `PaxTrendSignalFetcher` (OpenRange)

**Files:**
- Create: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalModel.java`
- Create: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalSnapshotParser.java`
- Create: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendSignalFetcher.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxTrendSignalSnapshotParserTest.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxTrendSignalFetcherTest.java`

- [ ] Step 1: Write `PaxTrendSignalSnapshotParserTest::parsesHappyPathStrongBull`. Run → FAIL.
- [ ] Step 2: Implement `PaxTrendSignalModel` (immutable carrier: `kind`, `mid`, `asOfMs`, `fetchedAtMs`, `changed`, `bucketEnteredMs`) + parser that extracts from a JSON string. **The parser MUST use only `java.net.http` + the existing recursive-descent style from `PaxHeatwaveSnapshotParser` — no external JSON library.**
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Write `…::parsesNoneKindForMissingTrendSignal`, `…::handlesNullMid`, `…::unknownKindFallsBackToNone`. Run iteratively → PASS each.
- [ ] Step 5: Write `PaxTrendSignalFetcherTest::tickOnceSetsDirtyOnSuccess` with a stub `HttpServer` returning a fixture body. Run → FAIL.
- [ ] Step 6: Implement `PaxTrendSignalFetcher` mirroring `PaxHeatwaveFetcher` exactly: same constants (`REQUEST_TIMEOUT_MS=750`, `FAIL_BACKOFF_THRESHOLD=3`, `MAX_BACKOFF_MS=5000`), same daemon-thread loop, same `Runnable repaintCallback`. **The fetcher MUST NOT touch any canvas-related class.**
- [ ] Step 7: Run all OpenRange tests → PASS.
- [ ] Step 8: Commit `openrange: PaxTrendSignalFetcher mirrors PaxHeatwaveFetcher`.

### Task 8: `PaxTrendTrianglePainter` (OpenRange)

**Files:**
- Create: `indicators/OpenRange/src/main/java/com/openrange/PaxTrendTrianglePainter.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxTrendTrianglePainterTest.java`

- [ ] Step 1: Write `PaxTrendTrianglePainterTest::rendersStrongBullProducesGreenApex`. Run → FAIL.
- [ ] Step 2: Implement `PaxTrendTrianglePainter` with static `render(kind, fontSize) -> PreparedImage` returning a `BufferedImage` with the appropriate triangle drawn (`Polygon` + `fillPolygon` for solid, `drawPolygon` for outline if needed). Use `PaxHeatwaveColors.BULL` / `.BEAR` for hues; STRONG = full alpha, WEAK = alpha 128.
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Tests for all 4 non-NONE kinds. Run → PASS each.
- [ ] Step 5: Commit `openrange: PaxTrendTrianglePainter renders 4 triangle kinds`.

### Task 9: Wire fetcher + painter into PaxOpeningRangeModule

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxPainterTrendTriangleDedupTest.java`
- Test: `indicators/OpenRange/src/test/java/com/openrange/PaxPainterStaleSignalTest.java`

- [ ] Step 1: Write `PaxPainterTrendTriangleDedupTest::twoUpdatesSameBucketEmitOneTriangle` — stub a `ScreenSpaceCanvas`, drive two `update()` calls with the same `bucketEnteredMs`, expect `canvas.addShape` called exactly once with a triangle icon. Run → FAIL.
- [ ] Step 2: Add to `PaxOpeningRangeModule`:
  - field `private final PaxTrendSignalFetcher trendSignals;`
  - field `private final AtomicBoolean trendSignalDirty = new AtomicBoolean(false);`
  - constructor: `this.trendSignals = new PaxTrendSignalFetcher(() -> trendSignalDirty.set(true));`
  - method `boolean consumeTrendSignalDirty() { return trendSignalDirty.compareAndSet(true, false); }`
  - `finish()` calls `trendSignals.stop()`.
- [ ] Step 3: Modify `InstrumentState.shouldRepaint(eventTime)` to also `consumeTrendSignalDirty()` (parallel to existing `consumeHeatwaveDirty()`).
- [ ] Step 4: Add to `InstrumentState`:
  ```java
  final Deque<TrendTriangleEvent> liveTriangles = new ArrayDeque<>(8);
  String lastEmittedKind = "";
  long lastEmittedBucketEnteredMs = 0L;
  ```
  And a tiny `record TrendTriangleEvent(String kind, long bucketEnteredMs, double price)`.
- [ ] Step 5: Add to `PaxPainter.update()` (after `addHeatwaveBox` / `addSignalStatus`): a new `addTrendTriangles(state)` method that:
  - reads `signal = trendSignals.snapshot()`
  - emits a new `TrendTriangleEvent` per the §5.4 dedup rule
  - iterates `state.liveTriangles` and calls `addShape(image, …)` for each, using `DATA_ZERO` coordinates anchored at `event.bucketEnteredMs` / `event.price ± offset`.
- [ ] Step 6: Run dedup test → PASS.
- [ ] Step 7: Write `PaxPainterStaleSignalTest::staleFetcherDoesNotEmitNew`. Run → FAIL → fix the staleness branch → PASS.
- [ ] Step 8: Bump fixed-name jar version note in CLAUDE.md if needed (none; OpenRange jar is unversioned by design — but confirm Bookmap is closed before re-deploy per `openrange_deploy_lazy_classload.md`).
- [ ] Step 9: `cd indicators/OpenRange && pwsh ./build.ps1` → green.
- [ ] Step 10: Commit `openrange: wire trend triangles into PaxPainter with dedup + stale handling`.

### Task 10: Settings toggle (OpenRange UI)

**Files:**
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeUiSettings.java`
- Modify: `indicators/OpenRange/src/main/java/com/openrange/PaxOpeningRangeModule.java` (apply setting to fetcher)

- [ ] Step 1: Add field `boolean showTrendTriangles = true;` to `PaxOpeningRangeUiSettings`.
- [ ] Step 2: Add a `JCheckBox "Show Trend Triangles"` in `PaxOpeningRangeModule.getCustomGuiFor(...)` next to the existing heatwave checkbox.
- [ ] Step 3: In `PaxTrendSignalFetcher.applySettings` (new helper), start/stop the worker based on `ui.showTrendTriangles`.
- [ ] Step 4: In `PaxPainter.addTrendTriangles`, early-return if `!ui.showTrendTriangles`.
- [ ] Step 5: `gradlew test` + `build.ps1` → green.
- [ ] Step 6: Commit `openrange: showTrendTriangles toggle (default on)`.

### Task 11: Replay validation

**Files:**
- Create: `mcp-server/tests/test_trend_analyzer_replay.py`
- Create (optional): `mcp-server/bookmap_mcp/replay_tools.py` if a helper is needed (not strictly required — the test can `import` `compute_session_conviction` directly and feed snapshots from disk)

- [ ] Step 1: Write `test_replay_produces_signal_ledger`. Run → FAIL (file doesn't exist yet).
- [ ] Step 2: Implement the test using either a checked-in tiny fixture file (`tests/fixtures/replay_trending_session.jsonl`, ~100 lines) or a synthetic generator that emits a 30-poll uptrend then a 30-poll downtrend.
- [ ] Step 3: Run → PASS.
- [ ] Step 4: Write `test_with_and_without_trend_analyzer`. Run → FAIL.
- [ ] Step 5: Implement by mutating the `pax_weights.json`-equivalent dict in-test (do NOT touch the on-disk file — the test patches `_load_pax_weights` via monkeypatch to flip `trend_analyzer` weight between 0.0 and 0.06).
- [ ] Step 6: Run → PASS. Inspect produced CSV by hand to confirm Δcomposite distribution looks reasonable.
- [ ] Step 7: Commit `tests: trend_analyzer replay validation ledger + cluster-cap empirical check`.

### Task 12: Documentation

**Files:**
- Modify: `CLAUDE.md` (add a short subsection under "Code layout pointers" describing the trend package and the new endpoint; reference the new `compute_trend_signal` cache)
- Modify: `mcp-server/README.md` (add `trend_analyzer` to the source cluster table)
- Modify memory: `MEMORY.md` + new `trend_analyzer_port.md` capturing the architectural decision (bridge-resident, cluster `trend`, cap `0.10`) and the dedup rule.

- [ ] Step 1: Update `CLAUDE.md` with one paragraph under the existing "Code layout pointers" describing `com.bookmapmcp.trend` and the `_source_trend_analyzer` helper.
- [ ] Step 2: Update `mcp-server/README.md` (if it has a source table) with the new row.
- [ ] Step 3: Add memory entry per the auto-memory rules: `trend_analyzer_port.md` (type: `project`, with **Why:** = lowest-fragility port + cluster cap rationale and **How to apply:** = always inject as one capped source, never as a decision engine).
- [ ] Step 4: Commit `docs: trend_analyzer architecture note + memory`.

---

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| **TrendAnalyzer engine warmup is slower than other sources** — first `numberOfCandles` candles produce no usable signal. Bridge-process restart resets the engine; for the first ~5–15 minutes of the next session, `reliability == 0`. | High at startup | Low | Reliability gating + `warmedUp` flag in payload. Existing sources have the same warmup behavior (regime starts at WARMUP, vwap_slope at WARMUP). Documented expected behavior, not a bug. |
| **Cluster cap silently masking a bug** — if the source helper accidentally returns score 1.0 for everything, the composite is bounded at 0.10 and the bug is invisible in production. | Low | Medium | Replay test `test_trend_analyzer_cannot_dominate` checks the cap holds; a separate `test_score_distribution` (TBD enhancement) could check that `score` is not always at ±1. Not blocking; can add later. |
| **Engine disagreement on choppy days saturates reliability=0.6** — TrendAnalyzer contributes a noisy small signal for most of a chop day. | Medium | Low | This is the desired behavior: reliability=0.6 × score≈0 = effective_score≈0. Composite is correctly damped. Pinned by `test_engines_disagree_cuts_reliability`. |
| **Bridge-port classloading regression** (cf. May 2026 `PaxHeatwaveSnapshotParser NoClassDefFoundError`) — partial jar deploy mid-build. | Medium | High | Follow CLAUDE.md jar-versioning rule for the bridge jar (`bookmap-mcp-bridge-v<N>.jar`). Bridge's versioned filename means partial deploy lands as a new file, not an overwrite. |
| **OpenRange jar lock** — the new triangle painter requires a fresh OpenRange build, but Bookmap holds `openrange-release.jar` open while loaded. | High | Medium | Documented in CLAUDE.md and memory `openrange_deploy_lazy_classload.md`. Close Bookmap before `build.ps1`; never copy a fresh jar while Bookmap runs. |
| **Trade stream forwarded to TrendAccumulator adds per-onTrade latency on the bridge thread.** | Low | Medium | `TrendAccumulator.onTrade` is a `RollingAverage.add` + `BarAggregator.add` (constant-time append, no allocation) plus an optional `processEngines` only when a bar completes (every 15 s). Cost ≤ 1 μs per trade. Verified by `TrendAccumulatorTest::throughput` (TBD micro-test). |
| **Dashboard polling adds an extra `/trend_analyzer` round-trip per snapshot** | Low | Low | Bridge handler is GET, in-memory, ~50 μs. Net snapshot-build latency increase: negligible. |
| **`compute_trend_signal` cache (`_LAST_TREND_SIGNAL`) grows unboundedly across aliases** | Low (NQ-only deployment) | Low | Cache is keyed by alias — typical deployment has 1–3 aliases. No bound needed. If multi-instrument use grows, switch to `LRU(32)` later. |
| **Replay validation depends on a fixture file that hasn't been recorded yet** | High | Low | Task 11 includes a synthetic generator path so the test passes deterministically without a real recording. Real-replay validation is a follow-up task, not a blocker. |
| **TrendAnalyzer's own addon (trendanalyzer-mvp) is currently untracked** (it's its own embedded git repo and we chose to skip committing it). | — | Low | Plan does NOT depend on that addon being loaded in Bookmap. The port is mechanical class-copy into the bridge package; we are not calling into the trendanalyzer-mvp addon at runtime. |

---

## 9. File structure summary

```
mcp-server/
  bookmap_mcp/
    dashboard.py                                          [MODIFY]   +_source_trend_analyzer
                                                                     +trend_analyzer fetch in fetch_snapshot
                                                                     +compute_trend_signal
                                                                     +trend_analyzer to module-constant defaults
    pax_weights.json                                      [MODIFY]   +weight, +cluster, +cap
  tests/
    test_trend_analyzer.py                                [CREATE]   §6.2 source-helper tests
    test_trend_analyzer_fetch.py                          [CREATE]   §6.2 snap key tests
    test_trend_signal.py                                  [CREATE]   §6.2 classification + state tests
    test_pax_weights_load.py                              [CREATE]   §6.2 config-load test
    test_trend_analyzer_replay.py                         [CREATE]   §6.4 replay validation
    test_session_conviction.py                            [MODIFY]   +2 assertions

src/main/java/com/bookmapmcp/
  trend/                                                  [CREATE]   port of trendanalyzer-mvp/core/*
    Candle.java
    OrderflowSnapshot.java
    TrendDirection.java
    SwitchCondition.java
    TrendConfig.java
    RollingAverage.java
    OrderflowAccumulator.java
    OrderflowConfirmations.java
    BarAggregator.java
    TrendSnapshot.java
    TrendEngine.java
    TrendRegimeFilter.java
    ConfidenceModel.java
    StableTrendSnapshot.java
    StableTrendEngine.java
    TrendAccumulator.java                                            new — orchestrates the engines for one alias
    TrendSnapshotDto.java                                            new — JSON shape carrier
  handlers/
    TrendAnalyzerHandler.java                             [CREATE]
  state/
    InstrumentState.java                                  [MODIFY]   +trend field, +onTrade forward, +trendSnapshot()
  BridgeServer.java                                       [MODIFY]   +/trend_analyzer createContext
build.gradle                                              [MODIFY]   bump version + archiveFileName

src/test/java/com/bookmapmcp/
  trend/                                                  [CREATE]   ported tests
    StableTrendEngineTest.java
    BarAggregatorTest.java
    OrderflowAccumulatorTest.java
    TrendEngineTest.java
    TrendAccumulatorTest.java                                        new
  state/
    InstrumentStateTrendTest.java                         [CREATE]
  handlers/
    TrendAnalyzerHandlerTest.java                         [CREATE]

indicators/OpenRange/src/main/java/com/openrange/
  PaxTrendSignalModel.java                                [CREATE]
  PaxTrendSignalSnapshotParser.java                       [CREATE]
  PaxTrendSignalFetcher.java                              [CREATE]
  PaxTrendTrianglePainter.java                            [CREATE]
  PaxOpeningRangeModule.java                              [MODIFY]   +trendSignals fetcher, +trendSignalDirty, +addTrendTriangles
  PaxOpeningRangeUiSettings.java                          [MODIFY]   +showTrendTriangles toggle

indicators/OpenRange/src/test/java/com/openrange/
  PaxTrendSignalSnapshotParserTest.java                   [CREATE]
  PaxTrendSignalFetcherTest.java                          [CREATE]
  PaxTrendTrianglePainterTest.java                        [CREATE]
  PaxPainterTrendTriangleDedupTest.java                   [CREATE]
  PaxPainterStaleSignalTest.java                          [CREATE]

docs/superpowers/plans/
  2026-05-19-trend-analyzer-port.md                       [THIS DOC]

CLAUDE.md                                                  [MODIFY]   short note under "Code layout pointers"
MEMORY.md                                                  [MODIFY]   +entry
memory/trend_analyzer_port.md                              [CREATE]   project-type memory
```

Approximate commit count: **12 commits** (Tasks 1–12 above). Each is independently revertable.

---

## 10. Hard constraints — compliance check

| Constraint | How this plan satisfies it |
|---|---|
| Do not touch live trading behavior | No changes to `server.py`, `pax_daemon.py`, or any order-placement code path. `_source_trend_analyzer` is read-only; `compute_trend_signal` is read-only. |
| No blocking HTTP calls on Bookmap market-data callbacks | `InstrumentState.onTrade` calls `TrendAccumulator.onTrade` which is pure in-memory math. The bridge HTTP endpoint runs on the bridge's own thread pool (`Executors.newFixedThreadPool(4, …)`), not on a Bookmap callback thread. |
| No canvas mutation from background threads | `PaxTrendSignalFetcher` only sets `trendSignalDirty.set(true)`. All `canvas.addShape` / `removeShape` happen inside `PaxPainter.update()`, which runs on Bookmap callbacks (`onTrade`/`onDepth`/`onMoveEnd`). Same enforcement pattern as Heatwave, documented and pinned by tests. |
| TrendAnalyzer does NOT override the dashboard's final conviction | `_source_trend_analyzer` returns one entry in `_CONVICTION_SOURCES`. The composite is computed by `compute_session_conviction` from all sources weighted + capped. `compute_trend_signal` reads `snap["conviction"]`, NOT `snap["trend_analyzer"]`. |
| Functions small and auditable | Helper is ~50 lines, `compute_trend_signal` ~25 lines, every Java class single-responsibility. |
| Preserve existing dashboard behavior if TrendAnalyzer missing | `_source_trend_analyzer` returns `{score:0, reliability:0}` when `snap["trend_analyzer"]` is `None`/error. Conviction engine's `effective[name] = base × reliability` then zeroes out the contribution. Composite is unchanged from pre-change behavior. Test `test_missing_data_returns_reliability_zero` pins this. |
| Use existing project patterns over new abstractions | Bridge handler mirrors `MomentumHandler`. Source helper mirrors `_source_bias_score`. Painter/fetcher mirror `PaxHeatwavePainter`/`PaxHeatwaveFetcher`. Jar versioning follows `bookmap-mcp-bridge-v<N>.jar` convention. |

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-19-trend-analyzer-port.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
