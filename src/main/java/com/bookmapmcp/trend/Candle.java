package com.bookmapmcp.trend;

public record Candle(
        long timestampMillis,
        double open,
        double high,
        double low,
        double close,
        long volume,
        double delta) {

    public Candle {
        if (high < low) {
            throw new IllegalArgumentException("high must be greater than or equal to low");
        }
    }

    public double typicalPrice() {
        return (high + low + close) / 3.0;
    }
}
