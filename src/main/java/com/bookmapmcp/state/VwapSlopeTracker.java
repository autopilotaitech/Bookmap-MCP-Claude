package com.bookmapmcp.state;

/**
 * Rolling OLS slope of session VWAP, ATR-normalized and z-scored.
 *
 * <p>Per Cartea/Jaimungal 2015 (Algorithmic and High-Frequency Trading, Ch. 7-8):
 * VWAP drift sign is the dominant short-horizon expected-return component, so
 * we surface it as a first-class signal that the level reaction model can gate
 * its FOLLOW vs FADE decisions on.</p>
 *
 * <p>Samples VWAP every 30s into a 20-sample ring (10 minutes window). Fits a
 * simple OLS slope y = ax + b on those 20 points, normalizes by the trailing
 * stddev of VWAP residuals, then z-scores the normalized slope against a
 * Welford-EWMA baseline so the sign + magnitude are comparable across vol
 * regimes.</p>
 *
 * <p>Thread-safety: synchronized snapshot read. Sampling is event-driven from
 * the trade thread.</p>
 */
public final class VwapSlopeTracker {

    private static final int    WINDOW       = 20;          // 20 samples × 30s = 10 min
    private static final long   SAMPLE_MS    = 30_000L;
    private static final double EWMA_ALPHA   = 0.05;        // ~14-sample half-life
    private static final int    WARMUP_N     = 5;

    private final Object lock = new Object();

    private final double[] ring = new double[WINDOW];
    private int    count = 0;
    private int    idx   = 0;
    private long   lastSampleMs = 0L;

    private double slopeNormMean = 0.0, slopeNormVar = 0.0;
    private long   ewmaN = 0;

    // last snapshot fields
    private double lastVwap = Double.NaN;
    private double lastSlopePerMin = 0.0;
    private double lastSlopeNorm   = 0.0;   // slope normalized by trailing stddev
    private double lastSlopeZ      = 0.0;
    private String lastSlopeLabel  = "WARMUP";

    /** Feed the latest session VWAP and current wall time. Auto-samples at SAMPLE_MS cadence. */
    public void onVwapTick(double vwap, long nowMs) {
        if (Double.isNaN(vwap) || vwap <= 0) return;
        synchronized (lock) {
            lastVwap = vwap;
            if (lastSampleMs == 0L) { lastSampleMs = nowMs; pushSample(vwap); return; }
            if (nowMs - lastSampleMs < SAMPLE_MS) return;
            lastSampleMs = nowMs;
            pushSample(vwap);
        }
    }

    private void pushSample(double vwap) {
        ring[idx] = vwap;
        idx = (idx + 1) % WINDOW;
        if (count < WINDOW) count++;
        if (count < WARMUP_N) { lastSlopeLabel = "WARMUP"; return; }
        // OLS slope of last `count` samples
        double n = count;
        double sx = (n - 1) * n / 2.0;          // sum of 0..n-1
        double sxx = (n - 1) * n * (2 * n - 1) / 6.0;
        double sy = 0.0, sxy = 0.0;
        for (int i = 0; i < count; i++) {
            // ring is filled chronologically; oldest is at (idx - count + WINDOW) % WINDOW
            int r = (idx - count + i + WINDOW) % WINDOW;
            double y = ring[r];
            sy += y;
            sxy += i * y;
        }
        double denom = n * sxx - sx * sx;
        if (denom <= 0) { lastSlopePerMin = 0; lastSlopeNorm = 0; lastSlopeZ = 0; return; }
        double slope = (n * sxy - sx * sy) / denom;    // VWAP units per sample (30s)
        // Per-minute slope for human-friendly reporting
        lastSlopePerMin = slope * 2.0;

        // Residual stddev — used to normalize slope into σ/sample units
        double mean = sy / n;
        // Reconstruct OLS intercept
        double intercept = (sy - slope * sx) / n;
        double ss = 0.0;
        for (int i = 0; i < count; i++) {
            int r = (idx - count + i + WINDOW) % WINDOW;
            double resid = ring[r] - (slope * i + intercept);
            ss += resid * resid;
        }
        double sd = Math.sqrt(Math.max(1e-9, ss / Math.max(1, count - 2)));
        // slope-per-sample / residual-σ  → dimensionless drift rate
        lastSlopeNorm = slope / sd;

        // EWMA z-score of slopeNorm against its trailing distribution
        double prev = slopeNormMean;
        if (ewmaN == 0) { slopeNormMean = lastSlopeNorm; slopeNormVar = 0; }
        else {
            slopeNormMean = slopeNormMean + EWMA_ALPHA * (lastSlopeNorm - slopeNormMean);
            slopeNormVar  = (1.0 - EWMA_ALPHA) * (slopeNormVar
                + EWMA_ALPHA * (lastSlopeNorm - prev) * (lastSlopeNorm - prev));
        }
        ewmaN++;
        if (ewmaN >= WARMUP_N) {
            double zsd = Math.sqrt(Math.max(1e-9, slopeNormVar));
            lastSlopeZ = (lastSlopeNorm - slopeNormMean) / zsd;
        }
        // Discrete label for downstream consumers
        double az = Math.abs(lastSlopeZ);
        if      (az < 0.5)             lastSlopeLabel = "FLAT";
        else if (lastSlopeZ >  0)      lastSlopeLabel = (az > 1.5 ? "STRONG_UP"   : "RISING");
        else                            lastSlopeLabel = (az > 1.5 ? "STRONG_DOWN" : "FALLING");
    }

    public VwapSlopeSnapshot snapshot() {
        synchronized (lock) {
            return new VwapSlopeSnapshot(
                lastVwap, count, lastSlopePerMin, lastSlopeNorm,
                lastSlopeZ, lastSlopeLabel
            );
        }
    }

    public static final class VwapSlopeSnapshot {
        public final double vwap;
        public final int    samples;
        public final double slopePerMin;     // raw slope in VWAP units / minute
        public final double slopeNorm;       // slope / residual σ — dimensionless
        public final double slopeZ;          // EWMA z-score of slopeNorm
        public final String label;           // STRONG_UP / RISING / FLAT / FALLING / STRONG_DOWN / WARMUP

        public VwapSlopeSnapshot(double vwap, int samples, double slopePerMin,
                                 double slopeNorm, double slopeZ, String label) {
            this.vwap = vwap;
            this.samples = samples;
            this.slopePerMin = slopePerMin;
            this.slopeNorm = slopeNorm;
            this.slopeZ = slopeZ;
            this.label = label;
        }
    }
}
