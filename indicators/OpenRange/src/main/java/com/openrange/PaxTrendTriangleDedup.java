package com.openrange;

/**
 * Pure-function dedup decisions for trend triangles. Extracted so the
 * (kind, bucketEnteredMs) emit-rule is independently testable without
 * pulling in a Bookmap canvas. The painter is the only caller; this
 * helper has no state of its own.
 *
 * <p>Refuses to emit when ANY of the following are true:</p>
 * <ul>
 *   <li>signal is null or stale (older than {@code staleAgeMs} since last fetch)</li>
 *   <li>kind is NONE or unrecognized</li>
 *   <li>{@code eventMs <= 0} — would anchor the triangle at chart-time-zero (1970)</li>
 *   <li>{@code mid} is NaN, infinite, or non-positive</li>
 *   <li>{@code tickSize} is NaN, infinite, or non-positive — needed for the
 *       above/below-price offset arithmetic</li>
 *   <li>same (kind, bucketEnteredMs) as the last emit — pure dedup</li>
 * </ul>
 */
final class PaxTrendTriangleDedup {

    private PaxTrendTriangleDedup() {}

    /** Should the painter emit a new triangle event for this signal?
     *
     * @param signal current trend signal model (may be null when fetcher has never succeeded)
     * @param nowMs current wall-clock used by the painter for stale-gating
     * @param staleAgeMs stale cliff in ms (signal older than this → no emit)
     * @param lastEmittedKind name() of the last emitted kind, "" if none
     * @param lastEmittedBucketEnteredMs bucketEnteredMs of the last emit, 0 if none
     * @param tickSize the instrument tick size (state.pips). Triangle position
     *                 uses tickSize × N as the offset above/below mid; with a
     *                 non-positive tickSize the offset is zero and the triangle
     *                 would overlap the price exactly.
     */
    static boolean shouldEmit(PaxTrendSignalModel signal, long nowMs, long staleAgeMs,
            String lastEmittedKind, long lastEmittedBucketEnteredMs,
            double tickSize) {
        if (signal == null) return false;
        // Dashboard's plot-eligibility gate. This is the authoritative
        // upstream check — eligible=false means the dashboard already
        // determined the signal is not safe to render (invalid mid,
        // bridge offline, etc.). The local field checks below are
        // defense-in-depth.
        if (!signal.eligible) return false;
        if (signal.isStale(nowMs, staleAgeMs)) return false;
        if (!signal.kind.isRenderable()) return false;
        if (signal.eventMs <= 0L) return false;
        if (!Double.isFinite(signal.mid) || signal.mid <= 0.0) return false;
        if (!Double.isFinite(tickSize) || tickSize <= 0.0) return false;
        if (signal.bucketEnteredMs != lastEmittedBucketEnteredMs) return true;
        return !signal.kind.name().equals(lastEmittedKind);
    }
}
