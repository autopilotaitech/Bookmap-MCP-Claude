package com.bookmapmcp.state;

/**
 * Immutable snapshot of one execution (fill) as reported by Bookmap.
 *
 * <p>Side is looked up from {@link InstrumentState#knownOrderSidesForTests()}
 * at execution time; if the originating order was never seen on the order
 * update stream (rare), {@link #side} will be {@code "unknown"}.
 */
public record RecentExecution(
        String orderId,
        String executionId,
        String side,
        double price,
        int size,
        long timeMillis,
        boolean simulated
) {}
