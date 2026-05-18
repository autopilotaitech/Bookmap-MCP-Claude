package com.openrange;

public class PaxOpeningRangeRangeQuality {
    private PaxOpeningRangeRangeQuality() {
    }

    public static String fromPercentile(int percentile) {
        if (percentile <= 0) {
            return "";
        }
        if (percentile <= 20) {
            return "TIGHT";
        }
        if (percentile >= 80) {
            return "WIDE";
        }
        return "OK";
    }
}
