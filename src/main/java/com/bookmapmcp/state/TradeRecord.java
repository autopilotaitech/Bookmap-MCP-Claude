package com.bookmapmcp.state;

/**
 * Immutable snapshot of a single trade as reported by Bookmap.
 *
 * @param price       trade print price, in display currency (already scaled by pips)
 * @param size        contracts/shares
 * @param bidAggressor true if the bid was the aggressor (buy aggressor, lifted offer);
 *                     false if the ask was the aggressor (sell aggressor, hit bid).
 *                     Matches Bookmap's TradeInfo.isBidAggressor semantics.
 * @param nanos       Bookmap's source-time nanoseconds (raw {@code TimeListener.onTimestamp} value)
 */
public record TradeRecord(double price, int size, boolean bidAggressor, long nanos) {}
