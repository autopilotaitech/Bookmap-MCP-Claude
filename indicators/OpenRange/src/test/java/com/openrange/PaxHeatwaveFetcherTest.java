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

public class PaxHeatwaveFetcherTest {

    private static final String VALID_BODY = ""
            + "{"
            + "\"ts\":\"2026-05-19T11:42:17\","
            + "\"conviction\":{\"score\":0.25,"
            + "\"sourceScores\":{\"flow_ofi\":0.30},"
            + "\"effectiveWeights\":{\"flow_ofi\":0.14},"
            + "\"sourceReliability\":{\"flow_ofi\":0.80}},"
            + "\"pax\":{\"decision\":\"WAIT\"},"
            + "\"decision\":{\"decision\":\"WAIT\"},"
            + "\"or_levels\":{\"levels\":[]}"
            + "}";

    public static void main(String[] args) throws Exception {
        successSwapsLatest();
        failureRetainsPreviousLatest();
        backoffActivatesAfterRepeatedFailures();
        applySettingsClampsPollMs();
        repaintCallbackInvokedOnSuccess();
        timeoutBudgetCoversLiveDashboardLatency();
    }

    private static void successSwapsLatest() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");

            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (!ok) {
                throw new AssertionError("expected tickOnce success");
            }
            PaxHeatwaveModel snap = fetcher.snapshot();
            if (snap == null) {
                throw new AssertionError("snapshot should be non-null after success");
            }
            if (!"WAIT".equals(snap.verdict)) {
                throw new AssertionError("expected verdict WAIT got " + snap.verdict);
            }
            if (fetcher.consecutiveFailures() != 0) {
                throw new AssertionError("failures should reset to 0");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void failureRetainsPreviousLatest() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");

            fetcher.tickOnce(System.currentTimeMillis());
            PaxHeatwaveModel before = fetcher.snapshot();
            if (before == null) {
                throw new AssertionError("setup: snapshot should be present");
            }

            status.set(500);
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (ok) {
                throw new AssertionError("expected tickOnce to report failure for 500");
            }
            PaxHeatwaveModel after = fetcher.snapshot();
            if (after != before) {
                throw new AssertionError("snapshot reference should be retained across failure");
            }
            if (fetcher.consecutiveFailures() < 1) {
                throw new AssertionError("failure counter should increment");
            }
        } finally {
            server.stop(0);
        }
    }

    private static void backoffActivatesAfterRepeatedFailures() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(500);
        HttpServer server = startServer(body, status);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");

            for (int i = 0; i < 5; i++) {
                fetcher.tickOnce(System.currentTimeMillis());
            }
            long sleep = fetcher.computeSleepMs(false);
            if (sleep <= 1000L) {
                throw new AssertionError("backoff should exceed base poll after repeated failures; got " + sleep);
            }
            if (sleep > PaxHeatwaveFetcher.MAX_BACKOFF_MS) {
                throw new AssertionError("backoff should cap at MAX_BACKOFF_MS; got " + sleep);
            }
        } finally {
            server.stop(0);
        }
    }

    private static void applySettingsClampsPollMs() {
        PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(() -> {});
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        s.showHeatwaveBox = false;
        s.heatwavePollMs = 50; // below clamp
        s.heatwaveUrl = "http://127.0.0.1:1/api/snapshot";
        fetcher.applySettings(s);
        // clamp to 500 minimum
        long sleep = fetcher.computeSleepMs(true);
        if (sleep != 500L) {
            throw new AssertionError("expected clamped poll 500ms, got " + sleep);
        }

        s.heatwavePollMs = 99999; // above clamp
        fetcher.applySettings(s);
        sleep = fetcher.computeSleepMs(true);
        if (sleep != 3000L) {
            throw new AssertionError("expected clamped poll 3000ms, got " + sleep);
        }
    }

    private static void repaintCallbackInvokedOnSuccess() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        AtomicInteger callbackCount = new AtomicInteger(0);
        try {
            PaxHeatwaveFetcher fetcher = new PaxHeatwaveFetcher(callbackCount::incrementAndGet);
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");

            fetcher.tickOnce(System.currentTimeMillis());
            if (callbackCount.get() != 1) {
                throw new AssertionError("expected repaint callback on success; count=" + callbackCount.get());
            }
        } finally {
            server.stop(0);
        }
    }

    private static void timeoutBudgetCoversLiveDashboardLatency() {
        if (PaxHeatwaveFetcher.REQUEST_TIMEOUT_MS < 10000L) {
            throw new AssertionError("request timeout must cover live /api/snapshot latency");
        }
        if (PaxHeatwaveFetcher.CONNECT_TIMEOUT_MS > PaxHeatwaveFetcher.REQUEST_TIMEOUT_MS) {
            throw new AssertionError("connect timeout must not exceed request timeout");
        }
    }

    private static void applyOff(PaxHeatwaveFetcher fetcher, String url) {
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        s.showHeatwaveBox = false;
        s.heatwaveUrl = url;
        s.heatwavePollMs = 1000;
        fetcher.applySettings(s);
    }

    private static HttpServer startServer(AtomicReference<String> body, AtomicInteger status) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        s.createContext("/api/snapshot", new HttpHandler() {
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
