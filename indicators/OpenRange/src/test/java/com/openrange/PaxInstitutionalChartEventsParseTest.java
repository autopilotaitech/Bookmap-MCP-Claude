package com.openrange;

import java.util.List;

public class PaxInstitutionalChartEventsParseTest {

    public static void main(String[] args) {
        parsesAllTenEventTypes();
        emptyArrayYieldsEmpty();
        missingArrayYieldsEmpty();
        healthOfflineYieldsEmpty();
        malformedYieldsEmpty();
        partialFieldsHandled();
        colorHintParsesToColor();
        severityRanksAreCorrect();
        System.out.println("PaxInstitutionalChartEventsParseTest OK");
    }

    private static void parsesAllTenEventTypes() {
        // One of each event type so we know the parser can read the full
        // taxonomy without dropping events.
        StringBuilder sb = new StringBuilder();
        sb.append("{\"health\":\"ok\",\"institutional_chart_events\":[")
          .append(ev("WATCH_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                  "WATCH", "#E5C100", "OR-H", 20000.0, 1L)).append(",")
          .append(ev("TOUCHED_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                  "TCH", "#E5C100", "OR-H", 20000.0, 2L)).append(",")
          .append(ev("LIQUIDITY_SWEEP", "WARNING", "NONE", "WAIT_FOR_CONFIRM",
                  "SWP↑", "#FF9900", "OR-H", 20000.0, 3L)).append(",")
          .append(ev("ABSORPTION", "WARNING", "NONE", "STAND_DOWN",
                  "ABS-B", "#00BFFF", "OR-H", 20000.0, 4L)).append(",")
          .append(ev("ICEBERG_DEFENSE", "WARNING", "NONE", "STAND_DOWN",
                  "ICE-A", "#A86DEC", "OR-H", 20000.0, 5L)).append(",")
          .append(ev("SPOOF_RISK", "WARNING", "NONE", "STAND_DOWN",
                  "SPD", "#FF6633", "OR-H", 20000.0, 6L)).append(",")
          .append(ev("PULLING", "INFO", "NONE", "CONTEXT",
                  "PULL", "#B0B0B0", "OR-H", 20000.0, 7L)).append(",")
          .append(ev("STACKING", "INFO", "NONE", "CONTEXT",
                  "STACK", "#B0B0B0", "OR-H", 20000.0, 8L)).append(",")
          .append(ev("ACCEPTANCE", "ENTRY", "LONG", "PAY_FOR_TRADE",
                  "ACC-L", "#2BD25B", "OR-H", 20000.0, 9L)).append(",")
          .append(ev("REJECTION", "ENTRY", "SHORT", "PAY_FOR_TRADE",
                  "REJ-S", "#FF4D4D", "OR-H", 20000.0, 10L)).append(",")
          .append(ev("SCRATCH", "EXIT", "NONE", "SCRATCH_READY",
                  "SCR", "#B0B0B0", "OR-H", 20000.0, 11L))
          .append("]}");
        List<PaxInstitutionalChartEvent> out = PaxTrendSignalSnapshotParser.parseChartEvents(sb.toString());
        if (out.size() != 11) {
            throw new AssertionError("expected 11 events; got " + out.size());
        }
        // Each event should be renderable.
        for (PaxInstitutionalChartEvent e : out) {
            if (!e.isRenderable()) {
                throw new AssertionError("event " + e.eventType + " not renderable");
            }
        }
    }

    private static void emptyArrayYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out = PaxTrendSignalSnapshotParser.parseChartEvents(
                "{\"health\":\"ok\",\"institutional_chart_events\":[]}");
        if (!out.isEmpty()) throw new AssertionError("empty array must yield empty");
    }

    private static void missingArrayYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out = PaxTrendSignalSnapshotParser.parseChartEvents(
                "{\"health\":\"ok\"}");
        if (!out.isEmpty()) throw new AssertionError("missing array must yield empty");
    }

    private static void healthOfflineYieldsEmpty() {
        String body = "{\"health\":\"offline\",\"institutional_chart_events\":[" +
                ev("ACCEPTANCE", "ENTRY", "LONG", "PAY_FOR_TRADE", "ACC-L",
                        "#2BD25B", "OR-H", 20000.0, 1L) + "]}";
        if (!PaxTrendSignalSnapshotParser.parseChartEvents(body).isEmpty()) {
            throw new AssertionError("health=offline must yield empty");
        }
    }

    private static void malformedYieldsEmpty() {
        if (!PaxTrendSignalSnapshotParser.parseChartEvents("{bad").isEmpty()) {
            throw new AssertionError("malformed must yield empty");
        }
        if (!PaxTrendSignalSnapshotParser.parseChartEvents(null).isEmpty()) {
            throw new AssertionError("null must yield empty");
        }
        if (!PaxTrendSignalSnapshotParser.parseChartEvents("").isEmpty()) {
            throw new AssertionError("empty string must yield empty");
        }
    }

    private static void partialFieldsHandled() {
        String body = "{\"health\":\"ok\",\"institutional_chart_events\":["
                + "{\"id\":\"idX\",\"price\":20000.0,\"timestamp_ms\":1000,"
                + "\"event_type\":\"WATCH_LEVEL\",\"marker_text\":\"WATCH\"}]}";
        List<PaxInstitutionalChartEvent> out = PaxTrendSignalSnapshotParser.parseChartEvents(body);
        if (out.size() != 1) throw new AssertionError("size mismatch");
        PaxInstitutionalChartEvent e = out.get(0);
        if (!"NONE".equals(e.direction)) throw new AssertionError("default direction");
        if (!"WAIT_FOR_CONFIRM".equals(e.executionRead)) throw new AssertionError("default exec");
    }

    private static void colorHintParsesToColor() {
        String body = "{\"health\":\"ok\",\"institutional_chart_events\":[" +
                ev("LIQUIDITY_SWEEP", "WARNING", "NONE", "WAIT_FOR_CONFIRM",
                        "SWP↑", "#FF9900", "OR-H", 20000.0, 1L) + "]}";
        PaxInstitutionalChartEvent e =
                PaxTrendSignalSnapshotParser.parseChartEvents(body).get(0);
        java.awt.Color c = e.colorFromHint();
        if (c.getRed() != 0xFF || c.getGreen() != 0x99 || c.getBlue() != 0x00) {
            throw new AssertionError("color parse failed: " + c);
        }
    }

    private static void severityRanksAreCorrect() {
        if (sample("ENTRY").severityRank() != 0) throw new AssertionError("ENTRY=0");
        if (sample("EXIT").severityRank() != 1) throw new AssertionError("EXIT=1");
        if (sample("WARNING").severityRank() != 2) throw new AssertionError("WARNING=2");
        if (sample("WATCH").severityRank() != 3) throw new AssertionError("WATCH=3");
        if (sample("INFO").severityRank() != 4) throw new AssertionError("INFO=4");
    }

    private static PaxInstitutionalChartEvent sample(String severity) {
        return new PaxInstitutionalChartEvent("id", "alias", "OR-H", 20000.0, "above",
                "WATCH_LEVEL", "NONE", "WAIT_FOR_CONFIRM", "WATCH", "#E5C100",
                severity, 1L, "institutional_thesis", 0.35);
    }

    private static String ev(String eventType, String severity, String direction,
                              String executionRead, String markerText, String color,
                              String label, double price, long ts) {
        return "{"
                + "\"id\":\"id-" + eventType + "-" + ts + "\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"label\":\"" + label + "\","
                + "\"price\":" + price + ","
                + "\"side\":\"above\","
                + "\"event_type\":\"" + eventType + "\","
                + "\"direction\":\"" + direction + "\","
                + "\"execution_read\":\"" + executionRead + "\","
                + "\"marker_text\":\"" + markerText + "\","
                + "\"marker_color_hint\":\"" + color + "\","
                + "\"severity\":\"" + severity + "\","
                + "\"timestamp_ms\":" + ts + ","
                + "\"source\":\"institutional_thesis\","
                + "\"confidence\":0.5,"
                + "\"reason_codes\":[]"
                + "}";
    }
}
