package com.openrange;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class PaxLevelEdgeMinVisibleTest {

    public static void main(String[] args) {
        nullCurrentAndEmptyHoldoversReturnsEmpty();
        currentActionableRowsAreReturnedFirst();
        currentActionableUpdatesPriorHoldoverSlot();
        heldOverRowIsRenderedWhenWithinWindow();
        heldOverRowIsDroppedWhenWindowExpired();
        currentActionableTakesPrecedenceOverStaleHoldover();
        nonActionableRowInCurrentIsIgnored();
        nullRowsAreSkipped();
        nullLabelsAreSkipped();
        ordersMatchInsertionForDeterminism();
        holdoverIsVisibleHelperRespectsWindow();
        zeroOrNegativeMinVisibleSuppressesHoldovers();
        System.out.println("PaxLevelEdgeMinVisibleTest OK");
        System.exit(0);
    }

    private static PaxLevelEdgeModel.Row actionable(String label) {
        return new PaxLevelEdgeModel.Row(
                label, 30000.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.6, 1.2, 30001.0, "HALF", true,
                "OR_BREAK_FOLLOW", Arrays.asList("pull_stack", "vwap"),
                null, 1.0, true);
    }

    private static PaxLevelEdgeModel.Row nonActionable(String label) {
        return new PaxLevelEdgeModel.Row(
                label, 30000.0,
                PaxLevelEdgeModel.Direction.WAIT,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.NEUTRAL,
                0.6, 1.2, 30001.0, "HALF", false,
                "OR_BREAK_FOLLOW", Arrays.asList("pull_stack"),
                "not_near_level", 10.0, false);
    }

    private static void nullCurrentAndEmptyHoldoversReturnsEmpty() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                null, h, 10_000L, 15_000L);
        if (!got.isEmpty()) throw new AssertionError("null current + empty hold -> empty");
    }

    private static void currentActionableRowsAreReturnedFirst() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        List<PaxLevelEdgeModel.Row> current = Arrays.asList(actionable("OR-H"), actionable("OR-L"));
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                current, h, 10_000L, 15_000L);
        if (got.size() != 2) throw new AssertionError("expected 2, got " + got.size());
        if (!"OR-H".equals(got.get(0).label) || !"OR-L".equals(got.get(1).label))
            throw new AssertionError("order must be current-first by input order");
    }

    private static void currentActionableUpdatesPriorHoldoverSlot() {
        // Simulate the module flow: when a row is currently actionable, the
        // module updates the hold-over map first; selectLevelEdgeRowsToRender
        // does NOT redraw the same label twice.
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("OR-H", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 9_500L));
        List<PaxLevelEdgeModel.Row> current = Arrays.asList(actionable("OR-H"));
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                current, h, 10_000L, 15_000L);
        if (got.size() != 1) throw new AssertionError("must not double-render same label");
    }

    private static void heldOverRowIsRenderedWhenWithinWindow() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("OR-H", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 1_000L));
        List<PaxLevelEdgeModel.Row> current = Collections.emptyList();
        // nowMs - lastActionableAtMs = 5_000 ms < 15s window
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                current, h, 6_000L, 15_000L);
        if (got.size() != 1) throw new AssertionError("expected hold-over to render, got " + got.size());
    }

    private static void heldOverRowIsDroppedWhenWindowExpired() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("OR-H", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 1_000L));
        List<PaxLevelEdgeModel.Row> current = Collections.emptyList();
        // nowMs - lastActionableAtMs = 20_001 ms > 15s window
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                current, h, 21_001L, 15_000L);
        if (!got.isEmpty()) throw new AssertionError("expired hold-over must drop");
    }

    private static void currentActionableTakesPrecedenceOverStaleHoldover() {
        // Even when a hold-over for OR-H is expired, the current actionable
        // row for OR-H still renders.
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("OR-H", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 1_000L));
        PaxLevelEdgeModel.Row fresh = actionable("OR-H");
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                Arrays.asList(fresh), h, 50_000L, 15_000L);
        if (got.size() != 1) throw new AssertionError("expected 1, got " + got.size());
        if (got.get(0) != fresh) throw new AssertionError("must be the current actionable row");
    }

    private static void nonActionableRowInCurrentIsIgnored() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                Arrays.asList(nonActionable("OR-H")), h, 10_000L, 15_000L);
        if (!got.isEmpty()) throw new AssertionError("non-actionable must be ignored");
    }

    private static void nullRowsAreSkipped() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        List<PaxLevelEdgeModel.Row> current = new ArrayList<>();
        current.add(null);
        current.add(actionable("OR-H"));
        current.add(null);
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                current, h, 10_000L, 15_000L);
        if (got.size() != 1) throw new AssertionError("null rows must be skipped");
    }

    private static void nullLabelsAreSkipped() {
        PaxLevelEdgeModel.Row nullLabel = new PaxLevelEdgeModel.Row(
                null, 30000.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.6, 1.2, 30001.0, "HALF", true,
                "OR_BREAK_FOLLOW", Arrays.asList("pull_stack"),
                null, 1.0, true);
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                Arrays.asList(nullLabel), h, 10_000L, 15_000L);
        if (!got.isEmpty()) throw new AssertionError("null-label rows must be skipped");
    }

    private static void ordersMatchInsertionForDeterminism() {
        // Important for the render-key stability: the order must be the
        // input order for current actionables, then the iteration order
        // of the (LinkedHashMap) hold-overs.
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("z", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("z"), 9_000L));
        h.put("a", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("a"), 9_500L));
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                Collections.emptyList(), h, 10_000L, 15_000L);
        if (got.size() != 2) throw new AssertionError("expected 2");
        if (!"z".equals(got.get(0).label) || !"a".equals(got.get(1).label))
            throw new AssertionError("must preserve LinkedHashMap iteration order");
    }

    private static void holdoverIsVisibleHelperRespectsWindow() {
        PaxOpeningRangeModule.LevelEdgeHoldOver e =
                new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 100L);
        if (!e.isVisible(15_100L, 15_000L)) throw new AssertionError("boundary must be visible");
        if (e.isVisible(15_101L, 15_000L)) throw new AssertionError("1ms past must be invisible");
    }

    private static void zeroOrNegativeMinVisibleSuppressesHoldovers() {
        Map<String, PaxOpeningRangeModule.LevelEdgeHoldOver> h = new LinkedHashMap<>();
        h.put("OR-H", new PaxOpeningRangeModule.LevelEdgeHoldOver(actionable("OR-H"), 9_000L));
        List<PaxLevelEdgeModel.Row> got = PaxOpeningRangeModule.selectLevelEdgeRowsToRender(
                Collections.emptyList(), h, 10_000L, 0L);
        if (!got.isEmpty()) throw new AssertionError("zero minVisible must drop hold-overs (window is 0ms)");
    }
}
