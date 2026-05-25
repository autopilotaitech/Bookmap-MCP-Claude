package com.openrange;

import java.util.List;

/**
 * Parser-contract tests for {@code snap["pax_ai_chart_events"]}.
 *
 * Mirrors the assertions style used by
 * {@link PaxInstitutionalChartEventsParseTest}. Each test method is
 * invoked from {@link #main(String[])}; failures throw
 * {@code AssertionError} and propagate.
 */
public class PaxAiChartEventsParseTest {

    public static void main(String[] args) {
        parsesValidPayForTradeLong();
        parsesValidWaitForConfirm();
        parsesValidStandDown();
        emptyArrayYieldsEmpty();
        missingKeyYieldsEmpty();
        healthOfflineYieldsEmpty();
        malformedYieldsEmpty();
        nullAndEmptyYieldEmpty();
        sourceIsAlwaysPaxAi();
        confidenceParses();
        renderableEntryRendersFine();
        System.out.println("PaxAiChartEventsParseTest OK");
    }

    // --- Fixture helpers ---------------------------------------------------

    private static String payLong() {
        return "{"
            + "\"id\":\"pax_ai|abc\","
            + "\"alias\":\"NQM6.CME@RITHMIC\","
            + "\"label\":\"OR-H\","
            + "\"price\":20000.0,"
            + "\"side\":\"above\","
            + "\"event_type\":\"AI_ACCEPTANCE\","
            + "\"direction\":\"LONG\","
            + "\"execution_read\":\"PAY_FOR_TRADE\","
            + "\"marker_text\":\"AI \\u25B2 OR-H 72\","
            + "\"marker_color_hint\":\"#FF40D9\","
            + "\"severity\":\"ENTRY\","
            + "\"timestamp_ms\":1700000000000,"
            + "\"source\":\"pax_ai\","
            + "\"confidence\":0.72"
            + "}";
    }

    private static String waitForConfirm() {
        return "{"
            + "\"id\":\"pax_ai|w1\","
            + "\"alias\":\"NQM6.CME@RITHMIC\","
            + "\"label\":\"OR-H\","
            + "\"price\":20000.0,"
            + "\"side\":\"above\","
            + "\"event_type\":\"AI_WATCH\","
            + "\"direction\":\"NONE\","
            + "\"execution_read\":\"WAIT_FOR_CONFIRM\","
            + "\"marker_text\":\"AI \\u25C6 OR-H 40\","
            + "\"marker_color_hint\":\"#A86DEC\","
            + "\"severity\":\"WATCH\","
            + "\"timestamp_ms\":1700000000001,"
            + "\"source\":\"pax_ai\","
            + "\"confidence\":0.40"
            + "}";
    }

    private static String standDown() {
        return "{"
            + "\"id\":\"pax_ai|sd1\","
            + "\"alias\":\"NQM6.CME@RITHMIC\","
            + "\"label\":\"OR-L\","
            + "\"price\":19950.0,"
            + "\"side\":\"below\","
            + "\"event_type\":\"AI_STAND_DOWN\","
            + "\"direction\":\"NONE\","
            + "\"execution_read\":\"STAND_DOWN\","
            + "\"marker_text\":\"AI \\u25C6 OR-L 60\","
            + "\"marker_color_hint\":\"#A86DEC\","
            + "\"severity\":\"WARNING\","
            + "\"timestamp_ms\":1700000000002,"
            + "\"source\":\"pax_ai\","
            + "\"confidence\":0.60"
            + "}";
    }

    // --- Tests -------------------------------------------------------------

    private static void parsesValidPayForTradeLong() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + payLong() + "]}";
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body);
        if (out.size() != 1) throw new AssertionError("size " + out.size());
        PaxInstitutionalChartEvent e = out.get(0);
        if (!"pax_ai|abc".equals(e.id)) throw new AssertionError("id=" + e.id);
        if (!"LONG".equals(e.direction)) throw new AssertionError("dir=" + e.direction);
        if (!"PAY_FOR_TRADE".equals(e.executionRead)) throw new AssertionError();
        if (!"ENTRY".equals(e.severity)) throw new AssertionError("sev=" + e.severity);
        if (!"AI_ACCEPTANCE".equals(e.eventType)) throw new AssertionError();
        if (Math.abs(e.price - 20000.0) > 1e-6) throw new AssertionError();
    }

    private static void parsesValidWaitForConfirm() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + waitForConfirm() + "]}";
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body);
        if (out.size() != 1) throw new AssertionError();
        PaxInstitutionalChartEvent e = out.get(0);
        if (!"WAIT_FOR_CONFIRM".equals(e.executionRead)) throw new AssertionError();
        if (!"WATCH".equals(e.severity)) throw new AssertionError();
        if (!"NONE".equals(e.direction)) throw new AssertionError();
    }

    private static void parsesValidStandDown() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + standDown() + "]}";
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body);
        if (out.size() != 1) throw new AssertionError();
        if (!"WARNING".equals(out.get(0).severity)) throw new AssertionError();
        if (!"STAND_DOWN".equals(out.get(0).executionRead)) throw new AssertionError();
    }

    private static void emptyArrayYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(
                    "{\"health\":\"ok\",\"pax_ai_chart_events\":[]}");
        if (!out.isEmpty()) throw new AssertionError();
    }

    private static void missingKeyYieldsEmpty() {
        List<PaxInstitutionalChartEvent> out =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents("{\"health\":\"ok\"}");
        if (!out.isEmpty()) throw new AssertionError();
    }

    private static void healthOfflineYieldsEmpty() {
        String body = "{\"health\":\"offline\",\"pax_ai_chart_events\":["
                + payLong() + "]}";
        if (!PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).isEmpty()) {
            throw new AssertionError("offline health must yield empty");
        }
    }

    private static void malformedYieldsEmpty() {
        if (!PaxTrendSignalSnapshotParser.parsePaxAiChartEvents("{bad").isEmpty()) {
            throw new AssertionError("malformed must yield empty");
        }
    }

    private static void nullAndEmptyYieldEmpty() {
        if (!PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(null).isEmpty()) {
            throw new AssertionError("null must yield empty");
        }
        if (!PaxTrendSignalSnapshotParser.parsePaxAiChartEvents("").isEmpty()) {
            throw new AssertionError("empty must yield empty");
        }
    }

    private static void sourceIsAlwaysPaxAi() {
        // Defense in depth: even if a payload tries to spoof source=
        // "institutional_thesis", the parser MUST stamp source=pax_ai
        // since it came from the pax_ai_chart_events key.
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[{"
                + "\"id\":\"x\",\"alias\":\"a\",\"label\":\"OR-H\","
                + "\"price\":1.0,\"side\":\"above\","
                + "\"event_type\":\"AI_ACCEPTANCE\",\"direction\":\"LONG\","
                + "\"execution_read\":\"PAY_FOR_TRADE\",\"marker_text\":\"t\","
                + "\"marker_color_hint\":\"#FFFFFF\",\"severity\":\"ENTRY\","
                + "\"timestamp_ms\":1,\"source\":\"institutional_thesis\","
                + "\"confidence\":0.5}]}";
        PaxInstitutionalChartEvent e =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).get(0);
        if (!"pax_ai".equals(e.source)) {
            throw new AssertionError("source must be forced to pax_ai; got " + e.source);
        }
    }

    private static void confidenceParses() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + payLong() + "]}";
        PaxInstitutionalChartEvent e =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).get(0);
        if (Math.abs(e.confidence - 0.72) > 1e-6) {
            throw new AssertionError("confidence not parsed: " + e.confidence);
        }
    }

    private static void renderableEntryRendersFine() {
        String body = "{\"health\":\"ok\",\"pax_ai_chart_events\":[" + payLong() + "]}";
        PaxInstitutionalChartEvent e =
                PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(body).get(0);
        if (!e.isRenderable()) {
            throw new AssertionError("expected renderable; id=" + e.id
                    + " price=" + e.price + " ts=" + e.timestampMs
                    + " text='" + e.markerText + "'");
        }
    }
}
