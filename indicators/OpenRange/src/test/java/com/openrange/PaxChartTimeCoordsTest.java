package com.openrange;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;

public class PaxChartTimeCoordsTest {

    public static void main(String[] args) {
        zeroMsReturnsZero();
        negativeMsReturnsZero();
        knownEpochMsConvertsToExpectedNanos();
        roundTripWithSameMethodAsOpeningRangeAnchoring();
        System.out.println("PaxChartTimeCoordsTest OK");
    }

    private static void zeroMsReturnsZero() {
        if (PaxChartTimeCoords.epochMsToChartNanos(0L) != 0L)
            throw new AssertionError("zero ms must return zero nanos");
    }

    private static void negativeMsReturnsZero() {
        if (PaxChartTimeCoords.epochMsToChartNanos(-1L) != 0L)
            throw new AssertionError("negative ms must return zero nanos");
        if (PaxChartTimeCoords.epochMsToChartNanos(-1_000_000L) != 0L)
            throw new AssertionError("negative ms must return zero nanos");
    }

    private static void knownEpochMsConvertsToExpectedNanos() {
        // 1_747_680_123_456 ms = 2025-05-19T... in CT. The conversion is:
        // ms → Instant → LocalDateTime in America/Chicago → atZone(CT) → Instant → nanos.
        // The round-trip through LocalDateTime in CT is identity for epoch points
        // because the CT zone offset cancels out (epoch ms → CT LDT → atZone(CT) → same Instant).
        long epochMs = 1_747_680_123_456L;
        long actualNanos = PaxChartTimeCoords.epochMsToChartNanos(epochMs);
        long expectedNanos = epochMs * 1_000_000L;
        if (actualNanos != expectedNanos)
            throw new AssertionError("conversion mismatch: expected " + expectedNanos
                    + " got " + actualNanos);
    }

    private static void roundTripWithSameMethodAsOpeningRangeAnchoring() {
        // Verify the implementation matches the existing toNanos(LocalDateTime)
        // method inside PaxOpeningRangeModule. That method:
        //   Instant instant = time.atZone(EXCHANGE_ZONE).toInstant();
        //   return instant.getEpochSecond() * 1_000_000_000L + instant.getNano();
        // We replicate it here from a known LDT and compare.
        ZoneId ct = ZoneId.of("America/Chicago");
        long epochMs = 1_747_680_123_456L;
        LocalDateTime ldt = LocalDateTime.ofInstant(Instant.ofEpochMilli(epochMs), ct);
        Instant via = ldt.atZone(ct).toInstant();
        long expected = via.getEpochSecond() * 1_000_000_000L + via.getNano();
        long actual = PaxChartTimeCoords.epochMsToChartNanos(epochMs);
        if (actual != expected)
            throw new AssertionError("must match PaxOpeningRangeModule.toNanos(LocalDateTime) convention");
    }
}
