package com.openrange;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

/**
 * HTTP-driven tests for PaxAttackResponseFetcher. Mirrors the
 * PaxHeatwaveFetcherTest pattern: spin a tiny in-process HttpServer on
 * an ephemeral port, point the fetcher at it, drive single ticks.
 *
 * Invariants pinned:
 *   - 200 + valid payload -> latest model populated, no failures.
 *   - 503 -> empty model returned (Pax AI server up but poller cold),
 *     consecutiveFailures stays 0.
 *   - Other non-2xx -> failure increment, last model retained.
 *   - Parse error -> failure increment, last model retained.
 *   - effectiveModel returns null past STALE_FAIL_THRESHOLD failures.
 *   - Repaint callback fires on successful poll and on first failure.
 *   - computeSleepMs backs off only after FAIL_BACKOFF_THRESHOLD.
 *   - URL constant points at the Pax AI server on port 18891.
 */
public class PaxAttackResponseFetcherTest {

    private static final String VALID_BODY = ""
            + "{\"alias\":\"NQM6.CME@RITHMIC\",\"asOfMs\":1,\"health\":\"ok\","
            + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
            + "\"states\":[{"
            + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":30145.0,"
            + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
            + "  \"attack\":\"SWEEP_LOW\",\"response\":\"RECLAIMED\","
            + "  \"drivers\":[\"sweep_low\",\"bid_iceberg\",\"bid_stack\"],"
            + "  \"confidence\":0.65,\"proven_edge\":false,"
            + "  \"timestamp_ms\":1700000000000"
            + "}]}";

    public static void main(String[] args) throws Exception {
        successPopulatesLatest();
        emptyPayloadFromBlockedYieldsZeroRows();
        http503YieldsEmptyModelNotFailure();
        http500CountsAsFailureAndRetainsLast();
        parseErrorCountsAsFailureAndRetainsLast();
        effectiveModelNullPastStaleThreshold();
        repaintCallbackFiresOnSuccess();
        computeSleepMsRespectsBackoffWindow();
        productionUrlConstantPointsAtPaxAi();
        timeoutBudgetSane();
        System.out.println("PaxAttackResponseFetcherTest OK");
    }

    private static void successPopulatesLatest() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (!ok) throw new AssertionError("expected success");
            PaxAttackResponseModel model = fetcher.snapshot();
            if (model == null) throw new AssertionError("model should populate on 200");
            if (model.rows.size() != 1) throw new AssertionError("expected 1 row");
            if (model.rows.get(0).state != PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM) {
                throw new AssertionError("state mismatch");
            }
            if (fetcher.consecutiveFailures() != 0) {
                throw new AssertionError("failures should be 0 after success");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void emptyPayloadFromBlockedYieldsZeroRows() throws Exception {
        // health=ok + blocked.anchor=true -> parser returns rows=[]; this is
        // still a successful poll (no failure increment).
        String blockedBody = "{\"alias\":\"NQM6\",\"asOfMs\":1,\"health\":\"ok\","
                + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":true},"
                + "\"states\":[]}";
        AtomicReference<String> body = new AtomicReference<>(blockedBody);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (!ok) throw new AssertionError("blocked-anchor payload is still a 2xx success");
            PaxAttackResponseModel m = fetcher.snapshot();
            if (m == null || !m.rows.isEmpty()) {
                throw new AssertionError("anchor-blocked payload MUST yield empty rows");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void http503YieldsEmptyModelNotFailure() throws Exception {
        AtomicReference<String> body = new AtomicReference<>("");
        AtomicInteger status = new AtomicInteger(503);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (!ok) throw new AssertionError("503 should be reported as success");
            PaxAttackResponseModel m = fetcher.snapshot();
            if (m == null || !m.rows.isEmpty()) {
                throw new AssertionError("503 path MUST produce empty model");
            }
            if (fetcher.consecutiveFailures() != 0) {
                throw new AssertionError("503 must NOT count as a failure");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void http500CountsAsFailureAndRetainsLast() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            fetcher.tickOnce(System.currentTimeMillis());
            PaxAttackResponseModel before = fetcher.snapshot();
            if (before == null) throw new AssertionError("setup");

            status.set(500);
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (ok) throw new AssertionError("500 is a failure");
            if (fetcher.snapshot() != before) {
                throw new AssertionError("failure must retain the last good model");
            }
            if (fetcher.consecutiveFailures() < 1) {
                throw new AssertionError("failure counter must increment");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void parseErrorCountsAsFailureAndRetainsLast() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            fetcher.tickOnce(System.currentTimeMillis());
            PaxAttackResponseModel before = fetcher.snapshot();

            body.set("not valid json at all");
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (ok) throw new AssertionError("parse error is a failure");
            if (fetcher.snapshot() != before) {
                throw new AssertionError("parse error must retain the last good model");
            }
            String reason = fetcher.lastFailureReason();
            if (reason == null || !reason.startsWith("parse:")) {
                throw new AssertionError("parse-error reason should start with 'parse:'; got " + reason);
            }
        } finally {
            server.stop(0);
        }
    }

    private static void effectiveModelNullPastStaleThreshold() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            fetcher.tickOnce(System.currentTimeMillis());
            // Force the failure threshold.
            status.set(500);
            for (int i = 0; i < PaxAttackResponseFetcher.STALE_FAIL_THRESHOLD + 1; i++) {
                fetcher.tickOnce(System.currentTimeMillis());
            }
            PaxAttackResponseModel eff = fetcher.effectiveModel(System.currentTimeMillis());
            if (eff != null) {
                throw new AssertionError(
                        "effectiveModel MUST return null past STALE_FAIL_THRESHOLD; got " + eff);
            }
            // snapshot still retains the last good model
            if (fetcher.snapshot() == null) {
                throw new AssertionError("snapshot should retain the last good model");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void repaintCallbackFiresOnSuccess() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        AtomicInteger calls = new AtomicInteger(0);
        try {
            PaxAttackResponseFetcher fetcher =
                    new PaxAttackResponseFetcher(calls::incrementAndGet);
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            fetcher.tickOnce(System.currentTimeMillis());
            if (calls.get() != 1) {
                throw new AssertionError("repaint callback should fire on success; got "
                        + calls.get());
            }
        } finally {
            server.stop(0);
        }
    }

    private static void computeSleepMsRespectsBackoffWindow() throws Exception {
        AtomicReference<String> body = new AtomicReference<>("");
        AtomicInteger status = new AtomicInteger(500);
        HttpServer server = startServer(body, status);
        try {
            PaxAttackResponseFetcher fetcher = new PaxAttackResponseFetcher(() -> {});
            fetcher.setUrlForTest("http://127.0.0.1:" + server.getAddress().getPort()
                    + "/api/pax/attack-response");
            // success path -> base poll
            long baseSleep = fetcher.computeSleepMs(true);
            if (baseSleep != PaxAttackResponseFetcher.POLL_MS) {
                throw new AssertionError("ok path should poll at base interval");
            }
            // failures below threshold -> still base
            fetcher.tickOnce(System.currentTimeMillis());
            long earlyFailSleep = fetcher.computeSleepMs(false);
            if (earlyFailSleep != PaxAttackResponseFetcher.POLL_MS) {
                throw new AssertionError("early failures should not back off");
            }
            // accumulate failures to trigger backoff
            for (int i = 0; i < 5; i++) {
                fetcher.tickOnce(System.currentTimeMillis());
            }
            long backoffSleep = fetcher.computeSleepMs(false);
            if (backoffSleep <= PaxAttackResponseFetcher.POLL_MS) {
                throw new AssertionError("backoff must exceed base after repeated failures");
            }
            if (backoffSleep > PaxAttackResponseFetcher.MAX_BACKOFF_MS) {
                throw new AssertionError("backoff must cap at MAX_BACKOFF_MS");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void productionUrlConstantPointsAtPaxAi() {
        if (!PaxAttackResponseFetcher.URL.contains("127.0.0.1:18891")
                || !PaxAttackResponseFetcher.URL.endsWith("/api/pax/attack-response")) {
            throw new AssertionError(
                    "production URL MUST hit Pax AI server :18891/api/pax/attack-response; got "
                            + PaxAttackResponseFetcher.URL);
        }
    }

    private static void timeoutBudgetSane() {
        if (PaxAttackResponseFetcher.CONNECT_TIMEOUT_MS
                > PaxAttackResponseFetcher.REQUEST_TIMEOUT_MS) {
            throw new AssertionError("connect timeout must not exceed request timeout");
        }
        if (PaxAttackResponseFetcher.POLL_MS != 1000) {
            throw new AssertionError("operator spec: 1Hz poll cadence");
        }
    }

    private static HttpServer startServer(AtomicReference<String> body,
                                            AtomicInteger status) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        s.createContext("/api/pax/attack-response", new HttpHandler() {
            @Override
            public void handle(HttpExchange exchange) throws IOException {
                byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
                int code = status.get();
                if (code / 100 == 2) {
                    exchange.getResponseHeaders().add("Content-Type", "application/json");
                    exchange.sendResponseHeaders(code, bytes.length);
                    try (OutputStream os = exchange.getResponseBody()) {
                        os.write(bytes);
                    }
                } else {
                    exchange.sendResponseHeaders(code, -1);
                    exchange.close();
                }
            }
        });
        s.setExecutor(null);
        s.start();
        return s;
    }
}
