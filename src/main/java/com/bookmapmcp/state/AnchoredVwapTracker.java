package com.bookmapmcp.state;

import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

/**
 * Intraday anchored VWAPs — Brian Shannon's confluence framework, light edition.
 *
 * <p>Tracks two anchored VWAPs anchored at the OR session start (the
 * operator-configured OpenRange indicator start time, pushed to the
 * bridge via /config rth_open):
 * <ul>
 *   <li><b>OpeningDriveTop AVWAP</b> — anchored at the timestamp of the highest
 *       trade during the opening-drive window (OR open → OR open +
 *       {@link InstrumentState#configOpeningDriveSeconds()}).</li>
 *   <li><b>OpeningDriveBottom AVWAP</b> — anchored at the lowest trade during
 *       the same window.</li>
 * </ul>
 *
 * <p>v19 (institutional): both the session anchor AND the drive window are
 * derived from {@link InstrumentState#configSessionOpen()}, not hard-coded
 * 08:30 CT. Operator changing OR settings rolls the tracker to the new
 * anchor at the next trade after the change.
 *
 * <p>Thread-safety: internal lock guards all mutation + snapshot reads. Single
 * onTrade callback from the trade dispatcher.</p>
 */
public final class AnchoredVwapTracker {

    private static final ZoneId CT = ZoneId.of("America/Chicago");

    private final Object lock = new Object();

    // Per-session state (resets across sessions)
    private long   sessionAnchorMs = 0L;
    // Effective drive window for the active session — derived from the
    // current InstrumentState.configSessionOpen() and configOpeningDriveSeconds()
    // at the moment of session reset. Exposed in the snapshot for audit.
    private long   driveOpenMs     = 0L;
    private long   driveCloseMs    = 0L;
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
            // v19: read OR anchor + drive duration dynamically every tick so
            // operator changes to the OpenRange indicator propagate live.
            LocalTime driveOpen = InstrumentState.configSessionOpen();
            int driveSeconds   = InstrumentState.configOpeningDriveSeconds();
            long sStart = sessionAnchorMs(nowMs, driveOpen);
            if (sStart != sessionAnchorMs) {
                // New session (first trade after start, OR anchor changed,
                // or session rolled to next day) — reset everything.
                sessionAnchorMs = sStart;
                driveOpenMs     = sStart;
                driveCloseMs    = sStart + (long) driveSeconds * 1000L;
                driveHighPx = Double.NEGATIVE_INFINITY;
                driveLowPx  = Double.POSITIVE_INFINITY;
                driveHighMs = driveLowMs = 0L;
                driveFrozen = false;
                topPriceVolume = topVolume = 0.0; topAnchorMs = 0L;
                botPriceVolume = botVolume = 0.0; botAnchorMs = 0L;
                driveTradeCount = 0;
            }

            // Drive window check is now wall-clock-millis based, derived
            // directly from the OR-anchored sessionAnchorMs + driveSeconds.
            boolean inDrive = nowMs >= driveOpenMs && nowMs < driveCloseMs;

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
            } else if (!driveFrozen && nowMs >= driveCloseMs) {
                // Drive just closed — anchors frozen for the session.
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

    private long sessionAnchorMs(long nowMs, LocalTime sessionOpen) {
        ZonedDateTime z = Instant.ofEpochMilli(nowMs).atZone(CT);
        ZonedDateTime open = z.toLocalDate().atTime(sessionOpen).atZone(CT);
        if (z.isBefore(open)) open = open.minusDays(1);
        return open.toInstant().toEpochMilli();
    }

    public AnchoredVwapSnapshot snapshot() {
        synchronized (lock) {
            double topVwap = topVolume > 0 ? topPriceVolume / topVolume : Double.NaN;
            double botVwap = botVolume > 0 ? botPriceVolume / botVolume : Double.NaN;
            return new AnchoredVwapSnapshot(
                sessionAnchorMs,
                driveOpenMs, driveCloseMs,
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
        public final long   driveOpenMs;      // v19: effective drive window open ms (= sessionAnchorMs)
        public final long   driveCloseMs;     // v19: effective drive window close ms
        public final double driveHigh;
        public final double driveLow;
        public final long   driveHighMs;
        public final long   driveLowMs;
        public final boolean driveFrozen;
        public final long   topAnchorMs;
        public final double topVwap;
        public final long   topVolume;
        public final long   botAnchorMs;
        public final double botVwap;
        public final long   botVolume;

        public AnchoredVwapSnapshot(long sessionAnchorMs,
                                    long driveOpenMs, long driveCloseMs,
                                    double driveHigh, double driveLow,
                                    long driveHighMs, long driveLowMs, boolean driveFrozen,
                                    long topAnchorMs, double topVwap, long topVolume,
                                    long botAnchorMs, double botVwap, long botVolume) {
            this.sessionAnchorMs = sessionAnchorMs;
            this.driveOpenMs = driveOpenMs;
            this.driveCloseMs = driveCloseMs;
            this.driveHigh = driveHigh; this.driveLow = driveLow;
            this.driveHighMs = driveHighMs; this.driveLowMs = driveLowMs;
            this.driveFrozen = driveFrozen;
            this.topAnchorMs = topAnchorMs; this.topVwap = topVwap; this.topVolume = topVolume;
            this.botAnchorMs = botAnchorMs; this.botVwap = botVwap; this.botVolume = botVolume;
        }
    }
}
