package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class PaxInstitutionalChartEventsHistoryTest {

    public static void main(String[] args) {
        emptyMergeKeepsPriorHistory();
        nullMergeKeepsPriorHistory();
        sameIdDoesNotReplot();
        newIdAdds();
        nonRenderableSkipped();
        bothEntryAndWarningCoexistInHistory();
        chartEventCollisionBucketIgnoresLabelText();
        chartEventCollisionBucketSeparatesDistantPrices();
        maxRolloverDropsOldest();
        clearWipes();
        System.out.println("PaxInstitutionalChartEventsHistoryTest OK");
    }

    private static void emptyMergeKeepsPriorHistory() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        h.merge(Collections.singletonList(watch("idW", 20000.0, 1_000L)));
        if (h.merge(Collections.<PaxInstitutionalChartEvent>emptyList()) != 0) {
            throw new AssertionError("empty merge must add 0");
        }
        if (h.size() != 1) {
            throw new AssertionError("empty merge must keep prior history");
        }
    }

    private static void nullMergeKeepsPriorHistory() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        h.merge(Collections.singletonList(watch("idW", 20000.0, 1_000L)));
        h.merge(null);
        if (h.size() != 1) throw new AssertionError("null merge must keep prior");
    }

    private static void sameIdDoesNotReplot() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        PaxInstitutionalChartEvent ev = watch("idW", 20000.0, 1_000L);
        h.merge(Collections.singletonList(ev));
        h.merge(Collections.singletonList(ev));
        h.merge(Collections.singletonList(ev));
        if (h.size() != 1) throw new AssertionError("dedup by id failed; size=" + h.size());
    }

    private static void newIdAdds() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        h.merge(Collections.singletonList(watch("idA", 20000.0, 1_000L)));
        h.merge(Collections.singletonList(watch("idB", 20000.0, 2_000L)));
        if (h.size() != 2) throw new AssertionError("new id must add");
    }

    private static void nonRenderableSkipped() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        List<PaxInstitutionalChartEvent> bad = Arrays.asList(
                ev("", "WATCH_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                        20000.0, 1_000L, "above"),
                ev("idA", "WATCH_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                        Double.NaN, 1_000L, "above"),
                ev("idB", "WATCH_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                        20000.0, 0L, "above"));
        if (h.merge(bad) != 0) throw new AssertionError("non-renderable must be skipped");
    }

    private static void bothEntryAndWarningCoexistInHistory() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        PaxInstitutionalChartEvent acc = ev("idAcc", "ACCEPTANCE", "ENTRY", "LONG",
                "PAY_FOR_TRADE", 20000.0, 5_000L, "above");
        PaxInstitutionalChartEvent ice = ev("idIce", "ICEBERG_DEFENSE", "WARNING", "NONE",
                "STAND_DOWN", 20000.0, 5_000L, "above");
        h.merge(Arrays.asList(acc, ice));
        if (h.size() != 2) {
            throw new AssertionError("ENTRY + WARNING at same level/ts must both stay");
        }
        // ENTRY rank=0, WARNING rank=2 — both renderable, no inter-event suppression.
        if (h.snapshot().get(0).severityRank() != 0) {
            throw new AssertionError("ENTRY rank must be 0");
        }
        if (h.snapshot().get(1).severityRank() != 2) {
            throw new AssertionError("WARNING rank must be 2");
        }
    }

    private static void chartEventCollisionBucketIgnoresLabelText() {
        PaxInstitutionalChartEvent watch = ev("idWatch", "WATCH_LEVEL", "WATCH", "NONE",
                "WAIT_FOR_CONFIRM", 20000.0, 5_000L, "above", "OR-H", "WATCH");
        PaxInstitutionalChartEvent sweep = ev("idSweep", "LIQUIDITY_SWEEP", "WARNING", "NONE",
                "WAIT_FOR_CONFIRM", 20000.25, 5_250L, "above", "+2", "2(-1)");
        String a = PaxOpeningRangeModule.chartEventCollisionKey(watch, 0.25);
        String b = PaxOpeningRangeModule.chartEventCollisionKey(sweep, 0.25);
        if (!a.equals(b)) {
            throw new AssertionError("nearby same-second markers must share collision lane: "
                    + a + " vs " + b);
        }
    }

    private static void chartEventCollisionBucketSeparatesDistantPrices() {
        PaxInstitutionalChartEvent near = ev("idNear", "WATCH_LEVEL", "WATCH", "NONE",
                "WAIT_FOR_CONFIRM", 20000.0, 5_000L, "above", "OR-H", "WATCH");
        PaxInstitutionalChartEvent far = ev("idFar", "WATCH_LEVEL", "WATCH", "NONE",
                "WAIT_FOR_CONFIRM", 20004.0, 5_000L, "above", "OR-H", "WATCH");
        String a = PaxOpeningRangeModule.chartEventCollisionKey(near, 0.25);
        String b = PaxOpeningRangeModule.chartEventCollisionKey(far, 0.25);
        if (a.equals(b)) {
            throw new AssertionError("distant price markers should not consume the same lane");
        }
    }

    private static void maxRolloverDropsOldest() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(3);
        for (int i = 0; i < 5; i++) {
            h.merge(Collections.singletonList(watch("id" + i, 20000.0 + i, 1_000L + i)));
        }
        if (h.size() != 3) throw new AssertionError("cap must hold; size=" + h.size());
        List<PaxInstitutionalChartEvent> snap = h.snapshot();
        if (!snap.get(0).id.equals("id2")
                || !snap.get(1).id.equals("id3")
                || !snap.get(2).id.equals("id4")) {
            throw new AssertionError("rollover wrong order");
        }
    }

    private static void clearWipes() {
        PaxInstitutionalChartEventsHistory h = new PaxInstitutionalChartEventsHistory(50);
        h.merge(Collections.singletonList(watch("idA", 20000.0, 1_000L)));
        h.clear();
        if (!h.isEmpty()) throw new AssertionError("clear must empty");
    }

    private static PaxInstitutionalChartEvent watch(String id, double price, long ts) {
        return ev(id, "WATCH_LEVEL", "WATCH", "NONE", "WAIT_FOR_CONFIRM",
                price, ts, "above");
    }

    private static PaxInstitutionalChartEvent ev(String id, String eventType,
                                                   String severity, String direction,
                                                   String executionRead, double price,
                                                   long ts, String side) {
        return ev(id, eventType, severity, direction, executionRead, price, ts,
                side, "OR-H", "WATCH");
    }

    private static PaxInstitutionalChartEvent ev(String id, String eventType,
                                                   String severity, String direction,
                                                   String executionRead, double price,
                                                   long ts, String side,
                                                   String label, String markerText) {
        return new PaxInstitutionalChartEvent(id, "NQM6.CME@RITHMIC", label,
                price, side, eventType, direction, executionRead,
                markerText, "#E5C100", severity, ts, "institutional_thesis", 0.35);
    }
}
