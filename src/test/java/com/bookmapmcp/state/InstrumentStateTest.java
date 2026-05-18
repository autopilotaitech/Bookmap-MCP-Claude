package com.bookmapmcp.state;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.List;

import org.junit.jupiter.api.Test;

class InstrumentStateTest {

    private InstrumentState newState() {
        return new InstrumentState("NQ-CME", "NQ", "E-mini Nasdaq", 0.25, 20, Instant.now());
    }

    @Test
    void depthBuildsBothSidesAndComputesBboMidSpread() {
        InstrumentState s = newState();
        s.onDepth(true, 100, 5);
        s.onDepth(true, 101, 8);
        s.onDepth(true, 102, 3);
        s.onDepth(false, 104, 4);
        s.onDepth(false, 105, 7);
        s.onDepth(false, 106, 2);

        OrderbookSnapshot snap = s.orderbookSnapshot(2);
        assertEquals(2, snap.bids().size());
        assertEquals(2, snap.asks().size());
        assertEquals(25.5, snap.bids().get(0).price(), 1e-9);
        assertEquals(3, snap.bids().get(0).size());
        assertEquals(26.0, snap.asks().get(0).price(), 1e-9);
        assertEquals(4, snap.asks().get(0).size());
        assertEquals(25.5, snap.bestBid(), 1e-9);
        assertEquals(26.0, snap.bestAsk(), 1e-9);
        assertEquals(25.75, snap.mid(), 1e-9);
        assertEquals(0.5, snap.spread(), 1e-9);
    }

    @Test
    void depthSizeZeroRemovesLevel() {
        InstrumentState s = newState();
        s.onDepth(true, 100, 5);
        s.onDepth(true, 101, 5);
        s.onDepth(true, 101, 0);
        OrderbookSnapshot snap = s.orderbookSnapshot(10);
        assertEquals(1, snap.bids().size());
        assertEquals(25.0, snap.bids().get(0).price(), 1e-9);
    }

    @Test
    void recentTradesAreNewestFirstAndBoundedByCapacity() {
        InstrumentState s = newState();
        // pips=0.25, onTrade(rawTicks,..) stores rawTicks*0.25
        for (int i = 0; i < 10; i++) {
            s.onTrade(100 + i, 1, i % 2 == 0);
        }
        List<TradeRecord> last3 = s.recentTradesSnapshot(3);
        assertEquals(3, last3.size());
        assertEquals(27.25, last3.get(0).price(), 1e-9);
        assertEquals(27.0,  last3.get(1).price(), 1e-9);
        assertEquals(26.75, last3.get(2).price(), 1e-9);
    }

    @Test
    void recentTradesCappedAtCapacity() {
        InstrumentState s = newState();
        int over = InstrumentState.TRADES_CAPACITY + 10;
        for (int i = 0; i < over; i++) {
            s.onTrade(100 + i, 1, false);
        }
        List<TradeRecord> all = s.recentTradesSnapshot(InstrumentState.TRADES_CAPACITY + 100);
        assertEquals(InstrumentState.TRADES_CAPACITY, all.size());
        assertEquals((100.0 + over - 1) * 0.25, all.get(0).price(), 1e-9);
    }

    @Test
    void tradeSideIsBidAggressorMappedToSellAggressorLabel() {
        InstrumentState s = newState();
        s.onTrade(101.5, 2, true);
        s.onTrade(101.75, 1, false);
        List<TradeRecord> trades = s.recentTradesSnapshot(2);
        assertTrue(trades.get(1).bidAggressor());
        assertTrue(!trades.get(0).bidAggressor());
    }

    @Test
    void onTradeScalesTickPriceByPips() {
        // Bookmap onTrade delivers TICKS as double. pips=0.25, raw tick 115540 → 28885.
        InstrumentState s = newState();
        s.onTrade(115540.0, 1, false);
        assertEquals(28885.0, s.lastTradePrice(), 1e-9);
        assertEquals(28885.0, s.recentTradesSnapshot(1).get(0).price(), 1e-9);
    }

    // --- VWAP ---

    private static long nanosAt(ZonedDateTime t) {
        return t.toInstant().toEpochMilli() * 1_000_000L;
    }

    private static ZonedDateTime sessionOpenNyOn(LocalDate d) {
        return d.atTime(LocalTime.of(9, 30)).atZone(ZoneId.of("America/New_York"));
    }

    @Test
    void vwapStartsEmpty() {
        InstrumentState s = newState();
        VwapSnapshot v = s.vwapSnapshot();
        assertEquals(0L, v.samples());
        assertTrue(Double.isNaN(v.vwap()));
        assertTrue(Double.isNaN(v.upper1()));
    }

    @Test
    void vwapEqualsMeanWhenAllSizesEqualAndPricesEqual() {
        InstrumentState s = newState();
        ZonedDateTime t = sessionOpenNyOn(LocalDate.of(2026, 5, 15)).plusMinutes(5);
        s.accumulateVwap(100.0, 1, nanosAt(t));
        s.accumulateVwap(100.0, 1, nanosAt(t.plusSeconds(1)));
        s.accumulateVwap(100.0, 1, nanosAt(t.plusSeconds(2)));
        VwapSnapshot v = s.vwapSnapshot();
        assertEquals(3L, v.samples());
        assertEquals(100.0, v.vwap(), 1e-9);
        assertEquals(0.0, v.stddev(), 1e-9);
    }

    @Test
    void vwapWeightsBySize() {
        InstrumentState s = newState();
        ZonedDateTime t = sessionOpenNyOn(LocalDate.of(2026, 5, 15)).plusMinutes(5);
        s.accumulateVwap(100.0, 10, nanosAt(t));
        s.accumulateVwap(110.0, 90, nanosAt(t.plusSeconds(1)));
        VwapSnapshot v = s.vwapSnapshot();
        assertEquals(109.0, v.vwap(), 1e-9);
        assertEquals(3.0, v.stddev(), 1e-9);
        assertEquals(112.0, v.upper1(), 1e-9);
        assertEquals(106.0, v.lower1(), 1e-9);
        assertEquals(115.0, v.upper2(), 1e-9);
        assertEquals(118.0, v.upper3(), 1e-9);
    }

    @Test
    void vwapResetsAcrossSessionBoundary() {
        InstrumentState s = newState();
        ZonedDateTime day1 = sessionOpenNyOn(LocalDate.of(2026, 5, 15)).plusMinutes(5);
        s.accumulateVwap(100.0, 10, nanosAt(day1));
        s.accumulateVwap(200.0, 10, nanosAt(day1.plusSeconds(1)));
        long firstStart = s.vwapSnapshot().sessionStartMs();
        assertEquals(150.0, s.vwapSnapshot().vwap(), 1e-9);

        ZonedDateTime day2 = sessionOpenNyOn(LocalDate.of(2026, 5, 16)).plusMinutes(5);
        s.accumulateVwap(50.0, 1, nanosAt(day2));
        VwapSnapshot v = s.vwapSnapshot();
        assertEquals(1L, v.samples());
        assertEquals(50.0, v.vwap(), 1e-9);
        assertNotEquals(firstStart, v.sessionStartMs());
    }

    @Test
    void vwapIgnoresBadInputs() {
        InstrumentState s = newState();
        ZonedDateTime t = sessionOpenNyOn(LocalDate.of(2026, 5, 15)).plusMinutes(5);
        s.accumulateVwap(Double.NaN, 1, nanosAt(t));
        s.accumulateVwap(100.0, 0, nanosAt(t));
        s.accumulateVwap(100.0, -5, nanosAt(t));
        assertEquals(0L, s.vwapSnapshot().samples());
    }

    // --- Aggressor-side semantics (Bookmap TradeInfo.isBidAggressor) ---
    // Spec: isBidAggressor == true means the BID was the aggressor (buy aggressor /
    // lifted offer); false means SELL aggressor (hit bid). The tests below pin every
    // direct downstream consumer so the inversion cannot regress silently.

    @Test
    void momentumBuyAggressorIsCountedAsBuy() {
        InstrumentState s = newState();
        // momentumSnapshot uses TradeRecord.nanos() against a horizon derived from
        // lastSeenNanos; both must be > 0 for the trades to fall inside the window.
        s.onTimestamp(System.nanoTime());
        for (int i = 0; i < 5; i++) s.onTrade(100, 3, true);   // buy aggressors
        for (int i = 0; i < 2; i++) s.onTrade(100, 4, false);  // sell aggressors
        // Refresh lastSeenNanos so the window's "now" is past the trade timestamps.
        s.onTimestamp(System.nanoTime());
        MomentumSnapshot mo = s.momentumSnapshot(new int[]{600});
        MomentumSnapshot.Window w = mo.windows.get(0);
        assertEquals(15L, w.buyVolume,  "buy aggressor (isBidAggressor=true) must accumulate to buyVolume");
        assertEquals(8L,  w.sellVolume, "sell aggressor (isBidAggressor=false) must accumulate to sellVolume");
    }

    @Test
    void volumeProfileBuyAggressorPopulatesBuyVolume() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        s.onTrade(100, 7, true);   // buy aggressor
        s.onTrade(100, 4, false);  // sell aggressor at same price
        VolumeProfileSnapshot vp = s.volumeProfileEthSnapshot();
        assertEquals(1, vp.levels.size(), "single price should produce one VP level");
        VolumeProfileSnapshot.Level l = vp.levels.get(0);
        assertEquals(7L, l.buyVolume,  "buy aggressor must populate buyVolume");
        assertEquals(4L, l.sellVolume, "sell aggressor must populate sellVolume");
    }

    @Test
    void tapeBucketsBuyAggressorPopulatesBuyVolume() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        for (int i = 0; i < 5; i++) s.onTrade(100, 3, true);   // buy aggressors, sizes inside 1-10 bucket
        for (int i = 0; i < 2; i++) s.onTrade(100, 4, false);  // sell aggressors, same bucket
        s.onTimestamp(System.nanoTime());
        TapeBucketsSnapshot tb = s.tapeBucketsSnapshot();
        TapeBucketsSnapshot.Bucket b = tb.buckets.get(0);
        assertEquals("1-10", b.label);
        assertEquals(15L, b.buyVol5m,  "buy aggressor must accumulate to buyVol5m");
        assertEquals(8L,  b.sellVol5m, "sell aggressor must accumulate to sellVol5m");
    }

    @Test
    void recentTradesPreserveAggressorFlag() {
        // The handler's JSON ternary is covered by RecentTradesHandler at runtime;
        // here we verify the underlying TradeRecord boolean lines up with the input.
        InstrumentState s = newState();
        s.onTrade(101.5,  2, true);   // buy aggressor
        s.onTrade(101.75, 1, false);  // sell aggressor
        List<TradeRecord> trades = s.recentTradesSnapshot(2);
        // recentTradesSnapshot returns newest-first
        assertFalse(trades.get(0).bidAggressor(), "newest trade was sell aggressor");
        assertTrue(trades.get(1).bidAggressor(),  "second-newest trade was buy aggressor");
    }

    // --- Stop-sweep side emission (canonical: isBid=true means BIDS swept = bearish) ---

    @Test
    void stopSweepIsBidIsTrueWhenSellersSweepBids() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        // pips=0.25, raw tick 100 → display price 25.0. Magnet must lie inside
        // the 5s window's [minPx,maxPx] range for the sweep to fire.
        s.setMagnetLevels(new double[]{25.0});
        // detectStopSweep's adaptive threshold = max(STOP_FLOOR=80, μ+2σ). On the
        // first sample μ=σ=0 so a single trade of size > 80 trips the detector
        // before the EWMA catches up.
        s.onTrade(100, 200, false);   // false = sell aggressor (hit bid)
        MicrostructureEvent sweep = s.microstructureEvents(50).stream()
                .filter(e -> e.kind == MicrostructureEvent.Kind.STOP_SWEEP)
                .findFirst()
                .orElseThrow(() -> new AssertionError("STOP_SWEEP did not fire"));
        assertTrue(sweep.isBid, "sell-aggressor sweep should emit isBid=true (bids swept = bearish)");
    }

    @Test
    void stopSweepIsBidIsFalseWhenBuyersSweepAsks() {
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        s.setMagnetLevels(new double[]{25.0});
        s.onTrade(100, 200, true);    // true = buy aggressor (lifted offer)
        MicrostructureEvent sweep = s.microstructureEvents(50).stream()
                .filter(e -> e.kind == MicrostructureEvent.Kind.STOP_SWEEP)
                .findFirst()
                .orElseThrow(() -> new AssertionError("STOP_SWEEP did not fire"));
        assertFalse(sweep.isBid, "buy-aggressor sweep should emit isBid=false (asks swept = bullish)");
    }

    // --- MBO replace price-move semantics ---

    @Test
    void mboReplacePriceMoveEmitsPullAtOldAndStackAtNew() {
        // Send a 10-lot bid at tick 100, then move it to tick 99 keeping size.
        // BookDynamics should show a SEND-credited stack at tick 100, a REPLACE-
        // negative (pull) at tick 100, and a REPLACE-positive (stack) at tick 99 —
        // i.e. the move is modeled as a full pull + a full stack, not a delta.
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        s.onMboSend("X1", true, 100, 10);
        s.onMboReplace("X1", 99, 10);
        BookDynamicsSnapshot bd = s.bookDynamicsSnapshot(64);
        long stackedAtNew = 0, pulledAtOld = 0, stackedAtOld = 0;
        double pips = 0.25;  // matches newState() factory
        for (BookDynamicsSnapshot.Level l : bd.topActiveLevels) {
            if (!l.isBid) continue;
            if (Math.abs(l.price - 99 * pips)  < 1e-9) stackedAtNew = l.stacked1m;
            if (Math.abs(l.price - 100 * pips) < 1e-9) {
                pulledAtOld  = l.pulled1m;
                stackedAtOld = l.stacked1m;
            }
        }
        assertEquals(10L, stackedAtNew, "new level should be credited as a full stack");
        assertEquals(10L, pulledAtOld,  "old level should be credited as a full pull");
        assertEquals(10L, stackedAtOld, "original SEND stack at old tick must NOT be double-counted");
    }

    @Test
    void mboReplaceSizeOnlyPreservesSizeDeltaSemantics() {
        // Same-price replace: a size increase emits a positive REPLACE delta
        // (stack contribution = delta); a size decrease emits a negative
        // REPLACE delta (pull contribution = delta). Pull/stack stays additive.
        InstrumentState s = newState();
        s.onTimestamp(System.nanoTime());
        s.onMboSend("Y1", false, 200, 20);   // ask, size 20
        s.onMboReplace("Y1", 200, 25);       // grow to 25 → +5 stack
        s.onMboReplace("Y1", 200, 22);       // shrink to 22 → -3 pull
        BookDynamicsSnapshot bd = s.bookDynamicsSnapshot(64);
        long stacked = 0, pulled = 0;
        double pips = 0.25;
        for (BookDynamicsSnapshot.Level l : bd.topActiveLevels) {
            if (l.isBid) continue;
            if (Math.abs(l.price - 200 * pips) < 1e-9) {
                stacked = l.stacked1m;
                pulled  = l.pulled1m;
            }
        }
        // SEND(20) + REPLACE(+5) = 25 stacked; REPLACE(-3) = 3 pulled.
        assertEquals(25L, stacked, "same-price grow should add size delta to stacked");
        assertEquals(3L,  pulled,  "same-price shrink should add size delta to pulled");
    }
}
