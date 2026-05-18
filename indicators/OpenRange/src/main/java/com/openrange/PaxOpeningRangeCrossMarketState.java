package com.openrange;

import java.time.LocalDate;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * Tracks the latest per-root signal so cross-market confirmation can be
 * computed for ES/NQ (and their micro counterparts).
 *
 * <p>Each stored entry carries (a) the originating symbol that produced it,
 * (b) the event-time nanosecond timestamp, and (c) the LocalDate the entry
 * was recorded on. When a counterpart entry is older than {@link #STALE_WINDOW_NANOS}
 * relative to the query's "now", or its recorded date is different from the
 * date implied by the query's "now", the counterpart is treated as UNKNOWN.
 *
 * <p>The legacy methods {@link #update(String, PaxOpeningRangeSignal)} and
 * {@link #statusFor(String)} are preserved for backward compatibility and
 * delegate to the time-aware overloads using {@link System#nanoTime()} and
 * the UTC-derived date. Production code paths should use the overloads that
 * take an explicit {@code nowNanos} driven by the Bookmap event clock.
 */
public class PaxOpeningRangeCrossMarketState {

    /** TTL after which a stored counterpart signal is considered stale. */
    private static final long STALE_WINDOW_NANOS = 60L * 1_000_000_000L;

    private static final long NANOS_PER_DAY = 86_400L * 1_000_000_000L;

    private final Map<String, Entry> entriesByRoot = new ConcurrentHashMap<>();

    /** Backward-compatible: uses System.nanoTime() and UTC-derived date. */
    public void update(String symbol, PaxOpeningRangeSignal signal) {
        long now = System.nanoTime();
        update(symbol, signal, now, dateFromNanos(now));
    }

    /** Time-aware overload using nowNanos and UTC-derived date. */
    public void update(String symbol, PaxOpeningRangeSignal signal, long nowNanos) {
        update(symbol, signal, nowNanos, dateFromNanos(nowNanos));
    }

    /** Time-aware overload with explicit recorded date. */
    public void update(String symbol, PaxOpeningRangeSignal signal, long nowNanos, LocalDate date) {
        String root = root(symbol);
        if (root.isBlank() || signal == null || date == null) {
            return;
        }
        entriesByRoot.put(root, new Entry(symbol, signal, nowNanos, date));
    }

    /** Backward-compatible: uses System.nanoTime() and UTC-derived date. */
    public PaxOpeningRangeCrossMarketStatus statusFor(String symbol) {
        long now = System.nanoTime();
        return statusFor(symbol, now, dateFromNanos(now));
    }

    /** Time-aware overload using nowNanos and UTC-derived date. */
    public PaxOpeningRangeCrossMarketStatus statusFor(String symbol, long nowNanos) {
        return statusFor(symbol, nowNanos, dateFromNanos(nowNanos));
    }

    /** Time-aware overload with explicit query date. */
    public PaxOpeningRangeCrossMarketStatus statusFor(String symbol, long nowNanos, LocalDate today) {
        String root = root(symbol);
        String related = relatedRoot(root);
        if (related.isBlank()) {
            return PaxOpeningRangeCrossMarketStatus.UNKNOWN;
        }
        Entry current = freshEntry(entriesByRoot.get(root), nowNanos, today);
        Entry other = freshEntry(entriesByRoot.get(related), nowNanos, today);
        if (current == null || other == null
                || current.signal.bias() == PaxOpeningRangeSignalBias.NEUTRAL
                || other.signal.bias() == PaxOpeningRangeSignalBias.NEUTRAL) {
            return PaxOpeningRangeCrossMarketStatus.UNKNOWN;
        }
        return current.signal.bias() == other.signal.bias()
                ? PaxOpeningRangeCrossMarketStatus.CONFIRM
                : PaxOpeningRangeCrossMarketStatus.DIVERGE;
    }

    /**
     * Remove the entry for the root of {@code symbol}, but only if the
     * originating symbol of the stored entry matches. This prevents one alias
     * (e.g. ESH5) from erasing another alias's data (e.g. ESM5) when both map
     * to the same root (ES).
     */
    public void remove(String symbol) {
        if (symbol == null) {
            return;
        }
        String root = root(symbol);
        if (root.isBlank()) {
            return;
        }
        entriesByRoot.computeIfPresent(root, (k, existing) ->
                existing.symbol.equalsIgnoreCase(symbol) ? null : existing);
    }

    private static Entry freshEntry(Entry entry, long nowNanos, LocalDate today) {
        if (entry == null) {
            return null;
        }
        if (today != null && !today.equals(entry.date)) {
            return null;
        }
        if (nowNanos - entry.nanos > STALE_WINDOW_NANOS) {
            return null;
        }
        return entry;
    }

    private static LocalDate dateFromNanos(long nanos) {
        // UTC day boundary - deterministic and consistent for tests.
        long epochDay = Math.floorDiv(nanos, NANOS_PER_DAY);
        return LocalDate.ofEpochDay(epochDay);
    }

    private static String relatedRoot(String root) {
        if ("ES".equals(root) || "MES".equals(root)) {
            return "NQ";
        }
        if ("NQ".equals(root) || "MNQ".equals(root)) {
            return "ES";
        }
        return "";
    }

    private static String root(String symbol) {
        if (symbol == null) {
            return "";
        }
        String upper = symbol.toUpperCase();
        if (upper.startsWith("MES")) {
            return "MES";
        }
        if (upper.startsWith("MNQ")) {
            return "MNQ";
        }
        if (upper.startsWith("ES")) {
            return "ES";
        }
        if (upper.startsWith("NQ")) {
            return "NQ";
        }
        return "";
    }

    private static final class Entry {
        final String symbol;
        final PaxOpeningRangeSignal signal;
        final long nanos;
        final LocalDate date;

        Entry(String symbol, PaxOpeningRangeSignal signal, long nanos, LocalDate date) {
            this.symbol = symbol;
            this.signal = signal;
            this.nanos = nanos;
            this.date = date;
        }
    }
}
