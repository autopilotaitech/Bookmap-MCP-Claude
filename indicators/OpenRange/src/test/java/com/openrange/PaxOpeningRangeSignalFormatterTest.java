package com.openrange;

public class PaxOpeningRangeSignalFormatterTest {
    public static void main(String[] args) {
        formatsAllowedLongAsCompactStatus();
        shortensLongReasonsForOverlay();
        includesScoreAndEvidenceWhenAvailable();
        formatsExtendedBreakoutAsCompactStatus();
        formatsSignalAsTelemetryInsteadOfVerdict();
        formatsExtendedSignalAsTelemetryInsteadOfBlockText();
        formatsTelemetryWithZScoresWhenAvailable();
    }

    private static void includesScoreAndEvidenceWhenAvailable() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                "CVD+ BID+ ASK PULL NET+");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("LONG 4/4 OK | CVD+ BID+ ASK PULL NET+", text, "formatted scored signal");
    }

    private static void formatsExtendedBreakoutAsCompactStatus() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                PaxOpeningRangeSignalBias.NEUTRAL,
                PaxOpeningRangeSignalConfidence.LOW,
                "Breakout is more than 12 ticks beyond opening range.",
                0,
                4,
                "");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("EXTENDED | >12 ticks", text, "extended breakout text");
        assertTrue(text.length() <= 24, "extended breakout text should stay short");
    }

    private static void formatsSignalAsTelemetryInsteadOfVerdict() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                "CVD+ BID+ ASK PULL NET+",
                "ORH",
                4,
                10.0,
                42,
                25,
                1250,
                0,
                0,
                82,
                91,
                "OK");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("ORH +4t RNG 10.0 OK AGE 42s\nCVD p82 UP  PS p91 UP", text, "telemetry text");
    }

    private static void formatsExtendedSignalAsTelemetryInsteadOfBlockText() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                PaxOpeningRangeSignalBias.NEUTRAL,
                PaxOpeningRangeSignalConfidence.LOW,
                "Breakout is more than 12 ticks beyond opening range.",
                0,
                4,
                "",
                "ORH",
                12,
                10.0,
                42,
                0,
                0,
                0,
                0,
                0,
                0,
                "OK");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("ORH +12t RNG 10.0 OK AGE 42s\nCVD p0 FLAT  PS p0 FLAT", text, "telemetry text");
    }

    private static void formatsTelemetryWithZScoresWhenAvailable() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                "CVD+ BID+ ASK PULL NET+",
                "ORH",
                4,
                10.0,
                42,
                25,
                1250,
                1.75,
                2.44,
                82,
                91,
                "OK");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("ORH +4t RNG 10.0 OK AGE 42s\nCVD p82 UP  PS p91 UP", text, "z telemetry text");
    }

    private static void formatsAllowedLongAsCompactStatus() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertEquals("LONG OK HIGH | Above ORH + flow strong", text, "formatted signal");
    }

    private static void shortensLongReasonsForOverlay() {
        PaxOpeningRangeSignal signal = new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                PaxOpeningRangeSignalBias.SHORT,
                PaxOpeningRangeSignalConfidence.LOW,
                "Price is below ORL but order-flow confirmation is weak.");

        String text = PaxOpeningRangeSignalFormatter.format(signal);

        assertTrue(text.length() <= 48, "overlay text should stay compact");
        assertEquals("SHORT BLOCK LOW | Below ORL + flow weak", text, "formatted signal");
    }

    private static void assertEquals(String expected, String actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected \"" + expected + "\" but got \"" + actual + "\"");
        }
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }
}
