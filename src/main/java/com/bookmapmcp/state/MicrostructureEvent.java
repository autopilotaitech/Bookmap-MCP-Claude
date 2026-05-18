package com.bookmapmcp.state;

/** One detected microstructure event — spoof, iceberg, or stop-run. */
public final class MicrostructureEvent {
    public enum Kind { SPOOF, ICEBERG, STOP_SWEEP }
    public final Kind kind;
    public final long timeMs;
    public final double price;
    public final long size;
    public final boolean isBid;        // true = bid side, false = ask side (or buy/sell for stops)
    public final String reason;        // human-readable detail

    public MicrostructureEvent(Kind kind, long timeMs, double price, long size, boolean isBid, String reason) {
        this.kind = kind;
        this.timeMs = timeMs;
        this.price = price;
        this.size = size;
        this.isBid = isBid;
        this.reason = reason;
    }
}
