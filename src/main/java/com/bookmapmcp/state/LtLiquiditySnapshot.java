package com.bookmapmcp.state;

/**
 * Time-weighted (EWMA) average resting size at best bid / best ask.
 * Half-life is configurable; default 30s (short-term liquidity pulse).
 */
public final class LtLiquiditySnapshot {
    public final long asOfNanos;
    public final double bestBid;
    public final double bestAsk;
    public final int bestBidSize;
    public final int bestAskSize;
    public final double ltBidSize;
    public final double ltAskSize;
    public final double ratio;   // (ltBid - ltAsk) / (ltBid + ltAsk)
    public final long halfLifeMillis;
    public LtLiquiditySnapshot(long asOfNanos, double bestBid, double bestAsk,
                               int bestBidSize, int bestAskSize,
                               double ltBidSize, double ltAskSize,
                               long halfLifeMillis) {
        this.asOfNanos = asOfNanos;
        this.bestBid = bestBid; this.bestAsk = bestAsk;
        this.bestBidSize = bestBidSize; this.bestAskSize = bestAskSize;
        this.ltBidSize = ltBidSize; this.ltAskSize = ltAskSize;
        double total = ltBidSize + ltAskSize;
        this.ratio = total > 0 ? (ltBidSize - ltAskSize) / total : 0.0;
        this.halfLifeMillis = halfLifeMillis;
    }
}
