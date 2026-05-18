package com.bookmapmcp.state;

import java.util.List;

/**
 * Time-windowed flow + microstructure snapshot.
 *
 * <p>Regime and quant fields populated from {@link FlowRegime}: adaptive
 * Welford-EWMA classifier over Cont/Kukanov/Stoikov OFI, CVD divergence,
 * volume-per-tick absorption, and bias trajectory.</p>
 */
public final class MomentumSnapshot {

    public static final class Window {
        public final String label;
        public final int windowSeconds;
        public final long tradeCount;
        public final long buyVolume;
        public final long sellVolume;
        public final double imbalance;
        public final double avgSize;

        public Window(String label, int windowSeconds,
                      long tradeCount, long buyVolume, long sellVolume) {
            this.label = label;
            this.windowSeconds = windowSeconds;
            this.tradeCount = tradeCount;
            this.buyVolume = buyVolume;
            this.sellVolume = sellVolume;
            long total = buyVolume + sellVolume;
            this.imbalance = total > 0 ? ((double)(buyVolume - sellVolume)) / total : 0.0;
            this.avgSize   = tradeCount > 0 ? ((double) total) / tradeCount : 0.0;
        }

        public String label() { return label; }
        public int windowSeconds() { return windowSeconds; }
        public long tradeCount() { return tradeCount; }
        public long buyVolume() { return buyVolume; }
        public long sellVolume() { return sellVolume; }
        public double imbalance() { return imbalance; }
        public double avgSize() { return avgSize; }
    }

    public final long asOfNanos;
    public final List<Window> windows;
    public final double mid;
    public final double microprice;
    public final double microMidTicks;
    public final double bookPressureTop5;
    public final double bookPressureTop25;
    public final String regime;
    public final String regimeReason;
    public final double regimeConfidence;
    public final double biasScore;
    public final String biasTrajectory;
    public final double ofi;
    public final double ofiZ;
    public final double cvdDelta;
    public final double cvdDeltaZ;
    public final double vpt;
    public final double vptZ;
    public final double rvolTicks;
    public final double rvolZ;

    public MomentumSnapshot(long asOfNanos, List<Window> windows,
                            double mid, double microprice, double microMidTicks,
                            double bookPressureTop5, double bookPressureTop25,
                            String regime, String regimeReason,
                            double regimeConfidence, double biasScore, String biasTrajectory,
                            double ofi, double ofiZ,
                            double cvdDelta, double cvdDeltaZ,
                            double vpt, double vptZ,
                            double rvolTicks, double rvolZ) {
        this.asOfNanos = asOfNanos;
        this.windows = windows;
        this.mid = mid;
        this.microprice = microprice;
        this.microMidTicks = microMidTicks;
        this.bookPressureTop5 = bookPressureTop5;
        this.bookPressureTop25 = bookPressureTop25;
        this.regime = regime;
        this.regimeReason = regimeReason;
        this.regimeConfidence = regimeConfidence;
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
    }
}
