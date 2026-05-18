package com.bookmapmcp.handlers;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Pure-Java validation of {@link TradingHandler#parsePrice(String)}.
 * Does not touch Bookmap runtime — exercises the price-validation helper
 * directly so SimpleOrderSendParameters can never be constructed with
 * NaN, Infinity, or non-positive prices.
 */
class TradingHandlerPriceTest {

    @Test void rejects_null()         { assertTrue(Double.isNaN(TradingHandler.parsePrice(null))); }
    @Test void rejects_empty()        { assertTrue(Double.isNaN(TradingHandler.parsePrice(""))); }
    @Test void rejects_garbage()      { assertTrue(Double.isNaN(TradingHandler.parsePrice("abc"))); }
    @Test void rejects_NaN_literal()  { assertTrue(Double.isNaN(TradingHandler.parsePrice("NaN"))); }
    @Test void rejects_infinity()     { assertTrue(Double.isNaN(TradingHandler.parsePrice("Infinity"))); }
    @Test void rejects_neg_infinity() { assertTrue(Double.isNaN(TradingHandler.parsePrice("-Infinity"))); }
    @Test void rejects_zero()         { assertTrue(Double.isNaN(TradingHandler.parsePrice("0"))); }
    @Test void rejects_negative()     { assertTrue(Double.isNaN(TradingHandler.parsePrice("-1.0"))); }
    @Test void accepts_normal()       { assertEquals(21340.25, TradingHandler.parsePrice("21340.25"), 1e-9); }
    @Test void accepts_tiny()         { assertEquals(0.01,     TradingHandler.parsePrice("0.01"),     1e-9); }
}
