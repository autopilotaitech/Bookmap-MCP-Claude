package com.openrange;

public class PaxOpeningRangeSignalQualityGate {
    private final PaxOpeningRangeSignalQualitySettings settings;

    public PaxOpeningRangeSignalQualityGate(PaxOpeningRangeSignalQualitySettings settings) {
        this.settings = settings == null ? PaxOpeningRangeSignalQualitySettings.defaults() : settings;
    }

    public PaxOpeningRangeSignal apply(PaxOpeningRangeSignal signal, PaxOpeningRangeCrossMarketStatus crossMarketStatus) {
        if (signal == null || signal.action() != PaxOpeningRangeSignalAction.ALLOW_SIGNAL) {
            return signal;
        }
        if (settings.blockCrossMarketDivergence() && crossMarketStatus == PaxOpeningRangeCrossMarketStatus.DIVERGE) {
            return blocked(signal, "Cross-market divergence blocks signal.");
        }
        if (!passesDirectionalPercentile(signal.bias(), signal.cvdPercentile(), settings.minCvdPercentile())) {
            return blocked(signal, "CVD percentile " + signal.cvdPercentile()
                    + " outside gate " + settings.minCvdPercentile() + ".");
        }
        if (!passesDirectionalPercentile(signal.bias(), signal.pullingStackingPercentile(),
                settings.minPullingStackingPercentile())) {
            return blocked(signal, "PS percentile " + signal.pullingStackingPercentile()
                    + " outside gate " + settings.minPullingStackingPercentile() + ".");
        }
        return signal;
    }

    private boolean passesDirectionalPercentile(PaxOpeningRangeSignalBias bias, int percentile, int threshold) {
        if (threshold <= 0) {
            return true;
        }
        if (percentile < 0) {
            return false;
        }
        if (bias == PaxOpeningRangeSignalBias.SHORT) {
            return percentile <= 100 - threshold;
        }
        return percentile >= threshold;
    }

    private PaxOpeningRangeSignal blocked(PaxOpeningRangeSignal signal, String reason) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                signal.bias(),
                PaxOpeningRangeSignalConfidence.LOW,
                reason,
                signal.score(),
                signal.maxScore(),
                signal.evidence(),
                signal.location(),
                signal.distanceTicks(),
                signal.rangeWidth(),
                signal.ageSeconds(),
                signal.cvdDelta(),
                signal.pullingStackingDelta(),
                signal.cvdZScore(),
                signal.pullingStackingZScore(),
                signal.cvdPercentile(),
                signal.pullingStackingPercentile(),
                signal.rangeQuality());
    }
}
