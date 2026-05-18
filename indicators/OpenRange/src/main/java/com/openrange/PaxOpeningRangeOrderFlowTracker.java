package com.openrange;

import java.util.HashMap;
import java.util.Map;

public class PaxOpeningRangeOrderFlowTracker {
    private final double tickSize;
    private final int depthLevels;
    private final Map<Double, Integer> bidSizes = new HashMap<>();
    private final Map<Double, Integer> askSizes = new HashMap<>();
    private double lastPrice = Double.NaN;
    private double cvdDelta;
    private double bidDepthDelta;
    private double askDepthDelta;

    public PaxOpeningRangeOrderFlowTracker(double tickSize, int depthLevels) {
        this.tickSize = tickSize <= 0 ? 0.25 : tickSize;
        this.depthLevels = Math.max(1, depthLevels);
    }

    public void onTrade(double price, int size, boolean isBidAggressor) {
        lastPrice = price;
        int signedSize = isBidAggressor ? -Math.max(0, size) : Math.max(0, size);
        cvdDelta += signedSize;
    }

    public void onDepth(boolean isBid, double price, int size) {
        // Depth before first trade is ignored: near-price filter needs lastPrice; unfiltered depth would bloat size maps.
        if (Double.isNaN(lastPrice) || !isNearLastPrice(price)) {
            return;
        }

        Map<Double, Integer> sizes = isBid ? bidSizes : askSizes;
        int sanitizedSize = Math.max(0, size);
        if (!sizes.containsKey(price)) {
            sizes.put(price, sanitizedSize);
            return;
        }
        int previous = sizes.get(price);
        sizes.put(price, sanitizedSize);

        int delta = sanitizedSize - previous;
        if (isBid) {
            bidDepthDelta += delta;
        } else {
            askDepthDelta += delta;
        }
    }

    public PaxOpeningRangeMarketState snapshot() {
        return new PaxOpeningRangeMarketState(lastPrice, cvdDelta, bidDepthDelta, askDepthDelta,
                bidDepthDelta - askDepthDelta);
    }

    public void reset() {
        bidSizes.clear();
        askSizes.clear();
        lastPrice = Double.NaN;
        cvdDelta = 0;
        bidDepthDelta = 0;
        askDepthDelta = 0;
    }

    private boolean isNearLastPrice(double price) {
        return Math.abs(price - lastPrice) <= tickSize * depthLevels;
    }
}
