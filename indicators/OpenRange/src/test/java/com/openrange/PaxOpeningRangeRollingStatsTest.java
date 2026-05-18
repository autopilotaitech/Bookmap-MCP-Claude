package com.openrange;

public class PaxOpeningRangeRollingStatsTest {
    public static void main(String[] args) {
        returnsZeroWhenThereIsNotEnoughVariance();
        computesZScoreAgainstRollingSamples();
        dropsSamplesOutsideWindow();
    }

    private static void returnsZeroWhenThereIsNotEnoughVariance() {
        PaxOpeningRangeRollingStats stats = new PaxOpeningRangeRollingStats(120);

        stats.add(0, 10);
        stats.add(1_000_000_000L, 10);

        assertEquals(0.0, stats.zScore(10), "z score");
    }

    private static void computesZScoreAgainstRollingSamples() {
        PaxOpeningRangeRollingStats stats = new PaxOpeningRangeRollingStats(120);

        stats.add(0, 10);
        stats.add(1_000_000_000L, 20);
        stats.add(2_000_000_000L, 30);

        double z = stats.zScore(40);

        assertTrue(z > 1.9 && z < 2.1, "z score should be near 2.0 but was " + z);
    }

    private static void dropsSamplesOutsideWindow() {
        PaxOpeningRangeRollingStats stats = new PaxOpeningRangeRollingStats(2);

        stats.add(0, 100);
        stats.add(1_000_000_000L, 10);
        stats.add(3_000_000_000L, 20);

        assertEquals(15.0, stats.mean(), "mean");
    }

    private static void assertEquals(double expected, double actual, String message) {
        if (Math.abs(expected - actual) > 0.0000001) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }
}
