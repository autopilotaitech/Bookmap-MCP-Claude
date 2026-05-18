package com.openrange;

import java.time.LocalDate;

public class PaxOpeningRangeCrossMarketTest {
    private static final LocalDate DAY1 = LocalDate.of(2026, 5, 10);
    private static final LocalDate DAY2 = LocalDate.of(2026, 5, 11);

    public static void main(String[] args) {
        confirmsWhenRelatedMarketBreaksSameSide();
        divergesWhenRelatedMarketBreaksOppositeSide();
        staysUnknownWhenRelatedMarketIsInside();
        staleSignalIsTreatedAsUnknown();
        priorDaySignalIsTreatedAsUnknown();
        freshOppositeBiasStillDiverges();
        freshAlignedBiasConfirms();
        removeClearsRootForSameSymbol();
        removeDoesNotClearWhenSymbolDiffers();
        System.out.println("PaxOpeningRangeCrossMarketTest: all tests passed");
    }

    private static void confirmsWhenRelatedMarketBreaksSameSide() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);

        assertEquals(PaxOpeningRangeCrossMarketStatus.CONFIRM,
                state.statusFor("ESM6", 0L, DAY1), "status");
    }

    private static void divergesWhenRelatedMarketBreaksOppositeSide() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORL", PaxOpeningRangeSignalBias.SHORT), 0L, DAY1);

        assertEquals(PaxOpeningRangeCrossMarketStatus.DIVERGE,
                state.statusFor("ESM6", 0L, DAY1), "status");
    }

    private static void staysUnknownWhenRelatedMarketIsInside() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("INSIDE", PaxOpeningRangeSignalBias.NEUTRAL), 0L, DAY1);

        assertEquals(PaxOpeningRangeCrossMarketStatus.UNKNOWN,
                state.statusFor("ESM6", 0L, DAY1), "status");
    }

    private static void staleSignalIsTreatedAsUnknown() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);

        // Query 120 seconds later — both entries are stale.
        long later = 120L * 1_000_000_000L;
        assertEquals(PaxOpeningRangeCrossMarketStatus.UNKNOWN,
                state.statusFor("ESM6", later, DAY1), "stale -> UNKNOWN");
    }

    private static void priorDaySignalIsTreatedAsUnknown() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        // Inserted on DAY1, very recent timestamps (within stale window).
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);

        // Query 5 seconds later but on DAY2 — date mismatch makes them UNKNOWN.
        long fiveSeconds = 5L * 1_000_000_000L;
        assertEquals(PaxOpeningRangeCrossMarketStatus.UNKNOWN,
                state.statusFor("ESM6", fiveSeconds, DAY2), "prior-day -> UNKNOWN");
    }

    private static void freshOppositeBiasStillDiverges() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        long t = 1_000_000_000L;
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), t, DAY1);
        state.update("NQM6", signal("ORL", PaxOpeningRangeSignalBias.SHORT), t, DAY1);

        assertEquals(PaxOpeningRangeCrossMarketStatus.DIVERGE,
                state.statusFor("ESM6", t, DAY1), "fresh opposite -> DIVERGE");
    }

    private static void freshAlignedBiasConfirms() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        long t = 1_000_000_000L;
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), t, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), t, DAY1);

        assertEquals(PaxOpeningRangeCrossMarketStatus.CONFIRM,
                state.statusFor("ESM6", t, DAY1), "fresh aligned -> CONFIRM");
    }

    private static void removeClearsRootForSameSymbol() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        state.update("ESM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);

        state.remove("ESM6");

        // ES entry gone -> current side absent -> UNKNOWN.
        assertEquals(PaxOpeningRangeCrossMarketStatus.UNKNOWN,
                state.statusFor("ESM6", 0L, DAY1), "removed ESM6 -> UNKNOWN");
    }

    private static void removeDoesNotClearWhenSymbolDiffers() {
        PaxOpeningRangeCrossMarketState state = new PaxOpeningRangeCrossMarketState();
        // ESH5 occupies the ES root slot.
        state.update("ESH5", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);
        state.update("NQM6", signal("ORH", PaxOpeningRangeSignalBias.LONG), 0L, DAY1);

        // Removing a different alias that maps to the same ES root must not erase ESH5.
        state.remove("ESM5");

        assertEquals(PaxOpeningRangeCrossMarketStatus.CONFIRM,
                state.statusFor("ESH5", 0L, DAY1), "non-matching remove preserves entry");
    }

    private static PaxOpeningRangeSignal signal(String location, PaxOpeningRangeSignalBias bias) {
        return new PaxOpeningRangeSignal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, bias,
                PaxOpeningRangeSignalConfidence.HIGH, "", 4, 4, "", location, 4, 10.0, 0, 1, 1, 1, 1);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
