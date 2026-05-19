package com.bookmapmcp.trend;

public final class RollingAverage {
    private final long[] window;
    private int start;
    private int size;
    private long sum;

    public RollingAverage(int capacity) {
        this.window = new long[Math.max(1, capacity)];
    }

    public void add(long value) {
        if (size == window.length) {
            sum -= window[start];
            window[start] = value;
            start = (start + 1) % window.length;
            sum += value;
        } else {
            window[(start + size) % window.length] = value;
            size++;
            sum += value;
        }
    }

    public double average() {
        if (size == 0) {
            return 0.0;
        }
        return (double) sum / (double) size;
    }

    public int size() {
        return size;
    }

    public void reset() {
        start = 0;
        size = 0;
        sum = 0L;
    }
}
