package com.bookmapmcp.state;

import java.util.List;

/**
 * Per-price-level depth-event activity over rolling time windows.
 * Distinguishes "stacked" (size added) from "pulled" (size removed without
 * trading) from "hit" (size removed AND a trade printed at that level).
 */
public final class BookDynamicsSnapshot {
    public static final class Level {
        public final double price;
        public final boolean isBid;
        public final long stacked1m, pulled1m, hit1m;
        public final long stacked3m, pulled3m, hit3m;
        public final long stacked15m, pulled15m, hit15m;
        public Level(double price, boolean isBid,
                     long s1, long p1, long h1,
                     long s3, long p3, long h3,
                     long s15, long p15, long h15) {
            this.price = price; this.isBid = isBid;
            this.stacked1m = s1; this.pulled1m = p1; this.hit1m = h1;
            this.stacked3m = s3; this.pulled3m = p3; this.hit3m = h3;
            this.stacked15m = s15; this.pulled15m = p15; this.hit15m = h15;
        }
    }
    public final long asOfNanos;
    public final List<Level> topActiveLevels;  // sorted by total activity desc
    public final boolean mboAvailable;
    public BookDynamicsSnapshot(long asOfNanos, List<Level> topActiveLevels, boolean mboAvailable) {
        this.asOfNanos = asOfNanos;
        this.topActiveLevels = topActiveLevels;
        this.mboAvailable = mboAvailable;
    }
}
