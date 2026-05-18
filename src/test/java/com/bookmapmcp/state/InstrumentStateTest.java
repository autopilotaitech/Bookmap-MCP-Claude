package com.bookmapmcp.state;

import static org.junit.jupiter.api.Assertions.assertEquals;
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
}
