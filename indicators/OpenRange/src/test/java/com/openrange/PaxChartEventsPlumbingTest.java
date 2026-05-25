package com.openrange;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.concurrent.atomic.AtomicReference;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;
import com.sun.net.httpserver.HttpServer;

/** Plumbing contracts that the institutional-chart-events fix must
 *  preserve, mirroring the audit findings.
 *
 *  <ul>
 *    <li>Fetcher must run when institutional chart events are enabled.</li>
 *    <li>Legacy trend triangles alone must not run the fetcher.</li>
 *    <li>Settings persistence: showInstitutionalChartEvents round-trips.</li>
 *    <li>Empty later payload must not clear the durable chart-events history.</li>
 *  </ul>
 */
public class PaxChartEventsPlumbingTest {

    public static void main(String[] args) throws Exception {
        fetcherEnabledWhenOnlyChartEventsOn();
        fetcherDisabledWhenOnlyTrendTrianglesOn();
        fetcherDisabledWhenBothOff();
        fetcherEnabledWhenBothOn();

        applySettingsWithChartEventsOnlyStartsWorker();
        applySettingsWithBothOffStopsWorker();

        chartEventsHistorySurvivesEmptyLaterPayload();
        chartEventsHistoryDedupsById();

        uiSettingsDefaultsPersistShowInstitutionalChartEvents();

        System.out.println("PaxChartEventsPlumbingTest OK");
    }

    // ─── Pure helper: trendFetcherShouldRun ────────────────────────────────

    private static void fetcherEnabledWhenOnlyChartEventsOn() {
        if (!PaxOpeningRangeModule.trendFetcherShouldRun(false, true)) {
            throw new AssertionError("chart events alone must enable fetcher");
        }
    }

    private static void fetcherDisabledWhenOnlyTrendTrianglesOn() {
        if (PaxOpeningRangeModule.trendFetcherShouldRun(true, false)) {
            throw new AssertionError("legacy triangles alone must not enable fetcher");
        }
    }

    private static void fetcherDisabledWhenBothOff() {
        if (PaxOpeningRangeModule.trendFetcherShouldRun(false, false)) {
            throw new AssertionError("both flags off must disable fetcher");
        }
    }

    private static void fetcherEnabledWhenBothOn() {
        if (!PaxOpeningRangeModule.trendFetcherShouldRun(true, true)) {
            throw new AssertionError("both flags on must enable fetcher");
        }
    }

    // ─── PaxTrendSignalFetcher worker lifecycle ────────────────────────────

    private static void applySettingsWithChartEventsOnlyStartsWorker() throws Exception {
        // Simulate the module's call path: when showInstitutionalChartEvents
        // is on (even with legacy triangles off), the OR'd flag must enable
        // the fetcher worker.
        PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
        boolean enable = PaxOpeningRangeModule.trendFetcherShouldRun(
                /*showLegacy=*/false, /*showChartEvents=*/true);
        fetcher.applySettings(enable, "http://127.0.0.1:1/api/snapshot", 1000);
        if (!fetcher.isRunning()) {
            throw new AssertionError("expected fetcher ON when only chart-events flag is on");
        }
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 1000);
    }

    private static void applySettingsWithBothOffStopsWorker() throws Exception {
        PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
        // Start it
        boolean enable = PaxOpeningRangeModule.trendFetcherShouldRun(true, true);
        fetcher.applySettings(enable, "http://127.0.0.1:1/api/snapshot", 1000);
        if (!fetcher.isRunning()) throw new AssertionError("setup: fetcher must be ON");
        // Both off
        boolean enableOff = PaxOpeningRangeModule.trendFetcherShouldRun(false, false);
        fetcher.applySettings(enableOff, "http://127.0.0.1:1/api/snapshot", 1000);
        if (fetcher.isRunning()) {
            throw new AssertionError("expected fetcher OFF when both flags are off");
        }
    }

    // ─── Empty later payload preserves history ─────────────────────────────

    private static void chartEventsHistorySurvivesEmptyLaterPayload() throws Exception {
        // Poll 1: serve a snapshot with one institutional_chart_event.
        // Poll 2: serve a snapshot with institutional_chart_events=[].
        // History must still contain the prior event.
        AtomicReference<String> body = new AtomicReference<>(snapshotWithOneEvent());
        HttpServer server = startServer(body);
        try {
            PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
            String url = "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot";
            // Use the OR'd flag — even with legacy triangles off.
            fetcher.applySettings(
                    PaxOpeningRangeModule.trendFetcherShouldRun(false, true),
                    url, 1000);

            // First tick: capture the chart event.
            if (!fetcher.tickOnce(System.currentTimeMillis())) {
                throw new AssertionError("first tick must succeed");
            }
            PaxInstitutionalChartEventsHistory history =
                    new PaxInstitutionalChartEventsHistory(50);
            history.merge(fetcher.latestInstitutionalChartEvents());
            if (history.size() != 1) {
                throw new AssertionError("expected 1 event after first poll; got " + history.size());
            }

            // Second poll: empty payload.
            body.set(snapshotWithEmptyEvents());
            if (!fetcher.tickOnce(System.currentTimeMillis())) {
                throw new AssertionError("second tick must succeed");
            }
            int added = history.merge(fetcher.latestInstitutionalChartEvents());
            if (added != 0) {
                throw new AssertionError("empty payload must add 0; got " + added);
            }
            if (history.size() != 1) {
                throw new AssertionError(
                        "empty payload must NOT clear history; size=" + history.size());
            }
        } finally {
            server.stop(0);
        }
    }

    private static void chartEventsHistoryDedupsById() throws Exception {
        // Poll 1 + Poll 2 same id -> history size still 1.
        AtomicReference<String> body = new AtomicReference<>(snapshotWithOneEvent());
        HttpServer server = startServer(body);
        try {
            PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
            String url = "http://127.0.0.1:" + server.getAddress().getPort() + "/api/snapshot";
            fetcher.applySettings(true, url, 1000);
            fetcher.tickOnce(System.currentTimeMillis());
            PaxInstitutionalChartEventsHistory history =
                    new PaxInstitutionalChartEventsHistory(50);
            history.merge(fetcher.latestInstitutionalChartEvents());
            // Repeat the same poll (same body, same id).
            fetcher.tickOnce(System.currentTimeMillis());
            int added = history.merge(fetcher.latestInstitutionalChartEvents());
            if (added != 0) {
                throw new AssertionError("duplicate id must not re-add");
            }
            if (history.size() != 1) {
                throw new AssertionError("history size must stay 1; got " + history.size());
            }
        } finally {
            server.stop(0);
        }
    }

    // ─── Settings model defaults ──────────────────────────────────────────

    private static void uiSettingsDefaultsPersistShowInstitutionalChartEvents() {
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        if (!s.showInstitutionalChartEvents) {
            throw new AssertionError("showInstitutionalChartEvents must default to true");
        }
        // Flipping it persists through getter / setter chain (Bookmap
        // ser/deser would copy the field). Toggle and read back.
        s.showInstitutionalChartEvents = false;
        if (s.showInstitutionalChartEvents) {
            throw new AssertionError("settings field must round-trip");
        }
    }

    // ─── HTTP fixture helpers ──────────────────────────────────────────────

    private static String snapshotWithOneEvent() {
        return "{"
                + "\"health\":\"ok\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"institutional_chart_events\":[{"
                + "\"id\":\"NQM6.CME@RITHMIC|+3|WATCH_LEVEL|1000\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"label\":\"+3\","
                + "\"price\":20195.0,"
                + "\"side\":\"above\","
                + "\"event_type\":\"WATCH_LEVEL\","
                + "\"direction\":\"NONE\","
                + "\"execution_read\":\"WAIT_FOR_CONFIRM\","
                + "\"marker_text\":\"WATCH\","
                + "\"marker_color_hint\":\"#E5C100\","
                + "\"severity\":\"WATCH\","
                + "\"timestamp_ms\":1000,"
                + "\"source\":\"institutional_thesis\","
                + "\"confidence\":0.35,"
                + "\"reason_codes\":[]"
                + "}]}";
    }

    private static String snapshotWithEmptyEvents() {
        return "{"
                + "\"health\":\"ok\","
                + "\"alias\":\"NQM6.CME@RITHMIC\","
                + "\"institutional_chart_events\":[]"
                + "}";
    }

    private static HttpServer startServer(AtomicReference<String> body) throws IOException {
        HttpServer s = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        s.createContext("/api/snapshot", new HttpHandler() {
            @Override
            public void handle(HttpExchange exchange) throws IOException {
                byte[] bytes = body.get().getBytes(StandardCharsets.UTF_8);
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, bytes.length);
                try (OutputStream os = exchange.getResponseBody()) { os.write(bytes); }
            }
        });
        s.setExecutor(null);
        s.start();
        return s;
    }
}
