package com.openrange;

public class PaxRepaintGuardTest {

    public static void main(String[] args) {
        firstTickAlwaysAllowed();
        triangleCooldownBlocksRapidRepaints();
        overlayCooldownBlocksRapidRepaints();
        emergencyDegradationEngagesOnOverrun();
        emergencyDegradationLiftsAfterCooldown();
        emergencyDegradationSkipsCountersIncrement();
        resetMinIntervalsAllowsImmediateRepaint();
        persistentBucketIsNeverGated();
        System.out.println("PaxRepaintGuardTest OK");
    }

    private static void firstTickAlwaysAllowed() {
        PaxRepaintGuard g = new PaxRepaintGuard();
        if (!g.allowTriangle(0L, 0L)) throw new AssertionError("first triangle tick must be allowed");
        if (!g.allowOverlay(0L, 0L)) throw new AssertionError("first overlay tick must be allowed");
    }

    private static void triangleCooldownBlocksRapidRepaints() {
        PaxRepaintGuard g = new PaxRepaintGuard(250L, 200L,
                PaxRepaintGuard.DEFAULT_OVERRUN_BUDGET_NANOS,
                PaxRepaintGuard.DEFAULT_DEGRADATION_COOLDOWN_NANOS);
        long t0 = 100_000L;
        if (!g.allowTriangle(t0, t0 * 1_000_000L)) throw new AssertionError("baseline allow");
        if (g.allowTriangle(t0 + 50L, (t0 + 50L) * 1_000_000L))
            throw new AssertionError("must block triangle inside cooldown");
        if (g.allowTriangle(t0 + 249L, (t0 + 249L) * 1_000_000L))
            throw new AssertionError("must block triangle at < min interval");
        if (!g.allowTriangle(t0 + 250L, (t0 + 250L) * 1_000_000L))
            throw new AssertionError("must allow at cooldown boundary");
        if (g.triangleSkippedCount() != 2)
            throw new AssertionError("expected 2 skips; got " + g.triangleSkippedCount());
    }

    private static void overlayCooldownBlocksRapidRepaints() {
        PaxRepaintGuard g = new PaxRepaintGuard(250L, 200L,
                PaxRepaintGuard.DEFAULT_OVERRUN_BUDGET_NANOS,
                PaxRepaintGuard.DEFAULT_DEGRADATION_COOLDOWN_NANOS);
        long t0 = 100_000L;
        if (!g.allowOverlay(t0, t0 * 1_000_000L)) throw new AssertionError("baseline allow");
        if (g.allowOverlay(t0 + 199L, (t0 + 199L) * 1_000_000L))
            throw new AssertionError("must block overlay at < min interval");
        if (!g.allowOverlay(t0 + 200L, (t0 + 200L) * 1_000_000L))
            throw new AssertionError("must allow at boundary");
    }

    private static void emergencyDegradationEngagesOnOverrun() {
        long budgetNanos = 1_000_000L;     // 1ms
        long cooldownNanos = 100_000_000L; // 100ms
        PaxRepaintGuard g = new PaxRepaintGuard(0L, 0L, budgetNanos, cooldownNanos);
        long now = 1_000_000_000L;
        if (g.isDegraded(now)) throw new AssertionError("must start non-degraded");
        g.recordApplyDurationNanos(budgetNanos * 4L, now);
        if (!g.isDegraded(now + 1L))
            throw new AssertionError("must engage degradation after budget overrun");
        if (g.degradationEnteredCount() != 1)
            throw new AssertionError("degradation entered counter must tick");
    }

    private static void emergencyDegradationLiftsAfterCooldown() {
        long budgetNanos = 1_000_000L;
        long cooldownNanos = 50_000_000L;  // 50ms
        PaxRepaintGuard g = new PaxRepaintGuard(0L, 0L, budgetNanos, cooldownNanos);
        long now = 5_000_000_000L;
        g.recordApplyDurationNanos(budgetNanos * 10L, now);
        if (!g.isDegraded(now + 1L)) throw new AssertionError("must be degraded");
        if (g.isDegraded(now + cooldownNanos + 1L))
            throw new AssertionError("must lift degradation after cooldown");
    }

    private static void emergencyDegradationSkipsCountersIncrement() {
        long budgetNanos = 1_000_000L;
        long cooldownNanos = 10_000_000_000L;
        PaxRepaintGuard g = new PaxRepaintGuard(0L, 0L, budgetNanos, cooldownNanos);
        long now = 2_000_000_000L;
        g.recordApplyDurationNanos(budgetNanos * 10L, now);
        // Now degraded - even though min-interval is 0, both buckets must
        // skip.
        if (g.allowTriangle(0L, now + 1L))
            throw new AssertionError("triangle must skip while degraded");
        if (g.allowOverlay(0L, now + 1L))
            throw new AssertionError("overlay must skip while degraded");
        if (g.triangleSkippedCount() == 0 || g.overlaySkippedCount() == 0)
            throw new AssertionError("skip counters must increment under degradation");
    }

    private static void resetMinIntervalsAllowsImmediateRepaint() {
        PaxRepaintGuard g = new PaxRepaintGuard();
        if (!g.allowTriangle(1000L, 1_000_000_000L)) throw new AssertionError("baseline");
        if (g.allowTriangle(1001L, 1_001_000_000L))
            throw new AssertionError("expected block right after first allow");
        g.resetMinIntervals();
        if (!g.allowTriangle(1002L, 1_002_000_000L))
            throw new AssertionError("reset must allow immediate repaint (onMoveEnd path)");
    }

    private static void persistentBucketIsNeverGated() {
        // Persistent bucket bypasses the guard at the call site
        // (PaxOpeningRangeModule#applyNeeds). This test pins the design
        // by verifying the guard does NOT expose a persistent-bucket API.
        // An accidental gate on persistent would be visible as a new
        // method appearing on the public surface. Reflection over the
        // declared methods catches any future "allowPersistent" addition.
        for (java.lang.reflect.Method m : PaxRepaintGuard.class.getDeclaredMethods()) {
            String name = m.getName().toLowerCase();
            if (name.contains("persistent")) {
                throw new AssertionError(
                        "PaxRepaintGuard must not gate the persistent bucket; found method " + m.getName());
            }
        }
    }
}
