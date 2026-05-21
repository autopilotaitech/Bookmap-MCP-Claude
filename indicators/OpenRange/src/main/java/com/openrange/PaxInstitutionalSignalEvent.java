package com.openrange;

/**
 * Immutable carrier for one institutional signal event parsed from
 * {@code snap["institutional_signals"]}.
 *
 * <p>Distinct from {@link PaxTrendSignalModel} — that class carries the
 * legacy single-best PAY mapping used by the existing trend-triangle dedup.
 * This carrier holds the FULL set of signal events (PAY, WAIT, STAND_DOWN,
 * SCRATCH) so the chart layer can plot non-entry markers with distinct
 * glyphs and keep a durable history that survives empty / NONE polls.</p>
 */
final class PaxInstitutionalSignalEvent {

    final String id;
    final String signalType;
    final String direction;        // LONG / SHORT / NONE
    final String executionRead;    // PAY_FOR_TRADE / WAIT_FOR_CONFIRM / STAND_DOWN / SCRATCH_READY
    final double price;
    final long timestampMs;
    final String label;            // OR-H / OR-L / +1 / etc.
    final double confidence;

    PaxInstitutionalSignalEvent(String id, String signalType, String direction,
                                 String executionRead, double price, long timestampMs,
                                 String label, double confidence) {
        this.id = id == null ? "" : id;
        this.signalType = signalType == null ? "" : signalType;
        this.direction = direction == null ? "NONE" : direction;
        this.executionRead = executionRead == null ? "WAIT_FOR_CONFIRM" : executionRead;
        this.price = price;
        this.timestampMs = timestampMs;
        this.label = label == null ? "" : label;
        this.confidence = confidence;
    }

    boolean isPayLong() {
        return "PAY_FOR_TRADE".equals(executionRead) && "LONG".equals(direction);
    }

    boolean isPayShort() {
        return "PAY_FOR_TRADE".equals(executionRead) && "SHORT".equals(direction);
    }

    boolean isPayEntry() {
        return isPayLong() || isPayShort();
    }

    /** Render-ready: valid id, positive price, positive timestamp. The painter
     *  must NOT plot markers for malformed payloads. */
    boolean isRenderable() {
        return !id.isEmpty()
                && Double.isFinite(price) && price > 0.0
                && timestampMs > 0L;
    }
}
