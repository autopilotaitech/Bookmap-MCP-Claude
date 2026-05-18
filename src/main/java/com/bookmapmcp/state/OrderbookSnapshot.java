package com.bookmapmcp.state;

import java.util.List;

/**
 * A point-in-time snapshot of the top-N levels on each side, plus a few derived
 * quick-look values for whoever's reading the JSON.
 */
public record OrderbookSnapshot(
        List<OrderbookLevel> bids,
        List<OrderbookLevel> asks,
        double bestBid,
        double bestAsk,
        double mid,
        double spread,
        long generatedNanos
) {}
