package com.openrange;

import java.util.ArrayDeque;
import java.util.Collection;
import java.util.Deque;
import java.util.HashSet;
import java.util.Iterator;
import java.util.List;
import java.util.Set;

/**
 * Durable append-only history of institutional signal events for the chart
 * painter. The latest {@code /api/snapshot} response defines NEW candidates
 * to add; it never defines the whole render state. Specifically:
 *
 * <ul>
 *   <li>Empty / missing {@code institutional_signals} on a poll adds nothing
 *       and removes nothing. Prior markers persist on the chart.</li>
 *   <li>Same {@code signal.id} on multiple polls adds nothing the second
 *       time — dedup by id.</li>
 *   <li>A new id (e.g., new state-change transition on the same level) adds
 *       a new marker.</li>
 *   <li>History is bounded; the oldest marker rolls off once {@code maxSize}
 *       is exceeded.</li>
 * </ul>
 *
 * <p>This class is pure (no canvas, no Bookmap dep, no clock). The painter
 * owns one instance per {@code InstrumentState} and queries
 * {@link #snapshot()} on every repaint to redraw the full history.</p>
 */
final class PaxInstitutionalSignalsHistory {

    private final int maxSize;
    private final Deque<PaxInstitutionalSignalEvent> events;
    private final Set<String> seenIds;

    PaxInstitutionalSignalsHistory(int maxSize) {
        if (maxSize <= 0) {
            throw new IllegalArgumentException("maxSize must be positive");
        }
        this.maxSize = maxSize;
        this.events = new ArrayDeque<>(maxSize + 1);
        this.seenIds = new HashSet<>();
    }

    /** Merge new candidates into history. Returns the number of newly-added
     *  markers (0 when none of the candidates have new ids). NULL or empty
     *  inputs are no-ops by contract — prior markers stay. */
    synchronized int merge(Collection<PaxInstitutionalSignalEvent> candidates) {
        if (candidates == null || candidates.isEmpty()) {
            return 0;
        }
        int added = 0;
        for (PaxInstitutionalSignalEvent ev : candidates) {
            if (ev == null || !ev.isRenderable()) continue;
            if (seenIds.contains(ev.id)) continue;
            seenIds.add(ev.id);
            events.addLast(ev);
            added++;
            while (events.size() > maxSize) {
                PaxInstitutionalSignalEvent removed = events.pollFirst();
                if (removed != null) {
                    seenIds.remove(removed.id);
                }
            }
        }
        return added;
    }

    /** Snapshot of the current history in insertion order. The painter
     *  iterates this on every repaint. The returned list is a defensive
     *  copy so the painter never mutates the deque mid-iteration. */
    synchronized List<PaxInstitutionalSignalEvent> snapshot() {
        return new java.util.ArrayList<>(events);
    }

    synchronized int size() {
        return events.size();
    }

    synchronized boolean isEmpty() {
        return events.isEmpty();
    }

    /** Reset path used by instrument dispose / module restart.
     *  Operator toggle of the institutional layer should NOT call this —
     *  it should just stop drawing. clear() permanently drops history. */
    synchronized void clear() {
        events.clear();
        seenIds.clear();
    }

    /** Reverse-order ids, newest first. For diagnostics only. */
    synchronized Iterator<String> idsNewestFirst() {
        return events.descendingIterator() instanceof Iterator
                ? wrapIds(events.descendingIterator())
                : java.util.Collections.emptyIterator();
    }

    private static Iterator<String> wrapIds(Iterator<PaxInstitutionalSignalEvent> src) {
        return new Iterator<String>() {
            @Override public boolean hasNext() { return src.hasNext(); }
            @Override public String next() { return src.next().id; }
        };
    }
}
