package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

public class PaxInstitutionalSignalsHistoryTest {

    public static void main(String[] args) {
        emptyMergeIsNoOp();
        nullMergeIsNoOp();
        emptyInstitutionalSignalsOnLaterPollKeepsPriorHistory();
        priorMarkersSurviveAcrossNonePolls();
        sameIdAddedOnceNotEveryPoll();
        newIdAddsNewMarker();
        nonRenderableEventsAreSkipped();
        maxRolloverDropsOldest();
        clearWipesEverything();
        System.out.println("PaxInstitutionalSignalsHistoryTest OK");
    }

    // ─── Core persistence contract (the bug fix) ──────────────────────────

    private static void emptyInstitutionalSignalsOnLaterPollKeepsPriorHistory() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        PaxInstitutionalSignalEvent pay = ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L);
        h.merge(Collections.singletonList(pay));
        if (h.size() != 1) throw new AssertionError("PAY must be added; size=" + h.size());

        // Empty institutional_signals on a later poll -> NO clear, NO add.
        int added = h.merge(Collections.<PaxInstitutionalSignalEvent>emptyList());
        if (added != 0) throw new AssertionError("empty merge must add 0; got " + added);
        if (h.size() != 1) {
            throw new AssertionError("empty merge must keep prior history; size=" + h.size());
        }
        // PAY event is still in snapshot.
        List<PaxInstitutionalSignalEvent> snap = h.snapshot();
        if (!"idA".equals(snap.get(0).id)) throw new AssertionError("PAY event missing");
    }

    private static void priorMarkersSurviveAcrossNonePolls() {
        // Simulate the disappearing-markers bug scenario:
        // Poll 1: PAY arrives.
        // Polls 2-5: institutional_signals is empty (e.g., dashboard temporary
        //   blip, aggressor goes MIXED, anchor reset).
        // Marker history must still contain the prior PAY event throughout.
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        h.merge(Collections.singletonList(ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L)));
        for (int poll = 0; poll < 4; poll++) {
            h.merge(Collections.<PaxInstitutionalSignalEvent>emptyList());
            if (h.size() != 1) {
                throw new AssertionError("history lost marker on empty poll " + poll);
            }
        }
        List<PaxInstitutionalSignalEvent> snap = h.snapshot();
        if (snap.size() != 1 || !"idA".equals(snap.get(0).id)) {
            throw new AssertionError("PAY event lost after multiple empty polls");
        }
    }

    // ─── Dedup by id ──────────────────────────────────────────────────────

    private static void sameIdAddedOnceNotEveryPoll() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        PaxInstitutionalSignalEvent pay = ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L);
        h.merge(Collections.singletonList(pay));
        // Same id appears on subsequent polls (Python emits stable id while
        // state hasn't changed).
        for (int i = 0; i < 10; i++) {
            int added = h.merge(Collections.singletonList(pay));
            if (added != 0) {
                throw new AssertionError("duplicate id must not re-add; got added=" + added);
            }
        }
        if (h.size() != 1) {
            throw new AssertionError("dedup failed; size=" + h.size());
        }
    }

    private static void newIdAddsNewMarker() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        h.merge(Collections.singletonList(ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L)));
        int added = h.merge(Collections.singletonList(ev("idB", "REJECTION_SHORT",
                "SHORT", "PAY_FOR_TRADE", 20000.0, 2_000L)));
        if (added != 1) {
            throw new AssertionError("new id must add; got added=" + added);
        }
        if (h.size() != 2) {
            throw new AssertionError("history must have 2 markers; size=" + h.size());
        }
    }

    // ─── Edge cases ───────────────────────────────────────────────────────

    private static void emptyMergeIsNoOp() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        if (h.merge(Collections.<PaxInstitutionalSignalEvent>emptyList()) != 0) {
            throw new AssertionError("empty merge must add 0");
        }
    }

    private static void nullMergeIsNoOp() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        if (h.merge(null) != 0) {
            throw new AssertionError("null merge must add 0");
        }
    }

    private static void nonRenderableEventsAreSkipped() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        List<PaxInstitutionalSignalEvent> bad = Arrays.asList(
                new PaxInstitutionalSignalEvent("", "ACCEPTANCE_LONG", "LONG",
                        "PAY_FOR_TRADE", 20000.0, 1_000L, "OR-H", 0.8),  // empty id
                new PaxInstitutionalSignalEvent("idA", "ACCEPTANCE_LONG", "LONG",
                        "PAY_FOR_TRADE", Double.NaN, 1_000L, "OR-H", 0.8),  // NaN price
                new PaxInstitutionalSignalEvent("idB", "ACCEPTANCE_LONG", "LONG",
                        "PAY_FOR_TRADE", 20000.0, 0L, "OR-H", 0.8),  // zero ts
                new PaxInstitutionalSignalEvent("idC", "ACCEPTANCE_LONG", "LONG",
                        "PAY_FOR_TRADE", -1.0, 1_000L, "OR-H", 0.8)  // negative price
        );
        if (h.merge(bad) != 0) {
            throw new AssertionError("non-renderable events must be skipped");
        }
    }

    // ─── Bounded history ──────────────────────────────────────────────────

    private static void maxRolloverDropsOldest() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(3);
        for (int i = 0; i < 5; i++) {
            h.merge(Collections.singletonList(
                    ev("id" + i, "ACCEPTANCE_LONG", "LONG",
                       "PAY_FOR_TRADE", 20000.0 + i, 1_000L + i)));
        }
        if (h.size() != 3) {
            throw new AssertionError("history must cap at maxSize=3; size=" + h.size());
        }
        List<PaxInstitutionalSignalEvent> snap = h.snapshot();
        // Oldest (id0, id1) rolled off; id2, id3, id4 remain in insertion order.
        if (!snap.get(0).id.equals("id2")
                || !snap.get(1).id.equals("id3")
                || !snap.get(2).id.equals("id4")) {
            throw new AssertionError("rollover did not drop oldest correctly: " + ids(snap));
        }
    }

    private static void clearWipesEverything() {
        PaxInstitutionalSignalsHistory h = new PaxInstitutionalSignalsHistory(30);
        h.merge(Collections.singletonList(ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L)));
        h.clear();
        if (!h.isEmpty()) {
            throw new AssertionError("clear() must empty history");
        }
        // After clear, the previously-seen id can be re-added (no LRU memory).
        if (h.merge(Collections.singletonList(ev("idA", "ACCEPTANCE_LONG", "LONG",
                "PAY_FOR_TRADE", 20000.0, 1_000L))) != 1) {
            throw new AssertionError("after clear, prior id must be re-addable");
        }
    }

    // ─── Builders ─────────────────────────────────────────────────────────

    private static PaxInstitutionalSignalEvent ev(String id, String signalType,
                                                    String direction, String executionRead,
                                                    double price, long timestampMs) {
        return new PaxInstitutionalSignalEvent(id, signalType, direction, executionRead,
                price, timestampMs, "OR-H", 0.80);
    }

    private static String ids(List<PaxInstitutionalSignalEvent> list) {
        StringBuilder sb = new StringBuilder("[");
        for (PaxInstitutionalSignalEvent e : list) {
            sb.append(e.id).append(",");
        }
        return sb.append("]").toString();
    }
}
