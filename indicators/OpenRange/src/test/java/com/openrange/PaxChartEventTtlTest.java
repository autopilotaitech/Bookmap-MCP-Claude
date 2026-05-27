package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class PaxChartEventTtlTest {

    public static void main(String[] args) {
        nullEventsReturnsEmpty();
        emptyEventsReturnsEmpty();
        zeroTtlReturnsEmpty();
        negativeTtlReturnsEmpty();
        freshEventsAreKept();
        staleEventsAreDropped();
        boundaryEventAtCutoffIsKept();
        nullEntriesInListAreSkipped();
        mixedFreshAndStaleSurvivesFreshOnly();
        institutionalSignalGateAcceptsPayLong();
        institutionalSignalGateAcceptsPayShort();
        institutionalSignalGateRejectsWaitForConfirm();
        institutionalSignalGateRejectsStandDown();
        institutionalSignalGateRejectsScratchReady();
        institutionalSignalGateRejectsPayWithoutDirection();
        institutionalSignalGateRejectsMalformed();
        System.out.println("PaxChartEventTtlTest OK");
        System.exit(0);
    }

    private static PaxInstitutionalChartEvent eventAt(long ts) {
        return new PaxInstitutionalChartEvent(
                "id-" + ts, "NQM6", "OR-H", 30000.0, "above",
                "ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                "ACC-L", "#3CDC7D", "ENTRY", ts, "institutional", 0.7);
    }

    private static void nullEventsReturnsEmpty() {
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(
                null, 10_000L, 4_000L);
        if (!got.isEmpty()) throw new AssertionError("null in -> non-empty out");
    }

    private static void emptyEventsReturnsEmpty() {
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(
                Collections.emptyList(), 10_000L, 4_000L);
        if (!got.isEmpty()) throw new AssertionError("empty in -> non-empty out");
    }

    private static void zeroTtlReturnsEmpty() {
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(
                Arrays.asList(eventAt(10_000L)), 10_000L, 0L);
        if (!got.isEmpty()) throw new AssertionError("zero ttl should suppress all");
    }

    private static void negativeTtlReturnsEmpty() {
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(
                Arrays.asList(eventAt(10_000L)), 10_000L, -1L);
        if (!got.isEmpty()) throw new AssertionError("negative ttl should suppress all");
    }

    private static void freshEventsAreKept() {
        long now = 10_000L;
        long ttl = 4_000L;
        List<PaxInstitutionalChartEvent> in = Arrays.asList(
                eventAt(9_500L), eventAt(8_000L), eventAt(7_000L));
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(in, now, ttl);
        if (got.size() != 3) throw new AssertionError("expected 3 fresh, got " + got.size());
    }

    private static void staleEventsAreDropped() {
        long now = 100_000L;
        long ttl = 4_000L;
        List<PaxInstitutionalChartEvent> in = Arrays.asList(
                eventAt(10_000L), eventAt(50_000L));
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(in, now, ttl);
        if (!got.isEmpty()) throw new AssertionError("expected stale events dropped");
    }

    private static void boundaryEventAtCutoffIsKept() {
        long now = 10_000L;
        long ttl = 4_000L;
        // exact cutoff (now - ttl == ts) should be kept (>= cutoff).
        List<PaxInstitutionalChartEvent> in = Arrays.asList(eventAt(6_000L));
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(in, now, ttl);
        if (got.size() != 1) throw new AssertionError("boundary event must be kept");
    }

    private static void nullEntriesInListAreSkipped() {
        long now = 10_000L;
        long ttl = 4_000L;
        List<PaxInstitutionalChartEvent> in = Arrays.asList(
                null, eventAt(9_000L), null, eventAt(8_000L));
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(in, now, ttl);
        if (got.size() != 2) throw new AssertionError("null entries should be skipped, got " + got.size());
    }

    private static void mixedFreshAndStaleSurvivesFreshOnly() {
        long now = 100_000L;
        long ttl = 4_000L;
        List<PaxInstitutionalChartEvent> in = Arrays.asList(
                eventAt(50_000L), eventAt(99_000L), eventAt(96_500L), eventAt(80_000L));
        List<PaxInstitutionalChartEvent> got = PaxOpeningRangeModule.filterEventsByTtl(in, now, ttl);
        if (got.size() != 2) throw new AssertionError("expected 2 fresh, got " + got.size());
    }

    private static PaxInstitutionalSignalEvent signal(String executionRead, String direction) {
        return new PaxInstitutionalSignalEvent(
                "sig-" + executionRead + "-" + direction,
                "ACCEPTANCE", direction, executionRead,
                30000.0, 1_000_000L, "OR-H", 0.7);
    }

    private static void institutionalSignalGateAcceptsPayLong() {
        if (!PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("PAY_FOR_TRADE", "LONG")))
            throw new AssertionError("PAY/LONG must render");
    }

    private static void institutionalSignalGateAcceptsPayShort() {
        if (!PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("PAY_FOR_TRADE", "SHORT")))
            throw new AssertionError("PAY/SHORT must render");
    }

    private static void institutionalSignalGateRejectsWaitForConfirm() {
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("WAIT_FOR_CONFIRM", "LONG")))
            throw new AssertionError("WAIT_FOR_CONFIRM must NOT render in price lane");
    }

    private static void institutionalSignalGateRejectsStandDown() {
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("STAND_DOWN", "LONG")))
            throw new AssertionError("STAND_DOWN must NOT render in price lane");
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("STAND_DOWN", "NONE")))
            throw new AssertionError("STAND_DOWN/NONE must NOT render in price lane");
    }

    private static void institutionalSignalGateRejectsScratchReady() {
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("SCRATCH_READY", "LONG")))
            throw new AssertionError("SCRATCH_READY must NOT render in price lane");
    }

    private static void institutionalSignalGateRejectsPayWithoutDirection() {
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(
                signal("PAY_FOR_TRADE", "NONE")))
            throw new AssertionError("PAY/NONE must NOT render (no direction)");
    }

    private static void institutionalSignalGateRejectsMalformed() {
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(null))
            throw new AssertionError("null event must NOT render");
        // empty id -> isRenderable() false
        PaxInstitutionalSignalEvent bad = new PaxInstitutionalSignalEvent(
                "", "ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                30000.0, 1_000_000L, "OR-H", 0.7);
        if (PaxOpeningRangeModule.shouldRenderInstitutionalSignalInPriceLane(bad))
            throw new AssertionError("empty-id event must NOT render");
    }
}
