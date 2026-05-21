package com.openrange;

/**
 * Immutable carrier for one event parsed from
 * {@code snap["institutional_chart_events"]}.
 *
 * <p>The chart-events payload is the FULL evidence trail (sweeps,
 * absorption, iceberg defense, spoof risk, pull/stack, watch/touch,
 * acceptance, rejection, scratch) the trader reads on the Bookmap chart.
 * Distinct from {@link PaxInstitutionalSignalEvent} which is the
 * entry-only trade-decision payload.</p>
 *
 * <p>Severity ranks (lower = visually higher priority): ENTRY=0, EXIT=1,
 * WARNING=2, WATCH=3, INFO=4. The painter staggers y-offsets by rank so
 * overlapping events at the same level / time don't cover each other.</p>
 *
 * <p>Spec: {@code docs/superpowers/specs/institutional-chart-markers.md}.</p>
 */
final class PaxInstitutionalChartEvent {

    final String id;
    final String alias;
    final String label;
    final double price;
    final String side;
    final String eventType;
    final String direction;         // LONG / SHORT / NONE
    final String executionRead;
    final String markerText;
    final String markerColorHint;   // hex "#RRGGBB"
    final String severity;          // ENTRY / EXIT / WARNING / WATCH / INFO
    final long timestampMs;
    final String source;            // micro_events / tape_flow / pull_stack / ...
    final double confidence;

    PaxInstitutionalChartEvent(String id, String alias, String label, double price,
                                String side, String eventType, String direction,
                                String executionRead, String markerText,
                                String markerColorHint, String severity,
                                long timestampMs, String source, double confidence) {
        this.id = id == null ? "" : id;
        this.alias = alias == null ? "" : alias;
        this.label = label == null ? "" : label;
        this.price = price;
        this.side = side == null ? "above" : side;
        this.eventType = eventType == null ? "" : eventType;
        this.direction = direction == null ? "NONE" : direction;
        this.executionRead = executionRead == null ? "WAIT_FOR_CONFIRM" : executionRead;
        this.markerText = markerText == null ? "" : markerText;
        this.markerColorHint = markerColorHint == null ? "#B0B0B0" : markerColorHint;
        this.severity = severity == null ? "INFO" : severity;
        this.timestampMs = timestampMs;
        this.source = source == null ? "" : source;
        this.confidence = confidence;
    }

    /** Render-ready: valid id, finite positive price, positive timestamp,
     *  non-empty marker text. */
    boolean isRenderable() {
        return !id.isEmpty()
                && Double.isFinite(price) && price > 0.0
                && timestampMs > 0L
                && !markerText.isEmpty();
    }

    /** Lower rank = higher visual priority (drawn closer to the level). */
    int severityRank() {
        switch (severity) {
            case "ENTRY":   return 0;
            case "EXIT":    return 1;
            case "WARNING": return 2;
            case "WATCH":   return 3;
            case "INFO":    return 4;
            default:        return 5;
        }
    }

    /** Parse the leading "#RRGGBB" hex hint into an AWT Color with full alpha.
     *  Falls back to gray on any malformed input. */
    java.awt.Color colorFromHint() {
        try {
            String s = markerColorHint == null ? "" : markerColorHint.trim();
            if (s.startsWith("#") && s.length() == 7) {
                int rgb = Integer.parseInt(s.substring(1), 16);
                return new java.awt.Color((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
            }
        } catch (NumberFormatException ignored) { /* fall through */ }
        return new java.awt.Color(176, 176, 176);
    }
}
