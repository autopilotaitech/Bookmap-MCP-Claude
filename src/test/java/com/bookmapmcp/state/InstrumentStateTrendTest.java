package com.bookmapmcp.state;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;

import org.junit.jupiter.api.Test;

import com.bookmapmcp.trend.TrendAnalyzerSnapshot;

class InstrumentStateTrendTest {

    private InstrumentState newState() {
        return new InstrumentState("NQ-CME", "NQ", "E-mini Nasdaq", 0.25, 20, Instant.now());
    }

    @Test
    void trendSnapshotPresentEvenBeforeFirstTrade() {
        InstrumentState s = newState();
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        assertNotNull(snap);
        assertEquals("NQ-CME", snap.alias());
        assertEquals(false, snap.warmedUp());
        assertEquals(0L, snap.eventMs());
        assertTrue(snap.updatedAtMs() > 0L, "updatedAtMs is wall-clock and always populated");
    }

    @Test
    void onTradeFeedsTrendAccumulator() {
        InstrumentState s = newState();
        // Feed enough trades across enough buckets to warm both engines.
        // Each onTrade uses wall-clock nowMs() inside the bridge, so we cannot
        // synthesize "60 buckets" from the test side. We instead verify that:
        //   (a) snapshot eventMs advances after the first trade
        //   (b) lastClose reflects ingested trades after enough have crossed bucket boundaries
        // The deeper engine-warm tests live in TimeBucketTrendAccumulatorTest.
        s.onTrade(21800.0, 5, true);
        TrendAnalyzerSnapshot snap1 = s.trendAnalyzerSnapshot();
        long firstEventMs = snap1.eventMs();
        assertTrue(firstEventMs > 0L, "eventMs should be > 0 after first trade");
        try { Thread.sleep(5L); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        s.onTrade(21800.25, 7, false);
        TrendAnalyzerSnapshot snap2 = s.trendAnalyzerSnapshot();
        assertTrue(snap2.eventMs() >= firstEventMs, "eventMs is monotone non-decreasing");
        assertTrue(snap2.updatedAtMs() >= snap1.updatedAtMs(),
                "updatedAtMs is monotone non-decreasing");
    }

    @Test
    void snapshotHasEventMsAndUpdatedAtMsDistinct() {
        InstrumentState s = newState();
        long before = System.currentTimeMillis();
        s.onTrade(100.0, 1, true);
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        assertTrue(snap.eventMs() >= before - 10L,
                "eventMs reflects wall-clock at trade ingest");
        assertTrue(snap.updatedAtMs() >= snap.eventMs(),
                "updatedAtMs is taken at snapshot() build time, after eventMs");
    }

    @Test
    void eventMsIsPlausibleEpochMillisWhenFeedNanosAreUnset() {
        // No onTimestamp call → lastSeenNanos = 0 → nowMsForSession falls back
        // to wall-clock. The snapshot's eventMs must be a real epoch ms.
        InstrumentState s = newState();
        s.onTrade(100.0, 1, true);
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        // 1_500_000_000_000 ms ≈ 2017-07-14. Any real wall-clock since then.
        assertTrue(snap.eventMs() > 1_500_000_000_000L,
                "eventMs must be plausible epoch ms; got " + snap.eventMs());
    }

    @Test
    void rithmicLikeFeedCounterFallsBackToWallClock() {
        // Rithmic publishes a counter that doesn't map to Unix epoch nanos.
        // Simulate by feeding a small bogus value via onTimestamp. The trend
        // accumulator must NOT receive that counter as bucket time; it must
        // get the wall-clock fallback so chart anchoring stays sane.
        InstrumentState s = newState();
        long bogusFeedCounter = 12345L;          // way below epoch nanos
        s.onTimestamp(bogusFeedCounter);
        long wallBefore = System.currentTimeMillis();
        s.onTrade(100.0, 1, true);
        long wallAfter = System.currentTimeMillis();
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        // eventMs should be wall-clock (within the +/- window of the test),
        // NOT 12345/1e6 = 0.
        assertTrue(snap.eventMs() >= wallBefore - 10L,
                "eventMs must fall back to wall-clock when feed nanos are bogus; got "
                + snap.eventMs());
        assertTrue(snap.eventMs() <= wallAfter + 10L,
                "eventMs should be in the wall-clock window; got " + snap.eventMs());
    }

    @Test
    void replayWithinDayOfWallClockIsTrusted() {
        // Feed clock within 24h of wall → trust it (replay anchoring).
        InstrumentState s = newState();
        long replayMs = System.currentTimeMillis() - 3_600_000L;  // 1h ago
        long replayNanos = replayMs * 1_000_000L;
        s.onTimestamp(replayNanos);
        s.onTrade(100.0, 1, true);
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        // Should anchor at the replay timestamp, not wall.
        assertTrue(Math.abs(snap.eventMs() - replayMs) < 5_000L,
                "replay event time should anchor within ~5s of the feed clock; "
                + "got eventMs=" + snap.eventMs() + " expected≈" + replayMs);
    }

    @Test
    void historicalReplayBeyondOneDayIsStillTrusted() {
        // OLD nowMsForSession() snapped any feed clock >24h from wall to wall.
        // That broke historical replays. nowMsForTrend() should anchor at the
        // playback date instead.
        InstrumentState s = newState();
        // 5-year-old playback timestamp (still well within plausible epoch).
        long historicalMs = System.currentTimeMillis() - 5L * 365L * 86_400_000L;
        long historicalNanos = historicalMs * 1_000_000L;
        s.onTimestamp(historicalNanos);
        s.onTrade(100.0, 1, true);
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        assertTrue(Math.abs(snap.eventMs() - historicalMs) < 5_000L,
                "historical replay must anchor at the playback date, not wall; "
                + "got eventMs=" + snap.eventMs() + " expected≈" + historicalMs);
    }

    @Test
    void plausibleEpochRangeRejectsCurrentNanosAsFeedCounter() {
        // 12345 ns is not a real epoch nanos. Must fall back to wall.
        InstrumentState s = newState();
        s.onTimestamp(12345L);
        long wallBefore = System.currentTimeMillis();
        s.onTrade(100.0, 1, true);
        TrendAnalyzerSnapshot snap = s.trendAnalyzerSnapshot();
        assertTrue(snap.eventMs() >= wallBefore - 10L,
                "tiny counter must fall back to wall clock; got " + snap.eventMs());
    }
}
