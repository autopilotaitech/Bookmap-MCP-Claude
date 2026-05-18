package com.bookmapmcp;

import java.time.Instant;
import java.util.Objects;

/**
 * Immutable snapshot of one instrument the MCP bridge is attached to.
 */
public final class AttachedInstrument {

    private final String alias;
    private final String symbol;
    private final String fullName;
    private final double pips;
    private final double multiplier;
    private final Instant attachedAt;

    public AttachedInstrument(String alias, String symbol, String fullName,
                              double pips, double multiplier, Instant attachedAt) {
        this.alias = Objects.requireNonNull(alias, "alias");
        this.symbol = symbol == null ? "" : symbol;
        this.fullName = fullName == null ? "" : fullName;
        this.pips = pips;
        this.multiplier = multiplier;
        this.attachedAt = Objects.requireNonNull(attachedAt, "attachedAt");
    }

    public String alias() { return alias; }
    public String symbol() { return symbol; }
    public String fullName() { return fullName; }
    public double pips() { return pips; }
    public double multiplier() { return multiplier; }
    public Instant attachedAt() { return attachedAt; }
}
