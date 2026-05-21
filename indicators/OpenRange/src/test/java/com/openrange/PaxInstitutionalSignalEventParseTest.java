package com.openrange;

import java.util.List;

public class PaxInstitutionalSignalEventParseTest {

    public static void main(String[] args) {
        parsesFullPayLongEvent();
        parsesWaitForConfirmEvent();
        parsesStandDownEvent();
        parsesScratchReadyEvent();
        parsesAllEventsInArray();
        emptyArrayYieldsEmptyList();
        missingArrayYieldsEmptyList();
        healthOfflineYieldsEmptyList();
        malformedJsonYieldsEmptyList();
        missingFieldsHandled();
        System.out.println("PaxInstitutionalSignalEventParseTest OK");
    }

    private static void parsesFullPayLongEvent() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":[" + insSignal(
                "idA", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1_000L, "OR-H") + "]}";
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body);
        if (events.size() != 1) {
            throw new AssertionError("expected 1 event; got " + events.size());
        }
        PaxInstitutionalSignalEvent e = events.get(0);
        if (!"idA".equals(e.id)) throw new AssertionError("id mismatch");
        if (!"LONG".equals(e.direction)) throw new AssertionError("direction mismatch");
        if (!"PAY_FOR_TRADE".equals(e.executionRead)) throw new AssertionError("exec mismatch");
        if (!"ACCEPTANCE_LONG".equals(e.signalType)) throw new AssertionError("type mismatch");
        if (Math.abs(e.price - 20000.0) > 1e-9) throw new AssertionError("price mismatch");
        if (e.timestampMs != 1_000L) throw new AssertionError("ts mismatch");
        if (!"OR-H".equals(e.label)) throw new AssertionError("label mismatch");
        if (Math.abs(e.confidence - 0.80) > 1e-9) throw new AssertionError("conf mismatch");
        if (!e.isPayLong()) throw new AssertionError("isPayLong must be true");
        if (!e.isRenderable()) throw new AssertionError("renderable must be true");
    }

    private static void parsesWaitForConfirmEvent() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":[" + insSignal(
                "idW", "NONE", "STOP_SWEEP_LONG", "WAIT_FOR_CONFIRM",
                "NONE", 0.35, 20000.0, 2_000L, "OR-H") + "]}";
        PaxInstitutionalSignalEvent e =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body).get(0);
        if (!"WAIT_FOR_CONFIRM".equals(e.executionRead)) throw new AssertionError("exec mismatch");
        if (e.isPayEntry()) throw new AssertionError("WAIT must not be PAY entry");
    }

    private static void parsesStandDownEvent() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":[" + insSignal(
                "idS", "NONE", "ICEBERG_DEFENSE", "STAND_DOWN",
                "NONE", 0.80, 20000.0, 3_000L, "OR-H") + "]}";
        PaxInstitutionalSignalEvent e =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body).get(0);
        if (!"STAND_DOWN".equals(e.executionRead)) throw new AssertionError("exec mismatch");
        if (!"ICEBERG_DEFENSE".equals(e.signalType)) throw new AssertionError("type mismatch");
    }

    private static void parsesScratchReadyEvent() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":[" + insSignal(
                "idC", "NONE", "SCRATCH", "SCRATCH_READY",
                "NONE", 0.55, 20000.0, 4_000L, "OR-H") + "]}";
        PaxInstitutionalSignalEvent e =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body).get(0);
        if (!"SCRATCH_READY".equals(e.executionRead)) throw new AssertionError("exec mismatch");
    }

    private static void parsesAllEventsInArray() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + insSignal("idA", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                        "FULL", 0.80, 20000.0, 1_000L, "OR-H") + ","
                + insSignal("idB", "NONE", "STOP_SWEEP_LONG", "WAIT_FOR_CONFIRM",
                        "NONE", 0.35, 20100.0, 2_000L, "+1") + ","
                + insSignal("idC", "NONE", "ICEBERG_DEFENSE", "STAND_DOWN",
                        "NONE", 0.80, 19950.0, 3_000L, "OR-L")
                + "]}";
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body);
        if (events.size() != 3) {
            throw new AssertionError("expected 3 events; got " + events.size());
        }
        if (!"idA".equals(events.get(0).id)
                || !"idB".equals(events.get(1).id)
                || !"idC".equals(events.get(2).id)) {
            throw new AssertionError("event order not preserved");
        }
    }

    private static void emptyArrayYieldsEmptyList() {
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(
                        "{\"health\":\"ok\",\"institutional_signals\":[]}");
        if (!events.isEmpty()) {
            throw new AssertionError("empty array must yield empty list");
        }
    }

    private static void missingArrayYieldsEmptyList() {
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents("{\"health\":\"ok\"}");
        if (!events.isEmpty()) {
            throw new AssertionError("missing institutional_signals must yield empty list");
        }
    }

    private static void healthOfflineYieldsEmptyList() {
        // Even with valid signals, offline health gates rendering.
        String body = "{\"health\":\"offline\",\"institutional_signals\":[" + insSignal(
                "idA", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1_000L, "OR-H") + "]}";
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body);
        if (!events.isEmpty()) {
            throw new AssertionError("health!=ok must yield empty list");
        }
    }

    private static void malformedJsonYieldsEmptyList() {
        // parseInstitutionalEvents is forgiving (does NOT throw on bad input).
        List<PaxInstitutionalSignalEvent> e1 =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents("{bad");
        if (!e1.isEmpty()) throw new AssertionError("malformed must yield empty");
        List<PaxInstitutionalSignalEvent> e2 =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(null);
        if (!e2.isEmpty()) throw new AssertionError("null must yield empty");
        List<PaxInstitutionalSignalEvent> e3 =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents("");
        if (!e3.isEmpty()) throw new AssertionError("empty string must yield empty");
    }

    private static void missingFieldsHandled() {
        // Partial signal — missing direction, executionRead, label.
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + "{\"id\":\"idX\",\"price\":20000.0,\"timestamp_ms\":1000}]}";
        List<PaxInstitutionalSignalEvent> events =
                PaxTrendSignalSnapshotParser.parseInstitutionalEvents(body);
        if (events.size() != 1) throw new AssertionError("size mismatch");
        PaxInstitutionalSignalEvent e = events.get(0);
        // Defaults applied by the carrier.
        if (!"NONE".equals(e.direction)) throw new AssertionError("default direction");
        if (!"WAIT_FOR_CONFIRM".equals(e.executionRead)) throw new AssertionError("default exec");
    }

    // ─── Builder ──────────────────────────────────────────────────────────

    private static String insSignal(String id, String direction, String signalType,
                                     String executionRead, String sizeTier,
                                     double confidence, double price, long timestampMs,
                                     String label) {
        return "{"
                + "\"id\":\"" + id + "\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"label\":\"" + label + "\","
                + "\"price\":" + price + ","
                + "\"side\":\"above\","
                + "\"direction\":\"" + direction + "\","
                + "\"signal_type\":\"" + signalType + "\","
                + "\"execution_read\":\"" + executionRead + "\","
                + "\"confidence\":" + confidence + ","
                + "\"size_tier\":\"" + sizeTier + "\","
                + "\"timestamp_ms\":" + timestampMs
                + "}";
    }
}
