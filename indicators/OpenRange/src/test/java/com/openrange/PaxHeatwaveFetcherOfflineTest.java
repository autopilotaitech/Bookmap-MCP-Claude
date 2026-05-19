package com.openrange;

public class PaxHeatwaveFetcherOfflineTest {

    public static void main(String[] args) throws Exception {
        unreachableDashboardYieldsDashboardOfflineEffectiveModel();
        successfulFetchRetainsLiveEffectiveModel();
        bridgeOfflinePayloadShowsAsBridgeOffline();
        successThenSustainedFailuresEscalatesToDashboardOffline();
        successThenOneFailureKeepsLiveModel();
        bridgeOfflinePayloadKeepsBridgeOfflineEvenAfterFailures();
        System.out.println("PaxHeatwaveFetcherOfflineTest OK");
        System.exit(0);
    }

    private static void successThenSustainedFailuresEscalatesToDashboardOffline() throws Exception {
        // First serve a valid payload to seed latest, then drop to 500s and
        // tick STALE_FAIL_THRESHOLD times. effectiveModel must flip to
        // DASHBOARD_OFFLINE despite a non-null latest.
        java.util.concurrent.atomic.AtomicReference<String> body =
                new java.util.concurrent.atomic.AtomicReference<>(VALID_BODY);
        java.util.concurrent.atomic.AtomicInteger status =
                new java.util.concurrent.atomic.AtomicInteger(200);
        com.sun.net.httpserver.HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            // Prime latest.
            fetcher.tickOnce(System.currentTimeMillis());
            if (fetcher.snapshot() == null)
                throw new AssertionError("setup: latest must be non-null after success");
            PaxHeatwaveModel liveBefore = fetcher.effectiveModel(System.currentTimeMillis());
            if (liveBefore == null || liveBefore.state != PaxHeatwaveModel.State.LIVE)
                throw new AssertionError("setup: should be LIVE before failures, got " +
                        (liveBefore == null ? "null" : liveBefore.state));
            // Now drop and tick until we exceed the threshold.
            status.set(500);
            for (int i = 0; i < PaxHeatwaveFetcher.STALE_FAIL_THRESHOLD; i++) {
                fetcher.tickOnce(System.currentTimeMillis());
            }
            PaxHeatwaveModel eff = fetcher.effectiveModel(System.currentTimeMillis());
            if (eff == null || eff.state != PaxHeatwaveModel.State.DASHBOARD_OFFLINE)
                throw new AssertionError("after " + PaxHeatwaveFetcher.STALE_FAIL_THRESHOLD
                        + " failures, must escalate to DASHBOARD_OFFLINE; got "
                        + (eff == null ? "null" : eff.state));
        } finally {
            server.stop(0);
        }
    }

    private static void successThenOneFailureKeepsLiveModel() throws Exception {
        // Single transient failure must NOT escalate.
        java.util.concurrent.atomic.AtomicReference<String> body =
                new java.util.concurrent.atomic.AtomicReference<>(VALID_BODY);
        java.util.concurrent.atomic.AtomicInteger status =
                new java.util.concurrent.atomic.AtomicInteger(200);
        com.sun.net.httpserver.HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            fetcher.tickOnce(System.currentTimeMillis());
            status.set(500);
            fetcher.tickOnce(System.currentTimeMillis());
            PaxHeatwaveModel eff = fetcher.effectiveModel(System.currentTimeMillis());
            if (eff == null || eff.state != PaxHeatwaveModel.State.LIVE)
                throw new AssertionError("one failure must not escalate; expected LIVE, got "
                        + (eff == null ? "null" : eff.state));
        } finally {
            server.stop(0);
        }
    }

    private static void bridgeOfflinePayloadKeepsBridgeOfflineEvenAfterFailures() throws Exception {
        // If the dashboard explicitly says health=offline (= BRIDGE_OFFLINE),
        // and then becomes unreachable, the effective state must still be
        // bridge-offline until our own failure threshold escalates it.
        // (After the threshold the dashboard itself is the unreachable layer,
        // which is more important — that's the DASHBOARD_OFFLINE escalation
        // and is tested above.)
        java.util.concurrent.atomic.AtomicReference<String> body =
                new java.util.concurrent.atomic.AtomicReference<>(
                        "{\"health\":\"offline\",\"bridgeUrl\":\"http://127.0.0.1:8765\","
                        + "\"bridgeError\":\"timed out\"}");
        java.util.concurrent.atomic.AtomicInteger status =
                new java.util.concurrent.atomic.AtomicInteger(200);
        com.sun.net.httpserver.HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            fetcher.tickOnce(System.currentTimeMillis());
            PaxHeatwaveModel eff = fetcher.effectiveModel(System.currentTimeMillis());
            if (eff == null || eff.state != PaxHeatwaveModel.State.BRIDGE_OFFLINE)
                throw new AssertionError("dashboard health=offline must yield BRIDGE_OFFLINE; got "
                        + (eff == null ? "null" : eff.state));
        } finally {
            server.stop(0);
        }
    }

    private static final String VALID_BODY = ""
            + "{"
            + "\"health\":\"ok\","
            + "\"conviction\":{\"score\":0.1,"
            + "\"sourceScores\":{\"flow_ofi\":0.1},"
            + "\"effectiveWeights\":{\"flow_ofi\":0.1},"
            + "\"sourceReliability\":{\"flow_ofi\":0.5}},"
            + "\"pax\":{\"decision\":\"WAIT\"},"
            + "\"decision\":{\"decision\":\"WAIT\"},"
            + "\"or_levels\":{\"levels\":[]}"
            + "}";

    private static void applyOff(PaxHeatwaveFetcher fetcher, String url) {
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        s.showHeatwaveBox = false;
        s.heatwaveUrl = url;
        s.heatwavePollMs = 1000;
        fetcher.applySettings(s);
    }

    private static com.sun.net.httpserver.HttpServer startServer(
            java.util.concurrent.atomic.AtomicReference<String> body,
            java.util.concurrent.atomic.AtomicInteger status) throws java.io.IOException {
        com.sun.net.httpserver.HttpServer s =
                com.sun.net.httpserver.HttpServer.create(
                        new java.net.InetSocketAddress("127.0.0.1", 0), 0);
        s.createContext("/api/snapshot", exchange -> {
            byte[] bytes = body.get().getBytes(java.nio.charset.StandardCharsets.UTF_8);
            int code = status.get();
            if (code / 100 == 2) {
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(code, bytes.length);
                try (java.io.OutputStream os = exchange.getResponseBody()) {
                    os.write(bytes);
                }
            } else {
                exchange.sendResponseHeaders(code, -1);
                exchange.close();
            }
        });
        s.setExecutor(null);
        s.start();
        return s;
    }

    private static void unreachableDashboardYieldsDashboardOfflineEffectiveModel() {
        // Point fetcher at a port that nothing listens on. The connect will fail.
        PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        s.showHeatwaveBox = false;
        s.heatwaveUrl = "http://127.0.0.1:1/api/snapshot";   // port 1 = unreachable
        s.heatwavePollMs = 1000;
        fetcher.applySettings(s);
        // Force a single tick — it will fail.
        fetcher.tickOnce(System.currentTimeMillis());
        if (fetcher.snapshot() != null)
            throw new AssertionError("snapshot() must remain null when no fetch ever succeeded");
        // effectiveModel must now synthesize DASHBOARD_OFFLINE.
        PaxHeatwaveModel eff = fetcher.effectiveModel(System.currentTimeMillis());
        if (eff == null)
            throw new AssertionError("effectiveModel must NOT be null after a failure");
        if (eff.state != PaxHeatwaveModel.State.DASHBOARD_OFFLINE)
            throw new AssertionError("expected DASHBOARD_OFFLINE state, got " + eff.state);
        if (!eff.offlineUrl.contains("127.0.0.1:1"))
            throw new AssertionError("dashboardOffline.offlineUrl must echo the URL polled, got "
                    + eff.offlineUrl);
        if (eff.offlineReason == null || eff.offlineReason.isEmpty())
            throw new AssertionError("dashboardOffline.offlineReason must be populated");
    }

    private static void successfulFetchRetainsLiveEffectiveModel() {
        // Without any fetch attempt, effectiveModel must be null (caller decides
        // whether to render NO_DATA).
        PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
        if (fetcher.effectiveModel(System.currentTimeMillis()) != null)
            throw new AssertionError("effectiveModel must be null before any fetch");
    }

    private static void bridgeOfflinePayloadShowsAsBridgeOffline() {
        // Round-trip: parser produces BRIDGE_OFFLINE model; effectiveModel
        // returns that one (since latest is non-null).
        PaxHeatwaveModel offlineModel = PaxHeatwaveSnapshotParser.parse(
                "{\"health\":\"offline\",\"bridgeUrl\":\"http://127.0.0.1:8765\","
                + "\"bridgeError\":\"timed out\"}", 1L);
        if (offlineModel.state != PaxHeatwaveModel.State.BRIDGE_OFFLINE)
            throw new AssertionError("parser failed to produce BRIDGE_OFFLINE");
    }
}
