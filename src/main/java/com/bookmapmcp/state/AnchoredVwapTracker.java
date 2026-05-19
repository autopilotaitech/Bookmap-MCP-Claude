package com.bookmapmcp.state;

import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

/**
 * Intraday anchored VWAPs — Brian Shannon's confluence framework, light edition.
 *
 * <p>Tracks two anchored VWAPs that update from the RTH session start (08:30 CT):
 * <ul>
 *   <li><b>OpeningDriveTop AVWAP</b> — anchored at the timestamp of the highest
 *       trade during the opening 5 minutes (08:30 – 08:35 CT). This is the
 *       price at which the early longs got positioned.</li>
 *   <li><b>OpeningDriveBottom AVWAP</b> — anchored at the lowest trade during
 *       the same 5-min window. The price at which early shorts got positioned.</li>
 * </ul>
 *
 * <p>Use as confluence levels: when current price approaches an anchored VWAP,
 * the corresponding cohort is at breakeven on the day. Bounces / rejections at
 * these levels are high-probability setups (Shannon, 2022).</p>
 *
 * <p>Anchor pinning: once the 5-min window closes (08:35 CT), the anchor times
 * are frozen for the rest of the session. Before 08:35 the anchor floats as
 * higher highs / lower lows print.</p>
 *
 * <p>Thread-safety: internal lock guards all mutation + snapshot reads. Single
 * onTrade callback from the trade dispatcher.</p>
 */
public final class AnchoredVwapTracker {

    private static final ZoneId    CT          = ZoneId.of("America/Chicago");
    private static final LocalTime DRIVE_OPEN  = LocalTime.of(8, 30);
    private static final LocalTime DRIVE_CLOSE = LocalTime.of(8, 35);

    private final Object lock = new Object();

    // Per-session state (resets across sessions)
    private long   sessionAnchorMs = 0L;
    private double driveHighPx = Double.NEGATIVE_INFINITY;
    private double driveLowPx  = Double.POSITIVE_INFINITY;
    private long   driveHighMs = 0L;
    private long   driveLowMs  = 0L;
    private boolean driveFrozen = false;

    // Anchored VWAP accumulators: priceVolume = Σ(p*v), volume = Σv since anchor
    private double topPriceVolume = 0.0; private double topVolume = 0.0; private long topAnchorMs = 0L;
    private double botPriceVolume = 0.0; private double botVolume = 0.0; private long botAnchorMs = 0L;

    // We track trades during the drive window so we can RE-anchor when a new
    // extreme prints. Capped to keep memory bounded; if a session somehow has
    // > MAX_DRIVE_TRADES prints in 5 min we just stop re-anchoring (extremely
    // unlikely for NQ which sees ~few hundred prints per minute peak).
    private static final int MAX_DRIVE_TRADES = 5000;
    private final double[] driveTradePrice = new double[MAX_DRIVE_TRADES];
    private final long[]   driveTradeSize  = new long[MAX_DRIVE_TRADES];
    private final long[]   driveTradeMs    = new long[MAX_DRIVE_TRADES];
    private int driveTradeCount = 0;

    public void onTrade(double price, long size, long nowMs) {
        synchronized (lock) {
            long sStart = sessionAnchorMs(nowMs);
            if (sStart != sessionAnchorMs) {
                // New RTH session — reset everything
                sessionAnchorMs = sStart;
                driveHighPx = Double.NEGATIVE_INFINITY;
                driveLowPx  = Double.POSITIVE_INFINITY;
                driveHighMs = driveLowMs = 0L;
                driveFrozen = false;
                topPriceVolume = topVolume = 0.0; topAnchorMs = 0L;
                botPriceVolume = botVolume = 0.0; botAnchorMs = 0L;
                driveTradeCount = 0;
            }

            ZonedDateTime z = Instant.ofEpochMilli(nowMs).atZone(CT);
            LocalTime lt = z.toLocalTime();
            boolean inDrive = !lt.isBefore(DRIVE_OPEN) && lt.isBefore(DRIVE_CLOSE);

            boolean topJustAnchored = false;
            boolean botJustAnchored = false;

            // Re-anchor logic — only during the drive window
            if (inDrive) {
                // Capture trade into the drive ring (for re-anchoring)
                if (driveTradeCount < MAX_DRIVE_TRADES) {
                    driveTradePrice[driveTradeCount] = price;
                    driveTradeSize[driveTradeCount]  = size;
                    driveTradeMs[driveTradeCount]    = nowMs;
                    driveTradeCount++;
                }
                // New high → re-anchor top AVWAP at this moment
                if (price > driveHighPx) {
                    driveHighPx = price;
                    driveHighMs = nowMs;
                    topAnchorMs = nowMs;
                    topPriceVolume = price * size;
                    topVolume = size;
                    topJustAnchored = true;
                }
                // New low → re-anchor bottom AVWAP
                if (price < driveLowPx) {
                    driveLowPx = price;
                    driveLowMs = nowMs;
                    botAnchorMs = nowMs;
                    botPriceVolume = price * size;
                    botVolume = size;
                    botJustAnchored = true;
                }
            } else if (!driveFrozen && !lt.isBefore(DRIVE_CLOSE)) {
                // Drive just closed — anchors are now frozen for the session.
                // The AVWAP state at this point is the running VWAP from the
                // extreme moment, which is what we want for the remainder of
                // the session.
                driveFrozen = true;
            }

            // Update both anchored VWAPs with every trade AFTER their anchor
            // (regardless of whether we're in the drive window or not).
            // Skip the trade that just re-set the anchor (already counted in the
            // reset). Same-ms trades AFTER the anchor must still be included.
            if (topAnchorMs > 0 && nowMs >= topAnchorMs && !topJustAnchored) {
                topPriceVolume += price * size;
                topVolume += size;
            }
            if (botAnchorMs > 0 && nowMs >= botAnchorMs && !botJustAnchored) {
                botPriceVolume += price * size;
                botVolume += size;
            }
        }
    }

    private long sessionAnchorMs(long nowMs) {
        ZonedDateTime z = Instant.ofEpochMilli(nowMs).atZone(CT);
        ZonedDateTime open = z.toLocalDate().atTime(DRIVE_OPEN).atZone(CT);
        if (z.isBefore(open)) open = open.minusDays(1);
        return open.toInstant().toEpochMilli();
    }

    public AnchoredVwapSnapshot snapshot() {
        synchronized (lock) {
            double topVwap = topVolume > 0 ? topPriceVolume / topVolume : Double.NaN;
            double botVwap = botVolume > 0 ? botPriceVolume / botVolume : Double.NaN;
            return new AnchoredVwapSnapshot(
                sessionAnchorMs,
                driveHighPx != Double.NEGATIVE_INFINITY ? driveHighPx : Double.NaN,
                driveLowPx  != Double.POSITIVE_INFINITY ? driveLowPx  : Double.NaN,
                driveHighMs, driveLowMs, driveFrozen,
                topAnchorMs, topVwap, (long) topVolume,
                botAnchorMs, botVwap, (long) botVolume
            );
        }
    }

    public static final class AnchoredVwapSnapshot {
        public final long   sessionAnchorMs;
        public final double driveHigh;        // highest trade price during the drive window
        public final double driveLow;         // lowest trade price during the drive window
        public final long   driveHighMs;      // wall-clock timestamp of driveHigh
        public final long   driveLowMs;       // wall-clock timestamp of driveLow
        public final boolean driveFrozen;     // true once the 5-min drive window closed
        public final long   topAnchorMs;      // when the top AVWAP anchor was set
        public final double topVwap;          // anchored VWAP from drive top
        public final long   topVolume;        // contracts traded since anchor
        public final long   botAnchorMs;
        public final double botVwap;
        public final long   botVolume;

        public AnchoredVwapSnapshot(long sessionAnchorMs,
                                    double driveHigh, double driveLow,
                                    long driveHighMs, long driveLowMs, boolean driveFrozen,
                                    long topAnchorMs, double topVwap, long topVolume,
                                    long botAnchorMs, double botVwap, long botVolume) {
            this.sessionAnchorMs = sessionAnchorMs;
            this.driveHigh = driveHigh; this.driveLow = driveLow;
            this.driveHighMs = driveHighMs; this.driveLowMs = driveLowMs;
            this.driveFrozen = driveFrozen;
            this.topAnchorMs = topAnchorMs; this.topVwap = topVwap; this.topVolume = topVolume;
            this.botAnchorMs = botAnchorMs; this.botVwap = botVwap; this.botVolume = botVolume;
        }
    }
}
