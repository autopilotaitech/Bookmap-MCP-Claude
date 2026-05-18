package com.bookmapmcp.state;

/**
 * Immutable snapshot of one of the user's working orders.
 *
 * <p>Only fields the agent is likely to reason over are exposed — we don't ship
 * internal flags like {@code isDuplicate}/{@code isSimulated}.
 */
public record WorkingOrderRecord(
        String orderId,
        boolean isBuy,
        String type,
        String status,
        double limitPrice,
        double stopPrice,
        boolean stopTriggered,
        int filled,
        int unfilled,
        double averageFillPrice,
        String duration,
        long modificationUtcMillis
) {}
