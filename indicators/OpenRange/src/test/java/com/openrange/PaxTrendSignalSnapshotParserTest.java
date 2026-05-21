package com.openrange;

public class PaxTrendSignalSnapshotParserTest {

    public static void main(String[] args) {
        // Institutional signal contract (authoritative)
        payLongFullMapsToStrongBull();
        payLongHalfMapsToWeakBull();
        payLongHighConfidenceMapsToStrongBull();
        payShortFullMapsToStrongBear();
        payShortHalfMapsToWeakBear();
        signalPriceBecomesModelMid();
        signalTimestampMsBecomesEventMs();
        signalIdDrivesBucketEnteredMs();
        eventMsSourceIsInstitutionalSignal();

        // Suppression rules
        waitForConfirmProducesNone();
        standDownProducesNone();
        scratchReadyProducesNone();
        directionNoneProducesNone();
        emptyInstitutionalSignalsProducesNone();
        missingInstitutionalSignalsProducesNone();

        // Hard no-fallback rules
        trendSignalStrongBullAloneProducesNone();
        paxDecisionEnterLongAloneProducesNone();
        bothTrendAndPaxWithoutInstitutionalProducesNone();

        // Multi-signal selection
        mostRecentPayForTradeWins();
        nonPayEntriesAreSkipped();

        // Misc safety
        missingPriceProducesNone();
        nonNumericPriceProducesNone();
        healthOfflineSuppressesEverything();
        emptyJsonRaises();
        malformedJsonRaises();
        nonObjectRootRaises();

        System.out.println("PaxTrendSignalSnapshotParserTest OK");
    }

    // ─── Institutional-signal happy paths ──────────────────────────────────

    private static void payLongFullMapsToStrongBull() {
        String body = baseSnap(insSignal(
                "NQM6.CME@RITHMIC|OR-H|above|1779385351000",
                "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 9999L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL) {
            throw new AssertionError("PAY LONG FULL must map to STRONG_BULL; got " + m.kind);
        }
        if (!m.eligible) throw new AssertionError("institutional marker must be eligible");
    }

    private static void payLongHalfMapsToWeakBull() {
        String body = baseSnap(insSignal(
                "id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "HALF", 0.45, 20000.0, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.WEAK_BULL) {
            throw new AssertionError("PAY LONG HALF must map to WEAK_BULL; got " + m.kind);
        }
    }

    private static void payLongHighConfidenceMapsToStrongBull() {
        // HALF size_tier but confidence >= 0.70 still escalates to STRONG.
        String body = baseSnap(insSignal(
                "id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "HALF", 0.72, 20000.0, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL) {
            throw new AssertionError("confidence>=0.70 must escalate to STRONG; got " + m.kind);
        }
    }

    private static void payShortFullMapsToStrongBear() {
        String body = baseSnap(insSignal(
                "id1", "SHORT", "ACCEPTANCE_SHORT", "PAY_FOR_TRADE",
                "FULL", 0.80, 19950.0, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BEAR) {
            throw new AssertionError("PAY SHORT FULL must map to STRONG_BEAR; got " + m.kind);
        }
    }

    private static void payShortHalfMapsToWeakBear() {
        String body = baseSnap(insSignal(
                "id1", "SHORT", "REJECTION_SHORT", "PAY_FOR_TRADE",
                "HALF", 0.40, 20000.0, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.WEAK_BEAR) {
            throw new AssertionError("PAY SHORT HALF must map to WEAK_BEAR; got " + m.kind);
        }
    }

    private static void signalPriceBecomesModelMid() {
        String body = baseSnap(insSignal(
                "id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20007.25, 1779385351000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (Math.abs(m.mid - 20007.25) > 1e-9) {
            throw new AssertionError("signal.price must become model.mid; got " + m.mid);
        }
    }

    private static void signalTimestampMsBecomesEventMs() {
        String body = baseSnap(insSignal(
                "id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1779385351999L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.eventMs != 1779385351999L) {
            throw new AssertionError("signal.timestamp_ms must become eventMs; got " + m.eventMs);
        }
    }

    private static void signalIdDrivesBucketEnteredMs() {
        String idA = "NQM6.CME@RITHMIC|OR-H|above|111";
        String idB = "NQM6.CME@RITHMIC|OR-H|above|222";
        PaxTrendSignalModel a = PaxTrendSignalSnapshotParser.parse(baseSnap(insSignal(
                idA, "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1000L)), 1L);
        PaxTrendSignalModel b = PaxTrendSignalSnapshotParser.parse(baseSnap(insSignal(
                idB, "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1000L)), 1L);
        if (a.bucketEnteredMs == b.bucketEnteredMs) {
            throw new AssertionError("distinct signal ids must yield distinct buckets");
        }
        // Same id -> same bucket.
        PaxTrendSignalModel aRepeat = PaxTrendSignalSnapshotParser.parse(baseSnap(insSignal(
                idA, "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 2000L)), 1L);
        if (aRepeat.bucketEnteredMs != a.bucketEnteredMs) {
            throw new AssertionError("same id must yield same bucket across polls");
        }
    }

    private static void eventMsSourceIsInstitutionalSignal() {
        String body = baseSnap(insSignal(
                "id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (!"institutional_signal".equals(m.eventMsSource)) {
            throw new AssertionError("eventMsSource must be 'institutional_signal'; got " + m.eventMsSource);
        }
    }

    // ─── Suppression rules ────────────────────────────────────────────────

    private static void waitForConfirmProducesNone() {
        String body = baseSnap(insSignal(
                "id1", "NONE", "STOP_SWEEP_LONG", "WAIT_FOR_CONFIRM",
                "NONE", 0.35, 20000.0, 1000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("WAIT_FOR_CONFIRM must yield NONE; got " + m.kind);
        }
    }

    private static void standDownProducesNone() {
        String body = baseSnap(insSignal(
                "id1", "NONE", "ICEBERG_DEFENSE", "STAND_DOWN",
                "NONE", 0.80, 20000.0, 1000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("STAND_DOWN must yield NONE");
        }
    }

    private static void scratchReadyProducesNone() {
        String body = baseSnap(insSignal(
                "id1", "NONE", "SCRATCH", "SCRATCH_READY",
                "NONE", 0.55, 20000.0, 1000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("SCRATCH_READY must yield NONE");
        }
    }

    private static void directionNoneProducesNone() {
        String body = baseSnap(insSignal(
                "id1", "NONE", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                "FULL", 0.80, 20000.0, 1000L));
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        // execution_read PAY_FOR_TRADE but direction=NONE must not produce a kind.
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("direction=NONE must yield NONE; got " + m.kind);
        }
    }

    private static void emptyInstitutionalSignalsProducesNone() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":[]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("empty institutional_signals must yield NONE");
        }
    }

    private static void missingInstitutionalSignalsProducesNone() {
        String body = "{\"health\":\"ok\"}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("missing institutional_signals must yield NONE");
        }
    }

    // ─── Hard no-fallback rules ───────────────────────────────────────────

    private static void trendSignalStrongBullAloneProducesNone() {
        String body = "{\"health\":\"ok\",\"trend_signal\":"
                + "{\"kind\":\"STRONG_BULL\",\"mid\":20000.0,\"eventMs\":1,\"eligible\":true}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("trend_signal alone must NOT produce a kind; got " + m.kind);
        }
    }

    private static void paxDecisionEnterLongAloneProducesNone() {
        String body = "{\"health\":\"ok\",\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"pax\":{\"decision\":\"ENTER_LONG_FOLLOW\",\"size_tier\":\"FULL\","
                + "\"level_label\":\"OR-H\",\"entry\":20000.0}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("pax.decision alone must NOT produce a kind; got " + m.kind);
        }
    }

    private static void bothTrendAndPaxWithoutInstitutionalProducesNone() {
        String body = "{\"health\":\"ok\",\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"trend_signal\":{\"kind\":\"STRONG_BULL\",\"mid\":20000.0,\"eventMs\":1,\"eligible\":true},"
                + "\"pax\":{\"decision\":\"ENTER_LONG_FOLLOW\",\"size_tier\":\"FULL\","
                + "\"level_label\":\"OR-H\",\"entry\":20000.0}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("trend+pax without institutional must yield NONE; got " + m.kind);
        }
    }

    // ─── Multi-signal selection ───────────────────────────────────────────

    private static void mostRecentPayForTradeWins() {
        // Two PAY_FOR_TRADE entries: SHORT older, LONG newer. LONG should win.
        String shortSig = insSignal("idShort", "SHORT", "REJECTION_SHORT",
                "PAY_FOR_TRADE", "FULL", 0.80, 19950.0, 1000L);
        String longSig = insSignal("idLong", "LONG", "ACCEPTANCE_LONG",
                "PAY_FOR_TRADE", "FULL", 0.80, 20007.25, 2000L);
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + shortSig + "," + longSig + "]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL) {
            throw new AssertionError("most-recent PAY must win; got " + m.kind);
        }
        if (Math.abs(m.mid - 20007.25) > 1e-9) {
            throw new AssertionError("winning signal's price must be model.mid; got " + m.mid);
        }
    }

    private static void nonPayEntriesAreSkipped() {
        String waiting = insSignal("idWait", "NONE", "STOP_SWEEP_LONG",
                "WAIT_FOR_CONFIRM", "NONE", 0.35, 20000.0, 5000L);
        String pay = insSignal("idPay", "LONG", "ACCEPTANCE_LONG",
                "PAY_FOR_TRADE", "FULL", 0.80, 20007.25, 1000L);
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + waiting + "," + pay + "]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL) {
            throw new AssertionError("non-PAY entries must be skipped; got " + m.kind);
        }
        if (Math.abs(m.mid - 20007.25) > 1e-9) {
            throw new AssertionError("PAY signal price must win, not WAIT signal price; got " + m.mid);
        }
    }

    // ─── Safety ───────────────────────────────────────────────────────────

    private static void missingPriceProducesNone() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + "{\"id\":\"id1\",\"direction\":\"LONG\",\"signal_type\":\"ACCEPTANCE_LONG\","
                + "\"execution_read\":\"PAY_FOR_TRADE\",\"size_tier\":\"FULL\","
                + "\"confidence\":0.80,\"timestamp_ms\":1000}"
                + "]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("missing price must yield NONE; got " + m.kind);
        }
    }

    private static void nonNumericPriceProducesNone() {
        String body = "{\"health\":\"ok\",\"institutional_signals\":["
                + "{\"id\":\"id1\",\"direction\":\"LONG\",\"signal_type\":\"ACCEPTANCE_LONG\","
                + "\"execution_read\":\"PAY_FOR_TRADE\",\"size_tier\":\"FULL\","
                + "\"confidence\":0.80,\"price\":null,\"timestamp_ms\":1000}"
                + "]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("null price must yield NONE; got " + m.kind);
        }
    }

    private static void healthOfflineSuppressesEverything() {
        String body = "{\"health\":\"offline\",\"institutional_signals\":["
                + insSignal("id1", "LONG", "ACCEPTANCE_LONG", "PAY_FOR_TRADE",
                        "FULL", 0.80, 20000.0, 1000L) + "]}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE) {
            throw new AssertionError("health!=ok must yield NONE; got " + m.kind);
        }
    }

    private static void emptyJsonRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("", 1L);
            throw new AssertionError("expected ParseException on empty input");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) { /* ok */ }
    }

    private static void malformedJsonRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("{not valid", 1L);
            throw new AssertionError("expected ParseException on malformed");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) { /* ok */ }
    }

    private static void nonObjectRootRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("[]", 1L);
            throw new AssertionError("expected ParseException on non-object root");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) { /* ok */ }
    }

    // ─── Builders ─────────────────────────────────────────────────────────

    private static String insSignal(String id, String direction, String signalType,
                                     String executionRead, String sizeTier,
                                     double confidence, double price, long timestampMs) {
        return "{"
                + "\"id\":\"" + id + "\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"label\":\"OR-H\","
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

    private static String baseSnap(String singleSignal) {
        return "{\"health\":\"ok\",\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"institutional_signals\":[" + singleSignal + "]}";
    }
}
