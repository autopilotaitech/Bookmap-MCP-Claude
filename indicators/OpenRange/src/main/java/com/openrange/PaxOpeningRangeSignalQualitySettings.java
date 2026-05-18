package com.openrange;

public record PaxOpeningRangeSignalQualitySettings(
        int minCvdPercentile,
        int minPullingStackingPercentile,
        boolean blockCrossMarketDivergence) {

    public static PaxOpeningRangeSignalQualitySettings defaults() {
        return new PaxOpeningRangeSignalQualitySettings(70, 70, true);
    }

    public PaxOpeningRangeSignalQualitySettings {
        minCvdPercentile = clamp(minCvdPercentile);
        minPullingStackingPercentile = clamp(minPullingStackingPercentile);
    }

    private static int clamp(int value) {
        return Math.max(0, Math.min(100, value));
    }
}
