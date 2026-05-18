package com.bookmapmcp.state;

/**
 * One side of the order book at one price level.
 *
 * @param price display-currency price (already converted from int-tick units by {@code pips})
 * @param size  aggregated resting size at that level
 */
public record OrderbookLevel(double price, int size) {}
