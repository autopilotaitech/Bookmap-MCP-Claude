package com.openrange;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * Centralizes the epoch-ms → chart-data-nanos conversion used by every
 * DATA_ZERO-anchored screen shape. Bookmap's {@code CompositeCoordinateBase.DATA_ZERO}
 * x is measured in chart data nanos relative to the chart's epoch — NOT
 * raw epoch ms. Passing ms directly anchors the shape at January 1970.
 *
 * <p>Mirrors the conversion used inside {@code PaxOpeningRangeModule.toNanos(LocalDateTime)}:
 * convert through {@code America/Chicago} so the result matches the OR
 * line/label anchoring already in use.</p>
 */
final class PaxChartTimeCoords {

    static final ZoneId EXCHANGE_ZONE = ZoneId.of("America/Chicago");

    private PaxChartTimeCoords() {}

    /** Convert epoch ms to chart data nanos. Returns 0 for non-positive ms. */
    static long epochMsToChartNanos(long epochMs) {
        if (epochMs <= 0L) return 0L;
        LocalDateTime ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMs), EXCHANGE_ZONE);
        Instant instant = ldt.atZone(EXCHANGE_ZONE).toInstant();
        return instant.getEpochSecond() * 1_000_000_000L + instant.getNano();
    }
}
