package com.bookmapmcp.trend;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class TimeBucketTrendAccumulatorTest {

    private static final long FAST_MS = 5_000L;

    private TimeBucketTrendAccumulator newAcc() {
        return new TimeBucketTrendAccumulator("NQ", FAST_MS);
    }

    @Test
    void irregularTradesAggregateIntoSingleCandle() {
        TimeBucketTrendAccumulator acc = newAcc();
        // Bucket starts at floor(10_000 / 5000) * 5000 = 10_000; bucket runs [10_000, 15_000).
        long base = 10_000L;
        acc.onTrade(base + 0,    100.0, 1, true);  // open
        acc.onTrade(base + 137,  102.0, 2, false); // high
        acc.onTrade(base + 1789, 99.0,  3, true);  // low
        acc.onTrade(base + 3000, 101.0, 1, false); // close-of-bucket
        // Cross into next bucket — closes the first one.
        acc.onTrade(base + 5_001, 101.5, 1, true);

        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertEquals(1L, acc.fastCandleCount(), "exactly one closed bucket");
        // lastClose reflects the close price of the most recently CLOSED candle.
        assertEquals(101.0, snap.lastClose(), 1e-9);
    }

    @Test
    void sparseTradesDoNotFakeMomentum() {
        TimeBucketTrendAccumulator acc = newAcc();
        long t = 100_000L;
        // One trade per 30s for 5 minutes at the same price = 10 buckets crossed.
        for (int i = 0; i < 10; i++) {
            acc.onTrade(t, 21800.0, 1, i % 2 == 0);
            t += 30_000L;
        }
        TrendAnalyzerSnapshot snap = acc.snapshot();
        // The accumulator closed many buckets but each is a tiny flat candle.
        // The engine should NOT report a confident direction.
        assertTrue(snap.fast().confidence() < 40,
                "sparse flat trades should produce low confidence, got " + snap.fast().confidence());
    }

    @Test
    void longNoTradeGapBoundsWorkAndDoesNotBlock() {
        TimeBucketTrendAccumulator acc = newAcc();
        // First trade at t=0.
        acc.onTrade(0L, 100.0, 1, true);
        // 24-hour gap: would naively close 24h/5s = 17,280 buckets.
        long start = System.nanoTime();
        acc.onTrade(24L * 3600L * 1000L, 105.0, 1, true);
        long elapsedMs = (System.nanoTime() - start) / 1_000_000L;
        assertTrue(elapsedMs < 200L,
                "long-gap onTrade should be bounded, took " + elapsedMs + "ms");
        // Carry-forward emitted at most MAX_CARRY_FORWARD_BUCKETS candles.
        assertTrue(acc.fastCandleCount() <= TimeBucketTrendAccumulator.MAX_CARRY_FORWARD_BUCKETS,
                "carry-forward must be bounded, was " + acc.fastCandleCount());
    }

    @Test
    void uptrendDrivesPositiveScoreAfterWarmup() {
        TimeBucketTrendAccumulator acc = newAcc();
        // Monotonic uptrend across many buckets. Each bucket has multiple trades.
        long t = 0L;
        double price = 100.0;
        for (int bucket = 0; bucket < 60; bucket++) {
            // 3 trades per bucket, climbing within the bucket and across buckets.
            acc.onTrade(t,           price,        10, true);
            acc.onTrade(t + 1_000L,  price + 0.3,  10, true);
            acc.onTrade(t + 2_500L,  price + 0.5,  20, true);
            t += FAST_MS;
            price += 0.5;
        }
        // Cross one more bucket boundary to close the last accumulated bucket.
        acc.onTrade(t + 100L, price, 1, true);
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertTrue(snap.warmedUp(), "should be warmed up after 60 buckets");
        assertEquals(TrendDirection.UP, snap.fast().direction(),
                "fast should be UP for monotonic uptrend");
        assertTrue(snap.score() > 0,
                "blended score should be positive for uptrend, was " + snap.score());
    }

    @Test
    void downtrendDrivesNegativeScoreAfterWarmup() {
        TimeBucketTrendAccumulator acc = newAcc();
        long t = 0L;
        double price = 200.0;
        for (int bucket = 0; bucket < 60; bucket++) {
            acc.onTrade(t,           price,        10, false);
            acc.onTrade(t + 1_000L,  price - 0.3,  10, false);
            acc.onTrade(t + 2_500L,  price - 0.5,  20, false);
            t += FAST_MS;
            price -= 0.5;
        }
        acc.onTrade(t + 100L, price, 1, false);
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertTrue(snap.warmedUp());
        assertEquals(TrendDirection.DOWN, snap.fast().direction());
        assertTrue(snap.score() < 0,
                "blended score should be negative for downtrend, was " + snap.score());
    }

    @Test
    void snapshotPopulatesEventMsAndUpdatedAtMsDistinctly() {
        TimeBucketTrendAccumulator acc = newAcc();
        long eventMs = System.currentTimeMillis() - 10_000L;
        acc.onTrade(eventMs, 100.0, 1, true);
        // Cross a bucket so snapshot's lastClose is populated and the path is exercised.
        acc.onTrade(eventMs + FAST_MS + 1, 100.5, 1, true);
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertEquals(eventMs + FAST_MS + 1, snap.eventMs(), "eventMs from last trade");
        assertTrue(snap.updatedAtMs() >= eventMs,
                "updatedAtMs is wall-clock and >= eventMs");
        assertTrue(snap.updatedAtMs() - eventMs >= 0L);
    }

    @Test
    void warmupBeforeFirstCandlesReportsNotWarmed() {
        TimeBucketTrendAccumulator acc = newAcc();
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertFalse(snap.warmedUp(), "fresh accumulator must not be warmed");
        assertEquals(0.0, snap.score(), 1e-9);
        assertEquals(0.0, snap.reliabilityHint(), 1e-9);
        assertNotNull(snap.fast());
        assertNotNull(snap.slow());
        assertEquals(FAST_MS, snap.fast().candleIntervalMillis());
        assertEquals(FAST_MS * TimeBucketTrendAccumulator.SLOW_RATIO, snap.slow().candleIntervalMillis());
    }

    @Test
    void notWarmedBeforeFastEngineMeetsRequiredCandles() {
        TimeBucketTrendAccumulator acc = newAcc();
        int requiredFast = acc.fastRequiredCandles();
        // Drive (requiredFast - 1) closed buckets: cross that many boundaries
        // and then one more trade to keep an open bucket without closing it.
        long t = 0L;
        double price = 100.0;
        for (int i = 0; i < requiredFast - 1; i++) {
            acc.onTrade(t, price, 10, true);
            t += FAST_MS;
            price += 0.5;
        }
        // No closing trade yet — the (requiredFast - 1)th bucket is OPEN.
        // closedCount = requiredFast - 2 at this point because we only close
        // a bucket when a later trade crosses its boundary.
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertFalse(snap.warmedUp(),
                "fast must not be warmed until fastCandleCount >= " + requiredFast
                + " (got fast=" + acc.fastCandleCount() + ")");
        assertEquals(0.0, snap.score(), 1e-9);
    }

    @Test
    void notWarmedBetweenFastAndSlowWarmups() {
        TimeBucketTrendAccumulator acc = newAcc();
        int requiredSlow = acc.slowRequiredCandles();
        int slowRatio = TimeBucketTrendAccumulator.SLOW_RATIO;
        int requiredFast = acc.fastRequiredCandles();
        // Drive enough buckets to satisfy fast but NOT slow.
        // Need to close at least `requiredFast` fast buckets but fewer than
        // requiredSlow * slowRatio. requiredFast=10, requiredSlow=10, ratio=4
        // → close 12 fast buckets → slow closed = 12/4 = 3 < 10.
        int targetFastClosed = Math.max(requiredFast + 2, requiredFast);
        long t = 0L;
        double price = 100.0;
        // Loop one extra iteration past targetFastClosed so the next-onTrade
        // closes the targetFastClosed-th bucket.
        for (int i = 0; i <= targetFastClosed; i++) {
            acc.onTrade(t, price, 10, true);
            t += FAST_MS;
            price += 0.5;
        }
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertTrue(acc.fastCandleCount() >= requiredFast,
                "fast must be at least required, got " + acc.fastCandleCount());
        assertTrue(acc.slowCandleCount() < requiredSlow,
                "slow must NOT yet be at required, got " + acc.slowCandleCount());
        assertFalse(snap.warmedUp(),
                "must not be warmed until slow is also at " + requiredSlow);
        assertEquals(0.0, snap.score(), 1e-9);
        assertEquals(0.0, snap.reliabilityHint(), 1e-9);
    }

    @Test
    void warmedOnlyAfterBothEnginesMeetRequiredCandles() {
        TimeBucketTrendAccumulator acc = newAcc();
        int requiredSlow = acc.slowRequiredCandles();
        int slowRatio = TimeBucketTrendAccumulator.SLOW_RATIO;
        int needFastBuckets = requiredSlow * slowRatio;   // 10 * 4 = 40
        long t = 0L;
        double price = 100.0;
        for (int i = 0; i <= needFastBuckets; i++) {
            acc.onTrade(t, price, 10, true);
            t += FAST_MS;
            price += 0.5;
        }
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertTrue(acc.fastCandleCount() >= acc.fastRequiredCandles(), "fast required met");
        assertTrue(acc.slowCandleCount() >= requiredSlow, "slow required met");
        assertTrue(snap.warmedUp(), "must be warmed when both engines hit their required counts");
    }

    @Test
    void invalidPriceOrSizeIsIgnored() {
        TimeBucketTrendAccumulator acc = newAcc();
        acc.onTrade(1000L, Double.NaN, 1, true);
        acc.onTrade(1000L, 100.0, 0, true);
        acc.onTrade(1000L, 100.0, -5, true);
        // No trade should have been accepted; no buckets closed.
        TrendAnalyzerSnapshot snap = acc.snapshot();
        assertEquals(0L, acc.fastCandleCount());
        assertFalse(snap.warmedUp());
    }
}
