package com.openrange;

public record PaxOpeningRangeMarketState(
        double lastPrice,
        double cvdDelta,
        double bidDepthDelta,
        double askDepthDelta,
        double netDepthDelta) {
}
