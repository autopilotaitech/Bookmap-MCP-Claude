package com.openrange;

import java.util.ArrayDeque;
import java.util.Deque;

public class PaxOpeningRangeRollingStats {
    private final long windowNanos;
    private final Deque<Sample> samples = new ArrayDeque<>();
    private double sum;
    private double sumSquares;
    private double cachedMean;
    private double cachedStdDev;

    public PaxOpeningRangeRollingStats(int windowSeconds) {
        this.windowNanos = Math.max(1, windowSeconds) * 1_000_000_000L;
    }

    public void add(long timeNanos, double value) {
        samples.addLast(new Sample(timeNanos, value));
        sum += value;
        sumSquares += value * value;
        evict(timeNanos);
        recomputeStats();
    }

    public double mean() {
        return cachedMean;
    }

    public double standardDeviation() {
        return cachedStdDev;
    }

    public double zScore(double value) {
        if (cachedStdDev <= 0.0000001) {
            return 0;
        }
        return (value - cachedMean) / cachedStdDev;
    }

    public void reset() {
        samples.clear();
        sum = 0;
        sumSquares = 0;
        cachedMean = 0;
        cachedStdDev = 0;
    }

    private void evict(long now) {
        while (!samples.isEmpty() && now - samples.peekFirst().timeNanos > windowNanos) {
            Sample sample = samples.removeFirst();
            sum -= sample.value;
            sumSquares -= sample.value * sample.value;
        }
    }

    private void recomputeStats() {
        int size = samples.size();
        if (size == 0) {
            cachedMean = 0;
            cachedStdDev = 0;
            return;
        }
        cachedMean = sum / size;
        if (size < 2) {
            cachedStdDev = 0;
            return;
        }
        double variance = (sumSquares - (size * cachedMean * cachedMean)) / (size - 1);
        cachedStdDev = Math.sqrt(Math.max(0, variance));
    }

    private record Sample(long timeNanos, double value) {
    }
}
