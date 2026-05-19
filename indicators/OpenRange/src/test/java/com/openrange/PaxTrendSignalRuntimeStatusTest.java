package com.openrange;

public class PaxTrendSignalRuntimeStatusTest {

    private static final long NOW_MS = 1_747_680_123_999L;
    private static final long STALE_AGE_MS = 30_000L;
    private static final double TICK = 0.25;

    public static void main(String[] args) {
        priorEmitNotRepeatedWhenDashboardThenOffline();
        priorEmitNotRepeatedAfterConnectionFailureCachedSignal();
        offlinePayloadParsesToNone();
        missingTrendSignalParsesToNone();
        fetcherTracksLastFailureReasonAndMs();
        System.out.println("PaxTrendSignalRuntimeStatusTest OK");
        System.exit(0);
    }

    /** Once a triangle has been emitted for (kind, bucketEnteredMs), the same
     *  signal must NOT trigger another emit even if the dashboard later goes
     *  offline (latest still holds the old signal). */
    private static void priorEmitNotRepeatedWhenDashboardThenOffline() {
        PaxTrendSignalModel sig = makeSig(PaxTrendSignalModel.Kind.STRONG_BULL,
                100L, 21800.0, NOW_MS);
        // First call: emit (lastEmittedBucketEnteredMs = 0, mismatch -> emit).
        boolean first = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS,
                "", 0L, TICK);
        if (!first) throw new AssertionError("first call must emit");
        // Simulate "we emitted it" then dashboard goes offline → fetcher.snapshot()
        // still returns this same signal. Subsequent ticks must not re-emit.
        boolean second = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS + 1_000L, STALE_AGE_MS,
                "STRONG_BULL", 100L, TICK);
        if (second) throw new AssertionError("same (kind, bucketEnteredMs) must not re-emit after offline");
    }

    /** Same property but signal becomes stale (older than STALE_AGE_MS). */
    private static void priorEmitNotRepeatedAfterConnectionFailureCachedSignal() {
        // signal fetchedAtMs is older than STALE_AGE_MS — must NOT emit at all.
        // eligible=true so the stale gate is what we're verifying.
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                /*eventMs=*/1_700_000_000_000L, /*asOfMs=*/NOW_MS,
                /*bucketEnteredMs=*/100L, /*changed=*/true,
                /*fetchedAtMs=*/NOW_MS - STALE_AGE_MS - 1,
                /*eligible=*/true, /*blockedReason=*/"", /*eventMsSource=*/"trend_analyzer");
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS,
                "WEAK_BEAR", 50L, TICK);   // even with a "different prior" state
        if (ok) throw new AssertionError("stale signal must never emit");
    }

    private static void offlinePayloadParsesToNone() {
        String body = "{\"health\":\"offline\",\"bridgeError\":\"timeout\"}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, NOW_MS);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("health=offline must yield NONE, got " + m.kind);
    }

    private static void missingTrendSignalParsesToNone() {
        String body = "{\"health\":\"ok\",\"conviction\":{\"trend\":\"CHOP\"}}";
        PaxTrendSignalModel m = PaxTrendSignalSnapshotParser.parse(body, NOW_MS);
        if (m.kind != PaxTrendSignalModel.Kind.NONE)
            throw new AssertionError("missing trend_signal must yield NONE, got " + m.kind);
    }

    private static void fetcherTracksLastFailureReasonAndMs() {
        // Point fetcher at an unreachable port. After tickOnce(), failure
        // metadata must be populated so diagnostics surface a real reason.
        PaxTrendSignalFetcher fetcher = new PaxTrendSignalFetcher(() -> {});
        fetcher.applySettings(false, "http://127.0.0.1:1/api/snapshot", 1000);
        long before = System.currentTimeMillis();
        fetcher.tickOnce(System.currentTimeMillis());
        if (fetcher.consecutiveFailures() < 1)
            throw new AssertionError("expected >=1 consecutive failure after unreachable tick");
        if (fetcher.lastFailureReason() == null || fetcher.lastFailureReason().isEmpty())
            throw new AssertionError("expected non-empty lastFailureReason after failed tick");
        if (fetcher.lastFailureAtMs() < before - 10L)
            throw new AssertionError("lastFailureAtMs must be in or after the test window");
    }

    private static PaxTrendSignalModel makeSig(PaxTrendSignalModel.Kind kind,
            long bucketMs, double mid, long fetchedAtMs) {
        // Valid renderable signal — eligible=true so the dedup test
        // isolates the (kind, bucketEnteredMs) tuple it's actually testing.
        return new PaxTrendSignalModel(kind, "NQ", mid,
                /*eventMs=*/1_700_000_000_000L,
                /*asOfMs=*/NOW_MS,
                /*bucketEnteredMs=*/bucketMs,
                /*changed=*/true,
                /*fetchedAtMs=*/fetchedAtMs,
                /*eligible=*/true, /*blockedReason=*/"", /*eventMsSource=*/"trend_analyzer");
    }
}
