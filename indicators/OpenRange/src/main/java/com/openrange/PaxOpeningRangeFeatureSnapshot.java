package com.openrange;

public record PaxOpeningRangeFeatureSnapshot(
        long timeNanos,
        PaxOpeningRangeMarketState market,
        PaxOpeningRangeSignal signal,
        String badgeText,
        PaxOpeningRangeSignalColorState colorState) {
}
