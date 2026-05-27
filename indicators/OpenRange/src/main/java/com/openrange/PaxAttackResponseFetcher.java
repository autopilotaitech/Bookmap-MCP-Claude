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
 * Polls Pax AI server at {@code /api/pax/attack-response} for the
 * deterministic attack-response state list. Mirrors
 * {@link PaxLevelEdgeFetcher}: daemon thread, HTTP timeouts, exponential
 * backoff after sustained failures, throttled warn log, and the SAME
 * threading invariant - the worker NEVER mutates the Bookmap canvas.
 * It updates {@link #latest} and fires the supplied repaint callback
 * (the module wires it to {@code heatwaveDirty.set(true)} so canvas
 * mutation only happens on Bookmap-callback threads).
 *
 * <p>Endpoint is hard-coded to the Pax AI server (port 18891), NOT the
 * dashboard (port 18888). The two surfaces are intentionally separate;
 * see {@code reports/pax-ai-attack-response-implementation-2026-05-27.md}.</p>
 */
final class PaxAttackResponseFetcher {

    static final long REQUEST_TIMEOUT_MS = 5_000L;
    static final long CONNECT_TIMEOUT_MS = 1_000L;
    static final int  FAIL_BACKOFF_THRESHOLD = 3;
    static final long MAX_BACKOFF_MS = 5_000L;
    static final long LOG_THROTTLE_MS = 60_000L;
    static final int  POLL_MS = 1_000;
    static final String URL = "http://127.0.0.1:18891/api/pax/attack-response";
    /** After this many consecutive HTTP failures the painter stops
     *  trusting the last good model. {@link #effectiveModel(long)}
     *  returns null past this threshold so the chart goes clean rather
     *  than showing stale attack-response labels. */
    static final int STALE_FAIL_THRESHOLD = 5;

    private final Object lifecycleLock = new Object();
    private final Runnable repaintCallback;

    private volatile HttpClient httpClient;
    private volatile Thread worker;
    private volatile boolean enabled;
    private volatile PaxAttackResponseModel latest;
    private final AtomicInteger consecutiveFailures = new AtomicInteger(0);
    private final AtomicLong lastWarnLogMs = new AtomicLong(0L);
    private volatile String lastFailureReason = "";
    private volatile long lastFailureAtMs = 0L;
    private volatile boolean inFailureState = false;

    PaxAttackResponseFetcher(Runnable repaintCallback) {
        this.repaintCallback = repaintCallback;
    }

    void start() {
        synchronized (lifecycleLock) {
            if (enabled && worker != null) return;
            enabled = true;
            startWorkerLocked();
        }
    }

    void stop() {
        Thread t;
        synchronized (lifecycleLock) {
            enabled = false;
            t = worker;
            worker = null;
        }
        if (t != null) {
            t.interrupt();
            try { t.join(2000L); }
            catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
    }

    /** Most recent successfully parsed model. Null until the first
     *  successful poll. Callers should prefer
     *  {@link #effectiveModel(long)} which also enforces
     *  STALE_FAIL_THRESHOLD escalation. */
    PaxAttackResponseModel snapshot() { return latest; }

    /** Disposition-aware view used by the painter. Returns null when
     *  the chart should draw nothing - either we never had a successful
     *  fetch, or sustained failures have invalidated the last good
     *  model. */
    PaxAttackResponseModel effectiveModel(long nowMs) {
        PaxAttackResponseModel cur = latest;
        int fails = consecutiveFailures.get();
        if (cur == null) return null;
        if (fails >= STALE_FAIL_THRESHOLD) return null;
        return cur;
    }

    boolean isRunning() {
        Thread t = worker;
        return t != null && t.isAlive();
    }

    int consecutiveFailures() { return consecutiveFailures.get(); }
    String lastFailureReason() { return lastFailureReason; }
    long lastFailureAtMs() { return lastFailureAtMs; }

    /** Test seam - runs one synchronous fetch attempt. */
    boolean tickOnceForTest(long nowMs) { return tickOnce(nowMs); }

    /** Test seam - override the polled URL. Used by HTTP tests that
     *  spin up an ephemeral local server. Production callers MUST NOT
     *  use this method - the production endpoint is hard-coded. */
    private volatile String urlOverride;
    void setUrlForTest(String url) { this.urlOverride = url; }

    private void startWorkerLocked() {
        ensureHttpClient();
        Thread t = new Thread(this::loop, "OpenRange-AttackResponse-Fetcher");
        t.setDaemon(true);
        worker = t;
        t.start();
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
            try { Thread.sleep(computeSleepMs(ok)); }
            catch (InterruptedException e) { self.interrupt(); break; }
        }
    }

    boolean tickOnce(long nowMs) {
        HttpClient client = httpClient;
        if (client == null) { ensureHttpClient(); client = httpClient; }
        String url = urlOverride != null ? urlOverride : URL;
        try {
            HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                    .timeout(Duration.ofMillis(REQUEST_TIMEOUT_MS))
                    .GET()
                    .build();
            HttpResponse<String> resp = client.send(req, HttpResponse.BodyHandlers.ofString());
            int status = resp.statusCode();
            if (status == 503) {
                // Pax AI server up but poller cold -> empty model, not a failure.
                handleEmptyOk(nowMs);
                return true;
            }
            if (status / 100 != 2) {
                handleFailure(nowMs, "http " + status);
                return false;
            }
            PaxAttackResponseModel parsed =
                    PaxAttackResponseSnapshotParser.parse(resp.body(), nowMs);
            latest = parsed;
            consecutiveFailures.set(0);
            if (inFailureState) {
                inFailureState = false;
                try { Log.info("OpenRange attack-response fetch back online"); }
                catch (Throwable ignored) {}
            }
            fireRepaint();
            return true;
        } catch (PaxAttackResponseSnapshotParser.ParseException pe) {
            handleFailure(nowMs, "parse: " + pe.getMessage());
            return false;
        } catch (Exception e) {
            handleFailure(nowMs, e.getClass().getSimpleName());
            return false;
        }
    }

    private void handleEmptyOk(long nowMs) {
        latest = PaxAttackResponseModel.empty(nowMs);
        consecutiveFailures.set(0);
        fireRepaint();
    }

    private void handleFailure(long nowMs, String reason) {
        int fails = consecutiveFailures.incrementAndGet();
        lastFailureReason = reason == null ? "" : reason;
        lastFailureAtMs = nowMs;
        long last = lastWarnLogMs.get();
        if (fails == 1 || nowMs - last >= LOG_THROTTLE_MS) {
            if (lastWarnLogMs.compareAndSet(last, nowMs)) {
                try { Log.warn("OpenRange attack-response fetch failed (" + fails + ") " + reason); }
                catch (Throwable ignored) {}
            }
        }
        inFailureState = true;
        if (fails == 1 || fails == STALE_FAIL_THRESHOLD) {
            fireRepaint();
        }
    }

    private void fireRepaint() {
        Runnable cb = repaintCallback;
        if (cb == null) return;
        try { cb.run(); }
        catch (Throwable ignored) { /* never let UI exceptions kill the loop */ }
    }

    long computeSleepMs(boolean ok) {
        if (ok) return POLL_MS;
        int fails = consecutiveFailures.get();
        if (fails < FAIL_BACKOFF_THRESHOLD) return POLL_MS;
        long shift = Math.min(3, fails - FAIL_BACKOFF_THRESHOLD + 1);
        long backoff = (long) POLL_MS * (1L << shift);
        return Math.max(500L, Math.min(MAX_BACKOFF_MS, backoff));
    }
}
