package com.openrange;

public enum PaxOpeningRangeSignalColorState {
    BULLISH,
    BEARISH,
    DIVERGENT,
    NEUTRAL;

    public static PaxOpeningRangeSignalColorState from(PaxOpeningRangeSignal signal) {
        if (signal == null || signal.bias() == PaxOpeningRangeSignalBias.NEUTRAL) {
            return NEUTRAL;
        }

        double cvd = Math.abs(signal.cvdZScore()) > 0.0000001 ? signal.cvdZScore() : signal.cvdDelta();
        double ps = Math.abs(signal.pullingStackingZScore()) > 0.0000001
                ? signal.pullingStackingZScore()
                : signal.pullingStackingDelta();

        if (signal.bias() == PaxOpeningRangeSignalBias.LONG) {
            return cvd > 0 && ps > 0 ? BULLISH : DIVERGENT;
        }
        if (signal.bias() == PaxOpeningRangeSignalBias.SHORT) {
            return cvd < 0 && ps < 0 ? BEARISH : DIVERGENT;
        }
        return NEUTRAL;
    }
}
