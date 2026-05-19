package com.bookmapmcp.trend;

import java.util.ArrayDeque;
import java.util.Deque;

public final class TrendEngine {
    private final TrendConfig config;
    private final Deque<Candle> candles = new ArrayDeque<>();
    private TrendDirection direction = TrendDirection.NEUTRAL;
    private double atr = Double.NaN;
    private double trendLine = Double.NaN;
    private double previousClose = Double.NaN;
    private int pendingBreaks;

    public TrendEngine(TrendConfig config) {
        this.config = config;
    }

    public TrendSnapshot onCandle(Candle candle, OrderflowSnapshot orderflow) {
        if (!isValid(candle)) {
            return new TrendSnapshot(direction, visibleFallback(candle), Double.NaN, Double.NaN, atr, false,
                    visibleFallback(candle));
        }
        double trueRange = trueRange(candle);
        previousClose = candle.close();
        candles.addLast(candle);
        if (candles.size() > config.numberOfCandles()) {
            candles.removeFirst();
        }
        if (candles.size() < config.numberOfCandles()) {
            TrendDirection provisional = candle.close() >= candle.open() ? TrendDirection.UP : TrendDirection.DOWN;
            return new TrendSnapshot(provisional, candle.close(), Double.NaN, Double.NaN, Double.NaN, false, candle.close());
        }

        atr = Double.isNaN(atr) ? averageTrueRange() : ema(atr, trueRange, config.numberOfCandles());
        double typical = candle.typicalPrice();
        double upperBand = typical + config.multiplier() * atr;
        double lowerBand = typical - config.multiplier() * atr;

        if (direction == TrendDirection.NEUTRAL) {
            direction = candle.close() >= candle.open() ? TrendDirection.UP : TrendDirection.DOWN;
            trendLine = direction == TrendDirection.UP ? lowerBand : upperBand;
            return snapshot(candle, upperBand, lowerBand, false);
        }

        boolean switched = false;
        if (direction == TrendDirection.UP) {
            trendLine = Math.max(trendLine, lowerBand);
            if (breaksDown(candle, orderflow)) {
                pendingBreaks++;
                if (pendingBreaks >= requiredBreaks()) {
                    direction = TrendDirection.DOWN;
                    trendLine = upperBand;
                    switched = true;
                    pendingBreaks = 0;
                }
            } else {
                pendingBreaks = 0;
            }
        } else {
            trendLine = Math.min(trendLine, upperBand);
            if (breaksUp(candle, orderflow)) {
                pendingBreaks++;
                if (pendingBreaks >= requiredBreaks()) {
                    direction = TrendDirection.UP;
                    trendLine = lowerBand;
                    switched = true;
                    pendingBreaks = 0;
                }
            } else {
                pendingBreaks = 0;
            }
        }

        return snapshot(candle, upperBand, lowerBand, switched);
    }

    public TrendDirection direction() {
        return direction;
    }

    public double trendLine() {
        return trendLine;
    }

    private TrendSnapshot snapshot(Candle candle, double upperBand, double lowerBand, boolean switched) {
        return new TrendSnapshot(direction, trendLine, upperBand, lowerBand, atr, switched, candle.close());
    }

    private boolean breaksDown(Candle candle, OrderflowSnapshot orderflow) {
        return switch (config.switchCondition()) {
            case WICK_CROSS -> candle.low() < trendLine;
            case PRICE_VOLUME -> candle.close() < trendLine && orderflow.volume() > orderflow.averageVolume();
            case PRICE_DELTA -> candle.close() < trendLine && orderflow.delta() < 0.0;
            case PRICE_DELTA_BOOK -> candle.close() < trendLine && orderflow.delta() < 0.0 && orderflow.bboImbalance() < 0.0;
            case CLOSE_CROSS, CONFIRMED_CLOSE -> candle.close() < trendLine;
        };
    }

    private boolean breaksUp(Candle candle, OrderflowSnapshot orderflow) {
        return switch (config.switchCondition()) {
            case WICK_CROSS -> candle.high() > trendLine;
            case PRICE_VOLUME -> candle.close() > trendLine && orderflow.volume() > orderflow.averageVolume();
            case PRICE_DELTA -> candle.close() > trendLine && orderflow.delta() > 0.0;
            case PRICE_DELTA_BOOK -> candle.close() > trendLine && orderflow.delta() > 0.0 && orderflow.bboImbalance() > 0.0;
            case CLOSE_CROSS, CONFIRMED_CLOSE -> candle.close() > trendLine;
        };
    }

    private int requiredBreaks() {
        return config.switchCondition() == SwitchCondition.CONFIRMED_CLOSE ? config.confirmationCandles() : 1;
    }

    private double trueRange(Candle candle) {
        if (Double.isNaN(previousClose)) {
            return candle.high() - candle.low();
        }
        return Math.max(candle.high() - candle.low(),
                Math.max(Math.abs(candle.high() - previousClose), Math.abs(candle.low() - previousClose)));
    }

    private double averageTrueRange() {
        Candle previous = null;
        double sum = 0.0;
        for (Candle candle : candles) {
            if (previous == null) {
                sum += candle.high() - candle.low();
            } else {
                sum += Math.max(candle.high() - candle.low(),
                        Math.max(Math.abs(candle.high() - previous.close()), Math.abs(candle.low() - previous.close())));
            }
            previous = candle;
        }
        return sum / candles.size();
    }

    private static double ema(double current, double next, int period) {
        double alpha = 2.0 / (period + 1.0);
        return current + alpha * (next - current);
    }

    private static boolean isValid(Candle candle) {
        return Double.isFinite(candle.open())
                && Double.isFinite(candle.high())
                && Double.isFinite(candle.low())
                && Double.isFinite(candle.close())
                && candle.high() >= candle.low();
    }

    private double visibleFallback(Candle candle) {
        if (Double.isFinite(trendLine)) {
            return trendLine;
        }
        if (Double.isFinite(candle.close())) {
            return candle.close();
        }
        if (Double.isFinite(previousClose)) {
            return previousClose;
        }
        return Double.NaN;
    }
}
