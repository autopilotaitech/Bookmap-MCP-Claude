package com.bookmapmcp.trend;

public final class StableTrendEngine {
    private final TrendEngine rawEngine;
    private final TrendRegimeFilter regimeFilter = new TrendRegimeFilter();
    private final int antiChopThreshold;
    private final int smoothing;
    private TrendDirection stableDirection = TrendDirection.NEUTRAL;
    private int smoothedConfidence;
    private boolean confidenceInitialized;

    public StableTrendEngine(TrendConfig config, int antiChopThreshold, int smoothing) {
        this.rawEngine = new TrendEngine(config);
        this.antiChopThreshold = Math.max(0, Math.min(100, antiChopThreshold));
        this.smoothing = Math.max(1, smoothing);
    }

    public StableTrendSnapshot onCandle(Candle candle, OrderflowSnapshot orderflow) {
        TrendSnapshot raw = rawEngine.onCandle(candle, orderflow);
        int regime = regimeFilter.score(candle, raw, orderflow);
        boolean chop = regime < antiChopThreshold;

        TrendDirection outputDirection = stableDirection;
        boolean switched = false;
        if (stableDirection == TrendDirection.NEUTRAL) {
            outputDirection = raw.direction();
            stableDirection = outputDirection;
        } else if (raw.switched() && !chop) {
            outputDirection = raw.direction();
            switched = outputDirection != stableDirection;
            stableDirection = outputDirection;
        }

        int targetConfidence = chop ? Math.min(regime, antiChopThreshold) : Math.max(regime, antiChopThreshold);
        smoothedConfidence = smooth(targetConfidence);
        return new StableTrendSnapshot(outputDirection, raw.trendLine(), smoothedConfidence, switched, chop);
    }

    private int smooth(int target) {
        if (!confidenceInitialized) {
            confidenceInitialized = true;
            return target;
        }
        return smoothedConfidence + (target - smoothedConfidence) / smoothing;
    }
}
