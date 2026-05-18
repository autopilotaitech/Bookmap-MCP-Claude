package com.openrange;

public class PaxOpeningRangeQualityTest {
    public static void main(String[] args) {
        labelsRangeQualityFromPercentile();
    }

    private static void labelsRangeQualityFromPercentile() {
        assertEquals("TIGHT", PaxOpeningRangeRangeQuality.fromPercentile(15), "tight");
        assertEquals("OK", PaxOpeningRangeRangeQuality.fromPercentile(50), "ok");
        assertEquals("WIDE", PaxOpeningRangeRangeQuality.fromPercentile(85), "wide");
        assertEquals("", PaxOpeningRangeRangeQuality.fromPercentile(0), "unknown");
    }

    private static void assertEquals(String expected, String actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
