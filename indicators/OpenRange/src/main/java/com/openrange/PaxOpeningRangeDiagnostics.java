package com.openrange;

public class PaxOpeningRangeDiagnostics {
    private PaxOpeningRangeDiagnostics() {
    }

    public static String format(PaxOpeningRangeFeatureCache cache, long nowNanos, String csvPath) {
        int snapshotCount = cache == null ? 0 : cache.history().size();
        PaxOpeningRangeFeatureSnapshot latest = cache == null ? null : cache.latest();
        if (latest == null) {
            return "Snapshots: " + snapshotCount
                    + "\nLast update: none"
                    + "\nCVD: n/a"
                    + "\nPS: n/a"
                    + "\nRange: n/a"
                    + "\nCSV: " + value(csvPath);
        }

        PaxOpeningRangeSignal signal = latest.signal();
        return "Snapshots: " + snapshotCount
                + "\nLast update: " + age(nowNanos, latest.timeNanos())
                + "\nCVD: p" + signal.cvdPercentile()
                + "\nPS: p" + signal.pullingStackingPercentile()
                + "\nRange: " + value(signal.rangeQuality())
                + "\nCSV: " + value(csvPath);
    }

    private static String age(long nowNanos, long updateNanos) {
        if (updateNanos <= 0 || nowNanos < updateNanos) {
            return "none";
        }
        long seconds = (nowNanos - updateNanos) / 1_000_000_000L;
        return seconds + "s ago";
    }

    private static String value(String text) {
        return text == null || text.isBlank() ? "n/a" : text;
    }
}
