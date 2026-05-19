package com.openrange;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicLong;

import velox.api.layer1.common.Log;

final class PaxHeatwaveFetcher {

    static final long REQUEST_TIMEOUT_MS = 750L;
    static final long CONNECT_TIMEOUT_MS = 750L;
    static final int FAIL_BACKOFF_THRESHOLD = 3;
    static final long MAX_BACKOFF_MS = 5_000L;
    static final long LOG_THROTTLE_MS = 60_000L;
    static final String DEFAULT_URL = "http://127.0.0.1:18888/api/snapshot";

    private final Object lifecycleLock = new Object();
    private final Runnable repaintCallback;

    private volatile HttpClient httpClient;
    private volatile Thread worker;
    private volatile boolean enabled;
    private volatile String url = DEFAULT_URL;
    private volatile int pollMs = 1000;
    private volatile PaxHeatwaveModel latest;
    private final AtomicInteger consecutiveFailures = new AtomicInteger(0);
    private final AtomicLong lastWarnLogMs = new AtomicLong(0L);

    PaxHeatwaveFetcher(Runnable repaintCallback) {
        this.repaintCallback = repaintCallback;
    }

    void applySettings(PaxOpeningRangeUiSettings s) {
        if (s == null) {
            return;
        }
        boolean shouldRun = s.showHeatwaveBox;
        String newUrl = s.heatwaveUrl == null || s.heatwaveUrl.isBlank() ? DEFAULT_URL : s.heatwaveUrl;
        int newPoll = Math.max(500, Math.min(3000, s.heatwavePollMs));
        synchronized (lifecycleLock) {
            this.url = newUrl;
            this.pollMs = newPoll;
            this.enabled = shouldRun;
            if (shouldRun && worker == null) {
                startWorkerLocked();
            } else if (!shouldRun && worker != null) {
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

    void stop() {
        stopWorkerInternal();
    }

    PaxHeatwaveModel snapshot() {
        return latest;
    }

    boolean isRunning() {
        Thread t = worker;
        return t != null && t.isAlive();
    }

    int consecutiveFailures() {
        return consecutiveFailures.get();
    }

    void tickOnceForTest() {
        tickOnce(System.currentTimeMillis());
    }

    private void startWorkerLocked() {
        ensureHttpClient();
        Thread t = new Thread(this::loop, "OpenRange-Heatwave-Fetcher");
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
            PaxHeatwaveModel parsed = PaxHeatwaveSnapshotParser.parse(resp.body(), nowMs);
            latest = parsed;
            consecutiveFailures.set(0);
            fireRepaint();
            return true;
        } catch (PaxHeatwaveSnapshotParser.ParseException pe) {
            handleFailure(nowMs, "parse: " + pe.getMessage());
            return false;
        } catch (Exception e) {
            handleFailure(nowMs, e.getClass().getSimpleName());
            return false;
        }
    }

    private void handleFailure(long nowMs, String reason) {
        int fails = consecutiveFailures.incrementAndGet();
        long last = lastWarnLogMs.get();
        if (fails == 1 || nowMs - last >= LOG_THROTTLE_MS) {
            if (lastWarnLogMs.compareAndSet(last, nowMs)) {
                try {
                    Log.warn("OpenRange heatwave fetch failed (" + fails + ") " + reason);
                } catch (Throwable t) {
                    // Bookmap Log may be unavailable in unit tests; suppress
                }
            }
        }
        if (fails == 1) {
            fireRepaint();
        }
    }

    private void fireRepaint() {
        Runnable cb = repaintCallback;
        if (cb == null) {
            return;
        }
        try {
            cb.run();
        } catch (Throwable t) {
            // Never let painter exceptions kill the fetch loop
        }
    }

    long computeSleepMs(boolean ok) {
        if (ok) {
            return pollMs;
        }
        int fails = consecutiveFailures.get();
        if (fails < FAIL_BACKOFF_THRESHOLD) {
            return pollMs;
        }
        long shift = Math.min(3, fails - FAIL_BACKOFF_THRESHOLD + 1);
        long backoff = pollMs * (1L << shift);
        return Math.max(500L, Math.min(MAX_BACKOFF_MS, backoff));
    }
}
