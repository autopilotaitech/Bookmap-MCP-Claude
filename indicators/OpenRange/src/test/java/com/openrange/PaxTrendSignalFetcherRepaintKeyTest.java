package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/**
 * Unit tests for the fetcher's combined repaint-dedup key.
 *
 * Locked semantics:
 *  - A new AI event id appearing -> different key (repaint fires).
 *  - An AI event id disappearing (TTL drop) -> different key (repaint
 *    fires so the painter can call replaceActiveSet and clear the marker).
 *  - A new local chart event id appearing -> different key.
 *  - Same trend model + same event sets -> same key (no spurious
 *    repaint).
 *  - Order of events in the input list does NOT matter (sorted-id
 *    signature).
 */
public class PaxTrendSignalFetcherRepaintKeyTest {

    public static void main(String[] args) {
        identicalInputsProduceIdenticalKey();
        addingAiEventChangesKey();
        removingAiEventChangesKey();
        addingLocalEventChangesKey();
        eventOrderDoesNotAffectKey();
        emptyAndNullAreSameKey();
        eventsKeyEmpty();
        eventsKeySorted();
        System.out.println("PaxTrendSignalFetcherRepaintKeyTest OK");
    }

    private static PaxInstitutionalChartEvent ev(String id, String source) {
        return new PaxInstitutionalChartEvent(id, "NQM6", "OR-H", 20000.0,
                "above", "AI_ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                "t", "#FFFFFF", "ENTRY", 1L, source, 0.5);
    }

    private static PaxTrendSignalModel model() {
        // Reuse the "none" carrier; the chart-event part of the key is
        // what we're exercising.
        return PaxTrendSignalModel.none(0L);
    }

    private static void identicalInputsProduceIdenticalKey() {
        List<PaxInstitutionalChartEvent> ai = Arrays.asList(ev("a1", "pax_ai"));
        List<PaxInstitutionalChartEvent> loc = Arrays.asList(ev("l1", "institutional_thesis"));
        String k1 = PaxTrendSignalFetcher.combinedSemanticKey(model(), loc, ai);
        String k2 = PaxTrendSignalFetcher.combinedSemanticKey(model(), loc, ai);
        if (!k1.equals(k2)) throw new AssertionError(k1 + " != " + k2);
    }

    private static void addingAiEventChangesKey() {
        List<PaxInstitutionalChartEvent> empty = Collections.emptyList();
        List<PaxInstitutionalChartEvent> one = Arrays.asList(ev("a1", "pax_ai"));
        String k0 = PaxTrendSignalFetcher.combinedSemanticKey(model(), empty, empty);
        String k1 = PaxTrendSignalFetcher.combinedSemanticKey(model(), empty, one);
        if (k0.equals(k1)) {
            throw new AssertionError("a new AI id MUST change the repaint key");
        }
    }

    private static void removingAiEventChangesKey() {
        List<PaxInstitutionalChartEvent> empty = Collections.emptyList();
        List<PaxInstitutionalChartEvent> one = Arrays.asList(ev("a1", "pax_ai"));
        String kBefore = PaxTrendSignalFetcher.combinedSemanticKey(model(), empty, one);
        String kAfter  = PaxTrendSignalFetcher.combinedSemanticKey(model(), empty, empty);
        if (kBefore.equals(kAfter)) {
            throw new AssertionError(
                "an AI id disappearing (TTL drop) MUST change the repaint key");
        }
    }

    private static void addingLocalEventChangesKey() {
        List<PaxInstitutionalChartEvent> empty = Collections.emptyList();
        List<PaxInstitutionalChartEvent> loc1 = Arrays.asList(ev("l1", "institutional_thesis"));
        String k0 = PaxTrendSignalFetcher.combinedSemanticKey(model(), empty, empty);
        String k1 = PaxTrendSignalFetcher.combinedSemanticKey(model(), loc1, empty);
        if (k0.equals(k1)) {
            throw new AssertionError("a new local chart-event id MUST change the key");
        }
    }

    private static void eventOrderDoesNotAffectKey() {
        // Sorted-id signature: same set in different order = same key.
        List<PaxInstitutionalChartEvent> ai_a = Arrays.asList(
                ev("a1", "pax_ai"), ev("a2", "pax_ai"));
        List<PaxInstitutionalChartEvent> ai_b = Arrays.asList(
                ev("a2", "pax_ai"), ev("a1", "pax_ai"));
        String k1 = PaxTrendSignalFetcher.combinedSemanticKey(model(),
                Collections.<PaxInstitutionalChartEvent>emptyList(), ai_a);
        String k2 = PaxTrendSignalFetcher.combinedSemanticKey(model(),
                Collections.<PaxInstitutionalChartEvent>emptyList(), ai_b);
        if (!k1.equals(k2)) {
            throw new AssertionError("order should not matter; got "
                    + k1 + " vs " + k2);
        }
    }

    private static void emptyAndNullAreSameKey() {
        String kEmpty = PaxTrendSignalFetcher.combinedSemanticKey(model(),
                Collections.<PaxInstitutionalChartEvent>emptyList(),
                Collections.<PaxInstitutionalChartEvent>emptyList());
        String kNull = PaxTrendSignalFetcher.combinedSemanticKey(model(), null, null);
        if (!kEmpty.equals(kNull)) {
            throw new AssertionError("empty and null lists must hash the same");
        }
    }

    private static void eventsKeyEmpty() {
        String e1 = PaxTrendSignalFetcher.eventsKey(null);
        String e2 = PaxTrendSignalFetcher.eventsKey(Collections.<PaxInstitutionalChartEvent>emptyList());
        if (!"0".equals(e1) || !"0".equals(e2)) throw new AssertionError();
    }

    private static void eventsKeySorted() {
        String k = PaxTrendSignalFetcher.eventsKey(
                Arrays.asList(ev("z", "pax_ai"), ev("a", "pax_ai")));
        // Sorted output begins with the lower id.
        if (!k.startsWith("2;a,")) {
            throw new AssertionError("expected sorted 2;a,...; got " + k);
        }
    }
}
