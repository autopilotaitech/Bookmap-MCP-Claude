package com.bookmapmcp.trend;

/**
 * Immutable JSON-shaped carrier for {@code GET /trend_analyzer?alias=...}.
 *
 * <p>Timestamp semantics — do NOT conflate:</p>
 * <ul>
 *   <li>{@code asOfNanos}: raw Bookmap event nanos if known, diagnostic only.
 *       0 when the bridge has no nanos source.</li>
 *   <li>{@code eventMs}: epoch ms of the last trade event. Used by the chart
 *       painter for anchoring. Survives playback / historical data.</li>
 *   <li>{@code updatedAtMs}: wall-clock epoch ms when the bridge built this
 *       snapshot. Used by the dashboard for staleness gating only.</li>
 * </ul>
 *
 * <p>{@code score} and {@code reliabilityHint} are diagnostic mirrors of what
 * the dashboard will compute. The dashboard is the single source of truth for
 * conviction math; this DTO exists so the JSON has self-describing values for
 * humans / replay tools without needing to re-derive them.</p>
 */
public record TrendAnalyzerSnapshot(
        String alias,
        long asOfNanos,
        long eventMs,
        long updatedAtMs,
        double lastClose,
        boolean warmedUp,
        double score,
        double reliabilityHint,
        Leg fast,
        Leg slow) {

    public record Leg(
            TrendDirection direction,
            int confidence,
            boolean switched,
            boolean chop,
            double trendLine,
            long candleIntervalMillis,
            long candleCount) {

        public int directionSign() {
            return direction == null ? 0 : direction.sign();
        }

        public static Leg warmup(long candleIntervalMillis) {
            return new Leg(TrendDirection.NEUTRAL, 0, false, true, Double.NaN, candleIntervalMillis, 0L);
        }
    }

    public static TrendAnalyzerSnapshot warmup(String alias, long updatedAtMs,
            long fastIntervalMs, long slowIntervalMs) {
        return new TrendAnalyzerSnapshot(alias, 0L, 0L, updatedAtMs, Double.NaN, false, 0.0, 0.0,
                Leg.warmup(fastIntervalMs), Leg.warmup(slowIntervalMs));
    }
}
