package com.openrange;

public class PaxTrendSignalSnapshotParserTest {

    public static void main(String[] args) {
        parsesStrongBullHappyPath();
        parsesWeakBearHappyPath();
        missingTrendSignalReturnsNone();
        unknownKindReturnsNone();
        nullMidIsHandled();
        emptyJsonRaises();
        malformedJsonRaises();
        nonObjectRootRaises();
        eventMsParsedAsLong();
        bucketEnteredMsParsedAsLong();
        changedFlagParsesTrueAndFalse();
        healthOfflineSuppressesKind();
        healthErrorSuppressesKind();
        eligibleFlagParsesTrue();
        eligibleFlagParsesFalseExplicit();
        missingEligibleFieldDefaultsFalseForSafety();
        blockedReasonAndEventMsSourceParse();
        paxEnterDecisionOverridesTrendSignalForMarker();
        paxWaitDoesNotOverrideTrendSignal();
        System.out.println("PaxTrendSignalSnapshotParserTest OK");
    }

    private static void eligibleFlagParsesTrue() {
        String body = ""
                + "{\"health\":\"ok\",\"trend_signal\":{"
                + "\"kind\":\"STRONG_BULL\",\"mid\":21800.0,\"eventMs\":1,"
                + "\"eligible\":true,\"blockedReason\":\"\","
                + "\"eventMsSource\":\"trend_analyzer\""
                + "}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (!m.eligible) throw new AssertionError("eligible=true must parse as true");
        if (!"trend_analyzer".equals(m.eventMsSource))
            throw new AssertionError("eventMsSource must round-trip; got " + m.eventMsSource);
    }

    private static void eligibleFlagParsesFalseExplicit() {
        String body = ""
                + "{\"health\":\"ok\",\"trend_signal\":{"
                + "\"kind\":\"STRONG_BULL\",\"mid\":21800.0,\"eventMs\":1,"
                + "\"eligible\":false,\"blockedReason\":\"invalid_mid\""
                + "}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.eligible) throw new AssertionError("eligible=false must parse as false");
        if (!"invalid_mid".equals(m.blockedReason))
            throw new AssertionError("blockedReason must round-trip; got " + m.blockedReason);
    }

    private static void missingEligibleFieldDefaultsFalseForSafety() {
        // Older / partial dashboard payloads omit `eligible` entirely. The
        // safe default is FALSE — we must not accidentally render a
        // triangle for a payload that hasn't been updated to the new
        // contract.
        String body = ""
                + "{\"health\":\"ok\",\"trend_signal\":{"
                + "\"kind\":\"STRONG_BULL\",\"mid\":21800.0,\"eventMs\":1"
                + "}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.eligible)
            throw new AssertionError("missing eligible field must default to FALSE for safety");
    }

    private static void blockedReasonAndEventMsSourceParse() {
        String body = ""
                + "{\"health\":\"ok\",\"trend_signal\":{"
                + "\"kind\":\"NONE\",\"mid\":null,"
                + "\"blockedReason\":\"invalid_mid\","
                + "\"eventMsSource\":\"wall_clock_fallback\""
                + "}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (!"invalid_mid".equals(m.blockedReason))
            throw new AssertionError("blockedReason mismatch: " + m.blockedReason);
        if (!"wall_clock_fallback".equals(m.eventMsSource))
            throw new AssertionError("eventMsSource mismatch: " + m.eventMsSource);
    }

    private static void paxEnterDecisionOverridesTrendSignalForMarker() {
        String body = ""
                + "{\"health\":\"ok\",\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"pax\":{\"decision\":\"ENTER_SHORT_FADE\",\"size_tier\":\"FULL\","
                + "\"level_label\":\"+1\",\"entry\":28998.5},"
                + "\"trend_signal\":{\"kind\":\"NONE\",\"mid\":28970.0,\"eligible\":false}"
                + "}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1000L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BEAR)
            throw new AssertionError("Pax ENTER_SHORT/FULL must map to STRONG_BEAR, got " + m.kind);
        if (!m.eligible)
            throw new AssertionError("Pax ENTER marker must be eligible");
        if (Math.abs(m.mid - 28998.5) > 1e-9)
            throw new AssertionError("Pax marker must use entry price");
        if (!"pax_decision".equals(m.eventMsSource))
            throw new AssertionError("Pax marker source mismatch: " + m.eventMsSource);
    }

    private static void paxWaitDoesNotOverrideTrendSignal() {
        String body = ""
                + "{\"health\":\"ok\","
                + "\"pax\":{\"decision\":\"WAIT\",\"size_tier\":\"NONE\",\"entry\":0},"
                + "\"trend_signal\":{\"kind\":\"WEAK_BEAR\",\"mid\":28970.0,"
                + "\"eventMs\":2,\"eligible\":true}"
                + "}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1000L);
        if (m.kind != PaxTrendSignalModel.Kind.WEAK_BEAR)
            throw new AssertionError("Pax WAIT must not override trend signal");
    }

    private static void healthOfflineSuppressesKind() {
        // Even if a trend_signal block somehow appears alongside an offline
        // health, the parser must refuse to surface it.
        String body = ""
                + "{"
                + "\"health\":\"offline\","
                + "\"trend_signal\":{\"kind\":\"STRONG_BULL\",\"mid\":21800.0,\"eventMs\":1}"
                + "}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("health=offline must hard-gate kind to NONE, got " + m.kind);
    }

    private static void healthErrorSuppressesKind() {
        String body = "{\"health\":\"error\",\"trend_signal\":{\"kind\":\"WEAK_BEAR\"}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("health=error must produce NONE, got " + m.kind);
    }

    private static void parsesStrongBullHappyPath() {
        String body = ""
                + "{"
                + "\"trend_signal\":{"
                + "\"kind\":\"STRONG_BULL\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"asOfMs\":1747680123999,"
                + "\"eventMs\":1747680123456,"
                + "\"mid\":21800.25,"
                + "\"bucketEnteredMs\":1747680113000,"
                + "\"changedSinceLastTick\":true"
                + "}"
                + "}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 5000L);
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL)
            throw new AssertionError("expected STRONG_BULL, got " + m.kind);
        if (!"NQM6.CME@RITHMIC".equals(m.alias))
            throw new AssertionError("alias mismatch");
        if (Math.abs(m.mid - 21800.25) > 1e-9)
            throw new AssertionError("mid mismatch");
        if (m.eventMs != 1747680123456L)
            throw new AssertionError("eventMs mismatch");
        if (m.bucketEnteredMs != 1747680113000L)
            throw new AssertionError("bucketEnteredMs mismatch");
        if (!m.changed)
            throw new AssertionError("changed flag mismatch");
        if (m.fetchedAtMs != 5000L)
            throw new AssertionError("fetchedAtMs should be passed through");
    }

    private static void parsesWeakBearHappyPath() {
        String body = "{\"trend_signal\":{\"kind\":\"WEAK_BEAR\",\"mid\":21800.0,\"eventMs\":1}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.WEAK_BEAR)
            throw new AssertionError("expected WEAK_BEAR");
    }

    private static void missingTrendSignalReturnsNone() {
        // Real-world: dashboard ran but conviction failed → trend_signal is null/missing.
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse("{\"conviction\":null}", 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("missing trend_signal must default to NONE");
    }

    private static void unknownKindReturnsNone() {
        String body = "{\"trend_signal\":{\"kind\":\"WHATEVER\"}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("unknown kind must default to NONE");
    }

    private static void nullMidIsHandled() {
        String body = "{\"trend_signal\":{\"kind\":\"STRONG_BULL\",\"mid\":null}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (!Double.isNaN(m.mid))
            throw new AssertionError("null mid must surface as NaN");
        if (m.kind != PaxTrendSignalModel.Kind.STRONG_BULL)
            throw new AssertionError("kind preserved when mid is null");
    }

    private static void emptyJsonRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("", 1L);
            throw new AssertionError("expected ParseException on empty input");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) {
            // ok
        }
    }

    private static void malformedJsonRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("{not valid", 1L);
            throw new AssertionError("expected ParseException on malformed");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) {
            // ok
        }
    }

    private static void nonObjectRootRaises() {
        try {
            PaxTrendSignalSnapshotParser.parse("[]", 1L);
            throw new AssertionError("expected ParseException on non-object root");
        } catch (PaxTrendSignalSnapshotParser.ParseException expected) {
            // ok
        }
    }

    private static void eventMsParsedAsLong() {
        String body = "{\"trend_signal\":{\"kind\":\"STRONG_BULL\",\"eventMs\":1747680123456}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.eventMs != 1747680123456L)
            throw new AssertionError("eventMs must be parsed as long, got " + m.eventMs);
    }

    private static void bucketEnteredMsParsedAsLong() {
        String body = "{\"trend_signal\":{\"kind\":\"STRONG_BULL\",\"bucketEnteredMs\":1747680113000}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, 1L);
        if (m.bucketEnteredMs != 1747680113000L)
            throw new AssertionError("bucketEnteredMs mismatch, got " + m.bucketEnteredMs);
    }

    private static void changedFlagParsesTrueAndFalse() {
        PaxTrendSignalModel a = PaxTrendSignalSnapshotParser.parse(
                "{\"trend_signal\":{\"kind\":\"WEAK_BULL\",\"changedSinceLastTick\":true}}", 1L);
        if (!a.changed) throw new AssertionError("changed=true mismatch");
        PaxTrendSignalModel b = PaxTrendSignalSnapshotParser.parse(
                "{\"trend_signal\":{\"kind\":\"WEAK_BULL\",\"changedSinceLastTick\":false}}", 1L);
        if (b.changed) throw new AssertionError("changed=false mismatch");
        PaxTrendSignalModel c = PaxTrendSignalSnapshotParser.parse(
                "{\"trend_signal\":{\"kind\":\"WEAK_BULL\"}}", 1L);
        if (c.changed) throw new AssertionError("missing changed must default to false");
    }
}
