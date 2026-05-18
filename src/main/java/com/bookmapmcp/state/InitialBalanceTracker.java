package com.bookmapmcp.state;

import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;

/**
 * Initial Balance tracker — captures high/low of the first hour of RTH
 * (08:30-09:30 CT) per Steidlmayer's canonical day-classification framework.
 *
 * <p>Per the 2015-2025 NQ futures study at tradingstats.net:
 * <ul>
 *   <li>96.2% of sessions break the IB on the same side at least once</li>
 *   <li>Narrow-IB days (IB &lt; 0.5 × 20d-avg-IB) break 98.7%</li>
 *   <li>Wide-IB days only break 66.7% → much weaker continuation prior</li>
 *   <li>50% extension hit rate: ~65% upside, ~70% downside</li>
 *   <li>100% extension: ~13-16%</li>
 * </ul>
 *
 * <p>Surfaces:
 * <ul>
 *   <li>{@code ibHigh}/{@code ibLow}/{@code ibRange} — the captured IB</li>
 *   <li>{@code ibComplete} — true after 09:30 CT</li>
 *   <li>{@code extensions} — 50%/100%/150%/200% above and below the IB</li>
 *   <li>{@code narrow}/{@code wide} — flag vs 20-day rolling avg IB range</li>
 *   <li>{@code dayType} — Steidlmayer-lite classification once enough range data is in</li>
 * </ul>
 */
public final class InitialBalanceTracker {

    private static final ZoneId    CT       = ZoneId.of("America/Chicago");
    private static final LocalTime IB_OPEN  = LocalTime.of(8, 30);
    private static final LocalTime IB_CLOSE = LocalTime.of(9, 30);

    private final Object lock = new Object();

    private long   sessionStartMs = 0L;
    private double ibHigh = Double.NEGATIVE_INFINITY;
    private double ibLow  = Double.POSITIVE_INFINITY;
    private boolean ibComplete = false;

    // Rolling daily IB ranges for narrow/wide flagging (last 20 sessions)
    private final double[] dailyRing = new double[20];
    private int dailyCount = 0;
    private int dailyIdx   = 0;
    private long lastSessionMsRecorded = 0L;

    // Day-stats for type classification
    private double sessionHigh = Double.NEGATIVE_INFINITY;
    private double sessionLow  = Double.POSITIVE_INFINITY;
    private long   tradesInSession = 0L;

    /** Called on every trade. */
    public void onTrade(double price, long nowMs) {
        synchronized (lock) {
            long sStart = sessionAnchorMs(nowMs);
            if (sStart != sessionStartMs) {
                // New RTH session: archive prior IB into the daily ring (if any),
                // then reset.
                if (sessionStartMs != 0L && ibLow != Double.POSITIVE_INFINITY) {
                    double range = ibHigh - ibLow;
                    if (range > 0 && lastSessionMsRecorded != sessionStartMs) {
                        dailyRing[dailyIdx] = range;
                        dailyIdx = (dailyIdx + 1) % dailyRing.length;
                        if (dailyCount < dailyRing.length) dailyCount++;
                        lastSessionMsRecorded = sessionStartMs;
                    }
                }
                sessionStartMs = sStart;
                ibHigh = Double.NEGATIVE_INFINITY;
                ibLow  = Double.POSITIVE_INFINITY;
                ibComplete = false;
                sessionHigh = Double.NEGATIVE_INFINITY;
                sessionLow  = Double.POSITIVE_INFINITY;
                tradesInSession = 0L;
            }
            // Maintain session range regardless of IB window
            if (price > sessionHigh) sessionHigh = price;
            if (price < sessionLow)  sessionLow  = price;
            tradesInSession++;

            // IB capture only during 08:30-09:30 CT
            ZonedDateTime z = Instant.ofEpochMilli(nowMs).atZone(CT);
            LocalTime lt = z.toLocalTime();
            if (!lt.isBefore(IB_OPEN) && lt.isBefore(IB_CLOSE)) {
                if (price > ibHigh) ibHigh = price;
                if (price < ibLow)  ibLow  = price;
            } else if (!lt.isBefore(IB_CLOSE)) {
                ibComplete = true;
            }
        }
    }

    private long sessionAnchorMs(long nowMs) {
        ZonedDateTime z = Instant.ofEpochMilli(nowMs).atZone(CT);
        LocalDate d = z.toLocalDate();
        ZonedDateTime open = d.atTime(IB_OPEN).atZone(CT);
        if (z.isBefore(open)) open = open.minusDays(1);
        return open.toInstant().toEpochMilli();
    }

    public InitialBalanceSnapshot snapshot() {
        synchronized (lock) {
            boolean haveIb = ibLow != Double.POSITIVE_INFINITY && ibHigh > ibLow;
            double ibRange = haveIb ? (ibHigh - ibLow) : 0.0;
            // Average of recent IB ranges
            double avgIb = 0.0;
            if (dailyCount > 0) {
                double sum = 0; for (int i = 0; i < dailyCount; i++) sum += dailyRing[i];
                avgIb = sum / dailyCount;
            }
            String ibSizeTag = "UNKNOWN";
            if (haveIb && avgIb > 0) {
                double ratio = ibRange / avgIb;
                if (ratio < 0.5)      ibSizeTag = "NARROW";   // 98.7% break rate
                else if (ratio > 1.5) ibSizeTag = "WIDE";     // weaker continuation
                else                  ibSizeTag = "NORMAL";
            }

            double[] extUp   = new double[]{0, 0, 0, 0};
            double[] extDown = new double[]{0, 0, 0, 0};
            if (haveIb) {
                double[] mults = {0.5, 1.0, 1.5, 2.0};
                for (int i = 0; i < mults.length; i++) {
                    extUp[i]   = ibHigh + mults[i] * ibRange;
                    extDown[i] = ibLow  - mults[i] * ibRange;
                }
            }

            // Steidlmayer-lite day-type classification (requires IB complete + range data)
            String dayType = "UNKNOWN";
            double rangeNow = (sessionHigh > sessionLow) ? (sessionHigh - sessionLow) : 0.0;
            if (haveIb && ibComplete && rangeNow > 0) {
                double ibToRange = ibRange / rangeNow;
                double rangeToAvgIb = (avgIb > 0) ? (rangeNow / avgIb) : 0.0;
                if      (ibToRange >= 0.85)                       dayType = "NORMAL";
                else if (ibToRange <= 0.33 && rangeToAvgIb >= 1.8) dayType = "TREND";
                else if (ibToRange >= 0.40 && ibToRange <= 0.65)   dayType = "NORMAL_VAR";
                else if (rangeToAvgIb <= 0.50)                     dayType = "NON_TREND";
                else                                                dayType = "NEUTRAL";
            }

            return new InitialBalanceSnapshot(
                sessionStartMs,
                haveIb ? ibHigh : Double.NaN,
                haveIb ? ibLow  : Double.NaN,
                ibRange, ibComplete, ibSizeTag,
                avgIb, dailyCount,
                sessionHigh > sessionLow ? sessionHigh : Double.NaN,
                sessionHigh > sessionLow ? sessionLow  : Double.NaN,
                rangeNow,
                dayType,
                extUp, extDown
            );
        }
    }

    public static final class InitialBalanceSnapshot {
        public final long   sessionStartMs;
        public final double ibHigh;
        public final double ibLow;
        public final double ibRange;
        public final boolean ibComplete;
        public final String ibSizeTag;      // NARROW / NORMAL / WIDE / UNKNOWN
        public final double avgIb;
        public final int    avgIbDays;
        public final double sessionHigh;
        public final double sessionLow;
        public final double sessionRange;
        public final String dayType;        // TREND / NORMAL / NORMAL_VAR / NEUTRAL / NON_TREND / UNKNOWN
        public final double[] extensionsUp;   // 50%, 100%, 150%, 200% above ibHigh
        public final double[] extensionsDown; // mirror below ibLow

        public InitialBalanceSnapshot(long sessionStartMs, double ibHigh, double ibLow, double ibRange,
                                      boolean ibComplete, String ibSizeTag,
                                      double avgIb, int avgIbDays,
                                      double sessionHigh, double sessionLow, double sessionRange,
                                      String dayType,
                                      double[] extensionsUp, double[] extensionsDown) {
            this.sessionStartMs = sessionStartMs;
            this.ibHigh = ibHigh; this.ibLow = ibLow; this.ibRange = ibRange;
            this.ibComplete = ibComplete; this.ibSizeTag = ibSizeTag;
            this.avgIb = avgIb; this.avgIbDays = avgIbDays;
            this.sessionHigh = sessionHigh; this.sessionLow = sessionLow; this.sessionRange = sessionRange;
            this.dayType = dayType;
            this.extensionsUp = extensionsUp; this.extensionsDown = extensionsDown;
        }
    }
}
