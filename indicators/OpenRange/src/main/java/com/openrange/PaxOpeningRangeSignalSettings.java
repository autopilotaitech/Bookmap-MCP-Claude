package com.openrange;

public record PaxOpeningRangeSignalSettings(
        double minCvdConfirmation,
        double minDepthConfirmation,
        int depthLevels,
        int minBreakoutTicks,
        int maxBreakoutTicks,
        int minScore) {

    public static PaxOpeningRangeSignalSettings defaults() {
        return new PaxOpeningRangeSignalSettings(1, 1, 10, 0, 0, 3);
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
