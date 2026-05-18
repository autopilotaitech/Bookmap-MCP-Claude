package com.bookmapmcp.state;

import java.util.List;

public final class PullStackSnapshot {

    public static final class Window {
        public final String label;
        public final String resetMode;
        public final long   resetWindowMs;
        public final long   resetCount;
        public final long   lastResetMs;
        public final long   bidStacked, bidPulled, askStacked, askPulled;
        public final double bidStackedPerMin, bidPulledPerMin, askStackedPerMin, askPulledPerMin;
        public final String status;
        public final long   elapsedSec;
        public final String bias;
        public final String biasReason;
        public double score = 0.0;
        public double mean = 0.0, stddev = 0.0;
        public double zScore = 0.0;

        public Window(String label, String resetMode, long resetWindowMs, long resetCount, long lastResetMs,
                      long bidStacked, long bidPulled, long askStacked, long askPulled,
                      String status, long elapsedSec) {
            this.label = label;
            this.resetMode = resetMode;
            this.resetWindowMs = resetWindowMs;
            this.resetCount = resetCount;
            this.lastResetMs = lastResetMs;
            this.bidStacked = bidStacked;
            this.bidPulled  = bidPulled;
            this.askStacked = askStacked;
            this.askPulled  = askPulled;
            this.status = status;
            this.elapsedSec = elapsedSec;
            double minutes = Math.max(elapsedSec, 1L) / 60.0;
            this.bidStackedPerMin = bidStacked / minutes;
            this.bidPulledPerMin  = bidPulled  / minutes;
            this.askStackedPerMin = askStacked / minutes;
            this.askPulledPerMin  = askPulled  / minutes;
            double bullRate = bidStackedPerMin + askPulledPerMin;
            double bearRate = askStackedPerMin + bidPulledPerMin;
            if (bullRate > bearRate * 1.3 && bullRate > 0) {
                this.bias = "BULLISH";
                this.biasReason = String.format("bid-stack/min %.0f + ask-pull/min %.0f vs %.0f", bidStackedPerMin, askPulledPerMin, bearRate);
            } else if (bearRate > bullRate * 1.3 && bearRate > 0) {
                this.bias = "BEARISH";
                this.biasReason = String.format("ask-stack/min %.0f + bid-pull/min %.0f vs %.0f", askStackedPerMin, bidPulledPerMin, bullRate);
            } else if (bullRate + bearRate == 0) {
                this.bias = "QUIET";
                this.biasReason = "no activity";
            } else {
                this.bias = "NEUTRAL";
                this.biasReason = String.format("bull/min %.0f vs bear/min %.0f", bullRate, bearRate);
            }
        }
    }

    public final long asOfNanos;
    public final double bestBid;
    public final double bestAsk;
    public final int    bestBidSize;
    public final int    bestAskSize;
    public final int    depthLevels;
    public final List<Window> windows;
    public double aggregateZ = 0.0;
    public String aggregateBias = "QUIET";
    public String rotation = "NONE";
    public String confirmation = "PASSIVE";

    public PullStackSnapshot(long asOfNanos, double bestBid, double bestAsk,
                             int bestBidSize, int bestAskSize, int depthLevels,
                             List<Window> windows) {
        this.asOfNanos = asOfNanos;
        this.bestBid = bestBid; this.bestAsk = bestAsk;
        this.bestBidSize = bestBidSize; this.bestAskSize = bestAskSize;
        this.depthLevels = depthLevels;
        this.windows = windows;
    }
}
