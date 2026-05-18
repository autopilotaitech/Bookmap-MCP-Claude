package com.openrange;

public record PaxOpeningRangeSignal(
        PaxOpeningRangeSignalAction action,
        PaxOpeningRangeSignalBias bias,
        PaxOpeningRangeSignalConfidence confidence,
        String reason,
        int score,
        int maxScore,
        String evidence,
        String location,
        int distanceTicks,
        double rangeWidth,
        long ageSeconds,
        double cvdDelta,
        double pullingStackingDelta,
        double cvdZScore,
        double pullingStackingZScore,
        int cvdPercentile,
        int pullingStackingPercentile,
        String rangeQuality) {

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason) {
        this(action, bias, confidence, reason, 0, 0, "", "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "");
    }

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason, int score, int maxScore, String evidence) {
        this(action, bias, confidence, reason, score, maxScore, evidence, "", 0, 0, 0, 0, 0, 0, 0, 0, 0, "");
    }

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason, int score, int maxScore, String evidence,
            String location, int distanceTicks, double cvdDelta, double pullingStackingDelta) {
        this(action, bias, confidence, reason, score, maxScore, evidence, location, distanceTicks, 0,
                0, cvdDelta, pullingStackingDelta, 0, 0, 0, 0, "");
    }

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason, int score, int maxScore, String evidence,
            String location, int distanceTicks, double rangeWidth, double cvdDelta, double pullingStackingDelta) {
        this(action, bias, confidence, reason, score, maxScore, evidence, location, distanceTicks, rangeWidth,
                0, cvdDelta, pullingStackingDelta, 0, 0, 0, 0, "");
    }

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason, int score, int maxScore, String evidence,
            String location, int distanceTicks, double rangeWidth, long ageSeconds,
            double cvdDelta, double pullingStackingDelta) {
        this(action, bias, confidence, reason, score, maxScore, evidence, location, distanceTicks, rangeWidth,
                ageSeconds, cvdDelta, pullingStackingDelta, 0, 0, 0, 0, "");
    }

    public PaxOpeningRangeSignal(PaxOpeningRangeSignalAction action, PaxOpeningRangeSignalBias bias,
            PaxOpeningRangeSignalConfidence confidence, String reason, int score, int maxScore, String evidence,
            String location, int distanceTicks, double rangeWidth, long ageSeconds,
            double cvdDelta, double pullingStackingDelta, double cvdZScore, double pullingStackingZScore) {
        this(action, bias, confidence, reason, score, maxScore, evidence, location, distanceTicks, rangeWidth,
                ageSeconds, cvdDelta, pullingStackingDelta, cvdZScore, pullingStackingZScore, 0, 0, "");
    }

    public static PaxOpeningRangeSignal waitSignal(String reason) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.WAIT,
                PaxOpeningRangeSignalBias.NEUTRAL,
                PaxOpeningRangeSignalConfidence.NONE,
                reason,
                0,
                0,
                "",
                "",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                "");
    }
}
