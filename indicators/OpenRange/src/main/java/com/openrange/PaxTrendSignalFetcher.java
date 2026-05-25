package com.openrange;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.Collections;
import java.util.List;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import velox.api.layer1.common.Log;

/**
 * Polls {@code /api/snapshot} for {@code trend_signal}. Mirrors
 * {@link PaxHeatwaveFetcher} exactly: same poll cadence, same timeouts,
 * same backoff. The worker MUST NOT touch the Bookmap canvas — it only
 * updates {@link #latest} and fires the repaint callback (which sets a
 * dirty-bit on the module). All canvas mutations happen on Bookmap
 * callbacks inside {@code PaxPainter.update()}.
 */
final class PaxTrendSignalFetcher {

    static final long REQUEST_TIMEOUT_MS = 12_000L;
    static final long CONNECT_TIMEOUT_MS = 1_000L;
    static final int FAIL_BACKOFF_THRESHOLD = 3;
    static final long MAX_BACKOFF_MS = 5_000L;
    static final long LOG_THROTTLE_MS = 60_000L;
    static final long UNCHANGED_REPAINT_MIN_MS = 5_000L;
    static final String DEFAULT_URL = "http://127.0.0.1:18888/api/snapshot";

    private final Object lifecycleLock = new Object();
    private final Runnable repaintCallback;

    private volatile HttpClient httpClient;
    private volatile Thread worker;
    private volatile boolean enabled;
    private volatile String url = DEFAULT_URL;
    private volatile int pollMs = 1000;
    private volatile PaxTrendSignalModel latest;
    private final AtomicReference<List<PaxInstitutionalSignalEvent>> latestEvents =
            new AtomicReference<>(Collections.emptyList());
    private final AtomicReference<List<PaxInstitutionalChartEvent>> latestChartEvents =
            new AtomicReference<>(Collections.emptyList());
    private final AtomicReference<List<PaxInstitutionalChartEvent>> latestPaxAiChartEvents =
            new AtomicReference<>(Collections.emptyList());
    private final AtomicInteger consecutiveFailures = new AtomicInteger(0);
    private final AtomicLong lastWarnLogMs = new AtomicLong(0L);
    /** Last failure reason (short, never contains the token) and timestamp.
     * Mirrors PaxHeatwaveFetcher so diagnostics see the same surface on both. */
    private volatile String lastFailureReason = "";
    private volatile long lastFailureAtMs = 0L;
    private volatile String lastRepaintKey = "";
    private volatile long lastRepaintAtMs = 0L;

    PaxTrendSignalFetcher(Runnable repaintCallback) {
        this.repaintCallback = repaintCallback;
    }

    void applySettings(boolean showTriangles, String settingsUrl, int settingsPollMs) {
        String newUrl = (settingsUrl == null || settingsUrl.isBlank()) ? DEFAULT_URL : settingsUrl;
        int newPoll = Math.max(500, Math.min(3000, settingsPollMs));
        synchronized (lifecycleLock) {
            this.url = newUrl;
            this.pollMs = newPoll;
            this.enabled = showTriangles;
            if (showTriangles && worker == null) {
                startWorkerLocked();
            } else if (!showTriangles && worker != null) {
                stopWorkerInternal();
            }
        }
    }

    void start() {
        synchronized (lifecycleLock) {
            if (enabled && worker == null) {
                startWorkerLocked();
            }
        }
    }

    void stop() { stopWorkerInternal(); }

    PaxTrendSignalModel snapshot() { return latest; }

    /** Latest institutional signal events parsed from the most recent
     *  successful poll. NEVER null; empty when no successful fetch has
     *  occurred yet OR the latest payload had no institutional_signals.
     *  The painter merges this into its durable history; the fetcher
     *  intentionally never clears prior history on empty polls. */
    List<PaxInstitutionalSignalEvent> latestInstitutionalEvents() {
        return latestEvents.get();
    }

    /** Latest institutional chart events (the evidence-trail payload).
     *  Never null; empty until a successful fetch or when the latest
     *  payload had no institutional_chart_events. The painter merges this
     *  into its durable history; empty polls do not clear prior markers. */
    List<PaxInstitutionalChartEvent> latestInstitutionalChartEvents() {
        return latestChartEvents.get();
    }

    /** Latest Pax AI chart events. Same persistence semantics as
     *  {@link #latestInstitutionalChartEvents()}: never null, empty until
     *  a successful poll or empty payload. The painter merges this into
     *  its durable AI-history; empty polls do NOT clear prior markers. */
    List<PaxInstitutionalChartEvent> latestPaxAiChartEvents() {
        return latestPaxAiChartEvents.get();
    }

    String lastFailureReason() { return lastFailureReason; }
    long lastFailureAtMs() { return lastFailureAtMs; }
    String currentUrl() { return url; }

    boolean isRunning() {
        Thread t = worker;
        return t != null && t.isAlive();
    }

    int consecutiveFailures() { return consecutiveFailures.get(); }

    void tickOnceForTest() { tickOnce(System.currentTimeMillis()); }

    private void startWorkerLocked() {
        ensureHttpClient();
        Thread t = new Thread(this::loop, "OpenRange-TrendSignal-Fetcher");
        t.setDaemon(true);
        worker = t;
        t.start();
    }

    private void stopWorkerInternal() {
        Thread t;
        synchronized (lifecycleLock) {
            t = worker;
            worker = null;
        }
        if (t != null) {
            t.interrupt();
            try {
                t.join(2000L);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private void ensureHttpClient() {
        if (httpClient == null) {
            httpClient = HttpClient.newBuilder()
                    .connectTimeout(Duration.ofMillis(CONNECT_TIMEOUT_MS))
                    .build();
        }
    }

    private void loop() {
        Thread self = Thread.currentThread();
        while (worker == self && !self.isInterrupted()) {
            long started = System.currentTimeMillis();
            boolean ok = tickOnce(started);
            try {
                Thread.sleep(computeSleepMs(ok));
            } catch (InterruptedException e) {
                self.interrupt();
                break;
            }
        }
    }

    boolean tickOnce(long nowMs) {
        HttpClient client = httpClient;
        if (client == null) {
            ensureHttpClient();
            client = httpClient;
        }
        String target = url;
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(target))
                    .timeout(Duration.ofMillis(REQUEST_TIMEOUT_MS))
                    .GET()
                    .build();
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            int status = resp.statusCode();
            if (status / 100 != 2) {
                handleFailure(nowMs, "http " + status);
                return false;
            }
            PaxTrendSignalModel parsed = PaxTrendSignalSnapshotParser.parse(resp.body(), nowMs);
            List<PaxInstitutionalSignalEvent> events =
                    PaxTrendSignalSnapshotParser.parseInstitutionalEvents(resp.body());
            List<PaxInstitutionalChartEvent> chartEvents =
                    PaxTrendSignalSnapshotParser.parseChartEvents(resp.body());
            List<PaxInstitutionalChartEvent> paxAiEvents =
                    PaxTrendSignalSnapshotParser.parsePaxAiChartEvents(resp.body());
            latest = parsed;
            latestEvents.set(events);
            latestChartEvents.set(chartEvents);
            latestPaxAiChartEvents.set(paxAiEvents);
            consecutiveFailures.set(0);
            fireRepaintIfNeeded(parsed, chartEvents, paxAiEvents, nowMs);
            return true;
        } catch (PaxTrendSignalSnapshotParser.ParseException pe) {
            handleFailure(nowMs, "parse: " + pe.getMessage());
            return false;
        } catch (Exception e) {
            handleFailure(nowMs, e.getClass().getSimpleName());
            return false;
        }
    }

    private void handleFailure(long nowMs, String reason) {
        int fails = consecutiveFailures.incrementAndGet();
        lastFailureReason = reason == null ? "" : reason;
        lastFailureAtMs = nowMs;
        long last = lastWarnLogMs.get();
        if (fails == 1 || nowMs - last >= LOG_THROTTLE_MS) {
            if (lastWarnLogMs.compareAndSet(last, nowMs)) {
                try {
                    Log.warn("OpenRange trend_signal fetch failed (" + fails + ") " + reason);
                } catch (Throwable t) {
                    // Bookmap Log unavailable in unit tests; suppress.
                }
            }
        }
        if (fails == 1) {
            fireRepaint();
        }
    }

    private void fireRepaint() {
        Runnable cb = repaintCallback;
        if (cb == null) return;
        try {
            cb.run();
        } catch (Throwable t) {
            // Never let painter exceptions kill the fetch loop.
        }
    }

    private void fireRepaintIfNeeded(PaxTrendSignalModel parsed,
                                       List<PaxInstitutionalChartEvent> chartEvents,
                                       List<PaxInstitutionalChartEvent> paxAiEvents,
                                       long nowMs) {
        String key = combinedSemanticKey(parsed, chartEvents, paxAiEvents);
        boolean changed = !key.equals(lastRepaintKey);
        if (changed || nowMs - lastRepaintAtMs >= UNCHANGED_REPAINT_MIN_MS) {
            lastRepaintKey = key;
            lastRepaintAtMs = nowMs;
            fireRepaint();
        }
    }

    /** Combined semantic key for repaint dedup. Includes the
     *  trend-signal kernel (legacy semanticKey) plus the local +
     *  Pax AI chart-event arrays as sorted-id signatures. Any of the
     *  following changes triggers a repaint within one poll:
     *  <ul>
     *    <li>A new AI event id appears.</li>
     *    <li>An AI event id disappears (TTL drop on the dashboard side).</li>
     *    <li>A new local institutional chart event id appears.</li>
     *    <li>The legacy trend_signal renderable tuple changes.</li>
     *  </ul>
     *  Package-private for testability. */
    static String combinedSemanticKey(PaxTrendSignalModel parsed,
                                       List<PaxInstitutionalChartEvent> chartEvents,
                                       List<PaxInstitutionalChartEvent> paxAiEvents) {
        return semanticKey(parsed)
                + "|CE=" + eventsKey(chartEvents)
                + "|AI=" + eventsKey(paxAiEvents);
    }

    /** Stable signature of a chart-event list: size + sorted ids.
     *  Two lists with the same set of ids (in any order) produce the
     *  same key, so a re-poll that returns the same events does NOT
     *  retrigger a repaint. */
    static String eventsKey(List<PaxInstitutionalChartEvent> events) {
        if (events == null || events.isEmpty()) return "0";
        java.util.ArrayList<String> ids = new java.util.ArrayList<>(events.size());
        for (PaxInstitutionalChartEvent e : events) {
            if (e == null) continue;
            ids.add(e.id == null ? "" : e.id);
        }
        java.util.Collections.sort(ids);
        StringBuilder sb = new StringBuilder(16 + ids.size() * 24);
        sb.append(ids.size()).append(';');
        for (String id : ids) sb.append(id).append(',');
        return sb.toString();
    }

    private static String semanticKey(PaxTrendSignalModel model) {
        if (model == null) {
            return "null";
        }
        // When the dashboard says the signal is ineligible (kind=NONE,
        // warmup, invalid mid, blockedReason), no triangle is drawn — so a
        // changing mid or eventMs each poll must NOT invalidate the dedup
        // key. Otherwise the fetcher would force a repaint every poll for
        // zero visible output. Keep only the renderable-tuple fields.
        if (!model.eligible
                || model.kind == null
                || model.kind == PaxTrendSignalModel.Kind.NONE) {
            return "INELIGIBLE|"
                    + model.kind + "|"
                    + model.alias + "|"
                    + model.eligible + "|"
                    + model.blockedReason;
        }
        // Eligible path — bucketEnteredMs already advances only on
        // renderable-kind transitions, so it stabilizes the key without
        // bringing back per-tick mid/eventMs drift. Drop mid; keep
        // bucketEnteredMs so legitimate kind transitions still trigger a
        // repaint.
        return model.kind + "|"
                + model.alias + "|"
                + model.bucketEnteredMs + "|"
                + model.eligible + "|"
                + model.blockedReason + "|"
                + model.eventMsSource;
    }

    long computeSleepMs(boolean ok) {
        if (ok) return pollMs;
        int fails = consecutiveFailures.get();
        if (fails < FAIL_BACKOFF_THRESHOLD) return pollMs;
        long shift = Math.min(3, fails - FAIL_BACKOFF_THRESHOLD + 1);
        long backoff = pollMs * (1L << shift);
        return Math.max(500L, Math.min(MAX_BACKOFF_MS, backoff));
    }
}
