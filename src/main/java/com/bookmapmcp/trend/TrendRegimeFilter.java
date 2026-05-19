package com.bookmapmcp.trend;

public final class TrendRegimeFilter {
    public int score(Candle candle, TrendSnapshot trend, OrderflowSnapshot orderflow) {
        if (trend.direction() == TrendDirection.NEUTRAL || Double.isNaN(trend.trendLine())) {
            return 0;
        }
        double range = Math.max(0.000001, candle.high() - candle.low());
        double body = Math.abs(candle.close() - candle.open());
        double bodyScore = Math.min(45.0, 45.0 * body / range);
        double distanceScore = Double.isNaN(trend.atr()) || trend.atr() <= 0.0
                ? 10.0
                : Math.min(25.0, 25.0 * Math.abs(candle.close() - trend.trendLine()) / trend.atr());
        double deltaScore = candle.volume() <= 0 ? 0.0 : Math.min(20.0, 20.0 * Math.abs(orderflow.delta()) / Math.max(1.0, candle.volume()));
        double bookScore = Math.min(10.0, 10.0 * Math.abs(orderflow.bboImbalance()));
        return Math.max(0, Math.min(100, (int) Math.round(bodyScore + distanceScore + deltaScore + bookScore)));
    }
}
