package com.bookmapmcp.state;

/**
 * Immutable snapshot of the account's status on one instrument, as reported by
 * Bookmap (which got it from the broker — so this is authoritative, not
 * computed from fills).
 */
public record PositionSnapshot(
        int position,
        double averagePrice,
        double unrealizedPnl,
        double realizedPnl,
        String currency,
        int volume,
        int workingBuys,
        int workingSells
) {
    public static final PositionSnapshot EMPTY =
            new PositionSnapshot(0, 0, 0, 0, "", 0, 0, 0);
}
