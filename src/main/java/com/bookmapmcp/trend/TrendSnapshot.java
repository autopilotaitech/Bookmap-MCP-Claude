package com.bookmapmcp.trend;

public record TrendSnapshot(
        TrendDirection direction,
        double trendLine,
        double upperBand,
        double lowerBand,
        double atr,
        boolean switched,
        double close) {

    public static TrendSnapshot neutral(double close) {
        return new TrendSnapshot(TrendDirection.NEUTRAL, Double.NaN, Double.NaN, Double.NaN, Double.NaN, false, close);
    }
}
