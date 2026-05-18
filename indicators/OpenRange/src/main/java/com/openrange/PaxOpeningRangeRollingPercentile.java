package com.openrange;

import java.util.ArrayDeque;
import java.util.Deque;

public class PaxOpeningRangeRollingPercentile {
    private final long windowNanos;
    private final Deque<Sample> samples = new ArrayDeque<>();

    public PaxOpeningRangeRollingPercentile(int windowSeconds) {
        this.windowNanos = Math.max(1, windowSeconds) * 1_000_000_000L;
    }

    public void add(long timeNanos, double value) {
        long bucketNanos = secondBucket(timeNanos);
        if (!samples.isEmpty() && samples.peekLast().timeNanos == bucketNanos) {
            samples.removeLast();
        }
        samples.addLast(new Sample(bucketNanos, value));
        evict(timeNanos);
    }

    public int percentile(double value) {
        if (samples.isEmpty()) {
            return -1;
        }
        int belowOrEqual = 0;
        for (Sample sample : samples) {
            if (sample.value <= value) {
                belowOrEqual++;
            }
        }
        return (int) Math.round((belowOrEqual * 100.0) / samples.size());
    }

    public void reset() {
        samples.clear();
    }

    private void evict(long now) {
        long bucketNanos = secondBucket(now);
        while (!samples.isEmpty() && bucketNanos - samples.peekFirst().timeNanos > windowNanos) {
            samples.removeFirst();
        }
    }

    private static long secondBucket(long timeNanos) {
        return (timeNanos / 1_000_000_000L) * 1_000_000_000L;
    }

    private record Sample(long timeNanos, double value) {
    }
}
