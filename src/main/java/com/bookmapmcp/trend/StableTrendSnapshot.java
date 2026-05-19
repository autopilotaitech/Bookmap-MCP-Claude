package com.bookmapmcp.trend;

public record StableTrendSnapshot(
        TrendDirection direction,
        double line,
        int confidence,
        boolean switched,
        boolean chop) {

    public static StableTrendSnapshot neutral(double line) {
        return new StableTrendSnapshot(TrendDirection.NEUTRAL, line, 0, false, true);
    }
}
