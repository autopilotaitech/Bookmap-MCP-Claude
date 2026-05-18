package com.bookmapmcp.state;

import java.util.List;

/**
 * Session-anchored Volume Profile.
 *
 * <p>Tracks total contracts traded at each price tick from the 09:30 ET RTH
 * open. Surfaces:
 * <ul>
 *   <li>{@code vpoc} — point of control (price with the most volume).</li>
 *   <li>{@code vah}/{@code val} — value area high/low (bounds of the 70% volume zone around VPOC).</li>
 *   <li>{@code levels} — full price-volume histogram.</li>
 * </ul>
 *
 * <p>If no trades have arrived yet, fields are NaN / empty and serializers
 * should emit nulls.
 */
public final class VolumeProfileSnapshot {

    public static final class Level {
        public final double price;
        public final long volume;
        public final long buyVolume;
        public final long sellVolume;

        public Level(double price, long buyVolume, long sellVolume) {
            this.price = price;
            this.buyVolume = buyVolume;
            this.sellVolume = sellVolume;
            this.volume = buyVolume + sellVolume;
        }

        public double price() { return price; }
        public long volume() { return volume; }
        public long buyVolume() { return buyVolume; }
        public long sellVolume() { return sellVolume; }
    }

    public final long sessionStartMs;
    public final long totalVolume;
    public final long sampleCount;     // total trade count this session
    public final double vpoc;
    public final double vah;
    public final double val;
    public final double valueAreaVolume;  // sum of volume inside VAH..VAL
    public final List<Level> levels;      // sorted ascending by price

    public VolumeProfileSnapshot(long sessionStartMs, long totalVolume, long sampleCount,
                                 double vpoc, double vah, double val,
                                 double valueAreaVolume, List<Level> levels) {
        this.sessionStartMs = sessionStartMs;
        this.totalVolume = totalVolume;
        this.sampleCount = sampleCount;
        this.vpoc = vpoc;
        this.vah = vah;
        this.val = val;
        this.valueAreaVolume = valueAreaVolume;
        this.levels = levels;
    }
}
