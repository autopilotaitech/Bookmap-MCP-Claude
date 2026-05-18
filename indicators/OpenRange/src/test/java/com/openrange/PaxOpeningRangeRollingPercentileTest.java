package com.openrange;

public class PaxOpeningRangeRollingPercentileTest {
    public static void main(String[] args) {
        returnsSentinelWhenEmpty();
        computesPercentRankAgainstRollingSamples();
        dropsSamplesOutsideWindow();
        coalescesSamplesWithinTheSameSecond();
    }

    private static void returnsSentinelWhenEmpty() {
        PaxOpeningRangeRollingPercentile percentile = new PaxOpeningRangeRollingPercentile(120);

        assertEquals(-1, percentile.percentile(10),
                "empty percentile must report -1 sentinel so the quality gate can detect insufficient samples");
    }

    private static void computesPercentRankAgainstRollingSamples() {
        PaxOpeningRangeRollingPercentile percentile = new PaxOpeningRangeRollingPercentile(120);

        percentile.add(0, 10);
        percentile.add(1_000_000_000L, 20);
        percentile.add(2_000_000_000L, 30);
        percentile.add(3_000_000_000L, 40);

        assertEquals(75, percentile.percentile(35), "percentile");
    }

    private static void dropsSamplesOutsideWindow() {
        PaxOpeningRangeRollingPercentile percentile = new PaxOpeningRangeRollingPercentile(2);

        percentile.add(0, 10);
        percentile.add(1_000_000_000L, 20);
        percentile.add(3_000_000_000L, 30);

        assertEquals(50, percentile.percentile(25), "percentile");
    }

    private static void coalescesSamplesWithinTheSameSecond() {
        PaxOpeningRangeRollingPercentile percentile = new PaxOpeningRangeRollingPercentile(120);

        percentile.add(100_000_000L, 10);
        percentile.add(200_000_000L, 20);
        percentile.add(900_000_000L, 30);

        assertEquals(0, percentile.percentile(20), "same-second samples should retain only the latest value");
    }

    private static void assertEquals(int expected, int actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
