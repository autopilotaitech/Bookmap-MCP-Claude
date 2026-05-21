package com.openrange;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Deque;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * Durable append-only history of institutional chart events for the
 * Bookmap chart painter. Same persistence semantics as
 * {@link PaxInstitutionalSignalsHistory}: empty later polls do NOT clear
 * prior markers, dedup is by {@code id}, bounded at {@code maxSize}.
 *
 * <p>Pure (no canvas, no clock, no Bookmap dep). The painter owns one
 * instance per {@link PaxOpeningRangeModule.InstrumentState} and queries
 * {@link #snapshot()} on every repaint to redraw the full evidence trail.</p>
 */
final class PaxInstitutionalChartEventsHistory {

    private final int maxSize;
    private final Deque<PaxInstitutionalChartEvent> events;
    private final Set<String> seenIds;

    PaxInstitutionalChartEventsHistory(int maxSize) {
        if (maxSize <= 0) {
            throw new IllegalArgumentException("maxSize must be positive");
        }
        this.maxSize = maxSize;
        this.events = new ArrayDeque<>(maxSize + 1);
        this.seenIds = new HashSet<>();
    }

    /** Merge new candidates into history. Returns the number of newly-added
     *  events. NULL or empty inputs are no-ops by contract — prior markers
     *  stay on the chart. */
    synchronized int merge(Collection<PaxInstitutionalChartEvent> candidates) {
        if (candidates == null || candidates.isEmpty()) {
            return 0;
        }
        int added = 0;
        for (PaxInstitutionalChartEvent ev : candidates) {
            if (ev == null || !ev.isRenderable()) continue;
            if (seenIds.contains(ev.id)) continue;
            seenIds.add(ev.id);
            events.addLast(ev);
            added++;
            while (events.size() > maxSize) {
                PaxInstitutionalChartEvent removed = events.pollFirst();
                if (removed != null) {
                    seenIds.remove(removed.id);
                }
            }
        }
        return added;
    }

    /** Snapshot of current history in insertion order. The painter
     *  iterates this on every repaint. */
    synchronized List<PaxInstitutionalChartEvent> snapshot() {
        return new ArrayList<>(events);
    }

    synchronized int size() {
        return events.size();
    }

    synchronized boolean isEmpty() {
        return events.isEmpty();
    }

    synchronized void clear() {
        events.clear();
        seenIds.clear();
    }
}
