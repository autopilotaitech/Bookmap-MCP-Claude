package com.openrange;

import java.util.ArrayList;
import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;

/**
 * Active-set history for Pax AI chart events.
 *
 * <p>Distinct from {@link PaxInstitutionalChartEventsHistory} (append-only)
 * because AI signals have a TTL on the dashboard side. When the dashboard
 * drops an expired AI row from its JSONL active-set, the chart MUST drop
 * the corresponding marker. Append-only semantics would let an expired
 * AI marker linger on the chart forever even though the dashboard no
 * longer considers it live.</p>
 *
 * <p>Contract:
 * <ul>
 *   <li>{@link #replaceActiveSet}: wipes prior state, installs the new
 *       active set, deduped by {@code id}, capped at {@code maxSize}.
 *       Non-renderable / null events are silently skipped.</li>
 *   <li>{@link #snapshot}: current active set in insertion order.</li>
 *   <li>{@link #clear}: empties the history.</li>
 * </ul>
 * Mutations are synchronized on {@code this}; reads return a defensive
 * copy so the painter can iterate without holding the lock.</p>
 *
 * <p>Local institutional chart events keep their existing append-only
 * semantics — they're observed evidence (a sweep, an acceptance) that
 * doesn't expire. Only AI signals use this active-set class.</p>
 */
final class PaxAiChartEventsActiveHistory {

    private final int maxSize;
    private final LinkedHashMap<String, PaxInstitutionalChartEvent> active;

    PaxAiChartEventsActiveHistory(int maxSize) {
        if (maxSize <= 0) {
            throw new IllegalArgumentException("maxSize must be positive");
        }
        this.maxSize = maxSize;
        this.active = new LinkedHashMap<>();
    }

    /** Replace the current active set. Returns the number of renderable
     *  events kept after dedup + cap. NULL inputs are equivalent to an
     *  empty list (wipes history). */
    synchronized int replaceActiveSet(Collection<PaxInstitutionalChartEvent> events) {
        active.clear();
        if (events == null || events.isEmpty()) {
            return 0;
        }
        for (PaxInstitutionalChartEvent ev : events) {
            if (ev == null || !ev.isRenderable()) continue;
            if (active.containsKey(ev.id)) continue;
            active.put(ev.id, ev);
            if (active.size() >= maxSize) break;
        }
        return active.size();
    }

    synchronized List<PaxInstitutionalChartEvent> snapshot() {
        return new ArrayList<>(active.values());
    }

    synchronized int size() {
        return active.size();
    }

    synchronized boolean isEmpty() {
        return active.isEmpty();
    }

    synchronized void clear() {
        active.clear();
    }
}
