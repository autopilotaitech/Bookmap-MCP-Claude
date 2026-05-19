package com.bookmapmcp.trend;

import java.util.ArrayDeque;
import java.util.Deque;

/**
 * Bridge-side accumulator that aggregates irregular {@code onTrade} events
 * into fixed-duration time buckets and drives a fast + slow
 * {@link StableTrendEngine}. Replaces upstream's count-based BarAggregator
 * which expects already-completed Bookmap bars.
 *
 * <p>Threading: this class is intended to be called from the bridge's market
 * data callback. Mutations are NOT thread-safe; {@link #snapshot()} is safe
 * to call from another thread because every field it reads is published via
 * a {@code volatile} write at the end of each {@code closeBucket()} (which
 * the trade-callback owns).</p>
 *
 * <p>Cost: O(1) per trade in steady state. Bounded work on resumption from
 * a long no-trade gap via {@link #MAX_CARRY_FORWARD_BUCKETS}.</p>
 */
public final class TimeBucketTrendAccumulator {

    /** Hard ceiling on carry-forward bucket emissions in a single onTrade
     * call. Beyond this, we snap forward without emitting individual buckets.
     * 240 fast buckets at 15s = 1 hour of no-trade. */
    public static final int MAX_CARRY_FORWARD_BUCKETS = 240;

    /** Slow bucket is the merge of this many consecutive closed fast buckets. */
    public static final int SLOW_RATIO = 4;

    private final String alias;
    private final long fastBucketMillis;
    private final long slowBucketMillis;
    private final TrendConfig fastCfg;
    private final TrendConfig slowCfg;
    private final StableTrendEngine fast;
    private final StableTrendEngine slow;
    private final RollingAverage fastVol = new RollingAverage(20);
    private final RollingAverage slowVol = new RollingAverage(20);
    /** Sliding window of the last SLOW_RATIO closed fast candles, used to
     * build a slow candle (open=first.open, close=last.close, high=max,
     * low=min, volume=sum, delta=sum). */
    private final Deque<Candle> recentFast = new ArrayDeque<>(SLOW_RATIO);

    /** Current open bucket fields. {@code firstTradeSeen == false} signals
     * "no bucket open yet" (no trade has ever arrived). Using a separate flag
     * because {@code bucketStartMs == 0} is a perfectly valid value (start of
     * the epoch / first synthetic test bucket). */
    private boolean firstTradeSeen;
    private long bucketStartMs;
    private double bucketOpen;
    private double bucketHigh;
    private double bucketLow;
    private double bucketClose;
    private long bucketVolume;
    private double bucketDelta;

    /** Last trade's event time. Frozen at the moment of closeBucket() so
     * snapshot() reflects the cadence of real data, not wall-clock. */
    private volatile long lastTradeEventMs;
    /** Last raw Bookmap event nanos if available; diagnostic only. */
    private volatile long lastEventNanos;
    /** Most recently closed-bucket snapshots, published volatile so the
     * HTTP handler thread can read without a lock. */
    private volatile StableTrendSnapshot latestFast;
    private volatile StableTrendSnapshot latestSlow;
    /** Counts of CLOSED candles fed to each engine. Used for warmup gating. */
    private volatile long fastCandleCount;
    private volatile long slowCandleCount;
    /** Last close price emitted (for carry-forward candles + diagnostic). */
    private volatile double lastClose = Double.NaN;

    public TimeBucketTrendAccumulator(String alias, long fastBucketMillis) {
        if (fastBucketMillis < TrendConfig.MIN_CANDLE_INTERVAL_MILLIS) {
            fastBucketMillis = TrendConfig.MIN_CANDLE_INTERVAL_MILLIS;
        }
        this.alias = alias;
        this.fastBucketMillis = fastBucketMillis;
        this.slowBucketMillis = fastBucketMillis * SLOW_RATIO;
        this.fastCfg = defaultConfig(fastBucketMillis);
        this.slowCfg = defaultConfig(this.slowBucketMillis);
        this.fast = new StableTrendEngine(this.fastCfg, 25, 3);
        this.slow = new StableTrendEngine(this.slowCfg, 25, 3);
    }

    private static TrendConfig defaultConfig(long bucketMillis) {
        return new TrendConfig(true, bucketMillis, 10, 3.0,
                SwitchCondition.CLOSE_CROSS, 1,
                false, false, false, 0);
    }

    /**
     * Ingest a single trade. {@code eventMs} is taken at face value (the
     * caller is responsible for supplying a sensible value — typically
     * Bookmap event-ms, falling back to {@code System.currentTimeMillis()}
     * upstream if Bookmap did not provide one). We never silently substitute
     * the wall clock here because tests and replay paths use synthetic
     * timestamps that may start at 0.
     */
    public void onTrade(long eventMs, double price, long size, boolean isBuyAggressor) {
        if (!Double.isFinite(price) || size <= 0L) {
            return;
        }
        long ts = eventMs;
        if (!firstTradeSeen) {
            // First trade ever: align to the start of the bucket containing ts.
            firstTradeSeen = true;
            bucketStartMs = Math.floorDiv(ts, fastBucketMillis) * fastBucketMillis;
            bucketOpen = bucketHigh = bucketLow = bucketClose = price;
            bucketVolume = 0L;
            bucketDelta = 0.0;
        }
        // Close zero or more buckets if we've crossed boundaries.
        int carried = 0;
        while (ts >= bucketStartMs + fastBucketMillis) {
            if (++carried > MAX_CARRY_FORWARD_BUCKETS) {
                // Snap forward without emitting; engine state preserved but stale.
                long aheadBuckets = (ts - bucketStartMs) / fastBucketMillis;
                bucketStartMs += aheadBuckets * fastBucketMillis;
                // Begin a fresh bucket at this price.
                bucketOpen = bucketHigh = bucketLow = bucketClose = price;
                bucketVolume = 0L;
                bucketDelta = 0.0;
                break;
            }
            closeBucket();
        }
        // Update open bucket with the new trade.
        if (bucketVolume == 0L && !Double.isFinite(bucketOpen)) {
            // Bucket was carried-forward with NaN sentinel; first real trade
            // sets open here.
            bucketOpen = price;
            bucketHigh = price;
            bucketLow = price;
        } else if (bucketVolume == 0L) {
            // Bucket was reset by a carry-forward with a known close price.
            // Treat this trade as the open.
            bucketOpen = price;
            bucketHigh = price;
            bucketLow = price;
        }
        if (price > bucketHigh) bucketHigh = price;
        if (price < bucketLow) bucketLow = price;
        bucketClose = price;
        bucketVolume += size;
        bucketDelta += isBuyAggressor ? size : -size;
        lastTradeEventMs = ts;
    }

    /** Bookmap event nanos passthrough — optional, diagnostic only. */
    public void recordEventNanos(long nanos) {
        if (nanos > 0L) lastEventNanos = nanos;
    }

    /** Close the currently-open bucket, advance bucketStartMs. */
    private void closeBucket() {
        Candle candle;
        if (bucketVolume == 0L) {
            // Carry-forward: no trades in this window. Emit a flat candle so
            // the engine sees time progressing but no synthetic price action.
            if (!Double.isFinite(lastClose)) {
                // We've never seen a real close; can't synthesize. Just advance.
                bucketStartMs += fastBucketMillis;
                bucketOpen = bucketHigh = bucketLow = bucketClose = Double.NaN;
                return;
            }
            candle = new Candle(bucketStartMs, lastClose, lastClose, lastClose, lastClose, 0L, 0.0);
        } else {
            candle = new Candle(bucketStartMs, bucketOpen, bucketHigh, bucketLow, bucketClose,
                    bucketVolume, bucketDelta);
            lastClose = bucketClose;
        }

        OrderflowSnapshot ofs = new OrderflowSnapshot(
                candle.volume(), candle.delta(), fastVol.average(), 0.0, 0.0);
        latestFast = fast.onCandle(candle, ofs);
        fastVol.add(candle.volume());
        fastCandleCount++;

        recentFast.addLast(candle);
        if (recentFast.size() > SLOW_RATIO) recentFast.removeFirst();
        if (recentFast.size() == SLOW_RATIO && (fastCandleCount % SLOW_RATIO == 0)) {
            Candle slowCandle = mergeRecentFast();
            OrderflowSnapshot slowOfs = new OrderflowSnapshot(
                    slowCandle.volume(), slowCandle.delta(), slowVol.average(), 0.0, 0.0);
            latestSlow = slow.onCandle(slowCandle, slowOfs);
            slowVol.add(slowCandle.volume());
            slowCandleCount++;
        }

        // Advance to the next bucket.
        bucketStartMs += fastBucketMillis;
        // Reset open-bucket fields; next onTrade will fill them.
        bucketOpen = Double.NaN;
        bucketHigh = Double.NEGATIVE_INFINITY;
        bucketLow = Double.POSITIVE_INFINITY;
        bucketClose = Double.NaN;
        bucketVolume = 0L;
        bucketDelta = 0.0;
    }

    private Candle mergeRecentFast() {
        double open = Double.NaN, high = Double.NEGATIVE_INFINITY, low = Double.POSITIVE_INFINITY, close = Double.NaN;
        long volume = 0L;
        double delta = 0.0;
        long startMs = -1L;
        for (Candle c : recentFast) {
            if (startMs < 0) { startMs = c.timestampMillis(); open = c.open(); }
            if (c.high() > high) high = c.high();
            if (c.low() < low) low = c.low();
            close = c.close();
            volume += c.volume();
            delta += c.delta();
        }
        if (low > high) { low = high = open = close = 0.0; }
        return new Candle(startMs, open, high, low, close, volume, delta);
    }

    public TrendAnalyzerSnapshot snapshot() {
        long now = System.currentTimeMillis();
        StableTrendSnapshot f = latestFast;
        StableTrendSnapshot s = latestSlow;
        TrendAnalyzerSnapshot.Leg fastLeg = (f != null)
                ? new TrendAnalyzerSnapshot.Leg(f.direction(), f.confidence(), f.switched(), f.chop(),
                        f.line(), fastBucketMillis, fastCandleCount)
                : TrendAnalyzerSnapshot.Leg.warmup(fastBucketMillis);
        TrendAnalyzerSnapshot.Leg slowLeg = (s != null)
                ? new TrendAnalyzerSnapshot.Leg(s.direction(), s.confidence(), s.switched(), s.chop(),
                        s.line(), slowBucketMillis, slowCandleCount)
                : TrendAnalyzerSnapshot.Leg.warmup(slowBucketMillis);
        // Warmup gate must match what each engine actually requires before it
        // produces a real (non-provisional) direction. TrendEngine.onCandle
        // returns provisional direction (close-vs-open of the current candle
        // only) until it has accumulated numberOfCandles candles. A lower
        // gate would mark the accumulator "warmed" while the engine is still
        // producing provisional output.
        boolean warmed = (f != null) && (s != null)
                && fastCandleCount >= fastCfg.numberOfCandles()
                && slowCandleCount >= slowCfg.numberOfCandles();
        double rawScore = 0.0;
        double reliabilityHint = 0.0;
        if (warmed) {
            double fastPart = fastLeg.directionSign() * fastLeg.confidence() / 100.0;
            double slowPart = slowLeg.directionSign() * slowLeg.confidence() / 100.0;
            rawScore = 0.4 * fastPart + 0.6 * slowPart;
            if (rawScore > 1.0) rawScore = 1.0;
            if (rawScore < -1.0) rawScore = -1.0;
            if (fastLeg.chop() && slowLeg.chop()) {
                reliabilityHint = 0.25;
            } else if (fastLeg.chop() || slowLeg.chop()) {
                reliabilityHint = 0.5;
            } else if (fastLeg.directionSign() != 0 && slowLeg.directionSign() != 0
                    && fastLeg.directionSign() * slowLeg.directionSign() < 0) {
                reliabilityHint = 0.6;
            } else {
                reliabilityHint = 1.0;
            }
        }
        // When not warmed: rawScore = 0.0, reliabilityHint = 0.0. The
        // dashboard's _source_trend_analyzer also gates reliability=0 on
        // !warmedUp, so the source contributes nothing to composite
        // regardless of which value the bridge reports here.
        double closeForSnapshot = Double.isFinite(lastClose) ? lastClose : Double.NaN;
        return new TrendAnalyzerSnapshot(alias, lastEventNanos, lastTradeEventMs, now,
                closeForSnapshot, warmed, rawScore, reliabilityHint, fastLeg, slowLeg);
    }

    // Test hooks / observability.
    public long fastBucketMillis() { return fastBucketMillis; }
    public long slowBucketMillis() { return slowBucketMillis; }
    public long fastCandleCount() { return fastCandleCount; }
    public long slowCandleCount() { return slowCandleCount; }
    public int fastRequiredCandles() { return fastCfg.numberOfCandles(); }
    public int slowRequiredCandles() { return slowCfg.numberOfCandles(); }
}
