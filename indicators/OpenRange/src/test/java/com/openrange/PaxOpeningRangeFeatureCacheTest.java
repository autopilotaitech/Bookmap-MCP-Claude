package com.openrange;

import java.util.List;

public class PaxOpeningRangeFeatureCacheTest {
    public static void main(String[] args) {
        storesLatestImmutableSnapshot();
        keepsBoundedHistory();
        rejectsInvalidCapacity();
    }

    private static void storesLatestImmutableSnapshot() {
        PaxOpeningRangeFeatureCache cache = new PaxOpeningRangeFeatureCache(3);
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalBias.LONG);
        PaxOpeningRangeMarketState market = new PaxOpeningRangeMarketState(6400.25, 10, 20, 5, 15);

        cache.update(100, market, signal);

        PaxOpeningRangeFeatureSnapshot latest = cache.latest();
        assertEquals(100L, latest.timeNanos(), "time");
        assertSame(signal, latest.signal(), "signal");
        assertSame(market, latest.market(), "market");
        assertEquals(PaxOpeningRangeSignalColorState.BULLISH, latest.colorState(), "color");
        assertContains(latest.badgeText(), "ORH +4t", "badge");
    }

    private static void keepsBoundedHistory() {
        PaxOpeningRangeFeatureCache cache = new PaxOpeningRangeFeatureCache(2);

        cache.update(100, market(1), signal(PaxOpeningRangeSignalBias.LONG));
        cache.update(200, market(2), signal(PaxOpeningRangeSignalBias.SHORT));
        cache.update(300, market(3), signal(PaxOpeningRangeSignalBias.NEUTRAL));

        List<PaxOpeningRangeFeatureSnapshot> history = cache.history();
        assertEquals(2, history.size(), "history size");
        assertEquals(200L, history.get(0).timeNanos(), "oldest retained");
        assertEquals(300L, history.get(1).timeNanos(), "newest retained");

        try {
            history.clear();
            throw new AssertionError("history should be immutable");
        } catch (UnsupportedOperationException expected) {
            // expected
        }
    }

    private static void rejectsInvalidCapacity() {
        try {
            new PaxOpeningRangeFeatureCache(0);
            throw new AssertionError("capacity should be rejected");
        } catch (IllegalArgumentException expected) {
            // expected
        }
    }

    private static PaxOpeningRangeMarketState market(double lastPrice) {
        return new PaxOpeningRangeMarketState(lastPrice, 0, 0, 0, 0);
    }

    private static PaxOpeningRangeSignal signal(PaxOpeningRangeSignalBias bias) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                bias,
                PaxOpeningRangeSignalConfidence.HIGH,
                "test signal",
                1,
                1,
                "test",
                bias == PaxOpeningRangeSignalBias.SHORT ? "ORL" : "ORH",
                bias == PaxOpeningRangeSignalBias.SHORT ? -4 : 4,
                10.0,
                0,
                bias == PaxOpeningRangeSignalBias.SHORT ? -100 : 100,
                bias == PaxOpeningRangeSignalBias.SHORT ? -50 : 50,
                bias == PaxOpeningRangeSignalBias.SHORT ? -1.0 : 1.0,
                bias == PaxOpeningRangeSignalBias.SHORT ? -1.0 : 1.0);
    }

    private static void assertContains(String actual, String expected, String message) {
        if (!actual.contains(expected)) {
            throw new AssertionError(message + ": expected \"" + actual + "\" to contain \"" + expected + "\"");
        }
    }

    private static void assertSame(Object expected, Object actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected same instance");
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
