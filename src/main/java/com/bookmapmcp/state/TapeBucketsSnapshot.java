package com.bookmapmcp.state;

import java.util.List;

/** Per-size-bucket trade flow over rolling time windows. */
public final class TapeBucketsSnapshot {
    public static final class Bucket {
        public final String label;     // e.g. "1-10", "50-99", "100+"
        public final long minSize, maxSize;  // inclusive; maxSize == -1 = unbounded
        public final long buyVol30s, sellVol30s, prints30s;
        public final long buyVol5m,  sellVol5m,  prints5m;
        public Bucket(String label, long minSize, long maxSize,
                      long buyVol30s, long sellVol30s, long prints30s,
                      long buyVol5m,  long sellVol5m,  long prints5m) {
            this.label = label; this.minSize = minSize; this.maxSize = maxSize;
            this.buyVol30s = buyVol30s; this.sellVol30s = sellVol30s; this.prints30s = prints30s;
            this.buyVol5m  = buyVol5m;  this.sellVol5m  = sellVol5m;  this.prints5m  = prints5m;
        }
        public double imbalance30s() {
            long t = buyVol30s + sellVol30s;
            return t > 0 ? ((double)(buyVol30s - sellVol30s)) / t : 0.0;
        }
        public double imbalance5m() {
            long t = buyVol5m + sellVol5m;
            return t > 0 ? ((double)(buyVol5m - sellVol5m)) / t : 0.0;
        }
    }
    public final long asOfNanos;
    public final List<Bucket> buckets;
    public TapeBucketsSnapshot(long asOfNanos, List<Bucket> buckets) {
        this.asOfNanos = asOfNanos;
        this.buckets = buckets;
    }
}
