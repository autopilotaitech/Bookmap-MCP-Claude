package com.openrange;

public class PaxOpeningRangeSignalQualityGateTest {
    public static void main(String[] args) {
        keepsStrongAllowedSignal();
        blocksWeakCvdPercentile();
        blocksWeakPullingStackingPercentile();
        keepsStrongShortLowerTailPercentiles();
        blocksCrossMarketDivergence();
        ignoresWaitSignals();
        blocksLongOnInsufficientCvdSamples();
        blocksShortOnInsufficientCvdSamples();
        allowsWhenThresholdDisabledEvenWithoutSamples();
        blocksWideRangeQuality();
        blocksWideRangeQualityCaseInsensitive();
    }

    private static void blocksWideRangeQuality() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signalWithQuality(82, 91, "WIDE"),
                PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(),
                "WIDE range quality must block an otherwise-strong allowed signal");
        assertContains(gated.reason(), "Wide opening range", "reason");
    }

    private static void blocksWideRangeQualityCaseInsensitive() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signalWithQuality(82, 91, " wide "),
                PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(),
                "WIDE match must be case-insensitive and tolerate surrounding whitespace");
        assertContains(gated.reason(), "Wide opening range", "reason");
    }

    private static void blocksLongOnInsufficientCvdSamples() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(-1, 91), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(),
                "LONG must block when CVD percentile is the insufficient-samples sentinel");
        assertContains(gated.reason(), "CVD percentile", "reason");
    }

    private static void blocksShortOnInsufficientCvdSamples() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(shortSignal(-1, 9), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(),
                "SHORT must also block on insufficient samples (regression: previous behavior allowed)");
        assertContains(gated.reason(), "CVD percentile", "reason");
    }

    private static void allowsWhenThresholdDisabledEvenWithoutSamples() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(0, 0, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(-1, -1), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, gated.action(),
                "with threshold=0 the gate must not consult percentile state");
    }

    private static void keepsStrongAllowedSignal() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(82, 91), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, gated.action(), "action");
        assertEquals(PaxOpeningRangeSignalConfidence.HIGH, gated.confidence(), "confidence");
    }

    private static void blocksWeakCvdPercentile() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(55, 91), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(), "action");
        assertContains(gated.reason(), "CVD percentile", "reason");
    }

    private static void blocksWeakPullingStackingPercentile() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(82, 60), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(), "action");
        assertContains(gated.reason(), "PS percentile", "reason");
    }

    private static void keepsStrongShortLowerTailPercentiles() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(shortSignal(18, 9), PaxOpeningRangeCrossMarketStatus.CONFIRM);

        assertEquals(PaxOpeningRangeSignalAction.ALLOW_SIGNAL, gated.action(), "action");
    }

    private static void blocksCrossMarketDivergence() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));

        PaxOpeningRangeSignal gated = gate.apply(signal(82, 91), PaxOpeningRangeCrossMarketStatus.DIVERGE);

        assertEquals(PaxOpeningRangeSignalAction.BLOCK_SIGNAL, gated.action(), "action");
        assertContains(gated.reason(), "Cross-market divergence", "reason");
    }

    private static void ignoresWaitSignals() {
        PaxOpeningRangeSignalQualityGate gate = new PaxOpeningRangeSignalQualityGate(settings(70, 70, true));
        PaxOpeningRangeSignal signal = PaxOpeningRangeSignal.waitSignal("wait");

        PaxOpeningRangeSignal gated = gate.apply(signal, PaxOpeningRangeCrossMarketStatus.DIVERGE);

        assertSame(signal, gated, "signal");
    }

    private static PaxOpeningRangeSignalQualitySettings settings(int cvdPercentile, int psPercentile,
            boolean blockCrossDivergence) {
        return new PaxOpeningRangeSignalQualitySettings(cvdPercentile, psPercentile, blockCrossDivergence);
    }

    private static PaxOpeningRangeSignal signal(int cvdPercentile, int psPercentile) {
        return signalWithQuality(cvdPercentile, psPercentile, "OK");
    }

    private static PaxOpeningRangeSignal signalWithQuality(int cvdPercentile, int psPercentile, String rangeQuality) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                "CVD+ BID+ ASK PULL NET+",
                "ORH",
                6,
                10.0,
                45,
                400,
                800,
                1.8,
                2.1,
                cvdPercentile,
                psPercentile,
                rangeQuality);
    }

    private static PaxOpeningRangeSignal shortSignal(int cvdPercentile, int psPercentile) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.SHORT,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is below ORL and order-flow confirmation is strong.",
                4,
                4,
                "CVD- BID PULL ASK+ NET-",
                "ORL",
                -6,
                10.0,
                45,
                -400,
                -800,
                -1.8,
                -2.1,
                cvdPercentile,
                psPercentile,
                "OK");
    }

    private static void assertContains(String actual, String expected, String message) {
        if (actual == null || !actual.contains(expected)) {
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
