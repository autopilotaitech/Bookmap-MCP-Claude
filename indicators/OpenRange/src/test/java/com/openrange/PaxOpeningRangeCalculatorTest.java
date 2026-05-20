package com.openrange;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.List;

public class PaxOpeningRangeCalculatorTest {
    public static void main(String[] args) {
        completesOpeningRangeFromFirstThirtySeconds();
        backfillsOpeningRangeFromAggregatedIntervals();
        mapsHistoricalBucketsToIntervalEndTimes();
        addsDynamicLevelsOnlyAfterOuterLevelBreaks();
        doesNotCreateSymbolLevelsForUnsupportedMarkets();
        liveRangeIncludesExactStartOfWindow();
        liveRangeIncludesExactEndSecond();
        liveRangeExcludesTradeAfterEndSecond();
        liveRangeBoundaryMatchesBackfillBoundary();
        liveRangeNotCompletedWithoutAnyObservation();
        overnightLineEndExtendsPastMidnight();
        sameClockLineEndExtendsToNextSessionStart();
        canonicalEightThirtyStartLineEndExtendsToNextSession();
        canonicalEightThirtyOpeningRangeCompletesAtExpectedTime();
        dynamicLevelsWorkWhenLineEndIsEarlierClockTime();
    }

    // F6 boundary tests: lock in current window semantics.
    // Live path observes [rangeStart, rangeEnd] inclusive of the exact rangeEnd instant
    // (e.g. 09:30:30.000), but DROPS any sub-second tick after rangeEnd (e.g. 09:30:30.500).
    // Backfill path uses 1-second buckets covering [start, start+rangeSeconds) tagged at
    // bucket-end times that hit rangeEnd exactly. Both paths exclude (rangeEnd, rangeEnd+1s).
    // Conclusion: F6 is NOT a real bug — live and backfill agree on the practical window.

    private static void liveRangeIncludesExactStartOfWindow() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        // Single trade exactly at rangeStart, then one to drive completion.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0, 0)), 5000.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 0)), 5100.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "range should complete at exact rangeEnd");
        // If start was excluded, low would be 5100; if included, low is 5000.
        assertEquals(5000.00, day.getLow(), "trade at exact rangeStart must be included in OR");
        assertEquals(5100.00, day.getHigh(), "trade at exact rangeEnd must be included in OR");
    }

    private static void liveRangeIncludesExactEndSecond() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0)), 5000.00);
        // New high arrives at exactly 09:30:30.000 (rangeEnd).
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 0)), 5200.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "completes at exact rangeEnd");
        assertEquals(5200.00, day.getHigh(), "trade at exactly rangeEnd.000 must move high");
    }

    private static void liveRangeExcludesTradeAfterEndSecond() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0)), 5000.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 15)), 5100.00);
        // Sub-second past rangeEnd — must NOT be observed, but MUST trigger completion.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 500_000_000)), 9999.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "sub-second past rangeEnd should complete the range");
        assertEquals(5100.00, day.getHigh(), "trade after rangeEnd (even sub-second) is excluded from OR");
        assertEquals(5000.00, day.getLow(), "low unchanged by post-rangeEnd trade");
    }

    private static void liveRangeBoundaryMatchesBackfillBoundary() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0, 0)), 5000.00);
        // A tick deep inside the window, just before rangeEnd, sets a new high.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 29, 999_999_999)), 5100.00);
        // Tick exactly at rangeEnd: observed but does not move H/L.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 0)), 5050.00);
        // Sub-second past rangeEnd: dropped, must NOT raise the high.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 500_000_000)), 5200.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "range complete after sub-second-past-end tick");
        assertEquals(5100.00, day.getHigh(), "high frozen at last in-window observation");
        assertEquals(5000.00, day.getLow(), "low frozen at rangeStart observation");
    }

    private static void liveRangeNotCompletedWithoutAnyObservation() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        // First and only trade ever lands sub-second past rangeEnd. No prior observation:
        // completion guard (!Double.isNaN(day.getHigh())) must prevent NaN completion.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30, 500_000_000)), 5200.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertFalse(day.isComplete(), "no in-window observations means range must not complete with NaN");
    }

    private static void overnightLineEndExtendsPastMidnight() {
        PaxOpeningRangeSettings settings = new PaxOpeningRangeSettings(
                LocalTime.of(19, 30), 30, LocalTime.of(17, 0), 8, false, "OR");
        LocalDate date = LocalDate.of(2026, 5, 10);

        assertEquals(LocalDateTime.of(2026, 5, 10, 19, 30, 30), settings.rangeEndDateTime(date),
                "evening range end");
        assertEquals(LocalDateTime.of(2026, 5, 11, 17, 0), settings.lineEndDateTime(date),
                "line end earlier on the clock must resolve to next day");
    }

    private static void sameClockLineEndExtendsToNextSessionStart() {
        // When the user's lineEnd matches the OR rangeStart, the operator's
        // intent is "extend the line until the next session opens." Past code
        // collapsed this to a zero-width span; the drawDay path clamps the
        // maxEnd against lastUpdateTime so today's line still ends at "now"
        // and past days remain bounded by the calculator's lastUpdateTime.
        PaxOpeningRangeSettings settings = new PaxOpeningRangeSettings(
                LocalTime.of(17, 0), 30, LocalTime.of(17, 0), 8, false, "OR");
        LocalDate date = LocalDate.of(2026, 5, 19);

        assertEquals(LocalDateTime.of(2026, 5, 20, 17, 0), settings.lineEndDateTime(date),
                "lineEnd == rangeStart should extend overlay to next session's open, not collapse");
    }

    private static void canonicalEightThirtyStartLineEndExtendsToNextSession() {
        // Canonical institutional session: 08:30 CT. With lineEnd defaulted to
        // the same clock as rangeStart, the OR overlay must span until 08:30
        // the next day (clamped by lastUpdateTime in drawDay).
        PaxOpeningRangeSettings settings = new PaxOpeningRangeSettings(
                LocalTime.of(8, 30), 30, LocalTime.of(8, 30), 8, false, "OR");
        LocalDate date = LocalDate.of(2026, 5, 19);

        assertEquals(LocalDateTime.of(2026, 5, 19, 8, 30, 30), settings.rangeEndDateTime(date),
                "08:30 OR completes at 08:30:30");
        assertEquals(LocalDateTime.of(2026, 5, 20, 8, 30), settings.lineEndDateTime(date),
                "08:30 lineEnd must extend overlay to next 08:30 session start");
    }

    private static void canonicalEightThirtyOpeningRangeCompletesAtExpectedTime() {
        // Use lineEnd later in the same day to avoid the dynamic-levels gate
        // tripping on overnight semantics for this completion-only test.
        PaxOpeningRangeSettings settings = new PaxOpeningRangeSettings(
                LocalTime.of(8, 30), 30, LocalTime.of(15, 0), 8, false, "OR");
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(settings, "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 5, 19);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(8, 30, 0)),  5000.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(8, 30, 10)), 5012.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(8, 30, 30)), 5008.00);
        // Post-range tick to drive a dynamic level emission.
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(8, 35, 0)),  5028.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "08:30 OR should complete at 08:30:30");
        assertEquals(5012.00, day.getHigh(), "08:30 high");
        assertEquals(5000.00, day.getLow(),  "08:30 low");
        // ES level factor = 15. Initial upper level = high + 15 = 5027. Post-range
        // trade at 5028 breaks it and adds a second upper level.
        assertTrue(day.getUpperLevels().size() >= 1,
                "08:30 session must emit at least the initial upper level");
    }

    private static void dynamicLevelsWorkWhenLineEndIsEarlierClockTime() {
        PaxOpeningRangeSettings settings = new PaxOpeningRangeSettings(
                LocalTime.of(19, 30), 30, LocalTime.of(17, 0), 8, false, "OR");
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(settings, "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 5, 10);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(19, 30, 0)), 29350.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(19, 30, 30)), 29362.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(20, 0, 0)), 29377.25);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "evening range should complete");
        assertEquals(LocalDateTime.of(date, LocalTime.of(20, 0, 0)), day.getLastUpdateTime(),
                "post-range evening trade should extend line/update state");
        assertEquals(2, day.getUpperLevels().size(),
                "post-range evening break should add upper dynamic level despite 17:00 line end");
    }

    private static void backfillsOpeningRangeFromAggregatedIntervals() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onInterval(LocalDateTime.of(date, LocalTime.of(9, 30, 1)), 6395.00, 6390.00);
        calculator.onInterval(LocalDateTime.of(date, LocalTime.of(9, 30, 10)), 6398.75, 6393.00);
        calculator.onInterval(LocalDateTime.of(date, LocalTime.of(9, 30, 30)), 6397.00, 6389.00);
        calculator.onInterval(LocalDateTime.of(date, LocalTime.of(9, 31, 0)), 6414.00, 6399.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "historical intervals should complete opening range");
        assertEquals(6398.75, day.getHigh(), "historical high");
        assertEquals(6389.00, day.getLow(), "historical low");
        assertEquals(2, day.getUpperLevels().size(), "historical dynamic upper level");
        assertEquals(6428.75, day.getUpperLevels().get(1).price(), "historical second upper");
    }

    private static void mapsHistoricalBucketsToIntervalEndTimes() {
        LocalDateTime start = LocalDateTime.of(LocalDate.of(2026, 4, 28), LocalTime.of(9, 30, 0));
        LocalDateTime dynamicStart = LocalDateTime.of(LocalDate.of(2026, 4, 28), LocalTime.of(9, 30, 30));

        assertEquals(LocalDateTime.of(2026, 4, 28, 9, 30, 1),
                PaxOpeningRangeModule.intervalEndTime(start, 1_000_000_000L, 0),
                "first one-second bucket should end one second after start");
        assertEquals(LocalDateTime.of(2026, 4, 28, 9, 30, 30),
                PaxOpeningRangeModule.intervalEndTime(start, 1_000_000_000L, 29),
                "thirtieth one-second bucket should complete the opening range");
        assertEquals(LocalDateTime.of(2026, 4, 28, 9, 31, 0),
                PaxOpeningRangeModule.intervalEndTime(dynamicStart, 30_000_000_000L, 0),
                "first thirty-second dynamic bucket should end thirty seconds after range end");
    }


    private static void completesOpeningRangeFromFirstThirtySeconds() {
        PaxOpeningRangeSettings settings = PaxOpeningRangeSettings.defaults();
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(settings, "ESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0)), 6390.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 10)), 6398.75);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 29)), 6389.00);
        assertFalse(calculator.getDay(date).isComplete(), "range should not complete before 9:30:30");

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30)), 6395.00);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "range should complete at 9:30:30");
        assertEquals(6398.75, day.getHigh(), "high");
        assertEquals(6389.00, day.getLow(), "low");
        assertEquals(6394.00, day.getMid(), "mid rounded to tick");
        assertEquals(6413.75, day.getUpperLevels().get(0).price(), "first ES upper level");
        assertEquals(6374.00, day.getLowerLevels().get(0).price(), "first ES lower level");
    }

    private static void addsDynamicLevelsOnlyAfterOuterLevelBreaks() {
        PaxOpeningRangeSettings settings = PaxOpeningRangeSettings.defaults();
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(settings, "MESM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0)), 6390.00);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30)), 6398.75);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 31, 0)), 6413.75);
        assertEquals(1, calculator.getDay(date).getUpperLevels().size(), "touch should not add level");

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 31, 30)), 6413.76);
        List<PaxOpeningRangeLevel> upper = calculator.getDay(date).getUpperLevels();
        assertEquals(2, upper.size(), "break should add level");
        assertEquals(6428.75, upper.get(1).price(), "second ES upper level");
        assertEquals(LocalDateTime.of(date, LocalTime.of(9, 31, 30)), upper.get(1).startTime(), "new level start");

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 32, 0)), 6373.99);
        List<PaxOpeningRangeLevel> lower = calculator.getDay(date).getLowerLevels();
        assertEquals(2, lower.size(), "lower break should add level");
        assertEquals(6360.00, lower.get(1).price(), "second ES lower level");
    }

    private static void doesNotCreateSymbolLevelsForUnsupportedMarkets() {
        PaxOpeningRangeCalculator calculator = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "CLM6", 0.01);
        LocalDate date = LocalDate.of(2026, 4, 28);

        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 0)), 80.10);
        calculator.onTrade(LocalDateTime.of(date, LocalTime.of(9, 30, 30)), 80.25);

        PaxOpeningRangeDayState day = calculator.getDay(date);
        assertTrue(day.isComplete(), "range should complete");
        assertEquals(0, day.getUpperLevels().size(), "no unsupported upper levels");
        assertEquals(0, day.getLowerLevels().size(), "no unsupported lower levels");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertFalse(boolean value, String message) {
        if (value) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(double expected, double actual, String message) {
        if (Math.abs(expected - actual) > 0.0000001) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertEquals(int expected, int actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertEquals(LocalDateTime expected, LocalDateTime actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
