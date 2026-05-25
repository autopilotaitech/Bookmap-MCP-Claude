package com.openrange;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Contract tests for the AI-side active-set chart-event history.
 *
 * Locked semantics:
 *  - replaceActiveSet wipes prior state every poll.
 *  - Empty / null input clears the history (so an expired dashboard set
 *    drops every marker on the next repaint).
 *  - Dedup by id within the new active set.
 *  - Cap at maxSize.
 *  - Non-renderable events silently skipped.
 *  - Local PaxInstitutionalChartEventsHistory keeps its append-only
 *    behavior (verified by a sanity check at the bottom).
 */
public class PaxAiChartEventsActiveHistoryTest {

    public static void main(String[] args) {
        firstPollAdds();
        secondEmptyPollClears();
        secondNullPollClears();
        nonRenderableSkipped();
        dedupWithinReplacement();
        maxSizeCap();
        clearWipes();
        snapshotIsDefensiveCopy();
        localHistoryStillAppendOnlyForRegression();
        System.out.println("PaxAiChartEventsActiveHistoryTest OK");
    }

    private static PaxInstitutionalChartEvent ev(String id, double price, long ts) {
        return new PaxInstitutionalChartEvent(id, "NQM6.CME@RITHMIC", "OR-H",
                price, "above", "AI_ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                "AI ▲ OR-H 72", "#FF40D9", "ENTRY", ts, "pax_ai", 0.72);
    }

    private static void firstPollAdds() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        int n = h.replaceActiveSet(Collections.singletonList(ev("a", 20000.0, 1L)));
        if (n != 1 || h.size() != 1) {
            throw new AssertionError("first poll must add; size=" + h.size());
        }
    }

    private static void secondEmptyPollClears() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        h.replaceActiveSet(Collections.singletonList(ev("a", 20000.0, 1L)));
        h.replaceActiveSet(Collections.<PaxInstitutionalChartEvent>emptyList());
        if (!h.isEmpty()) {
            throw new AssertionError("empty replace must clear; size=" + h.size());
        }
    }

    private static void secondNullPollClears() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        h.replaceActiveSet(Collections.singletonList(ev("a", 20000.0, 1L)));
        h.replaceActiveSet(null);
        if (!h.isEmpty()) {
            throw new AssertionError("null replace must clear; size=" + h.size());
        }
    }

    private static void nonRenderableSkipped() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        // empty id is non-renderable per PaxInstitutionalChartEvent.isRenderable
        List<PaxInstitutionalChartEvent> bad = Arrays.asList(
                ev("", 20000.0, 1L),
                ev("a", Double.NaN, 1L),
                ev("b", 20000.0, 0L),
                ev("good", 20000.0, 5L)
        );
        int n = h.replaceActiveSet(bad);
        if (n != 1 || h.size() != 1) {
            throw new AssertionError("only renderable should remain; size=" + h.size());
        }
        if (!h.snapshot().get(0).id.equals("good")) throw new AssertionError();
    }

    private static void dedupWithinReplacement() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        // Two rows with the same id in one replace -> first wins.
        h.replaceActiveSet(Arrays.asList(
                ev("dup", 20000.0, 1L),
                ev("dup", 20001.0, 2L)
        ));
        if (h.size() != 1) {
            throw new AssertionError("dedup by id in one replace; size=" + h.size());
        }
    }

    private static void maxSizeCap() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(3);
        List<PaxInstitutionalChartEvent> in = new ArrayList<>();
        for (int i = 0; i < 5; i++) {
            in.add(ev("id" + i, 20000.0 + i, 1_000L + i));
        }
        h.replaceActiveSet(in);
        if (h.size() != 3) {
            throw new AssertionError("cap to 3; got " + h.size());
        }
        // Keeps the FIRST 3 entries from the input (insertion-order
        // truncation). Documented because the dashboard sorts ascending
        // by timestamp_ms, so this drops the oldest within a cap-bound
        // set rather than the newest. Adjust if upstream ordering ever
        // changes.
        List<PaxInstitutionalChartEvent> snap = h.snapshot();
        if (!snap.get(0).id.equals("id0")
                || !snap.get(1).id.equals("id1")
                || !snap.get(2).id.equals("id2")) {
            throw new AssertionError("expected id0,id1,id2; got "
                    + snap.get(0).id + "," + snap.get(1).id + "," + snap.get(2).id);
        }
    }

    private static void clearWipes() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        h.replaceActiveSet(Collections.singletonList(ev("a", 20000.0, 1L)));
        h.clear();
        if (!h.isEmpty()) throw new AssertionError();
    }

    private static void snapshotIsDefensiveCopy() {
        PaxAiChartEventsActiveHistory h = new PaxAiChartEventsActiveHistory(50);
        h.replaceActiveSet(Collections.singletonList(ev("a", 20000.0, 1L)));
        List<PaxInstitutionalChartEvent> snap = h.snapshot();
        snap.clear();
        if (h.size() != 1) {
            throw new AssertionError("snapshot must not alias internal state");
        }
    }

    private static void localHistoryStillAppendOnlyForRegression() {
        // Regression guard: the local institutional history must remain
        // append-only. An empty merge MUST NOT clear prior markers
        // (observed evidence does not expire on a quiet poll).
        PaxInstitutionalChartEventsHistory h =
                new PaxInstitutionalChartEventsHistory(50);
        h.merge(Collections.singletonList(ev("a", 20000.0, 1L)));
        h.merge(Collections.<PaxInstitutionalChartEvent>emptyList());
        if (h.size() != 1) {
            throw new AssertionError(
                "local institutional history must stay append-only; size=" + h.size());
        }
    }
}
