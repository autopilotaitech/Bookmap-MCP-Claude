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

public class PaxTrendSignalFetcherTest {

    private static final String VALID_BODY = ""
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

    public static void main(String[] args) throws Exception {
        successSwapsLatestAndFiresCallback();
        failureRetainsPreviousLatest();
        backoffActivatesAfterRepeatedFailures();
        applySettingsClampsPollMs();
        applySettingsTogglesWorker();
        timeoutBudgetCoversLiveDashboardLatency();
        System.out.println("PaxTrendSignalFetcherTest OK");
    }

    private static void successSwapsLatestAndFiresCallback() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        AtomicInteger callbackCount = new AtomicInteger(0);
        try {
            PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(callbackCount::incrementAndGet);
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (!ok) throw new AssertionError("expected tickOnce success");
            PaxTrendSignalModel snap = fetcher.snapshot();
            if (snap == null) throw new AssertionError("snapshot must be non-null after success");
            if (snap.kind != PaxTrendSignalModel.Kind.STRONG_BULL)
                throw new AssertionError("expected STRONG_BULL kind, got " + snap.kind);
            if (callbackCount.get() != 1)
                throw new AssertionError("repaint callback must fire exactly once on success; count=" + callbackCount.get());
            if (fetcher.consecutiveFailures() != 0)
                throw new AssertionError("failures must reset on success");
        } finally {
            server.stop(0);
        }
    }

    private static void failureRetainsPreviousLatest() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(200);
        HttpServer server = startServer(body, status);
        try {
            PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            fetcher.tickOnce(System.currentTimeMillis());
            PaxTrendSignalModel before = fetcher.snapshot();
            if (before == null) throw new AssertionError("setup: must have latest");
            status.set(500);
            boolean ok = fetcher.tickOnce(System.currentTimeMillis());
            if (ok) throw new AssertionError("expected failure on 500");
            PaxTrendSignalModel after = fetcher.snapshot();
            if (after != before)
                throw new AssertionError("latest reference must be retained across failure");
            if (fetcher.consecutiveFailures() < 1)
                throw new AssertionError("failure counter must increment");
        } finally {
            server.stop(0);
        }
    }

    private static void backoffActivatesAfterRepeatedFailures() throws Exception {
        AtomicReference<String> body = new AtomicReference<>(VALID_BODY);
        AtomicInteger status = new AtomicInteger(500);
        HttpServer server = startServer(body, status);
        try {
            PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
            applyOff(fetcher, "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot");
            for (int i = 0; i < 5; i++) fetcher.tickOnce(System.currentTimeMillis());
            long sleep = fetcher.computeSleepMs(false);
            if (sleep <= 1000L)
                throw new AssertionError("backoff must exceed base poll after repeated failures; got " + sleep);
            if (sleep > PaxTrendSignalFetcher.MAX_BACKOFF_MS)
                throw new AssertionError("backoff must cap at MAX_BACKOFF_MS; got " + sleep);
        } finally {
            server.stop(0);
        }
    }

    private static void applySettingsClampsPollMs() {
        PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 50);
        if (fetcher.computeSleepMs(true) != 500L)
            throw new AssertionError("must clamp poll to 500 minimum");
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 99999);
        if (fetcher.computeSleepMs(true) != 3000L)
            throw new AssertionError("must clamp poll to 3000 maximum");
    }

    private static void applySettingsTogglesWorker() throws Exception {
        PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
        // Initially off.
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 1000);
        if (fetcher.isRunning()) throw new AssertionError("expected fetcher OFF after applySettings(false)");
        // Turn on.
        fetcher.applySettings(true, "http://127.0.0.1:1/api/snapshot", 1000);
        if (!fetcher.isRunning()) throw new AssertionError("expected fetcher ON after applySettings(true)");
        // Stop again.
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 1000);
        if (fetcher.isRunning()) throw new AssertionError("expected fetcher OFF after applySettings(false)");
    }

    private static void timeoutBudgetCoversLiveDashboardLatency() {
        if (PaxTrendSignalFetcher.REQUEST_TIMEOUT_MS < 10000L) {
            throw new AssertionError("request timeout must cover live /api/snapshot latency");
        }
        if (PaxTrendSignalFetcher.CONNECT_TIMEOUT_MS > PaxTrendSignalFetcher.REQUEST_TIMEOUT_MS) {
            throw new AssertionError("connect timeout must not exceed request timeout");
        }
    }

    private static void applyOff(PaxTrendSignalFetcher fetcher, String url) {
        fetcher.applySettings(false, url, 1000);
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
                    try (OutputStream os = exchange.getResponseBody()) { os.write(bytes); }
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
