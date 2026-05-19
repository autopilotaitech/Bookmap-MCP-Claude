package com.openrange;

public class PaxTrendTriangleDedupTest {

    private static final long NOW_MS = 1_747_680_123_999L;
    private static final long STALE_AGE_MS = 30_000L;
    private static final double TICK = 0.25;

    public static void main(String[] args) {
        // Dedup core
        sameBucketSameKindDoesNotEmit();
        differentBucketEmits();
        differentKindEmits();
        firstEmitWhenStateIsBlank();

        // Hard guards (audit fix 5)
        nullSignalNoEmit();
        staleSignalNoEmit();
        noneKindNoEmit();
        nonFiniteMidNoEmit();
        zeroMidNoEmit();
        negativeMidNoEmit();
        zeroEventMsNoEmit();
        negativeEventMsNoEmit();
        zeroTickSizeNoEmit();
        nanTickSizeNoEmit();
        negativeTickSizeNoEmit();
        validSignalEmits();
        eligibleFalseBlocksEmitEvenWhenStrongBull();
        eligibleTrueWithValidFieldsEmits();
        System.out.println("PaxTrendTriangleDedupTest OK");
        System.exit(0);
    }

    /** Plot-eligibility is the authoritative gate from the dashboard. Even
     *  a STRONG_BULL signal with all-other-fields-valid must be blocked
     *  when the dashboard marked it ineligible. */
    private static void eligibleFalseBlocksEmitEvenWhenStrongBull() {
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                /*eventMs=*/1_700_000_000_000L, /*asOfMs=*/NOW_MS,
                /*bucketEnteredMs=*/100L, /*changed=*/true,
                /*fetchedAtMs=*/NOW_MS,
                /*eligible=*/false,
                /*blockedReason=*/"invalid_mid",
                /*eventMsSource=*/"wall_clock_fallback");
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (ok) throw new AssertionError("eligible=false must block emit even for STRONG_BULL");
    }

    /** Same shape but eligible=true — must emit. Mirrors validSignalEmits
     *  with the eligible flag made explicit for clarity. */
    private static void eligibleTrueWithValidFieldsEmits() {
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                /*eventMs=*/1_700_000_000_000L, /*asOfMs=*/NOW_MS,
                /*bucketEnteredMs=*/100L, /*changed=*/true,
                /*fetchedAtMs=*/NOW_MS,
                /*eligible=*/true, "", "trend_analyzer");
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (!ok) throw new AssertionError("eligible=true valid STRONG_BULL must emit");
    }

    private static void sameBucketSameKindDoesNotEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS,
                "STRONG_BULL", 100L, TICK);
        if (ok) throw new AssertionError("same (kind, bucketEnteredMs) must not re-emit");
    }

    private static void differentBucketEmits() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 101L, 21800.0);
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS,
                "STRONG_BULL", 100L, TICK);
        if (!ok) throw new AssertionError("new bucket must emit");
    }

    private static void differentKindEmits() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS,
                "WEAK_BULL", 100L, TICK);
        if (!ok) throw new AssertionError("kind transition must emit even at same bucket");
    }

    private static void firstEmitWhenStateIsBlank() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (!ok) throw new AssertionError("first emit must fire when no prior state");
    }

    private static void nullSignalNoEmit() {
        boolean ok = PaxTrendTriangleDedup.shouldEmit(null, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (ok) throw new AssertionError("null signal must not emit");
    }

    private static void staleSignalNoEmit() {
        // eligible=true so this test isolates the stale-age gate (not the
        // eligibility gate). Without it the test would pass for the wrong
        // reason after the eligibility gate was added.
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                NOW_MS - 1_000, NOW_MS - 1_000, 100L, true,
                NOW_MS - STALE_AGE_MS - 1,    // fetchedAtMs = stale
                /*eligible=*/true, /*blockedReason=*/"", /*eventMsSource=*/"trend_analyzer");
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (ok) throw new AssertionError("stale signal must not emit");
    }

    private static void noneKindNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.NONE, 100L, 21800.0);
        boolean ok = PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK);
        if (ok) throw new AssertionError("NONE kind must not emit");
    }

    private static void nonFiniteMidNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, Double.NaN);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("NaN mid must not emit");
        PaxTrendSignalModel inf = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, Double.POSITIVE_INFINITY);
        if (PaxTrendTriangleDedup.shouldEmit(inf, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("infinite mid must not emit");
    }

    private static void zeroMidNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 0.0);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("zero mid must not emit");
    }

    private static void negativeMidNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, -1.0);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("negative mid must not emit");
    }

    private static void zeroEventMsNoEmit() {
        // eligible=true so the eventMs check is the gate under test.
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                /*eventMs=*/0L, /*asOfMs=*/NOW_MS, /*bucketEnteredMs=*/100L,
                /*changed=*/true, /*fetchedAtMs=*/NOW_MS,
                /*eligible=*/true, "", "trend_analyzer");
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("eventMs=0 must not emit (would anchor at 1970)");
    }

    private static void negativeEventMsNoEmit() {
        PaxTrendSignalModel sig = new PaxTrendSignalModel(
                PaxTrendSignalModel.Kind.STRONG_BULL, "NQ", 21800.0,
                /*eventMs=*/-1L, NOW_MS, 100L, true, NOW_MS,
                /*eligible=*/true, "", "trend_analyzer");
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("negative eventMs must not emit");
    }

    private static void zeroTickSizeNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, 0.0))
            throw new AssertionError("zero tickSize must not emit");
    }

    private static void nanTickSizeNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, Double.NaN))
            throw new AssertionError("NaN tickSize must not emit");
    }

    private static void negativeTickSizeNoEmit() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        if (PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, -0.25))
            throw new AssertionError("negative tickSize must not emit");
    }

    private static void validSignalEmits() {
        PaxTrendSignalModel sig = signal(PaxTrendSignalModel.Kind.STRONG_BULL, 100L, 21800.0);
        if (!PaxTrendTriangleDedup.shouldEmit(sig, NOW_MS, STALE_AGE_MS, "", 0L, TICK))
            throw new AssertionError("fully valid signal must emit");
    }

    private static PaxTrendSignalModel signal(PaxTrendSignalModel.Kind kind, long bucketMs, double mid) {
        // Test-side factory for VALID signals. Always eligible=true; the
        // 8-arg ctor (eligible defaults false) is reserved for the
        // "bad-field" tests so they exercise the specific field gate.
        return new PaxTrendSignalModel(kind, "NQ", mid,
                /*eventMs=*/1_700_000_000_000L,
                /*asOfMs=*/NOW_MS,
                /*bucketEnteredMs=*/bucketMs,
                /*changed=*/true,
                /*fetchedAtMs=*/NOW_MS,
                /*eligible=*/true,
                /*blockedReason=*/"",
                /*eventMsSource=*/"trend_analyzer");
    }
}
