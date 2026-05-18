package com.openrange;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.List;

public class PaxOpeningRangeFeatureCache {
    private final int capacity;
    private final Deque<PaxOpeningRangeFeatureSnapshot> history = new ArrayDeque<>();
    private volatile PaxOpeningRangeFeatureSnapshot latest;

    public PaxOpeningRangeFeatureCache(int capacity) {
        if (capacity < 1) {
            throw new IllegalArgumentException("capacity must be positive");
        }
        this.capacity = capacity;
    }

    public synchronized PaxOpeningRangeFeatureSnapshot update(long timeNanos, PaxOpeningRangeMarketState market,
            PaxOpeningRangeSignal signal) {
        PaxOpeningRangeFeatureSnapshot snapshot = new PaxOpeningRangeFeatureSnapshot(timeNanos, market, signal,
                PaxOpeningRangeSignalFormatter.format(signal), PaxOpeningRangeSignalColorState.from(signal));
        latest = snapshot;
        history.addLast(snapshot);
        while (history.size() > capacity) {
            history.removeFirst();
        }
        return snapshot;
    }

    public PaxOpeningRangeFeatureSnapshot latest() {
        return latest;
    }

    public synchronized List<PaxOpeningRangeFeatureSnapshot> history() {
        return Collections.unmodifiableList(new ArrayList<>(history));
    }

    public synchronized void reset() {
        history.clear();
        latest = null;
    }
}
