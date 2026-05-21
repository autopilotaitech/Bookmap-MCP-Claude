package com.openrange;

public record PaxOpeningRangeSignalSettings(
        double minCvdConfirmation,
        double minDepthConfirmation,
        int depthLevels,
        int minBreakoutTicks,
        int maxBreakoutTicks,
        int minScore) {

    public static PaxOpeningRangeSignalSettings defaults() {
        return new PaxOpeningRangeSignalSettings(25, 25, 10, 2, 80, 4);
    }

    public PaxOpeningRangeSignalSettings {
        minCvdConfirmation = Math.max(0, minCvdConfirmation);
        minDepthConfirmation = Math.max(0, minDepthConfirmation);
        depthLevels = Math.max(1, depthLevels);
        minBreakoutTicks = Math.max(0, minBreakoutTicks);
        maxBreakoutTicks = Math.max(0, maxBreakoutTicks);
        minScore = Math.max(1, Math.min(4, minScore));
    }
}
