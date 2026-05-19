package com.openrange;

/**
 * Immutable carrier for {@code snap["trend_signal"]} from the dashboard's
 * {@code /api/snapshot} endpoint.
 *
 * <p>Timestamp semantics:</p>
 * <ul>
 *   <li>{@code eventMs}: epoch ms of the underlying market event. Used by
 *       the chart painter for anchoring. May be 0 if upstream had no event.</li>
 *   <li>{@code asOfMs}: dashboard wall-clock at projection time. NOT used
 *       for staleness gating here — see {@code fetchedAtMs}.</li>
 *   <li>{@code fetchedAtMs}: local wall-clock at HTTP response receive time.
 *       This is the ONLY timestamp the fetcher / painter should use for
 *       staleness checks, because asOfMs may not advance when the dashboard
 *       is paused or under playback.</li>
 *   <li>{@code bucketEnteredMs}: dashboard wall-clock when this {@code kind}
 *       was first entered. Used for dedup ({@code (kind, bucketEnteredMs)}
 *       is the dedup key).</li>
 * </ul>
 *
 * <p>Eligibility semantics:</p>
 * <ul>
 *   <li>{@code eligible}: hard gate added by the dashboard's signal policy.
 *       A signal is eligible only when the projected kind is renderable AND
 *       mid is finite-and-positive AND eventMs > 0. The painter MUST NOT
 *       render unless this is true.</li>
 *   <li>{@code blockedReason}: when eligible is false on a conviction that
 *       would otherwise be renderable, this carries the reason
 *       (e.g. "invalid_mid", "invalid_event_ms"). Empty / null when
 *       eligible is true or when conviction was non-renderable to begin with.</li>
 *   <li>{@code eventMsSource}: "trend_analyzer" when eventMs came from
 *       snap["trend_analyzer"].eventMs; "wall_clock_fallback" otherwise.
 *       Empty for NO_DATA / offline carriers.</li>
 * </ul>
 */
final class PaxTrendSignalModel {

    enum Kind {
        STRONG_BULL,
        WEAK_BULL,
        STRONG_BEAR,
        WEAK_BEAR,
        NONE;

        boolean isBull() { return this == STRONG_BULL || this == WEAK_BULL; }
        boolean isBear() { return this == STRONG_BEAR || this == WEAK_BEAR; }
        boolean isStrong() { return this == STRONG_BULL || this == STRONG_BEAR; }
        boolean isRenderable() { return this != NONE; }

        static Kind from(String s) {
            if (s == null) return NONE;
            switch (s) {
                case "STRONG_BULL": return STRONG_BULL;
                case "WEAK_BULL":   return WEAK_BULL;
                case "STRONG_BEAR": return STRONG_BEAR;
                case "WEAK_BEAR":   return WEAK_BEAR;
                default: return NONE;
            }
        }
    }

    final Kind kind;
    final String alias;
    final double mid;            // NaN when book.mid was not available upstream
    final long eventMs;          // 0 when upstream had no event time
    final long asOfMs;           // dashboard wall-clock at projection time
    final long bucketEnteredMs;
    final boolean changed;       // changedSinceLastTick from upstream
    final long fetchedAtMs;      // local wall-clock at HTTP response receive
    final boolean eligible;      // dashboard's hard plot-eligibility gate
    final String blockedReason;  // populated when a renderable kind was downgraded
    final String eventMsSource;  // "trend_analyzer" | "wall_clock_fallback" | ""

    /** Full constructor — used by parser and tests that need to pin eligible. */
    PaxTrendSignalModel(Kind kind, String alias, double mid, long eventMs,
            long asOfMs, long bucketEnteredMs, boolean changed, long fetchedAtMs,
            boolean eligible, String blockedReason, String eventMsSource) {
        this.kind = kind == null ? Kind.NONE : kind;
        this.alias = alias == null ? "" : alias;
        this.mid = mid;
        this.eventMs = eventMs;
        this.asOfMs = asOfMs;
        this.bucketEnteredMs = bucketEnteredMs;
        this.changed = changed;
        this.fetchedAtMs = fetchedAtMs;
        this.eligible = eligible;
        this.blockedReason = blockedReason == null ? "" : blockedReason;
        this.eventMsSource = eventMsSource == null ? "" : eventMsSource;
    }

    /** Backward-compatible 8-arg constructor.
     *
     * <p>Defaults {@code eligible} to {@code false} for safety. Callers that
     * need a renderable signal in tests must use the 11-arg constructor.
     * This default ensures that any code path which forgot to plumb the new
     * field is treated as "do not render" rather than as "render anyway".</p>
     */
    PaxTrendSignalModel(Kind kind, String alias, double mid, long eventMs,
            long asOfMs, long bucketEnteredMs, boolean changed, long fetchedAtMs) {
        this(kind, alias, mid, eventMs, asOfMs, bucketEnteredMs, changed, fetchedAtMs,
             /*eligible=*/false, /*blockedReason=*/"", /*eventMsSource=*/"");
    }

    /** Sentinel for "no successful fetch yet" or "dashboard returned
     * non-ok health". Always ineligible. */
    static PaxTrendSignalModel none(long fetchedAtMs) {
        return new PaxTrendSignalModel(Kind.NONE, "", Double.NaN, 0L, 0L, 0L, false, fetchedAtMs,
                /*eligible=*/false, /*blockedReason=*/"", /*eventMsSource=*/"");
    }

    boolean isStale(long nowMs, long staleAgeMs) {
        return (nowMs - fetchedAtMs) >= staleAgeMs;
    }
}
