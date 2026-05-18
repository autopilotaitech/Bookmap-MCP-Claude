package com.openrange;

public class PaxOpeningRangeOrderFlowTrackerTest {
    public static void main(String[] args) {
        accumulatesCvdFromAggressorSide();
        accumulatesPullingAndStackingNearLastPrice();
        ignoresDepthOutsideConfiguredRange();
        resetClearsAllState();
        depthBeforeFirstTradeIsIgnored();
        depthAfterTradeWorksNormally();
    }

    private static void resetClearsAllState() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 4);
        tracker.onTrade(6400.00, 3, false);
        tracker.onTrade(6399.75, 2, true);
        tracker.onDepth(true, 6399.75, 10);
        tracker.onDepth(true, 6399.75, 14);
        tracker.onDepth(false, 6400.25, 20);

        tracker.reset();

        PaxOpeningRangeMarketState state = tracker.snapshot();
        if (!Double.isNaN(state.lastPrice())) {
            throw new AssertionError("lastPrice should be NaN after reset, got " + state.lastPrice());
        }
        assertEquals(0.0, state.cvdDelta(), "cvd after reset");
        assertEquals(0.0, state.bidDepthDelta(), "bid depth after reset");
        assertEquals(0.0, state.askDepthDelta(), "ask depth after reset");
        assertEquals(0.0, state.netDepthDelta(), "net depth after reset");

        tracker.onDepth(true, 6399.75, 50);
        PaxOpeningRangeMarketState afterDepth = tracker.snapshot();
        if (!Double.isNaN(afterDepth.lastPrice())) {
            throw new AssertionError("lastPrice should remain NaN after depth-only post-reset");
        }
        assertEquals(0.0, afterDepth.bidDepthDelta(), "bid depth ignored post-reset");
    }

    private static void depthBeforeFirstTradeIsIgnored() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 4);

        tracker.onDepth(true, 6399.75, 10);
        tracker.onDepth(false, 6400.25, 20);

        PaxOpeningRangeMarketState state = tracker.snapshot();
        assertEquals(0.0, state.bidDepthDelta(), "bid depth before first trade");
        assertEquals(0.0, state.askDepthDelta(), "ask depth before first trade");
        assertEquals(0.0, state.netDepthDelta(), "net depth before first trade");
    }

    private static void depthAfterTradeWorksNormally() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 4);
        tracker.onTrade(6400.00, 1, false);

        tracker.onDepth(true, 6399.75, 10);
        PaxOpeningRangeMarketState afterFirst = tracker.snapshot();
        assertEquals(0.0, afterFirst.bidDepthDelta(), "first depth seeds map without delta");

        tracker.onDepth(true, 6399.75, 15);
        PaxOpeningRangeMarketState afterStack = tracker.snapshot();
        assertEquals(5.0, afterStack.bidDepthDelta(), "stacking adds delta");

        tracker.onDepth(true, 6399.75, 3);
        PaxOpeningRangeMarketState afterPull = tracker.snapshot();
        assertEquals(-7.0, afterPull.bidDepthDelta(), "pulling subtracts delta");
        assertEquals(-7.0, afterPull.netDepthDelta(), "net reflects bid-only changes");
    }

    private static void accumulatesCvdFromAggressorSide() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 4);

        tracker.onTrade(6400.00, 3, false);
        tracker.onTrade(6399.75, 2, true);

        PaxOpeningRangeMarketState state = tracker.snapshot();
        assertEquals(1.0, state.cvdDelta(), "cvd");
        assertEquals(6399.75, state.lastPrice(), "last price");
    }

    private static void accumulatesPullingAndStackingNearLastPrice() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 4);
        tracker.onTrade(6400.00, 1, false);

        tracker.onDepth(true, 6399.75, 10);
        tracker.onDepth(true, 6399.75, 14);
        tracker.onDepth(false, 6400.25, 20);
        tracker.onDepth(false, 6400.25, 12);

        PaxOpeningRangeMarketState state = tracker.snapshot();
        assertEquals(4.0, state.bidDepthDelta(), "bid depth delta");
        assertEquals(-8.0, state.askDepthDelta(), "ask depth delta");
        assertEquals(12.0, state.netDepthDelta(), "net depth delta");
    }

    private static void ignoresDepthOutsideConfiguredRange() {
        PaxOpeningRangeOrderFlowTracker tracker = new PaxOpeningRangeOrderFlowTracker(0.25, 2);
        tracker.onTrade(6400.00, 1, false);

        tracker.onDepth(true, 6390.00, 100);
        tracker.onDepth(false, 6410.00, 100);

        PaxOpeningRangeMarketState state = tracker.snapshot();
        assertEquals(0.0, state.bidDepthDelta(), "bid depth delta");
        assertEquals(0.0, state.askDepthDelta(), "ask depth delta");
        assertEquals(0.0, state.netDepthDelta(), "net depth delta");
    }

    private static void assertEquals(double expected, double actual, String message) {
        if (Math.abs(expected - actual) > 0.0000001) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
