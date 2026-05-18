package com.bookmapmcp.state;

/**
 * Immutable snapshot of the {@link FlowRegime} classifier.
 *
 * <p>All z-scores are 0 until each respective baseline has ≥5 samples
 * (~2.5 minutes of warm-up at 30s windows).</p>
 */
public final class FlowRegimeSnapshot {

    public final long   asOfMs;
    public final String regime;            // TRENDING_UP/DN, ABSORPTION_BID/ASK, EXHAUSTION_UP/DN, BALANCED, QUIET, WARMUP
    public final String reason;
    public final double confidence;        // [0,1]
    public final double biasScore;         // [-1,+1] direction conviction
    public final String biasTrajectory;    // RISING / FALLING / FLAT / WARMUP
    public final double ofi;               // per-second OFI (Cont/Kukanov/Stoikov)
    public final double ofiZ;              // z-score vs EWMA baseline
    public final double cvdDelta;          // per-window ΔCVD
    public final double cvdDeltaZ;
    public final double vpt;               // volume per tick of range
    public final double vptZ;              // log-z-score
    public final double rvolTicks;         // realized vol (|ΔP| in ticks) this window
    public final double rvolZ;
    public final double imbalance;         // (buy − sell) / total this window
    public final double priceChangePts;

    public FlowRegimeSnapshot(long asOfMs, String regime, String reason, double confidence,
                              double biasScore, String biasTrajectory,
                              double ofi, double ofiZ,
                              double cvdDelta, double cvdDeltaZ,
                              double vpt, double vptZ,
                              double rvolTicks, double rvolZ,
                              double imbalance, double priceChangePts) {
        this.asOfMs = asOfMs;
        this.regime = regime;
        this.reason = reason;
        this.confidence = confidence;
        this.biasScore = biasScore;
        this.biasTrajectory = biasTrajectory;
        this.ofi = ofi;
        this.ofiZ = ofiZ;
        this.cvdDelta = cvdDelta;
        this.cvdDeltaZ = cvdDeltaZ;
        this.vpt = vpt;
        this.vptZ = vptZ;
        this.rvolTicks = rvolTicks;
        this.rvolZ = rvolZ;
        this.imbalance = imbalance;
        this.priceChangePts = priceChangePts;
    }
}
