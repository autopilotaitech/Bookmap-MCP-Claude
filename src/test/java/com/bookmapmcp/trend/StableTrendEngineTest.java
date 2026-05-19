package com.bookmapmcp.trend;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class StableTrendEngineTest {
    @Test
    void doesNotFlipOnSingleNoisyBreak() {
        TrendConfig config = new TrendConfig(true, 5_000, 3, 1.0, SwitchCondition.CONFIRMED_CLOSE, 2, false, false, false, 0);
        StableTrendEngine engine = new StableTrendEngine(config, 25, 3);

        engine.onCandle(candle(1, 100, 101, 99, 100), OrderflowSnapshot.EMPTY);
        engine.onCandle(candle(2, 100, 103, 100, 102), OrderflowSnapshot.EMPTY);
        StableTrendSnapshot up = engine.onCandle(candle(3, 102, 105, 101, 104), OrderflowSnapshot.EMPTY);
        StableTrendSnapshot noisyBreak = engine.onCandle(candle(4, 104, 105, 95, 96), OrderflowSnapshot.EMPTY);

        assertEquals(TrendDirection.UP, up.direction());
        assertEquals(TrendDirection.UP, noisyBreak.direction());
        assertTrue(noisyBreak.confidence() >= 0);
    }

    @Test
    void strongDirectionalSequenceBuildsHighConfidenceVisibleLine() {
        TrendConfig config = new TrendConfig(true, 5_000, 3, 1.0, SwitchCondition.CLOSE_CROSS, 1, false, false, false, 0);
        StableTrendEngine engine = new StableTrendEngine(config, 25, 2);

        StableTrendSnapshot latest = StableTrendSnapshot.neutral(Double.NaN);
        for (int i = 0; i < 8; i++) {
            latest = engine.onCandle(candle(i, 100 + i, 102 + i, 99 + i, 101 + i), new OrderflowSnapshot(1000, 500, 800, 0.2, 0.1));
        }

        assertEquals(TrendDirection.UP, latest.direction());
        assertTrue(!Double.isNaN(latest.line()));
        assertTrue(latest.confidence() >= 50);
    }

    @Test
    void chopSequenceStaysLowConfidence() {
        TrendConfig config = new TrendConfig(true, 5_000, 3, 1.0, SwitchCondition.CLOSE_CROSS, 1, false, false, false, 0);
        StableTrendEngine engine = new StableTrendEngine(config, 45, 3);

        StableTrendSnapshot latest = StableTrendSnapshot.neutral(Double.NaN);
        latest = engine.onCandle(candle(1, 100, 101, 99, 100), OrderflowSnapshot.EMPTY);
        latest = engine.onCandle(candle(2, 100, 101, 99, 100), OrderflowSnapshot.EMPTY);
        latest = engine.onCandle(candle(3, 100, 101, 99, 100), OrderflowSnapshot.EMPTY);
        latest = engine.onCandle(candle(4, 100, 101, 99, 100), OrderflowSnapshot.EMPTY);

        assertTrue(latest.confidence() <= 45);
    }

    private static Candle candle(long timestamp, double open, double high, double low, double close) {
        return new Candle(timestamp, open, high, low, close, 1_000, 0.0);
    }
}
