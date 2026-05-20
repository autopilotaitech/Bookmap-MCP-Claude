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
    private volatile PaxHeatwaveModel latest;
    private final AtomicInteger consecutiveFailures = new AtomicInteger(0);
    private final AtomicLong lastWarnLogMs = new AtomicLong(0L);
    /** Last failure reason (short, never contains the token) and timestamp.
     * Used by the painter to render DASHBOARD OFFLINE when the dashboard
     * HTTP endpoint itself is unreachable. */
    private volatile String lastFailureReason = "";
    private volatile long lastFailureAtMs = 0L;
    private volatile String lastRepaintKey = "";
    private volatile long lastRepaintAtMs = 0L;

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

    /** Threshold for escalating from "stale live model" to DASHBOARD_OFFLINE.
     * After this many consecutive HTTP failures we stop trusting the cached
     * last-success model and synthesize an offline carrier so the painter
     * tells the operator the dashboard is unreachable, not just stale. */
    static final int STALE_FAIL_THRESHOLD = 5;

    /** Disposition-aware view used by the painter.
     *
     * <p>Decision tree:</p>
     * <ul>
     *   <li>{@code latest == null && consecutiveFailures > 0}
     *       → DASHBOARD_OFFLINE (we never got a single successful response).</li>
     *   <li>{@code latest == null && consecutiveFailures == 0}
     *       → null (caller renders NO_DATA — cold start before any tick).</li>
     *   <li>{@code latest != null && consecutiveFailures >= STALE_FAIL_THRESHOLD}
     *       → DASHBOARD_OFFLINE (we had a success once but the dashboard has
     *       been unreachable for at least STALE_FAIL_THRESHOLD ticks; stop
     *       showing the stale live model).</li>
     *   <li>otherwise → latest (which may itself be a BRIDGE_OFFLINE carrier
     *       if the dashboard returned {@code health=offline}; the painter
     *       handles that via {@code model.state}).</li>
     * </ul>
     *
     * <p>STALE_AGE_MS handling (painter showing "STALE" on a LIVE model with
     * old fetchedAtMs) is independent of this escalation and continues to
     * apply: a fresh-success that hasn't been retried yet still ages out
     * via {@link PaxHeatwaveModel#ageState(long, long, long)}.</p>
     */
    PaxHeatwaveModel effectiveModel(long nowMs) {
        PaxHeatwaveModel cur = latest;
        int fails = consecutiveFailures.get();
        if (cur == null) {
            if (fails > 0) {
                String reason = lastFailureReason.isEmpty() ? "unreachable" : lastFailureReason;
                return PaxHeatwaveModel.dashboardOffline(nowMs, url, reason);
            }
            return null;
        }
        // We had a successful fetch at some point — but if the dashboard has
        // since been unreachable for a sustained run, escalate so the
        // operator doesn't keep staring at a frozen "live" overlay.
        if (fails >= STALE_FAIL_THRESHOLD) {
            String reason = lastFailureReason.isEmpty() ? "unreachable" : lastFailureReason;
            return PaxHeatwaveModel.dashboardOffline(nowMs, url, reason);
        }
        return cur;
    }

    String lastFailureReason() { return lastFailureReason; }
    long lastFailureAtMs() { return lastFailureAtMs; }
    String currentUrl() { return url; }

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
            fireRepaintIfNeeded(parsed, nowMs);
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
        lastFailureReason = reason == null ? "" : reason;
        lastFailureAtMs = nowMs;
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

    private void fireRepaintIfNeeded(PaxHeatwaveModel parsed, long nowMs) {
        String key = semanticKey(parsed);
        boolean changed = !key.equals(lastRepaintKey);
        if (changed || nowMs - lastRepaintAtMs >= UNCHANGED_REPAINT_MIN_MS) {
            lastRepaintKey = key;
            lastRepaintAtMs = nowMs;
            fireRepaint();
        }
    }

    private static String semanticKey(PaxHeatwaveModel model) {
        if (model == null) {
            return "null";
        }
        StringBuilder sb = new StringBuilder(256);
        sb.append(model.state).append('|')
          .append(model.verdict).append('|')
          .append(model.verdictTone).append('|')
          .append(quantize(model.scoreText)).append('|')
          .append(model.offlineUrl).append('|')
          .append(model.offlineReason);
        if (model.rows != null) {
            for (PaxHeatwaveModel.Row row : model.rows) {
                if (row == null) {
                    sb.append("|<null>");
                } else {
                    sb.append('|').append(row.label)
                      .append(':').append(quantize(row.scoreText))
                      .append(':').append(row.tone)
                      .append(':').append(row.hint);
                }
            }
        }
        return sb.toString();
    }

    /** Quantize a "%+0.2f" or "+NN" formatted score to 0.1 granularity so
     * tiny ±0.01 jitter does not invalidate the repaint dedup. Returns the
     * input unchanged for non-numeric strings like "--". */
    static String quantize(String scoreText) {
        if (scoreText == null || scoreText.isEmpty() || "--".equals(scoreText)) {
            return scoreText == null ? "" : scoreText;
        }
        try {
            // Drop a single leading '+' that Double.parseDouble rejects.
            String s = scoreText.charAt(0) == '+' ? scoreText.substring(1) : scoreText;
            // Verdict score uses "+NN"/"-NN" (percent ×100); row scores use
            // "+0.NN" / "-0.NN". Quantize anything in [-1,1] to 0.1; anything
            // outside (verdict percent) is already discrete enough.
            double v = Double.parseDouble(s);
            if (Math.abs(v) <= 1.0) {
                long q = Math.round(v * 10.0);
                return String.format(java.util.Locale.ROOT, "%+.1f", q / 10.0);
            }
            return scoreText;
        } catch (NumberFormatException nfe) {
            return scoreText;
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
