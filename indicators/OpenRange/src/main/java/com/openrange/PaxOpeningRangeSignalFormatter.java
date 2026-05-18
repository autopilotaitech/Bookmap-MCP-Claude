package com.openrange;

public class PaxOpeningRangeSignalFormatter {
    private PaxOpeningRangeSignalFormatter() {
    }

    public static String format(PaxOpeningRangeSignal signal) {
        if (signal == null) {
            return "WAIT | No signal";
        }

        String direction = signal.bias().toString();
        String action = actionText(signal.action());
        String confidence = signal.confidence().toString();
        String reason = reasonText(signal.reason());

        if (hasTelemetry(signal)) {
            return telemetryText(signal);
        }
        if (signal.bias() == PaxOpeningRangeSignalBias.NEUTRAL) {
            return neutralText(action, signal.reason(), reason);
        }
        if (signal.maxScore() > 0 && signal.evidence() != null && !signal.evidence().isBlank()) {
            return direction + " " + signal.score() + "/" + signal.maxScore() + " " + action
                    + " | " + signal.evidence();
        }
        return direction + " " + action + " " + confidence + " | " + reason;
    }

    private static String actionText(PaxOpeningRangeSignalAction action) {
        if (action == PaxOpeningRangeSignalAction.ALLOW_SIGNAL) {
            return "OK";
        }
        if (action == PaxOpeningRangeSignalAction.BLOCK_SIGNAL) {
            return "BLOCK";
        }
        return "WAIT";
    }

    private static String reasonText(String reason) {
        if (reason == null || reason.isBlank()) {
            return "No reason";
        }
        if (reason.contains("above ORH") && reason.contains("strong")) {
            return "Above ORH + flow strong";
        }
        if (reason.contains("above ORH") && reason.contains("acceptable")) {
            return "Above ORH + flow ok";
        }
        if (reason.contains("above ORH") && reason.contains("weak")) {
            return "Above ORH + flow weak";
        }
        if (reason.contains("below ORL") && reason.contains("strong")) {
            return "Below ORL + flow strong";
        }
        if (reason.contains("below ORL") && reason.contains("acceptable")) {
            return "Below ORL + flow ok";
        }
        if (reason.contains("below ORL") && reason.contains("weak")) {
            return "Below ORL + flow weak";
        }
        if (reason.contains("inside opening range")) {
            return "Inside OR";
        }
        if (reason.contains("not complete")) {
            return "OR building";
        }
        if (reason.contains("market data")) {
            return "Waiting data";
        }
        if (reason.contains("less than")) {
            return "Too close";
        }
        if (reason.contains("more than")) {
            return "Extended";
        }
        return reason.length() <= 28 ? reason : reason.substring(0, 25) + "...";
    }

    private static String neutralText(String action, String rawReason, String compactReason) {
        if (rawReason != null && rawReason.contains("more than")) {
            return "EXTENDED | >" + firstNumber(rawReason) + " ticks";
        }
        if (rawReason != null && rawReason.contains("less than")) {
            return "WAIT | <" + firstNumber(rawReason) + " ticks";
        }
        return action + " | " + compactReason;
    }

    private static String firstNumber(String text) {
        StringBuilder digits = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char ch = text.charAt(i);
            if (Character.isDigit(ch)) {
                digits.append(ch);
            } else if (digits.length() > 0) {
                break;
            }
        }
        return digits.length() == 0 ? "?" : digits.toString();
    }

    private static boolean hasTelemetry(PaxOpeningRangeSignal signal) {
        return signal.location() != null && !signal.location().isBlank();
    }

    private static String telemetryText(PaxOpeningRangeSignal signal) {
        return signal.location() + " " + signedTicks(signal.distanceTicks())
                + " RNG " + oneDecimal(signal.rangeWidth())
                + rangeQualityText(signal.rangeQuality())
                + " AGE " + ageText(signal.ageSeconds())
                + "\n"
                + "CVD " + metricText(signal.cvdDelta(), signal.cvdZScore(), signal.cvdPercentile()) + directionMarker(signal.cvdDelta(), signal.cvdZScore())
                + "  PS " + metricText(signal.pullingStackingDelta(), signal.pullingStackingZScore(), signal.pullingStackingPercentile()) + directionMarker(signal.pullingStackingDelta(), signal.pullingStackingZScore());
    }

    private static String signedTicks(int value) {
        return (value > 0 ? "+" : "") + value + "t";
    }

    private static String signedNumber(double value) {
        long rounded = Math.round(value);
        return (rounded > 0 ? "+" : "") + rounded;
    }

    private static String metricText(double rawValue, double zScore, int percentile) {
        return "p" + Math.max(0, Math.min(100, percentile));
    }

    private static String signedDecimal(double value) {
        String formatted = String.format("%.1f", value);
        return value > 0 ? "+" + formatted : formatted;
    }

    private static String oneDecimal(double value) {
        return String.format("%.1f", value);
    }

    private static String rangeQualityText(String rangeQuality) {
        return rangeQuality == null || rangeQuality.isBlank() ? "" : " " + rangeQuality;
    }

    private static String ageText(long seconds) {
        if (seconds < 60) {
            return seconds + "s";
        }
        return (seconds / 60) + "m";
    }

    private static String directionMarker(double rawValue, double zScore) {
        double value = Math.abs(zScore) > 0.0000001 ? zScore : rawValue;
        if (value > 0) {
            return " UP";
        }
        if (value < 0) {
            return " DN";
        }
        return " FLAT";
    }

}
