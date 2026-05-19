package com.bookmapmcp.trend;

public record TrendConfig(
        boolean enabled,
        long candleIntervalMillis,
        int numberOfCandles,
        double multiplier,
        SwitchCondition switchCondition,
        int confirmationCandles,
        boolean volumeConfirmationEnabled,
        boolean deltaConfirmationEnabled,
        boolean bookConfirmationEnabled,
        int minimumConfidence) {
    public static final long MIN_CANDLE_INTERVAL_MILLIS = 5_000L;

    public TrendConfig {
        candleIntervalMillis = Math.max(MIN_CANDLE_INTERVAL_MILLIS, candleIntervalMillis);
        numberOfCandles = Math.max(1, numberOfCandles);
        multiplier = Math.max(0.1, multiplier);
        switchCondition = switchCondition == null ? SwitchCondition.CLOSE_CROSS : switchCondition;
        confirmationCandles = Math.max(1, confirmationCandles);
        minimumConfidence = Math.max(0, Math.min(100, minimumConfidence));
    }
}
