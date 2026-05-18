package com.bookmapmcp.state;

/**
 * Immutable snapshot of a single trade as reported by Bookmap.
 *
 * @param price       trade print price, in display currency (already scaled by pips)
 * @param size        contracts/shares
 * @param bidAggressor true if the aggressor hit the bid (sell aggressor), false if lifted offer
 * @param nanos       Bookmap's source-time nanoseconds (raw {@code TimeListener.onTimestamp} value)
 */
public record TradeRecord(double price, int size, boolean bidAggressor, long nanos) {}
