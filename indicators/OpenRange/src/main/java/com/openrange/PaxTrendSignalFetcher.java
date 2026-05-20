package com.openrange;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

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
            latest = parsed;
            consecutiveFailures.set(0);
            fireRepaintIfNeeded(parsed, nowMs);
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

    private void fireRepaintIfNeeded(PaxTrendSignalModel parsed, long nowMs) {
        String key = semanticKey(parsed);
        boolean changed = !key.equals(lastRepaintKey);
        if (changed || nowMs - lastRepaintAtMs >= UNCHANGED_REPAINT_MIN_MS) {
            lastRepaintKey = key;
            lastRepaintAtMs = nowMs;
            fireRepaint();
        }
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
