package com.openrange;

public class PaxOpeningRangeSignalColorTest {
    public static void main(String[] args) {
        classifiesLongAlignedAsBullish();
        classifiesShortAlignedAsBearish();
        classifiesMixedFlowAsDivergent();
        classifiesInsideRangeAsNeutral();
    }

    private static void classifiesLongAlignedAsBullish() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalBias.LONG, "ORH", 10, 500, 1.2, 2.1);

        assertEquals(PaxOpeningRangeSignalColorState.BULLISH, PaxOpeningRangeSignalColorState.from(signal), "state");
    }

    private static void classifiesShortAlignedAsBearish() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalBias.SHORT, "ORL", -10, -500, -1.2, -2.1);

        assertEquals(PaxOpeningRangeSignalColorState.BEARISH, PaxOpeningRangeSignalColorState.from(signal), "state");
    }

    private static void classifiesMixedFlowAsDivergent() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalBias.LONG, "ORH", 10, -500, 1.2, -2.1);

        assertEquals(PaxOpeningRangeSignalColorState.DIVERGENT, PaxOpeningRangeSignalColorState.from(signal), "state");
    }

    private static void classifiesInsideRangeAsNeutral() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalBias.NEUTRAL, "INSIDE", 0, 0, 0, 0);

        assertEquals(PaxOpeningRangeSignalColorState.NEUTRAL, PaxOpeningRangeSignalColorState.from(signal), "state");
    }

    private static PaxOpeningRangeSignal signal(PaxOpeningRangeSignalBias bias, String location,
            double cvd, double ps, double cvdZ, double psZ) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                bias,
                PaxOpeningRangeSignalConfidence.HIGH,
                "",
                4,
                4,
                "",
                location,
                "ORL".equals(location) ? -4 : 4,
                10.0,
                0,
                cvd,
                ps,
                cvdZ,
                psZ);
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
