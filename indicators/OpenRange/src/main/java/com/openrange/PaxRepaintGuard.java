package com.openrange;

/**
 * Per-painter throttle + emergency degradation guard for the overlay and
 * triangle repaint buckets. Persistent OR shapes are deliberately NOT
 * gated by this guard - OR levels are load-bearing and must redraw on
 * structure changes regardless of recent paint load.
 *
 * <p>Two independent rules:</p>
 * <ol>
 *   <li><b>Min-interval gap.</b> Overlay rebuilds are coalesced to one per
 *       {@link #overlayMinIntervalMs}; triangle rebuilds to one per
 *       {@link #triangleMinIntervalMs}. Updates inside the cooldown are
 *       dropped - the upstream dirty bit either re-fires at the next
 *       fetcher poll, or the painter re-arms it (caller's choice).</li>
 *   <li><b>Emergency degradation.</b> When {@link #recordApplyDurationNanos}
 *       reports a paint operation that took longer than
 *       {@link #overrunBudgetNanos}, the guard enters degraded mode for
 *       {@link #degradationCooldownNanos}. In degraded mode overlay AND
 *       triangle rebuilds are skipped while the persistent OR bucket
 *       continues normally.</li>
 * </ol>
 *
 * <p>All time values are nanos for the budget/cooldown (matches
 * {@code System.nanoTime()} monotonicity guarantees) and millis for the
 * min-interval gap (matches the wall-clock {@code System.currentTimeMillis()}
 * cadence the fetchers already use).</p>
 *
 * <p>Not thread-safe. Intended to be accessed only from the painter's
 * synchronized {@code applyNeeds} path.</p>
 */
final class PaxRepaintGuard {

    static final long DEFAULT_TRIANGLE_MIN_INTERVAL_MS = 250L;
    static final long DEFAULT_OVERLAY_MIN_INTERVAL_MS  = 200L;
    static final long DEFAULT_OVERRUN_BUDGET_NANOS     = 50_000_000L;       // 50ms
    static final long DEFAULT_DEGRADATION_COOLDOWN_NANOS = 1_000_000_000L;  // 1s

    private final long triangleMinIntervalMs;
    private final long overlayMinIntervalMs;
    private final long overrunBudgetNanos;
    private final long degradationCooldownNanos;

    private long lastTriangleAtMs = Long.MIN_VALUE / 2;
    private long lastOverlayAtMs  = Long.MIN_VALUE / 2;
    private long degradedUntilNanos = 0L;
    private long lastDurationNanos = 0L;
    private int  degradationEnteredCount = 0;
    private int  triangleSkippedCount = 0;
    private int  overlaySkippedCount = 0;

    PaxRepaintGuard() {
        this(DEFAULT_TRIANGLE_MIN_INTERVAL_MS, DEFAULT_OVERLAY_MIN_INTERVAL_MS,
             DEFAULT_OVERRUN_BUDGET_NANOS, DEFAULT_DEGRADATION_COOLDOWN_NANOS);
    }

    PaxRepaintGuard(long triangleMinIntervalMs, long overlayMinIntervalMs,
                    long overrunBudgetNanos, long degradationCooldownNanos) {
        this.triangleMinIntervalMs = Math.max(0L, triangleMinIntervalMs);
        this.overlayMinIntervalMs  = Math.max(0L, overlayMinIntervalMs);
        this.overrunBudgetNanos    = Math.max(1L, overrunBudgetNanos);
        this.degradationCooldownNanos = Math.max(0L, degradationCooldownNanos);
    }

    /** Should the painter run a triangle rebuild now? */
    boolean allowTriangle(long nowMs, long nowNanos) {
        if (isDegraded(nowNanos)) {
            triangleSkippedCount++;
            return false;
        }
        if (nowMs - lastTriangleAtMs < triangleMinIntervalMs) {
            triangleSkippedCount++;
            return false;
        }
        lastTriangleAtMs = nowMs;
        return true;
    }

    /** Should the painter run an overlay rebuild now? */
    boolean allowOverlay(long nowMs, long nowNanos) {
        if (isDegraded(nowNanos)) {
            overlaySkippedCount++;
            return false;
        }
        if (nowMs - lastOverlayAtMs < overlayMinIntervalMs) {
            overlaySkippedCount++;
            return false;
        }
        lastOverlayAtMs = nowMs;
        return true;
    }

    /** Record the duration of the most recent applyNeeds call. Engages
     *  emergency degradation when the duration exceeds the budget. */
    void recordApplyDurationNanos(long durationNanos, long nowNanos) {
        lastDurationNanos = durationNanos;
        if (durationNanos > overrunBudgetNanos) {
            degradedUntilNanos = nowNanos + degradationCooldownNanos;
            degradationEnteredCount++;
        }
    }

    /** Force-clear cooldown timers - used by {@code update()} (full repaint
     *  path) so onMoveEnd / explicit rebuild never gets short-circuited. */
    void resetMinIntervals() {
        lastTriangleAtMs = Long.MIN_VALUE / 2;
        lastOverlayAtMs  = Long.MIN_VALUE / 2;
    }

    boolean isDegraded(long nowNanos) {
        return nowNanos < degradedUntilNanos;
    }

    long lastDurationNanos()      { return lastDurationNanos; }
    int  degradationEnteredCount(){ return degradationEnteredCount; }
    int  triangleSkippedCount()   { return triangleSkippedCount; }
    int  overlaySkippedCount()    { return overlaySkippedCount; }
    long overrunBudgetNanos()     { return overrunBudgetNanos; }
    long triangleMinIntervalMs()  { return triangleMinIntervalMs; }
    long overlayMinIntervalMs()   { return overlayMinIntervalMs; }
}
