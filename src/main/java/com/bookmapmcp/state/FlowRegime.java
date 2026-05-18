package com.bookmapmcp.state;

/**
 * Sub-minute order-flow regime classifier built from quant-canonical signals:
 *
 * <ul>
 *   <li><b>Order Flow Imbalance (OFI)</b> — Cont/Kukanov/Stoikov 2014 event-driven
 *       BBO depth change accumulator. Linear predictor of short-horizon price.</li>
 *   <li><b>Cumulative Volume Delta (CVD) divergence</b> — per-window ΔCVD vs ΔP
 *       sign mismatch flags absorption / exhaustion.</li>
 *   <li><b>Volume-per-tick (VPT) absorption</b> — log-z-scored traded volume
 *       divided by realized tick-range. Spike = absorption.</li>
 *   <li><b>Bias trajectory</b> — 3-bucket SMA slope of windowed imbalance.</li>
 *   <li><b>Adaptive thresholds</b> — Welford-EWMA running μ/σ² with ~5min half-life,
 *       so the same code works at the dense open and the thin midday.</li>
 * </ul>
 *
 * <p>Window: 30s. Bias trajectory ring: 6 samples (3 min). EWMA α = 0.067
 * (half-life ≈ 10 samples = 5 min). Z-scores are gated to 0 until each
 * baseline has ≥5 samples to avoid cold-start volatility.</p>
 *
 * <p>Thread-safety: all public methods are guarded by an internal lock so it
 * is safe to call from the depth/trade/MBO threads concurrently.</p>
 */
public final class FlowRegime {

    private final double pips;
    private final Object lock = new Object();

    private static final double EWMA_ALPHA   = 0.067;     // ~5min half-life on 30s samples
    private static final long   WINDOW_MS    = 30_000L;
    private static final int    BIAS_RING_LEN = 6;
    private static final int    WARMUP_N     = 5;

    // === OFI event-driven state ===
    private long   ofiAccumThisWindow = 0L;
    private int    prevBbTick = Integer.MIN_VALUE, prevBaTick = Integer.MIN_VALUE;
    private int    prevBbSize = 0,                 prevBaSize = 0;

    // === Per-window trade aggregates ===
    private long   winBuyVol = 0, winSellVol = 0, winPrints = 0;
    private double winHigh = Double.NEGATIVE_INFINITY, winLow = Double.POSITIVE_INFINITY;
    private double winFirstPrice = Double.NaN, winLastPrice = Double.NaN;
    private long   winStartMs   = 0L;

    // === Welford-EWMA running stats (μ, σ²) ===
    private double ofiMean = 0,        ofiVar = 0;        private long ofiN = 0;
    private double vptLogMean = 0,     vptLogVar = 0;     private long vptLogN = 0;
    private double rvolMean = 0,       rvolVar = 0;       private long rvolN = 0;
    private double cvdDeltaMean = 0,   cvdDeltaVar = 0;   private long cvdDeltaN = 0;

    // === Bias trajectory ring ===
    private final double[] biasRing = new double[BIAS_RING_LEN];
    private int  biasRingIdx = 0;
    private int  biasRingCount = 0;

    // === Last snapshot fields ===
    private long   lastWindowEndMs = 0L;
    private double lastImbalance   = 0.0;
    private double lastOfi         = 0.0;
    private double lastOfiZ        = 0.0;
    private double lastVpt         = 0.0;
    private double lastVptZ        = 0.0;
    private double lastCvdDelta    = 0.0;
    private double lastCvdDeltaZ   = 0.0;
    private double lastRvolTicks   = 0.0;
    private double lastRvolZ       = 0.0;
    private double lastPriceChangePts = 0.0;
    private double lastBiasScore   = 0.0;
    private String lastBiasTrajectory = "WARMUP";
    private String lastRegime      = "WARMUP";
    private String lastRegimeReason = "warming up baselines";
    private double lastRegimeConfidence = 0.0;

    public FlowRegime(double pips) { this.pips = pips; }

    // ───── Hooks ─────────────────────────────────────────────────────────

    /**
     * Called whenever the best bid or best ask changes (price or size).
     * Implements Cont/Kukanov/Stoikov 2014 Eq. 2.1 event-driven OFI:
     *   bid up  → +new_bid_size     (queue ascended → bullish)
     *   bid dn  → −old_bid_size     (queue canceled → bearish)
     *   bid =   → +(new−old) size   (size growth at level → bullish, shrink → bearish)
     *   ask up  → +old_ask_size     (prior ask LIFTED/canceled → bullish)
     *   ask dn  → −new_ask_size     (new aggressive ask placed lower → bearish)
     *   ask =   → −(new−old) size   (size growth → more sellers → bearish)
     */
    public void onBboChange(int bbTick, int bbSize, int baTick, int baSize, long nowMs) {
        synchronized (lock) {
            maybeRoll(nowMs);
            if (prevBbTick == Integer.MIN_VALUE) {
                prevBbTick = bbTick; prevBaTick = baTick;
                prevBbSize = bbSize; prevBaSize = baSize;
                return;
            }
            long e = 0L;
            // Bid side (CKS Eq 2.1 bid contributions)
            if      (bbTick > prevBbTick) e += bbSize;
            else if (bbTick < prevBbTick) e -= prevBbSize;
            else                          e += (bbSize - prevBbSize);
            // Ask side (CKS Eq 2.1 ask contributions — note the inversion vs bids)
            if      (baTick > prevBaTick) e += prevBaSize;     // ask UP = bullish
            else if (baTick < prevBaTick) e -= baSize;          // ask DOWN = bearish
            else                          e -= (baSize - prevBaSize);  // same-level: bigger ask = bearish

            ofiAccumThisWindow += e;
            prevBbTick = bbTick; prevBaTick = baTick;
            prevBbSize = bbSize; prevBaSize = baSize;
        }
    }

    /** Called on every print. */
    public void onTrade(double price, long size, boolean buyAggressor, long nowMs) {
        synchronized (lock) {
            maybeRoll(nowMs);
            if (winStartMs == 0L) winStartMs = nowMs;
            if (Double.isNaN(winFirstPrice)) winFirstPrice = price;
            winLastPrice = price;
            if (price > winHigh) winHigh = price;
            if (price < winLow)  winLow  = price;
            if (buyAggressor) winBuyVol += size; else winSellVol += size;
            winPrints++;
        }
    }

    // ───── Window roll ───────────────────────────────────────────────────

    private void maybeRoll(long nowMs) {
        if (winStartMs == 0L) { winStartMs = nowMs; return; }
        if (nowMs - winStartMs < WINDOW_MS) return;
        closeWindow(nowMs);
        // Reset for next window
        ofiAccumThisWindow = 0L;
        winBuyVol = winSellVol = winPrints = 0;
        winHigh = Double.NEGATIVE_INFINITY; winLow = Double.POSITIVE_INFINITY;
        winFirstPrice = winLastPrice = Double.NaN;
        winStartMs = nowMs;
    }

    private void closeWindow(long nowMs) {
        lastWindowEndMs = nowMs;
        long totalVol = winBuyVol + winSellVol;

        // OFI per second (normalize so windows of different lengths are comparable)
        double ofi = ofiAccumThisWindow / 30.0;
        lastOfi = ofi;
        lastOfiZ  = updateEwmaAndZ(ofi, true);

        // VPT — volume per tick of realized range
        double rangePts = (winHigh > winLow) ? (winHigh - winLow) : pips;
        double rangeTicks = Math.max(1.0, rangePts / pips);
        double vpt = (totalVol > 0) ? (totalVol / rangeTicks) : 0.0;
        lastVpt = vpt;
        if (vpt > 0) {
            double vptLog = Math.log(vpt);
            lastVptZ = updateVptEwma(vptLog);
        } else {
            lastVptZ = 0.0;
        }

        // Realized vol (abs price change in ticks)
        double priceChangePts = (!Double.isNaN(winFirstPrice) && !Double.isNaN(winLastPrice))
                                ? (winLastPrice - winFirstPrice) : 0.0;
        lastPriceChangePts = priceChangePts;
        double rvolTicks = Math.abs(priceChangePts) / pips;
        lastRvolTicks = rvolTicks;
        lastRvolZ = updateRvolEwma(rvolTicks);

        // CVD delta this window (signed)
        double cvdDelta = (double)(winBuyVol - winSellVol);
        lastCvdDelta = cvdDelta;
        lastCvdDeltaZ = updateCvdEwma(cvdDelta);

        // Imbalance and bias trajectory
        double imb = (totalVol > 0) ? ((double)(winBuyVol - winSellVol)) / totalVol : 0.0;
        lastImbalance = imb;

        biasRing[biasRingIdx] = imb;
        biasRingIdx = (biasRingIdx + 1) % BIAS_RING_LEN;
        if (biasRingCount < BIAS_RING_LEN) biasRingCount++;

        lastBiasTrajectory = computeBiasTrajectory();
        lastBiasScore      = computeBiasScore(imb);

        // Regime classification with confidence
        classifyRegime(totalVol, priceChangePts);
    }

    // ───── EWMA helpers ──────────────────────────────────────────────────

    private double updateEwmaAndZ(double x, boolean isOfi) {
        double prev = ofiMean;
        if (ofiN == 0) { ofiMean = x; ofiVar = 0; }
        else {
            ofiMean = ofiMean + EWMA_ALPHA * (x - ofiMean);
            ofiVar  = (1.0 - EWMA_ALPHA) * (ofiVar + EWMA_ALPHA * (x - prev) * (x - prev));
        }
        ofiN++;
        if (ofiN < WARMUP_N) return 0.0;
        double sd = Math.sqrt(Math.max(1e-9, ofiVar));
        return (x - ofiMean) / sd;
    }

    private double updateVptEwma(double xLog) {
        double prev = vptLogMean;
        if (vptLogN == 0) { vptLogMean = xLog; vptLogVar = 0; }
        else {
            vptLogMean = vptLogMean + EWMA_ALPHA * (xLog - vptLogMean);
            vptLogVar  = (1.0 - EWMA_ALPHA) * (vptLogVar + EWMA_ALPHA * (xLog - prev) * (xLog - prev));
        }
        vptLogN++;
        if (vptLogN < WARMUP_N) return 0.0;
        double sd = Math.sqrt(Math.max(1e-9, vptLogVar));
        return (xLog - vptLogMean) / sd;
    }

    private double updateRvolEwma(double x) {
        double prev = rvolMean;
        if (rvolN == 0) { rvolMean = x; rvolVar = 0; }
        else {
            rvolMean = rvolMean + EWMA_ALPHA * (x - rvolMean);
            rvolVar  = (1.0 - EWMA_ALPHA) * (rvolVar + EWMA_ALPHA * (x - prev) * (x - prev));
        }
        rvolN++;
        if (rvolN < WARMUP_N) return 0.0;
        double sd = Math.sqrt(Math.max(1e-9, rvolVar));
        return (x - rvolMean) / sd;
    }

    private double updateCvdEwma(double x) {
        double prev = cvdDeltaMean;
        if (cvdDeltaN == 0) { cvdDeltaMean = x; cvdDeltaVar = 0; }
        else {
            cvdDeltaMean = cvdDeltaMean + EWMA_ALPHA * (x - cvdDeltaMean);
            cvdDeltaVar  = (1.0 - EWMA_ALPHA) * (cvdDeltaVar + EWMA_ALPHA * (x - prev) * (x - prev));
        }
        cvdDeltaN++;
        if (cvdDeltaN < WARMUP_N) return 0.0;
        double sd = Math.sqrt(Math.max(1e-9, cvdDeltaVar));
        return (x - cvdDeltaMean) / sd;
    }

    // ───── Trajectory + bias score ───────────────────────────────────────

    private String computeBiasTrajectory() {
        if (biasRingCount < 4) return "WARMUP";
        int half = biasRingCount / 2;
        double recent = 0, older = 0;
        for (int i = 0; i < half; i++) {
            int recentI = (biasRingIdx - 1 - i + BIAS_RING_LEN) % BIAS_RING_LEN;
            int olderI  = (biasRingIdx - 1 - i - half + BIAS_RING_LEN) % BIAS_RING_LEN;
            recent += biasRing[recentI];
            older  += biasRing[olderI];
        }
        recent /= half; older /= half;
        double diff = recent - older;
        if (diff >  0.10) return "RISING";
        if (diff < -0.10) return "FALLING";
        return "FLAT";
    }

    private double computeBiasScore(double imb) {
        // Tanh-squash each component, then weighted sum, then clip to [-1,+1].
        double ofiZComp = Math.tanh(lastOfiZ / 2.0);
        double cvdZComp = Math.tanh(lastCvdDeltaZ / 2.0);
        double imbComp  = Math.tanh(imb * 3.0);
        double trajBonus = "RISING".equals(lastBiasTrajectory)  ?  0.15
                         : "FALLING".equals(lastBiasTrajectory) ? -0.15 : 0.0;
        double s = 0.40*ofiZComp + 0.25*cvdZComp + 0.20*imbComp + trajBonus;
        return Math.max(-1.0, Math.min(1.0, s));
    }

    // ───── Regime classifier ─────────────────────────────────────────────

    private void classifyRegime(long totalVol, double priceChangePts) {
        boolean warmedUp = (ofiN >= WARMUP_N && vptLogN >= WARMUP_N && cvdDeltaN >= WARMUP_N);
        if (!warmedUp) {
            lastRegime = "WARMUP";
            lastRegimeReason = "EWMA baselines building (ofiN=" + ofiN + ")";
            lastRegimeConfidence = 0.0;
            return;
        }

        double pcTicks = priceChangePts / pips;

        // QUIET: real below-baseline activity (the actual meaning of "thin")
        if (winPrints < 5 && lastRvolZ < -1.0) {
            lastRegime = "QUIET";
            lastRegimeReason = "rvol z=" + f(lastRvolZ) + ", prints=" + winPrints;
            lastRegimeConfidence = 0.7;
            return;
        }

        // ABSORPTION: high VPT + strong CVD + flat price (size traded but no movement)
        if (lastVptZ > 1.5 && Math.abs(lastCvdDeltaZ) > 1.0 && Math.abs(pcTicks) < 4.0) {
            if (lastCvdDeltaZ > 0) {
                // Buyers aggressing but price not moving = asks absorbing = bearish
                lastRegime = "ABSORPTION_ASK";
                lastRegimeReason = "VPT z=" + f(lastVptZ) + ", CVD z=" + f(lastCvdDeltaZ)
                                 + " absorbed by asks, ΔP " + f(pcTicks) + "t";
            } else {
                // Sellers aggressing but price not falling = bids absorbing = bullish
                lastRegime = "ABSORPTION_BID";
                lastRegimeReason = "VPT z=" + f(lastVptZ) + ", CVD z=" + f(lastCvdDeltaZ)
                                 + " absorbed by bids, ΔP " + f(pcTicks) + "t";
            }
            lastRegimeConfidence = Math.min(1.0, (lastVptZ + Math.abs(lastCvdDeltaZ)) / 4.0);
            return;
        }

        // EXHAUSTION: large move but CVD diverging from price direction
        if (Math.abs(pcTicks) > 8.0 &&
            ((pcTicks > 0 && lastCvdDeltaZ < -0.5) || (pcTicks < 0 && lastCvdDeltaZ > 0.5))) {
            lastRegime = pcTicks > 0 ? "EXHAUSTION_UP" : "EXHAUSTION_DOWN";
            lastRegimeReason = "ΔP " + f(pcTicks) + "t but CVD z=" + f(lastCvdDeltaZ) + " diverges";
            lastRegimeConfidence = Math.min(1.0, Math.abs(lastCvdDeltaZ));
            return;
        }

        // TRENDING: aligned OFI + CVD + imbalance
        if (lastOfiZ > 1.0 && lastCvdDeltaZ > 0.5 && lastImbalance > 0.15) {
            lastRegime = "TRENDING_UP";
            lastRegimeReason = "OFI z=" + f(lastOfiZ) + ", CVD z=" + f(lastCvdDeltaZ)
                             + ", imb " + pct(lastImbalance);
            lastRegimeConfidence = Math.min(1.0, (lastOfiZ + lastCvdDeltaZ) / 4.0);
            return;
        }
        if (lastOfiZ < -1.0 && lastCvdDeltaZ < -0.5 && lastImbalance < -0.15) {
            lastRegime = "TRENDING_DOWN";
            lastRegimeReason = "OFI z=" + f(lastOfiZ) + ", CVD z=" + f(lastCvdDeltaZ)
                             + ", imb " + pct(lastImbalance);
            lastRegimeConfidence = Math.min(1.0, (Math.abs(lastOfiZ) + Math.abs(lastCvdDeltaZ)) / 4.0);
            return;
        }

        // Default: balanced
        lastRegime = "BALANCED";
        lastRegimeReason = "OFI z=" + f(lastOfiZ) + ", CVD z=" + f(lastCvdDeltaZ)
                         + ", imb " + pct(lastImbalance);
        lastRegimeConfidence = 0.4;
    }

    public FlowRegimeSnapshot snapshot() {
        synchronized (lock) {
            return new FlowRegimeSnapshot(
                lastWindowEndMs, lastRegime, lastRegimeReason, lastRegimeConfidence,
                lastBiasScore, lastBiasTrajectory,
                lastOfi, lastOfiZ,
                lastCvdDelta, lastCvdDeltaZ,
                lastVpt, lastVptZ,
                lastRvolTicks, lastRvolZ,
                lastImbalance, lastPriceChangePts
            );
        }
    }

    private static String f(double x)   { return String.format("%+.2f", x); }
    private static String pct(double x) { return String.format("%+.0f%%", x * 100.0); }
}
