package com.openrange;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;

public class PaxOpeningRangeSignalEngineTest {
    public static void main(String[] args) {
        blocksSignalsBeforeOpeningRangeCompletes();
        blocksSignalsInsideOpeningRange();
        allowsLongBreakoutWhenOrderFlowConfirms();
        blocksLongBreakoutWhenOrderFlowDisagrees();
        allowsShortBreakoutWhenOrderFlowConfirms();
        blocksWhenMinimumScoreIsNotMet();
        blocksWhenBreakoutIsTooCloseToRange();
        blocksWhenBreakoutIsTooExtended();
    }

    private static void blocksSignalsBeforeOpeningRangeCompletes() {
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine();
        PaxOpeningRangeDayState day = new PaxOpeningRangeDayState(LocalDate.of(2026, 4, 28));

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6401.00, 10, 300, -400, 800));

        assertEquals(PaxOpeningRangeSignalAction.WAIT, signal.action(), "action");
        assertEquals(PaxOpeningRangeSignalBias.NEUTRAL, signal.bias(), "bias");
        assertContains(signal.reason(), "Opening range is not complete", "reason");
    }

    private static void blocksSignalsInsideOpeningRange() {
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine();
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6395.00, 10, 400, -400, 900));

        assertEquals(PaxOpeningRangeSignalAction.WAIT, signal.action(), "action");
        assertEquals(PaxOpeningRangeSignalBias.NEUTRAL, signal.bias(), "bias");
        assertContains(signal.reason(), "inside opening range", "reason");
    }

    private static void allowsLongBreakoutWhenOrderFlowConfirms() {
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine();
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6401.00, 25, 550, -700, 1250));

        assertEquals(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, signal.action(), "action");
        assertEquals(PaxOpeningRangeSignalBias.LONG, signal.bias(), "bias");
        assertEquals(PaxOpeningRangeSignalConfidence.HIGH, signal.confidence(), "confidence");
        assertContains(signal.reason(), "above ORH", "reason");
    }

    private static void blocksLongBreakoutWhenOrderFlowDisagrees() {
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine();
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6401.00, -15, -500, 600, -1100));

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, signal.action(), "action");
        assertEquals(PaxOpeningRangeSignalBias.LONG, signal.bias(), "bias");
        assertContains(signal.reason(), "needs 3/4", "reason");
    }

    private static void allowsShortBreakoutWhenOrderFlowConfirms() {
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine();
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6388.75, -30, -700, 650, -1350));

        assertEquals(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, signal.action(), "action");
        assertEquals(PaxOpeningRangeSignalBias.SHORT, signal.bias(), "bias");
        assertEquals(PaxOpeningRangeSignalConfidence.HIGH, signal.confidence(), "confidence");
        assertContains(signal.reason(), "below ORL", "reason");
    }

    private static void blocksWhenMinimumScoreIsNotMet() {
        PaxOpeningRangeSignalSettings settings = new PaxOpeningRangeSignalSettings(1, 1, 10, 0, 0, 4);
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine(settings, 0.25);
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6401.00, 25, 550, 0, 550));

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, signal.action(), "action");
        assertEquals(3, signal.score(), "score");
        assertContains(signal.reason(), "needs 4/4", "reason");
    }

    private static void blocksWhenBreakoutIsTooCloseToRange() {
        PaxOpeningRangeSignalSettings settings = new PaxOpeningRangeSignalSettings(1, 1, 10, 2, 0, 3);
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine(settings, 0.25);
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6400.25, 25, 550, -700, 1250));

        assertEquals(PaxOpeningRangeSignalAction.WAIT, signal.action(), "action");
        assertContains(signal.reason(), "less than 2 ticks", "reason");
    }

    private static void blocksWhenBreakoutIsTooExtended() {
        PaxOpeningRangeSignalSettings settings = new PaxOpeningRangeSignalSettings(1, 1, 10, 0, 4, 3);
        PaxOpeningRangeSignalEngine engine = new PaxOpeningRangeSignalEngine(settings, 0.25);
        PaxOpeningRangeDayState day = completedDay(6400.00, 6390.00);

        PaxOpeningRangeSignal signal = engine.evaluate(day, market(6401.25, 25, 550, -700, 1250));

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, signal.action(), "action");
        assertContains(signal.reason(), "more than 4 ticks", "reason");
    }

    private static PaxOpeningRangeDayState completedDay(double high, double low) {
        PaxOpeningRangeDayState day = new PaxOpeningRangeDayState(LocalDate.of(2026, 4, 28));
        day.setComplete(high, low, low + ((high - low) * 0.5),
                LocalDateTime.of(day.getDate(), LocalTime.of(9, 30, 30)));
        return day;
    }

    private static PaxOpeningRangeMarketState market(double price, double cvdDelta,
            double bidDepthDelta, double askDepthDelta, double depthDelta) {
        return new PaxOpeningRangeMarketState(price, cvdDelta, bidDepthDelta, askDepthDelta, depthDelta);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertContains(String actual, String expected, String message) {
        if (actual == null || !actual.contains(expected)) {
            throw new AssertionError(message + ": expected \"" + actual + "\" to contain \"" + expected + "\"");
        }
    }
}
