package com.openrange;

/**
 * Pins the render-key memoization contract used by PaxPainter.updateTriangles
 * and PaxPainter.updateOverlay to short-circuit no-op redraws.
 *
 * <p>The bug being pinned: the previous gate combined the render-key check
 * with a "AND triangleShapes is non-empty" / "AND volatileShapes is non-empty"
 * predicate. That meant intentionally-empty render outcomes (dashboard
 * ineligible, signal null, showTrendTriangles=false, badge with empty text)
 * never matched the gate and stormed clear+redraw on every dashboard poll.
 * The fix is to make the render key alone the source of truth - so identical
 * no-shape states still short-circuit on the second and subsequent ticks.</p>
 */
public class PaxOpeningRangeRenderKeyTest {

    public static void main(String[] args) {
        triangleKeyOff_isStableAcrossTicks();
        triangleKeyNull_isStableAcrossTicks();
        triangleKeyIneligible_isStableAcrossTicks();
        triangleKeyEligible_changesWhenKindChanges();
        triangleKeyEligible_changesWhenBucketAdvances();
        triangleKeyIneligibleToEligibleTransition_isDistinct();
        triangleKeyLiveCount_isIncludedInKey();
        triangleKeyShowFalse_overridesSignal();
        heatwaveModelKeyNullModel_returnsNullSentinel();
        heatwaveModelKeySameModel_isStable();
        heatwaveModelKeyDifferentVerdict_isDistinct();
        heatwaveModelKeyDifferentRow_isDistinct();
        heatwaveModelKeyDifferentState_isDistinct();
        System.out.println("PaxOpeningRangeRenderKeyTest OK");
    }

    // ---- triangleRenderKey ------------------------------------------------

    private static void triangleKeyOff_isStableAcrossTicks() {
        String k1 = PaxOpeningRangeModule.triangleRenderKey(false, eligibleSignal(), "WEAK_BULL", 1234L, 2);
        String k2 = PaxOpeningRangeModule.triangleRenderKey(false, ineligibleSignal(), "STRONG_BEAR", 9999L, 0);
        if (!k1.equals(k2)) {
            throw new AssertionError("show=false key must be stable regardless of signal; k1=" + k1 + " k2=" + k2);
        }
        if (!"OFF".equals(k1)) {
            throw new AssertionError("show=false must yield OFF sentinel; got " + k1);
        }
    }

    private static void triangleKeyNull_isStableAcrossTicks() {
        String k1 = PaxOpeningRangeModule.triangleRenderKey(true, null, "", 0L, 0);
        String k2 = PaxOpeningRangeModule.triangleRenderKey(true, null, "", 0L, 0);
        if (!k1.equals(k2)) {
            throw new AssertionError("null signal must produce stable key");
        }
        if (!"NULL".equals(k1)) {
            throw new AssertionError("null signal must yield NULL sentinel; got " + k1);
        }
    }

    private static void triangleKeyIneligible_isStableAcrossTicks() {
        // Two successive ticks where the dashboard remains ineligible -
        // the previously-storming case. The previous gate cared about
        // triangleShapes.isEmpty(); the new gate cares only about the key.
        PaxTrendSignalModel s1 = ineligibleSignal();
        PaxTrendSignalModel s2 = ineligibleSignal();
        String k1 = PaxOpeningRangeModule.triangleRenderKey(true, s1, "", 0L, 0);
        String k2 = PaxOpeningRangeModule.triangleRenderKey(true, s2, "", 0L, 0);
        if (!k1.equals(k2)) {
            throw new AssertionError("identical ineligible state must memoize; k1=" + k1 + " k2=" + k2);
        }
        // And the key MUST NOT collide with the sentinels.
        if ("OFF".equals(k1) || "NULL".equals(k1) || PaxOpeningRangeModule.TRIANGLE_KEY_UNSET.equals(k1)) {
            throw new AssertionError("ineligible signal must produce a non-sentinel composite key; got " + k1);
        }
    }

    private static void triangleKeyEligible_changesWhenKindChanges() {
        PaxTrendSignalModel bull = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 1000L);
        PaxTrendSignalModel bear = renderable(PaxTrendSignalModel.Kind.STRONG_BEAR, 1000L);
        String kBull = PaxOpeningRangeModule.triangleRenderKey(true, bull, "", 0L, 0);
        String kBear = PaxOpeningRangeModule.triangleRenderKey(true, bear, "", 0L, 0);
        if (kBull.equals(kBear)) {
            throw new AssertionError("kind change must invalidate the key");
        }
    }

    private static void triangleKeyEligible_changesWhenBucketAdvances() {
        PaxTrendSignalModel a = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 1000L);
        PaxTrendSignalModel b = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 2000L);
        String kA = PaxOpeningRangeModule.triangleRenderKey(true, a, "", 0L, 0);
        String kB = PaxOpeningRangeModule.triangleRenderKey(true, b, "", 0L, 0);
        if (kA.equals(kB)) {
            throw new AssertionError("bucket advance must invalidate the key");
        }
    }

    private static void triangleKeyIneligibleToEligibleTransition_isDistinct() {
        PaxTrendSignalModel ineligible = ineligibleSignal();
        PaxTrendSignalModel eligible   = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 5000L);
        String kInel = PaxOpeningRangeModule.triangleRenderKey(true, ineligible, "", 0L, 0);
        String kEl   = PaxOpeningRangeModule.triangleRenderKey(true, eligible,   "", 0L, 0);
        if (kInel.equals(kEl)) {
            throw new AssertionError("ineligible->eligible transition must yield a distinct key so the redraw fires");
        }
    }

    private static void triangleKeyLiveCount_isIncludedInKey() {
        PaxTrendSignalModel s = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 1000L);
        String k1 = PaxOpeningRangeModule.triangleRenderKey(true, s, "STRONG_BULL", 1000L, 1);
        String k2 = PaxOpeningRangeModule.triangleRenderKey(true, s, "STRONG_BULL", 1000L, 2);
        if (k1.equals(k2)) {
            throw new AssertionError("liveTriangleCount must participate in the key so re-add fires when deque grows");
        }
    }

    private static void triangleKeyShowFalse_overridesSignal() {
        PaxTrendSignalModel eligible = renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 1000L);
        String off = PaxOpeningRangeModule.triangleRenderKey(false, eligible, "STRONG_BULL", 1000L, 3);
        if (!"OFF".equals(off)) {
            throw new AssertionError("show=false must short-circuit to OFF even when the signal is eligible");
        }
    }

    // ---- heatwaveModelKey -------------------------------------------------

    private static void heatwaveModelKeyNullModel_returnsNullSentinel() {
        String k = PaxOpeningRangeModule.heatwaveModelKey(null);
        if (!"NULL".equals(k)) {
            throw new AssertionError("null heatwave model must produce NULL sentinel; got " + k);
        }
    }

    private static void heatwaveModelKeySameModel_isStable() {
        PaxHeatwaveModel a = PaxHeatwaveModel.noData(1_000_000L);
        PaxHeatwaveModel b = PaxHeatwaveModel.noData(2_000_000L);   // same content, different fetchedAtMs
        String kA = PaxOpeningRangeModule.heatwaveModelKey(a);
        String kB = PaxOpeningRangeModule.heatwaveModelKey(b);
        if (!kA.equals(kB)) {
            throw new AssertionError("noData models with identical content must memoize (fetchedAtMs is "
                    + "intentionally outside the semantic key); kA=" + kA + " kB=" + kB);
        }
    }

    private static void heatwaveModelKeyDifferentVerdict_isDistinct() {
        PaxHeatwaveModel.Row[] rows = new PaxHeatwaveModel.Row[]{
                new PaxHeatwaveModel.Row("OR", "0.0", PaxHeatwaveModel.Tone.NEUTRAL, "")
        };
        PaxHeatwaveModel a = new PaxHeatwaveModel("WAIT",         PaxHeatwaveModel.Tone.NEUTRAL, "0.00", rows, 100L, true);
        PaxHeatwaveModel b = new PaxHeatwaveModel("FOLLOW_LONG",  PaxHeatwaveModel.Tone.BULL,    "0.55", rows, 100L, true);
        if (PaxOpeningRangeModule.heatwaveModelKey(a).equals(PaxOpeningRangeModule.heatwaveModelKey(b))) {
            throw new AssertionError("verdict change must invalidate the heatwave key");
        }
    }

    private static void heatwaveModelKeyDifferentRow_isDistinct() {
        PaxHeatwaveModel.Row[] r1 = new PaxHeatwaveModel.Row[]{
                new PaxHeatwaveModel.Row("OR", "0.10", PaxHeatwaveModel.Tone.BULL, "")
        };
        PaxHeatwaveModel.Row[] r2 = new PaxHeatwaveModel.Row[]{
                new PaxHeatwaveModel.Row("OR", "0.20", PaxHeatwaveModel.Tone.BULL, "")
        };
        PaxHeatwaveModel a = new PaxHeatwaveModel("WAIT", PaxHeatwaveModel.Tone.NEUTRAL, "0.00", r1, 100L, true);
        PaxHeatwaveModel b = new PaxHeatwaveModel("WAIT", PaxHeatwaveModel.Tone.NEUTRAL, "0.00", r2, 100L, true);
        if (PaxOpeningRangeModule.heatwaveModelKey(a).equals(PaxOpeningRangeModule.heatwaveModelKey(b))) {
            throw new AssertionError("row scoreText change must invalidate the heatwave key");
        }
    }

    private static void heatwaveModelKeyDifferentState_isDistinct() {
        long t = 1_000_000L;
        PaxHeatwaveModel noData       = PaxHeatwaveModel.noData(t);
        PaxHeatwaveModel bridgeOffline = PaxHeatwaveModel.bridgeOffline(t, "http://x", "refused");
        if (PaxOpeningRangeModule.heatwaveModelKey(noData)
                .equals(PaxOpeningRangeModule.heatwaveModelKey(bridgeOffline))) {
            throw new AssertionError("state change (NO_DATA vs BRIDGE_OFFLINE) must invalidate the heatwave key");
        }
    }

    // ---- helpers ----------------------------------------------------------

    private static PaxTrendSignalModel renderable(PaxTrendSignalModel.Kind kind, long bucketEnteredMs) {
        return new PaxTrendSignalModel(kind, "NQM6", 21000.0, 1L, 1L,
                bucketEnteredMs, /*changed=*/true, /*fetchedAtMs=*/1L,
                /*eligible=*/true, /*blockedReason=*/"", /*eventMsSource=*/"trend_analyzer");
    }

    private static PaxTrendSignalModel ineligibleSignal() {
        // dashboard_ineligible - kind NONE, eligible=false, with a blocked reason.
        return new PaxTrendSignalModel(PaxTrendSignalModel.Kind.NONE, "NQM6", Double.NaN, 0L, 0L,
                7777L, /*changed=*/false, /*fetchedAtMs=*/0L,
                /*eligible=*/false, /*blockedReason=*/"invalid_mid", /*eventMsSource=*/"");
    }

    private static PaxTrendSignalModel eligibleSignal() {
        return renderable(PaxTrendSignalModel.Kind.STRONG_BULL, 1000L);
    }
}
