package com.bookmapmcp.trend;

public record OrderflowSnapshot(
        long volume,
        double delta,
        double averageVolume,
        double bboImbalance,
        double depthImbalance) {

    public static final OrderflowSnapshot EMPTY = new OrderflowSnapshot(0, 0.0, 0.0, 0.0, 0.0);
}
