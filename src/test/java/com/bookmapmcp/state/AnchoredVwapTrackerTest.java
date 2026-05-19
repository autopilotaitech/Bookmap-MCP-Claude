package com.bookmapmcp.state;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

import org.junit.jupiter.api.Test;

/**
 * Pins the same-ms accumulation contract: the trade that sets a new anchor
 * is counted exactly once (in the reset), and same-ms trades AFTER the anchor
 * must still be added — the previous timestamp-based skip silently dropped
 * them during fast NQ bursts.
 */
class AnchoredVwapTrackerTest {

    private static final ZoneId CT = ZoneId.of("America/Chicago");
    private static final LocalDate FIXED = LocalDate.of(2025, 6, 12); // Thursday, RTH, no DST edge

    private static long ctMs(int hour, int minute, int second, int milli) {
        return ZonedDateTime.of(
                FIXED,
                LocalTime.of(hour, minute, second, milli * 1_000_000),
                CT
        ).toInstant().toEpochMilli();
    }

    @Test
    void topAnchorSameMsSubsequentTradesCounted() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 0, 0);
        // Anchor top with the highest of the three. They share a timestamp,
        // so the prior bug would have dropped the two follow-on prints.
        t.onTrade(100.0, 5, ts);
        t.onTrade( 99.0, 3, ts);
        t.onTrade( 98.0, 4, ts);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        double expectedNumer = 100.0 * 5 + 99.0 * 3 + 98.0 * 4;
        long   expectedVol   = 5 + 3 + 4;
        assertEquals(expectedNumer / expectedVol, snap.topVwap, 1e-9);
        assertEquals(expectedVol, snap.topVolume);
        assertEquals(ts, snap.topAnchorMs);
    }

    @Test
    void topAnchorNotDoubleCountedOnSameMs() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 0, 0);
        t.onTrade(100.0, 5, ts);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        assertEquals(100.0, snap.topVwap, 1e-9);
        assertEquals(5L, snap.topVolume, "anchor trade must be counted exactly once, not doubled");
    }

    @Test
    void botAnchorSameMsSubsequentTradesCounted() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 0, 0);
        // Order: 100 sets top AND initial low, then 98 lowers the low,
        // then 99 and 97 share the new bot anchor's ms.
        t.onTrade(100.0, 5, ts);
        t.onTrade( 98.0, 3, ts); // re-anchors bot
        t.onTrade( 99.0, 2, ts);
        t.onTrade( 97.0, 4, ts); // re-anchors bot AGAIN at the same ms

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        // Bot accumulator should currently be anchored at the (97, 4) trade,
        // having been reset twice. The most recent reset is the final state.
        assertEquals(97.0, snap.botVwap, 1e-9);
        assertEquals(4L, snap.botVolume);
        assertEquals(ts, snap.botAnchorMs);
    }

    @Test
    void botAnchorSameMsAccumulatesAfterReset() {
        // Variant: anchor bot, then add same-ms follow-ons that DON'T undercut.
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 30, 0);
        t.onTrade(100.0, 5, ts);   // sets top + initial low at 100
        t.onTrade( 98.0, 3, ts);   // re-anchors bot to (98, 3)
        t.onTrade( 99.0, 2, ts);   // same ms, NOT a new low → must accumulate into bot
        t.onTrade( 98.5, 1, ts);   // same ms, NOT a new low → must accumulate into bot

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        double expectedNumer = 98.0 * 3 + 99.0 * 2 + 98.5 * 1;
        long   expectedVol   = 3 + 2 + 1;
        assertEquals(expectedNumer / expectedVol, snap.botVwap, 1e-9);
        assertEquals(expectedVol, snap.botVolume);
    }

    @Test
    void botAnchorNotDoubleCounted() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 0, 0);
        t.onTrade(100.0, 5, ts);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        assertEquals(100.0, snap.botVwap, 1e-9);
        assertEquals(5L, snap.botVolume, "anchor trade must be counted exactly once on bot side too");
    }

    @Test
    void reAnchorTopOnLaterHigherHigh() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long t1 = ctMs(8, 31, 0, 0);
        long t2 = ctMs(8, 31, 30, 0);
        long t3 = ctMs(8, 32, 0, 0);

        t.onTrade(100.0, 5, t1);   // top anchor #1
        t.onTrade( 99.5, 7, t2);   // accumulates into top@100
        t.onTrade(101.0, 4, t3);   // higher high → re-anchors top to t3 with (101,4)

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        assertEquals(t3, snap.topAnchorMs);
        assertEquals(101.0, snap.topVwap, 1e-9);
        assertEquals(4L, snap.topVolume,
                "old accumulator must be discarded; only the new anchor trade is counted once");
    }

    @Test
    void singleTradeResetsBothExtremes() {
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 30, 15, 250);
        t.onTrade(100.0, 5, ts);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        assertEquals(100.0, snap.topVwap, 1e-9);
        assertEquals(5L, snap.topVolume, "first-of-session trade is the high; counted exactly once on top");
        assertEquals(100.0, snap.botVwap, 1e-9);
        assertEquals(5L, snap.botVolume, "first-of-session trade is also the low; counted exactly once on bot");
        assertEquals(ts, snap.topAnchorMs);
        assertEquals(ts, snap.botAnchorMs);
    }

    @Test
    void tradeBeforeAnchorIsExcluded() {
        // After bot re-anchors to a later ms, a back-dated trade whose ms is
        // strictly BEFORE botAnchorMs and which neither sets a new high nor
        // a new low must not feed either accumulator's bot side.
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long tEarly = ctMs(8, 31, 0, 0);
        long tLate  = ctMs(8, 32, 0, 0);

        t.onTrade(100.0, 5, tEarly); // top + bot anchored at tEarly with (100, 5)
        t.onTrade( 99.0, 3, tLate);  // re-anchors bot to tLate with (99, 3)
        // Back-dated print at tEarly, price between current high and low —
        // no re-anchor on either side. Top side: nowMs == topAnchorMs (allowed,
        // must accumulate). Bot side: nowMs < botAnchorMs (must be excluded).
        t.onTrade( 99.5, 2, tEarly);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        // Top: (100*5) + (99*3) + (99.5*2) over 10 contracts (all ms >= topAnchorMs)
        double expectedTopNumer = 100.0 * 5 + 99.0 * 3 + 99.5 * 2;
        assertEquals(expectedTopNumer / 10.0, snap.topVwap, 1e-9);
        assertEquals(10L, snap.topVolume);
        // Bot anchored at tLate, only (99,3) — back-dated print excluded.
        assertEquals(99.0, snap.botVwap, 1e-9);
        assertEquals(3L, snap.botVolume);
        assertEquals(tLate, snap.botAnchorMs);
    }

    @Test
    void driveHighAndLowMatchAnchorTrades() {
        // Cross-check: snapshot.driveHigh / driveLow agree with the anchor trades.
        AnchoredVwapTracker t = new AnchoredVwapTracker();
        long ts = ctMs(8, 31, 10, 0);
        t.onTrade(100.0, 1, ts);
        t.onTrade(102.5, 2, ts);
        t.onTrade( 97.0, 3, ts);

        AnchoredVwapTracker.AnchoredVwapSnapshot snap = t.snapshot();
        assertEquals(102.5, snap.driveHigh, 1e-9);
        assertEquals( 97.0, snap.driveLow, 1e-9);
        assertTrue(snap.topAnchorMs > 0);
        assertTrue(snap.botAnchorMs > 0);
    }
}
