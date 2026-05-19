package com.openrange;

public class PaxHeatwaveOfflineParseTest {

    public static void main(String[] args) {
        healthOfflinePayloadProducesBridgeOfflineModel();
        offlineWithBridgeErrorIncludesReason();
        offlineFallsBackToErrorWhenNoBridgeError();
        healthOkProducesLiveModel();
        System.out.println("PaxHeatwaveOfflineParseTest OK");
        System.exit(0);
    }

    private static void healthOfflinePayloadProducesBridgeOfflineModel() {
        String json = ""
                + "{"
                + "\"health\":\"offline\","
                + "\"bridgeUrl\":\"http://127.0.0.1:8765\","
                + "\"bridgeError\":\"Bridge unreachable: timed out\","
                + "\"bridgeReachable\":false,"
                + "\"dashboardPort\":18888,"
                + "\"tokenConfigured\":true,"
                + "\"nextSteps\":[\"Open Bookmap and attach the addon\"]"
                + "}";
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(json, 1234L);
        if (m.state != PaxHeatwaveModel.State.BRIDGE_OFFLINE)
            throw new AssertionError("expected BRIDGE_OFFLINE state, got " + m.state);
        if (!"BRIDGE OFFLINE".equals(m.verdict))
            throw new AssertionError("expected verdict 'BRIDGE OFFLINE', got " + m.verdict);
        if (m.ok)
            throw new AssertionError("BRIDGE_OFFLINE must not be marked ok");
        if (!"http://127.0.0.1:8765".equals(m.offlineUrl))
            throw new AssertionError("offlineUrl must echo bridgeUrl, got " + m.offlineUrl);
        if (m.fetchedAtMs != 1234L)
            throw new AssertionError("fetchedAtMs must be passed through");
        // Age text must reflect state, not staleness duration.
        String age = m.ageText(System.currentTimeMillis(), 30_000L);
        if (!"BRIDGE OFF".equals(age))
            throw new AssertionError("expected ageText 'BRIDGE OFF', got " + age);
    }

    private static void offlineWithBridgeErrorIncludesReason() {
        String json = "{\"health\":\"offline\",\"bridgeUrl\":\"http://127.0.0.1:8765\","
                    + "\"bridgeError\":\"timed out\"}";
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(json, 0L);
        if (!"timed out".equals(m.offlineReason))
            throw new AssertionError("offlineReason must echo bridgeError, got " + m.offlineReason);
        // Row[1] hint must contain the reason for the operator overlay.
        if (m.rows == null || m.rows[1] == null || !m.rows[1].hint.contains("timed out"))
            throw new AssertionError("row[1].hint must surface the reason");
    }

    private static void offlineFallsBackToErrorWhenNoBridgeError() {
        // Older / legacy offline payloads only set "error". Parser must
        // still extract the reason.
        String json = "{\"health\":\"offline\",\"error\":\"connection refused\"}";
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(json, 0L);
        if (m.state != PaxHeatwaveModel.State.BRIDGE_OFFLINE)
            throw new AssertionError("legacy offline payload must still produce BRIDGE_OFFLINE");
        if (!"connection refused".equals(m.offlineReason))
            throw new AssertionError("legacy offline must use 'error' as fallback");
    }

    private static void healthOkProducesLiveModel() {
        // A minimal ok payload — health=ok with empty conviction. Should
        // produce a LIVE model regardless of how thin the content is.
        String json = "{\"health\":\"ok\",\"conviction\":{\"score\":0.0},"
                    + "\"pax\":{\"decision\":\"WAIT\"},\"decision\":{\"decision\":\"WAIT\"},"
                    + "\"or_levels\":{\"levels\":[]}}";
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(json, 0L);
        if (m.state != PaxHeatwaveModel.State.LIVE)
            throw new AssertionError("health=ok must produce LIVE state, got " + m.state);
        if (!m.ok)
            throw new AssertionError("LIVE model must be ok");
    }
}
