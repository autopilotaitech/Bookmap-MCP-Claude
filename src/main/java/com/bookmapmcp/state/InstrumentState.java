package com.bookmapmcp.state;

import java.time.Instant;
import java.time.LocalTime;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.Comparator;
import java.util.Deque;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NavigableMap;
import java.util.Objects;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.atomic.LongAdder;

import velox.api.layer1.data.BalanceInfo;
import velox.api.layer1.data.BalanceInfo.BalanceInCurrency;
import velox.api.layer1.data.ExecutionInfo;
import velox.api.layer1.data.OrderInfoUpdate;
import velox.api.layer1.data.OrderStatus;
import velox.api.layer1.data.StatusInfo;
import velox.api.layer1.data.TradeInfo;
import velox.api.layer1.simplified.Api;

import com.bookmapmcp.BridgeRegistry;

public final class InstrumentState {

    public static final int TRADES_CAPACITY = 5000;     // 5min @ 16 tps avg, enough for D + others
    public static final int FILLS_CAPACITY = 500;
    public static final int MBO_DELTA_CAPACITY = 20000; // 15min of MBO events for A
    public static final int MICRO_EVENT_CAPACITY = 200;

    private static final ZoneId CT = ZoneId.of("America/Chicago");
    private static final LocalTime RTH_OPEN  = LocalTime.of(8, 30);
    private static final LocalTime RTH_CLOSE = LocalTime.of(15, 0);
    private static final LocalTime ETH_OPEN  = LocalTime.of(17, 0);

    // ---- D: tape-bucket size cutoffs ----
    private static final long[][] TAPE_BUCKETS = {
        {1, 10}, {11, 25}, {26, 50}, {51, 99}, {100, -1}
    };
    private static final String[] TAPE_BUCKET_LABELS = {"1-10","11-25","26-50","51-99","100+"};

    // ---- C: LT EWMA half-life ----
    private static final long LT_HALFLIFE_MS = 30_000L;

    // ---- B-spoof / iceberg thresholds (NQ defaults; ES/RTY get different sets later) ----
    // --- Adaptive detector thresholds (Jane-Street style: scale to market regime, not hardcoded magic) ---
    // SPOOF: order placed, sat ≥ MIN_AGE_MS, then canceled with 0 filled. Size threshold is the rolling 95th
    // percentile of order sizes (floor at SPOOF_FLOOR to avoid noise during dead markets).
    private static final int  SPOOF_FLOOR        = 25;
    private static final long SPOOF_MIN_AGE_MS   = 250L;
    // ICEBERG: refills (distinct passive order IDs hit) ≥ MIN_REFILLS AND cumulative traded volume at the
    // level exceeds 2.5× the rolling-EWMA of level-traded volumes (floor at ICEBERG_FLOOR).
    private static final int  ICEBERG_MIN_REFILLS = 3;
    private static final int  ICEBERG_FLOOR       = 40;
    private static final double ICEBERG_MULTIPLE  = 2.5;
    // STOP SWEEP: 5s aggressor-volume burst exceeds rolling EWMA + 2σ AND crosses a configured magnet level.
    private static final long STOP_BURST_WINDOW_MS = 5_000L;
    private static final int  STOP_FLOOR           = 80;

    private final String alias;
    private final String symbol;
    private final String fullName;
    private final double pips;
    private final double multiplier;
    private final Instant attachedAt;

    private final TreeMap<Integer, Integer> bids = new TreeMap<>(Comparator.reverseOrder());
    private final TreeMap<Integer, Integer> asks = new TreeMap<>();
    private final Object depthLock = new Object();

    private final Deque<TradeRecord> recentTrades = new ArrayDeque<>(TRADES_CAPACITY);
    private final Object tradesLock = new Object();

    private final Deque<RecentExecution> recentExecutions = new ArrayDeque<>(FILLS_CAPACITY);
    private final Object executionsLock = new Object();

    private final ConcurrentMap<String, WorkingOrderRecord> workingOrders = new ConcurrentHashMap<>();
    private final ConcurrentMap<String, Boolean> knownOrderSides = new ConcurrentHashMap<>();

    private volatile PositionSnapshot position = PositionSnapshot.EMPTY;
    private volatile BalanceSnapshot balance = BalanceSnapshot.EMPTY;
    private volatile long lastSeenNanos;
    private volatile double lastTradePrice = Double.NaN;
    private volatile Api api;

    private final Object shadowLock = new Object();
    private int    shadowPos = 0;
    private double shadowAvg = 0.0;
    private double shadowRealizedPnl = 0.0;

    private final Object vwapLock = new Object();
    private final VwapBucket vwapEth = new VwapBucket();
    private final VwapBucket vwapRth = new VwapBucket();

    private final Object volProfileLock = new Object();
    private final VolBucket vpEth = new VolBucket();
    private final VolBucket vpRth = new VolBucket();

    // ---- C: LT liquidity EWMA ----
    private final Object ltLock = new Object();
    private double ltBidEwma = 0.0;
    private double ltAskEwma = 0.0;
    private long   ltLastUpdateMs = 0L;

    // ---- A: MBO depth-event history. Each entry: (timeMs, isBid, priceTick, kind, size)
    // kind: 0=SEND, 1=CANCEL, 2=REPLACE, 3=TRADE (level was hit) ----
    private static final class MboDelta {
        final long timeMs;
        final boolean isBid;
        final int priceTick;
        final byte kind;        // 0=SEND 1=CANCEL 2=REPLACE 3=TRADE
        final int size;         // delta size for SEND/CANCEL/REPLACE; trade size for TRADE
        MboDelta(long t, boolean b, int p, byte k, int s) {
            timeMs=t; isBid=b; priceTick=p; kind=k; size=s;
        }
    }
    private final Object mboLock = new Object();
    private final Deque<MboDelta> mboDeltas = new ArrayDeque<>(MBO_DELTA_CAPACITY);
    private volatile boolean mboAvailable = false;   // set true on first MBO event

    // ---- A: per-order state (used by spoof, iceberg) ----
    private static final class MboOrder {
        final boolean isBid;
        int priceTick;
        int currentSize;
        int peakSize;
        long sentMs;
        long filledSize;        // updated when this order id appears as passiveOrderId on a trade
        MboOrder(boolean isBid, int priceTick, int size, long sentMs) {
            this.isBid=isBid; this.priceTick=priceTick;
            this.currentSize=size; this.peakSize=size; this.sentMs=sentMs;
        }
    }
    private final ConcurrentMap<String, MboOrder> mboOrders = new ConcurrentHashMap<>();
    // Track recent passive-side trade chains per level (for iceberg refill detection).
    private static final class IcebergState {
        long lastTradeMs = 0L;
        long cumulativeTraded = 0L;
        int  refills = 0;          // distinct passive order IDs hit at this level recently
        String currentPassiveId = "";
    }
    private final Object icebergLock = new Object();
    private final Map<Integer, IcebergState> icebergByTick = new TreeMap<>();

    // ---- B: microstructure event log (shared spoof/iceberg/stops) ----
    private final Object microEventLock = new Object();
    private final Deque<MicrostructureEvent> microEvents = new ArrayDeque<>(MICRO_EVENT_CAPACITY);

    // ---- B-stops: configurable magnet levels (set externally via setMagnetLevels) ----
    private volatile double[] magnetLevels = new double[0];

    // ---- Adaptive-threshold baselines for detectors. EWMA over recent samples
    // gives a mean (and variance via Welford-style update) so we can flag
    // outliers relative to the current market regime instead of magic constants.
    // ICEBERG: samples are cumulative-traded values at the moment an iceberg
    // either fires or its level ages out (the "natural per-level traded vol").
    private final Object adaptiveLock = new Object();
    private double ewmaIcebergMean = 0.0;
    private long   ewmaIcebergSamples = 0L;
    // SPOOF: samples are sizes of orders that ARE canceled without filling (legitimate cancel population).
    private double ewmaSpoofMean = 0.0;
    private double ewmaSpoofSqDev = 0.0;
    private long   ewmaSpoofSamples = 0L;
    // STOP: samples are 5s aggressor-volume bursts (computed at every trade).
    private double ewmaStopMean = 0.0;
    private double ewmaStopSqDev = 0.0;
    private long   ewmaStopSamples = 0L;

    // ---- Pull/Stack indicator (Engineered Analytics style) ----
    // Configuration
    private static final int PS_DEPTH_LEVELS = 10;        // pull/stack ±N ticks around BBO (Engineered default)
    private static final int LT_DEPTH_LEVELS = 25;        // LT liquidity ±N ticks (wider for thick markets like NQ)
    private static final long PS_HIT_SUPPRESS_MS = 100L;
    // Last-known resting size per (side, priceTick), used to compute deltas
    private final Object pullStackLock = new Object();
    private final java.util.HashMap<Integer, Integer> lastBidSize = new java.util.HashMap<>();
    private final java.util.HashMap<Integer, Integer> lastAskSize = new java.util.HashMap<>();
    // Last trade time per priceTick — used to suppress hit-vs-pull
    private final java.util.HashMap<Integer, Long> lastTradeMsByTick = new java.util.HashMap<>();
    // Primary accumulator (BBO_RESET): zeroes whenever best bid or best ask price changes
    private long psBboBidStacked = 0L, psBboBidPulled = 0L, psBboAskStacked = 0L, psBboAskPulled = 0L;
    private long psBboLastResetMs = 0L;
    private long psBboResetCount = 0L;
    private int  psLastBestBidTick = Integer.MIN_VALUE;
    private int  psLastBestAskTick = Integer.MIN_VALUE;
    // Per-minute pre-aggregated buckets — keeps 16 minutes of history so 1m/3m/15m
    // windows are accurate regardless of event rate. Each bucket holds 4 longs:
    // [bidStacked, bidPulled, askStacked, askPulled]. The deque approach (capped
    // ring of every event) was wrong: at NQ event rates it could only hold ~30s
    // of events, which made 1m/3m/15m all show the same recent slice.
    private static final class PsMinuteBucket {
        final long minuteStartMs;
        long bidStacked = 0L, bidPulled = 0L, askStacked = 0L, askPulled = 0L;
        PsMinuteBucket(long startMs) { this.minuteStartMs = startMs; }
    }
    private final Deque<PsMinuteBucket> psHistory = new ArrayDeque<>(20);
    private PsMinuteBucket psCurrentBucket = null;
    private long psFirstEventMs = 0L;  // for warm-up gating

    // Rolling EWMA stats per window (Welford) for z-scoring the pull/stack score.
    // Score = bid_net − ask_net where each side weights pulls 1.3× stacks (intentional cancels stronger signal).
    // α = 1/min(N, 60) → effective ~1.5-min half-life at 1.5s poll cadence.
    private static final double PS_PULL_WEIGHT = 1.3;
    private static final double PS_W_BBO = 0.40, PS_W_1M = 0.35, PS_W_3M = 0.15, PS_W_15M = 0.10;
    private final Object psStatsLock = new Object();
    private double psBboMean = 0, psBboM2 = 0;  private long psBboN = 0;
    private double ps1mMean = 0, ps1mM2 = 0;    private long ps1mN  = 0;
    private double ps3mMean = 0, ps3mM2 = 0;    private long ps3mN  = 0;
    private double ps15mMean = 0, ps15mM2 = 0;  private long ps15mN = 0;

    // CVD (Cumulative Volume Delta) — running buy aggressor minus sell aggressor.
    // Anchored at RTH open (08:30 CT), resets daily. Matches what Engineered's
    // CVD gauge shows in the bottom panel.
    private final Object cvdLock = new Object();
    private long cvdSessionStartMs = 0L;
    private long cvdValue = 0L;

    // Flow-regime classifier (Cont/Kukanov/Stoikov OFI + CVD divergence +
    // VPT absorption + bias trajectory + Welford-EWMA adaptive thresholds).
    // See FlowRegime.java. Built lazily after pips is bound in the constructor.
    private FlowRegime flowRegime;
    private VwapSlopeTracker vwapSlope;
    private InitialBalanceTracker ibTracker;
    private AnchoredVwapTracker avwapTracker;

    private final LongAdder depthEvents     = new LongAdder();
    private final LongAdder tradeEvents     = new LongAdder();
    private final LongAdder orderEvents     = new LongAdder();
    private final LongAdder executionEvents = new LongAdder();
    private final LongAdder positionEvents  = new LongAdder();
    private final LongAdder balanceEvents   = new LongAdder();
    private final LongAdder mboSendEvents   = new LongAdder();
    private final LongAdder mboCancelEvents = new LongAdder();
    private final LongAdder mboReplaceEvents= new LongAdder();
    private volatile long lastOrderEventNanos    = 0L;
    private volatile long lastPositionEventNanos = 0L;
    private volatile long lastExecutionEventNanos= 0L;

    public InstrumentState(String alias, String symbol, String fullName,
                           double pips, double multiplier, Instant attachedAt) {
        this.alias = Objects.requireNonNull(alias, "alias");
        this.symbol = symbol == null ? "" : symbol;
        this.fullName = fullName == null ? "" : fullName;
        this.pips = pips;
        this.multiplier = multiplier;
        this.attachedAt = Objects.requireNonNull(attachedAt, "attachedAt");
        this.flowRegime = new FlowRegime(pips);
        this.vwapSlope = new VwapSlopeTracker();
        this.ibTracker = new InitialBalanceTracker();
        this.avwapTracker = new AnchoredVwapTracker();
    }

    public String alias() { return alias; }
    public String symbol() { return symbol; }
    public String fullName() { return fullName; }
    public double pips() { return pips; }
    public double multiplier() { return multiplier; }
    public Instant attachedAt() { return attachedAt; }
    public long lastSeenNanos() { return lastSeenNanos; }
    public double lastTradePrice() { return lastTradePrice; }
    public Api api() { return api; }
    public boolean mboAvailable() { return mboAvailable; }
    public void setMagnetLevels(double[] levels) { this.magnetLevels = levels == null ? new double[0] : levels.clone(); }

    public void setApi(Api api) { this.api = api; }
    public void onTimestamp(long nanos) { this.lastSeenNanos = nanos; }

    public void onDepth(boolean isBid, int priceTicks, int size) {
        depthEvents.increment();
        int prevSize;
        synchronized (depthLock) {
            TreeMap<Integer, Integer> side = isBid ? bids : asks;
            Integer prior = side.get(priceTicks);
            prevSize = prior == null ? 0 : prior;
            if (size <= 0) side.remove(priceTicks);
            else           side.put(priceTicks, size);
        }
        // Depth-only pull/stack fallback. When the feed delivers MBO events
        // (creditPullStackFromMbo) we prefer those — they are unambiguous about
        // send vs cancel. For feeds that do not surface MBO we still want
        // pull/stack to accumulate, so we infer events from per-level depth
        // deltas. Gated by !mboAvailable to avoid double-counting once MBO
        // begins flowing on the same instrument.
        if (!mboAvailable) {
            updatePullStack(isBid, priceTicks, prevSize, size);
        }
        // BBO-reset trigger for pull/stack. Pull/Stack event ACCUMULATION is
        // primarily driven by MBO send/cancel/replace (see creditPullStackFromMbo).
        // Depth events also detect when the BBO price moves, to reset the BBO-column.
        maybeResetBboPullStack();
        // C: LT EWMA update on best bid/ask change
        updateLtLiquidityIfBboChanged();
    }

    /** BBO-reset trigger: zero the BBO-column meters when best bid or ask tick moves. */
    private void maybeResetBboPullStack() {
        int bbTick, baTick, bbSize, baSize;
        synchronized (depthLock) {
            if (bids.isEmpty()) { bbTick = Integer.MIN_VALUE; bbSize = 0; }
            else { Map.Entry<Integer, Integer> e = bids.firstEntry(); bbTick = e.getKey(); bbSize = e.getValue(); }
            if (asks.isEmpty()) { baTick = Integer.MIN_VALUE; baSize = 0; }
            else { Map.Entry<Integer, Integer> e = asks.firstEntry(); baTick = e.getKey(); baSize = e.getValue(); }
        }
        long nowMs = nowMs();
        synchronized (pullStackLock) {
            if (bbTick != psLastBestBidTick || baTick != psLastBestAskTick) {
                psBboBidStacked = 0L;
                psBboBidPulled  = 0L;
                psBboAskStacked = 0L;
                psBboAskPulled  = 0L;
                psBboLastResetMs = nowMs;
                psBboResetCount++;
                psLastBestBidTick = bbTick;
                psLastBestAskTick = baTick;
            }
        }
        // Feed OFI tracker on every BBO event (price OR size change). FlowRegime
        // internally diffs against its own prev state so passing each tick is fine.
        if (bbTick != Integer.MIN_VALUE && baTick != Integer.MIN_VALUE) {
            flowRegime.onBboChange(bbTick, bbSize, baTick, baSize, nowMs);
        }
    }

    /**
     * Credit a pull/stack event from MBO. kind: 0=bidStack, 1=bidPull, 2=askStack, 3=askPull.
     * Only counts if priceTick is within ±PS_DEPTH_LEVELS of the same-side BBO.
     * Updates both BBO-reset accumulator and per-minute time buckets.
     */
    private void creditPullStackFromMbo(boolean isBid, int priceTick, byte kind, long absSize, long nowMs) {
        if (absSize <= 0) return;
        int reference;
        synchronized (depthLock) {
            if (isBid) reference = bids.isEmpty() ? Integer.MIN_VALUE : bids.firstKey();
            else       reference = asks.isEmpty() ? Integer.MIN_VALUE : asks.firstKey();
        }
        if (reference == Integer.MIN_VALUE) return;
        if (Math.abs(priceTick - reference) > PS_DEPTH_LEVELS) return;
        synchronized (pullStackLock) {
            // BBO-column accumulator
            switch (kind) {
                case 0: psBboBidStacked += absSize; break;
                case 1: psBboBidPulled  += absSize; break;
                case 2: psBboAskStacked += absSize; break;
                case 3: psBboAskPulled  += absSize; break;
            }
            // Per-minute bucket
            if (psFirstEventMs == 0L) psFirstEventMs = nowMs;
            long minuteStart = (nowMs / 60_000L) * 60_000L;
            if (psCurrentBucket == null || psCurrentBucket.minuteStartMs != minuteStart) {
                if (psCurrentBucket != null) {
                    psHistory.addLast(psCurrentBucket);
                    while (psHistory.size() > 16) psHistory.pollFirst();
                }
                psCurrentBucket = new PsMinuteBucket(minuteStart);
            }
            switch (kind) {
                case 0: psCurrentBucket.bidStacked += absSize; break;
                case 1: psCurrentBucket.bidPulled  += absSize; break;
                case 2: psCurrentBucket.askStacked += absSize; break;
                case 3: psCurrentBucket.askPulled  += absSize; break;
            }
        }
    }

    /**
     * Pull/Stack accounting. Compares this depth update against the last-known
     * size for the same (side, priceTick), and:
     *   - positive delta → STACK on that side
     *   - negative delta → PULL on that side UNLESS a trade printed at this
     *     priceTick within {@code PS_HIT_SUPPRESS_MS} (then it was a hit, not a pull)
     * Only counts events within ±{@code PS_DEPTH_LEVELS} of the current BBO.
     * BBO-reset meter zeroes whenever best bid or best ask price changes.
     */
    private void updatePullStack(boolean isBid, int priceTick, int prevSize, int newSize) {
        long delta = (long) newSize - (long) prevSize;
        long nowMs = nowMs();
        // Snapshot current BBO ticks to compute the depth-band and detect BBO change.
        int bbTick, baTick;
        synchronized (depthLock) {
            bbTick = bids.isEmpty() ? Integer.MIN_VALUE : bids.firstKey();
            baTick = asks.isEmpty() ? Integer.MIN_VALUE : asks.firstKey();
        }
        synchronized (pullStackLock) {
            // BBO-reset trigger
            if (bbTick != psLastBestBidTick || baTick != psLastBestAskTick) {
                psBboBidStacked = 0L;
                psBboBidPulled  = 0L;
                psBboAskStacked = 0L;
                psBboAskPulled  = 0L;
                psBboLastResetMs = nowMs;
                psBboResetCount++;
                psLastBestBidTick = bbTick;
                psLastBestAskTick = baTick;
            }
            if (delta == 0) {
                // No change. Still record last-known size to be safe.
                (isBid ? lastBidSize : lastAskSize).put(priceTick, newSize);
                return;
            }
            // Range filter: must be within ±PS_DEPTH_LEVELS of the relevant BBO side.
            int reference = isBid ? bbTick : baTick;
            if (reference == Integer.MIN_VALUE) {
                (isBid ? lastBidSize : lastAskSize).put(priceTick, newSize);
                return;
            }
            if (Math.abs(priceTick - reference) > PS_DEPTH_LEVELS) {
                (isBid ? lastBidSize : lastAskSize).put(priceTick, newSize);
                return;
            }
            // Distinguish pull from hit: if a trade printed at this tick within the
            // suppression window, the reduction was a fill, not a pull.
            byte kind;
            long absDelta = Math.abs(delta);
            if (delta > 0) {
                kind = isBid ? (byte)0 : (byte)2;   // STACK
            } else {
                Long lastTradeMs = lastTradeMsByTick.get(priceTick);
                boolean wasHit = lastTradeMs != null && (nowMs - lastTradeMs) <= PS_HIT_SUPPRESS_MS;
                if (wasHit) {
                    (isBid ? lastBidSize : lastAskSize).put(priceTick, newSize);
                    return;  // it was a hit, not a pull — don't count
                }
                kind = isBid ? (byte)1 : (byte)3;   // PULL
            }
            // Update BBO-reset accumulator
            switch (kind) {
                case 0: psBboBidStacked += absDelta; break;
                case 1: psBboBidPulled  += absDelta; break;
                case 2: psBboAskStacked += absDelta; break;
                case 3: psBboAskPulled  += absDelta; break;
            }
            // Update per-minute time-bucket accumulators
            if (psFirstEventMs == 0L) psFirstEventMs = nowMs;
            long minuteStart = (nowMs / 60_000L) * 60_000L;
            if (psCurrentBucket == null || psCurrentBucket.minuteStartMs != minuteStart) {
                if (psCurrentBucket != null) {
                    psHistory.addLast(psCurrentBucket);
                    // Keep 16 minutes (15-min window + one in-progress)
                    while (psHistory.size() > 16) psHistory.pollFirst();
                }
                psCurrentBucket = new PsMinuteBucket(minuteStart);
            }
            switch (kind) {
                case 0: psCurrentBucket.bidStacked += absDelta; break;
                case 1: psCurrentBucket.bidPulled  += absDelta; break;
                case 2: psCurrentBucket.askStacked += absDelta; break;
                case 3: psCurrentBucket.askPulled  += absDelta; break;
            }
            // Save new size as baseline for the next delta
            (isBid ? lastBidSize : lastAskSize).put(priceTick, newSize);
        }
    }

    /** Test-only overload: legacy boolean-aggressor signature. */
    public void onTrade(double rawPrice, int size, boolean bidAggressor) {
        onTrade(rawPrice, size, new TradeInfo(false, bidAggressor));
    }

    public void onTrade(double rawPrice, int size, TradeInfo info) {
        tradeEvents.increment();
        boolean bidAggressor = info.isBidAggressor;
        double price = rawPrice * pips;
        this.lastTradePrice = price;
        long nowMs = nowMs();
        TradeRecord record = new TradeRecord(price, size, bidAggressor, lastSeenNanos);
        synchronized (tradesLock) {
            if (recentTrades.size() == TRADES_CAPACITY) recentTrades.pollFirst();
            recentTrades.addLast(record);
        }
        // VWAP RTH/ETH accumulators
        synchronized (vwapLock) {
            vwapEth.add(price, size, ethAnchorMs(nowMsForSession()));
            if (isInRthWindow(nowMsForSession())) vwapRth.add(price, size, rthAnchorMs(nowMsForSession()));
            else                       vwapRth.maybeRoll(rthAnchorMs(nowMsForSession()));
        }
        int tick = (int) Math.round(rawPrice);
        // Volume profile RTH/ETH
        synchronized (volProfileLock) {
            vpEth.add(tick, size, bidAggressor, ethAnchorMs(nowMsForSession()));
            if (isInRthWindow(nowMsForSession())) vpRth.add(tick, size, bidAggressor, rthAnchorMs(nowMsForSession()));
            else                       vpRth.maybeRoll(rthAnchorMs(nowMsForSession()));
        }
        // A: trade event for the depth-event history
        synchronized (mboLock) {
            if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
            mboDeltas.addLast(new MboDelta(nowMs, !bidAggressor, tick, (byte)3, size));
        }
        // CVD accumulation — RTH-anchored, resets daily at 08:30 CT.
        synchronized (cvdLock) {
            long anchor = rthAnchorMs(nowMsForSession());
            if (anchor != cvdSessionStartMs) {
                cvdSessionStartMs = anchor;
                cvdValue = 0L;
            }
            if (isInRthWindow(nowMsForSession())) {
                if (bidAggressor) cvdValue -= size;  // sell aggressor
                else              cvdValue += size;  // buy aggressor
            }
        }
        // Flow-regime tracker — feed every print so it can build per-window
        // OFI/CVD/VPT and roll the 30s window when ready. !bidAggressor = buy
        // (lift offer); bidAggressor = sell (hit bid).
        flowRegime.onTrade(price, size, !bidAggressor, nowMs);
        // Tier 2 trackers
        ibTracker.onTrade(price, nowMs);
        avwapTracker.onTrade(price, size, nowMs);
        // Pull/Stack hit suppression: remember when each tick last had a trade,
        // so a subsequent depth-size reduction at the same tick is attributed
        // to a hit (consumed by this trade), not a pull (cancel).
        synchronized (pullStackLock) {
            lastTradeMsByTick.put(tick, nowMs);
            // Cap map size by trimming old entries occasionally.
            if (lastTradeMsByTick.size() > 1000) {
                long cutoff = nowMs - 5 * 60_000L;
                lastTradeMsByTick.entrySet().removeIf(e -> e.getValue() < cutoff);
            }
        }
        // B-icebergs: track passive-side fills per level
        if (info.passiveOrderId != null && !info.passiveOrderId.isEmpty()) {
            recordIcebergFill(tick, info.passiveOrderId, size, nowMs, !bidAggressor);
            // Also bump filledSize on the order if we have its MBO record
            MboOrder o = mboOrders.get(info.passiveOrderId);
            if (o != null) o.filledSize += size;
        }
        // B-stops: check for sweep
        detectStopSweep(price, size, bidAggressor, nowMs);
    }

    // -------- MBO callbacks (per-order book events) --------
    public void onMboSend(String orderId, boolean isBid, int priceTick, int size) {
        mboSendEvents.increment();
        mboAvailable = true;
        long nowMs = nowMs();
        mboOrders.put(orderId, new MboOrder(isBid, priceTick, size, nowMs));
        synchronized (mboLock) {
            if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
            mboDeltas.addLast(new MboDelta(nowMs, isBid, priceTick, (byte)0, size));
        }
        // Pull/Stack: an order being placed on the book is a STACK event.
        creditPullStackFromMbo(isBid, priceTick, (byte)(isBid ? 0 : 2), size, nowMs);
    }

    public void onMboReplace(String orderId, int newPriceTick, int newSize) {
        mboReplaceEvents.increment();
        mboAvailable = true;
        long nowMs = nowMs();
        MboOrder o = mboOrders.get(orderId);
        if (o != null) {
            int sizeDelta = newSize - o.currentSize;
            boolean isBid = o.isBid;
            o.priceTick = newPriceTick;
            o.currentSize = newSize;
            if (newSize > o.peakSize) o.peakSize = newSize;
            synchronized (mboLock) {
                if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
                mboDeltas.addLast(new MboDelta(nowMs, isBid, newPriceTick, (byte)2, sizeDelta));
            }
            // Pull/Stack: replace with a size change → STACK (size grew) or PULL (shrank).
            if (sizeDelta > 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 0 : 2), sizeDelta, nowMs);
            } else if (sizeDelta < 0) {
                creditPullStackFromMbo(isBid, newPriceTick, (byte)(isBid ? 1 : 3), -sizeDelta, nowMs);
            }
        }
    }

    public void onMboCancel(String orderId) {
        mboCancelEvents.increment();
        mboAvailable = true;
        long nowMs = nowMs();
        MboOrder o = mboOrders.remove(orderId);
        if (o == null) return;
        synchronized (mboLock) {
            if (mboDeltas.size() == MBO_DELTA_CAPACITY) mboDeltas.pollFirst();
            mboDeltas.addLast(new MboDelta(nowMs, o.isBid, o.priceTick, (byte)1, o.currentSize));
        }
        // Pull/Stack: cancel = order removed from book. Pull amount = remaining
        // (un-filled) size at time of cancel. If order was fully filled, no pull.
        long pullAmount = Math.max(0L, (long) o.currentSize - o.filledSize);
        if (pullAmount > 0) {
            creditPullStackFromMbo(o.isBid, o.priceTick, (byte)(o.isBid ? 1 : 3), pullAmount, nowMs);
        }
        // B-spoofing (adaptive): canceled-with-0-filled order whose peak size is an
        // outlier relative to recent cancel-population statistics (mean + 2σ).
        // Welford-style EWMA tracks mean + variance of cancel sizes.
        if (o.filledSize == 0L && (nowMs - o.sentMs) >= SPOOF_MIN_AGE_MS) {
            long adaptiveSpoof;
            double baseMean, baseStd;
            synchronized (adaptiveLock) {
                ewmaSpoofSamples++;
                double alpha = 1.0 / Math.min(ewmaSpoofSamples, 100L);
                double delta = o.peakSize - ewmaSpoofMean;
                ewmaSpoofMean += alpha * delta;
                ewmaSpoofSqDev = (1 - alpha) * ewmaSpoofSqDev + alpha * (delta * (o.peakSize - ewmaSpoofMean));
                baseMean = ewmaSpoofMean;
                baseStd = Math.sqrt(Math.max(ewmaSpoofSqDev, 0.0));
                adaptiveSpoof = (long) Math.max(SPOOF_FLOOR, baseMean + 2.0 * baseStd);
            }
            if (o.peakSize >= adaptiveSpoof) {
                double price = o.priceTick * pips;
                String reason = String.format("size %d %s canceled after %dms, 0 filled (threshold %d = μ%.0f + 2σ%.0f)",
                        o.peakSize, o.isBid ? "BID" : "ASK", nowMs - o.sentMs,
                        adaptiveSpoof, baseMean, baseStd);
                emitMicroEvent(MicrostructureEvent.Kind.SPOOF, nowMs, price, o.peakSize, o.isBid, reason);
            }
        }
    }

    /** B-iceberg detection (adaptive): a level whose cumulative traded volume
     *  through ≥3 distinct passive-order IDs exceeds 2.5× the EWMA of recent
     *  per-level traded volumes. Threshold adapts to the market regime — quiet
     *  markets get lower thresholds, busy markets get higher. */
    private void recordIcebergFill(int tick, String passiveOrderId, int size, long nowMs, boolean restingIsBid) {
        boolean shouldEvictExpired = false;
        long cumulativeAtFire = 0L;
        int refillsAtFire = 0;
        long adaptiveThreshold = 0L;
        boolean fired = false;
        synchronized (icebergLock) {
            IcebergState st = icebergByTick.computeIfAbsent(tick, k -> new IcebergState());
            // New passive order ID at same level = a refill
            if (!passiveOrderId.equals(st.currentPassiveId)) {
                st.refills++;
                st.currentPassiveId = passiveOrderId;
            }
            st.lastTradeMs = nowMs;
            st.cumulativeTraded += size;
            // Adaptive threshold: max(floor, 2.5× rolling EWMA of per-level traded volume)
            long threshold;
            synchronized (adaptiveLock) {
                threshold = (long) Math.max(ICEBERG_FLOOR, ICEBERG_MULTIPLE * ewmaIcebergMean);
            }
            if (st.refills >= ICEBERG_MIN_REFILLS && st.cumulativeTraded >= threshold) {
                fired = true;
                cumulativeAtFire = st.cumulativeTraded;
                refillsAtFire = st.refills;
                adaptiveThreshold = threshold;
                // Record the fired value into the rolling baseline so the threshold adapts upward
                recordIcebergBaseline(st.cumulativeTraded);
                // Reset for fresh refill detection at this level
                st.refills = 0;
                st.cumulativeTraded = 0;
            }
            shouldEvictExpired = icebergByTick.size() > 200;
        }
        if (fired) {
            double price = tick * pips;
            String reason = String.format("%d contracts through %d refills at %s (threshold %d, baseline %.0f)",
                    cumulativeAtFire, refillsAtFire, restingIsBid ? "BID" : "ASK",
                    adaptiveThreshold, currentIcebergBaseline());
            emitMicroEvent(MicrostructureEvent.Kind.ICEBERG, nowMs, price, cumulativeAtFire, restingIsBid, reason);
        }
        // Evict levels with no trades in 60s. Their final cumulative also informs the baseline.
        if (shouldEvictExpired) {
            long cutoff = nowMs - 60_000L;
            synchronized (icebergLock) {
                Iterator<Map.Entry<Integer, IcebergState>> it = icebergByTick.entrySet().iterator();
                while (it.hasNext()) {
                    Map.Entry<Integer, IcebergState> e = it.next();
                    if (e.getValue().lastTradeMs < cutoff) {
                        if (e.getValue().cumulativeTraded > 0) {
                            recordIcebergBaseline(e.getValue().cumulativeTraded);
                        }
                        it.remove();
                    }
                }
            }
        }
    }

    private void recordIcebergBaseline(long value) {
        synchronized (adaptiveLock) {
            // EWMA with α = 1/min(samples+1, 50) — first 50 samples form the baseline, then exponential decay.
            ewmaIcebergSamples++;
            double alpha = 1.0 / Math.min(ewmaIcebergSamples, 50L);
            ewmaIcebergMean += alpha * (value - ewmaIcebergMean);
        }
    }
    private double currentIcebergBaseline() {
        synchronized (adaptiveLock) { return ewmaIcebergMean; }
    }

    /** B-stop-sweep: aggressor volume burst across a magnet level in 5s. */
    private void detectStopSweep(double price, int size, boolean bidAggressor, long nowMs) {
        double[] magnets = magnetLevels;
        if (magnets.length == 0) return;
        // Sum aggressor volume in last 5s
        long windowStart = nowMs - STOP_BURST_WINDOW_MS;
        long aggroVol = 0;
        double minPx = price, maxPx = price;
        synchronized (tradesLock) {
            Iterator<TradeRecord> it = recentTrades.descendingIterator();
            while (it.hasNext()) {
                TradeRecord t = it.next();
                long tMs = t.nanos() > 0 ? t.nanos() / 1_000_000L : 0;
                if (tMs < windowStart) break;
                aggroVol += t.size();
                if (t.price() < minPx) minPx = t.price();
                if (t.price() > maxPx) maxPx = t.price();
            }
        }
        // Adaptive threshold: update rolling stats (every trade samples a fresh 5s burst sum),
        // then fire only when burst > μ + 2σ AND floor.
        long adaptiveStop;
        double baseMean, baseStd;
        synchronized (adaptiveLock) {
            ewmaStopSamples++;
            double alpha = 1.0 / Math.min(ewmaStopSamples, 200L);
            double delta = aggroVol - ewmaStopMean;
            ewmaStopMean += alpha * delta;
            ewmaStopSqDev = (1 - alpha) * ewmaStopSqDev + alpha * (delta * (aggroVol - ewmaStopMean));
            baseMean = ewmaStopMean;
            baseStd = Math.sqrt(Math.max(ewmaStopSqDev, 0.0));
            adaptiveStop = (long) Math.max(STOP_FLOOR, baseMean + 2.0 * baseStd);
        }
        if (aggroVol < adaptiveStop) return;
        // Did the window cross any magnet level?
        for (double m : magnets) {
            if (m >= minPx && m <= maxPx) {
                String reason = String.format("%d contracts swept %.2f in 5s (threshold %d = μ%.0f + 2σ%.0f)",
                        aggroVol, m, adaptiveStop, baseMean, baseStd);
                emitMicroEvent(MicrostructureEvent.Kind.STOP_SWEEP, nowMs, m, aggroVol, !bidAggressor, reason);
                return;
            }
        }
    }

    private void emitMicroEvent(MicrostructureEvent.Kind kind, long timeMs, double price, long size, boolean isBid, String reason) {
        MicrostructureEvent ev = new MicrostructureEvent(kind, timeMs, price, size, isBid, reason);
        synchronized (microEventLock) {
            if (microEvents.size() == MICRO_EVENT_CAPACITY) microEvents.pollFirst();
            microEvents.addLast(ev);
        }
    }

    private long nowMs() {
        long n = lastSeenNanos;
        return n > 0L ? n / 1_000_000L : System.currentTimeMillis();
    }

    /** Wall-clock-preferred timestamp for session-anchor calculations.
     *
     * <p>BM-API's {@code onTimestamp(long nanos)} provides a feed-clock value
     * whose epoch is feed-specific (Rithmic, for instance, publishes a counter
     * that does not align with Unix epoch nanos). Treating that value as Unix
     * epoch nanos for RTH/ETH anchor calculations causes the session anchor
     * to drift away from real-world wall clock, so accumulators never roll
     * over and RTH samples are dropped.</p>
     *
     * <p>This helper prefers wall clock when the feed-derived value differs
     * from wall by more than a day (a sign the feed clock isn't Unix epoch).
     * For playback within ~1 day of wall, the feed clock is honored so
     * historical replays anchor on the playback date correctly.</p>
     */
    private long nowMsForSession() {
        long wall = System.currentTimeMillis();
        long n = lastSeenNanos;
        if (n <= 0L) return wall;
        long candidate = n / 1_000_000L;
        if (Math.abs(candidate - wall) < 86_400_000L) return candidate; // within 24h: trust feed
        return wall;
    }

    /** EWMA update for LT liquidity. Sums the top {@link #LT_DEPTH_LEVELS}
     *  bid/ask levels (25 ticks for NQ — wider than pull/stack since the LT meter
     *  measures the full liquidity pad around price, not just inside-book activity). */
    private void updateLtLiquidityIfBboChanged() {
        int bb = 0, ba = 0;
        synchronized (depthLock) {
            int n = 0;
            for (Map.Entry<Integer, Integer> e : bids.entrySet()) {
                if (n++ >= LT_DEPTH_LEVELS) break;
                bb += e.getValue();
            }
            n = 0;
            for (Map.Entry<Integer, Integer> e : asks.entrySet()) {
                if (n++ >= LT_DEPTH_LEVELS) break;
                ba += e.getValue();
            }
        }
        long nowMs = nowMs();
        synchronized (ltLock) {
            if (ltLastUpdateMs == 0L) {
                ltBidEwma = bb;
                ltAskEwma = ba;
                ltLastUpdateMs = nowMs;
                return;
            }
            long dt = nowMs - ltLastUpdateMs;
            if (dt <= 0) {
                // Just take latest sample without decay
                ltBidEwma = bb;
                ltAskEwma = ba;
                return;
            }
            // alpha = 1 - exp(-dt/halfLife * ln2) — exponentially weighted
            double alpha = 1.0 - Math.exp(-((double) dt) / LT_HALFLIFE_MS * Math.log(2));
            if (alpha > 1.0) alpha = 1.0;
            ltBidEwma = ltBidEwma + alpha * (bb - ltBidEwma);
            ltAskEwma = ltAskEwma + alpha * (ba - ltAskEwma);
            ltLastUpdateMs = nowMs;
        }
    }

    // ----------------- Session anchor helpers (RTH / ETH) -----------------
    private static boolean isInRthWindow(long nowMs) {
        ZonedDateTime now = Instant.ofEpochMilli(nowMs).atZone(CT);
        LocalTime lt = now.toLocalTime();
        return !lt.isBefore(RTH_OPEN) && lt.isBefore(RTH_CLOSE);
    }
    private static long rthAnchorMs(long nowMs) {
        ZonedDateTime now = Instant.ofEpochMilli(nowMs).atZone(CT);
        ZonedDateTime open = now.toLocalDate().atTime(RTH_OPEN).atZone(CT);
        if (now.isBefore(open)) open = open.minusDays(1);
        return open.toInstant().toEpochMilli();
    }
    private static long ethAnchorMs(long nowMs) {
        ZonedDateTime now = Instant.ofEpochMilli(nowMs).atZone(CT);
        ZonedDateTime open = now.toLocalDate().atTime(ETH_OPEN).atZone(CT);
        if (now.isBefore(open)) open = open.minusDays(1);
        return open.toInstant().toEpochMilli();
    }

    public VwapSnapshot vwapEthSnapshot() { synchronized (vwapLock) { return vwapEth.snapshot(); } }
    public VwapSnapshot vwapRthSnapshot() { synchronized (vwapLock) { return vwapRth.snapshot(); } }
    public VwapSnapshot vwapSnapshot() { return vwapRthSnapshot(); }

    public VwapSlopeTracker.VwapSlopeSnapshot vwapSlopeSnapshot() {
        // Push the latest RTH VWAP into the slope tracker
        VwapSnapshot v = vwapRthSnapshot();
        if (v.vwap() > 0) vwapSlope.onVwapTick(v.vwap(), System.currentTimeMillis());
        return vwapSlope.snapshot();
    }

    public AnchoredVwapTracker.AnchoredVwapSnapshot anchoredVwapSnapshot() {
        return avwapTracker.snapshot();
    }

    public InitialBalanceTracker.InitialBalanceSnapshot ibSnapshot() {
        return ibTracker.snapshot();
    }

    void accumulateVwap(double price, int size, long nowNanos) {
        long nowMs = (nowNanos > 0L) ? (nowNanos / 1_000_000L) : System.currentTimeMillis();
        synchronized (vwapLock) {
            vwapEth.add(price, size, ethAnchorMs(nowMs));
            if (isInRthWindow(nowMs)) vwapRth.add(price, size, rthAnchorMs(nowMs));
            else                       vwapRth.maybeRoll(rthAnchorMs(nowMs));
        }
    }

    public VolumeProfileSnapshot volumeProfileEthSnapshot() {
        synchronized (volProfileLock) { return vpEth.snapshot(pips); }
    }
    public VolumeProfileSnapshot volumeProfileRthSnapshot() {
        synchronized (volProfileLock) { return vpRth.snapshot(pips); }
    }
    public VolumeProfileSnapshot volumeProfileSnapshot() { return volumeProfileRthSnapshot(); }

    // ----------------- D: tape buckets snapshot -----------------
    public TapeBucketsSnapshot tapeBucketsSnapshot() {
        long nowMs = nowMs();
        long w30s = nowMs - 30_000L;
        long w5m  = nowMs - 300_000L;
        long[] b30s = new long[TAPE_BUCKETS.length * 3];   // buy/sell/count per bucket
        long[] b5m  = new long[TAPE_BUCKETS.length * 3];
        synchronized (tradesLock) {
            Iterator<TradeRecord> it = recentTrades.descendingIterator();
            while (it.hasNext()) {
                TradeRecord t = it.next();
                long tMs = t.nanos() > 0 ? t.nanos() / 1_000_000L : 0;
                if (tMs < w5m) break;
                int bi = bucketIndex(t.size());
                if (bi < 0) continue;
                boolean is5m = true, is30s = tMs >= w30s;
                int base = bi * 3;
                if (is30s) {
                    if (t.bidAggressor()) b30s[base+1] += t.size(); else b30s[base] += t.size();
                    b30s[base+2]++;
                }
                if (is5m) {
                    if (t.bidAggressor()) b5m[base+1] += t.size(); else b5m[base] += t.size();
                    b5m[base+2]++;
                }
            }
        }
        ArrayList<TapeBucketsSnapshot.Bucket> out = new ArrayList<>(TAPE_BUCKETS.length);
        for (int i = 0; i < TAPE_BUCKETS.length; i++) {
            int base = i * 3;
            out.add(new TapeBucketsSnapshot.Bucket(
                    TAPE_BUCKET_LABELS[i], TAPE_BUCKETS[i][0], TAPE_BUCKETS[i][1],
                    b30s[base], b30s[base+1], b30s[base+2],
                    b5m[base],  b5m[base+1],  b5m[base+2]));
        }
        return new TapeBucketsSnapshot(lastSeenNanos, out);
    }
    private static int bucketIndex(int size) {
        for (int i = 0; i < TAPE_BUCKETS.length; i++) {
            long min = TAPE_BUCKETS[i][0], max = TAPE_BUCKETS[i][1];
            if (size >= min && (max == -1 || size <= max)) return i;
        }
        return -1;
    }

    // ----------------- C: LT liquidity snapshot -----------------
    public LtLiquiditySnapshot ltLiquiditySnapshot() {
        int bbSize = 0, baSize = 0;
        double bbPrice = Double.NaN, baPrice = Double.NaN;
        synchronized (depthLock) {
            if (!bids.isEmpty()) {
                Map.Entry<Integer, Integer> bb = bids.firstEntry();
                bbSize = bb.getValue();
                bbPrice = bb.getKey() * pips;
            }
            if (!asks.isEmpty()) {
                Map.Entry<Integer, Integer> ba = asks.firstEntry();
                baSize = ba.getValue();
                baPrice = ba.getKey() * pips;
            }
        }
        double ltBid, ltAsk;
        synchronized (ltLock) { ltBid = ltBidEwma; ltAsk = ltAskEwma; }
        return new LtLiquiditySnapshot(lastSeenNanos, bbPrice, baPrice, bbSize, baSize, ltBid, ltAsk, LT_HALFLIFE_MS);
    }

    // ----------------- A: book dynamics multi-TF snapshot -----------------
    public BookDynamicsSnapshot bookDynamicsSnapshot(int topN) {
        if (topN <= 0) topN = 12;
        long nowMs = nowMs();
        long w1m  = nowMs - 60_000L;
        long w3m  = nowMs - 180_000L;
        long w15m = nowMs - 900_000L;
        // Per (priceTick, isBid) tally — use long key: priceTick<<1 | isBid
        Map<Long, long[]> byLevel = new java.util.HashMap<>();   // 9 longs: s1,p1,h1,s3,p3,h3,s15,p15,h15
        synchronized (mboLock) {
            Iterator<MboDelta> it = mboDeltas.descendingIterator();
            while (it.hasNext()) {
                MboDelta d = it.next();
                if (d.timeMs < w15m) break;
                long key = (((long) d.priceTick) << 1) | (d.isBid ? 1 : 0);
                long[] e = byLevel.computeIfAbsent(key, k -> new long[9]);
                // Determine kind bucket: 0=SEND, 1=CANCEL/REPLACE-decrease, 2=REPLACE-increase, 3=TRADE
                long size = Math.abs(d.size);
                if (d.kind == 0) {                      // SEND → stacked
                    if (d.timeMs >= w1m)  e[0] += size;
                    if (d.timeMs >= w3m)  e[3] += size;
                    if (d.timeMs >= w15m) e[6] += size;
                } else if (d.kind == 1) {               // CANCEL → pulled
                    if (d.timeMs >= w1m)  e[1] += size;
                    if (d.timeMs >= w3m)  e[4] += size;
                    if (d.timeMs >= w15m) e[7] += size;
                } else if (d.kind == 3) {               // TRADE → hit
                    if (d.timeMs >= w1m)  e[2] += size;
                    if (d.timeMs >= w3m)  e[5] += size;
                    if (d.timeMs >= w15m) e[8] += size;
                }
                // REPLACE: sign tells us add vs remove
                else if (d.kind == 2) {
                    if (d.size >= 0) {
                        if (d.timeMs >= w1m)  e[0] += size;
                        if (d.timeMs >= w3m)  e[3] += size;
                        if (d.timeMs >= w15m) e[6] += size;
                    } else {
                        if (d.timeMs >= w1m)  e[1] += size;
                        if (d.timeMs >= w3m)  e[4] += size;
                        if (d.timeMs >= w15m) e[7] += size;
                    }
                }
            }
        }
        ArrayList<BookDynamicsSnapshot.Level> levels = new ArrayList<>(byLevel.size());
        for (Map.Entry<Long, long[]> e : byLevel.entrySet()) {
            long key = e.getKey();
            int tick = (int) (key >> 1);
            boolean isBid = (key & 1) == 1;
            long[] v = e.getValue();
            levels.add(new BookDynamicsSnapshot.Level(
                    tick * pips, isBid,
                    v[0], v[1], v[2],
                    v[3], v[4], v[5],
                    v[6], v[7], v[8]));
        }
        // Sort by 1m total activity desc
        levels.sort((a, b) -> Long.compare(b.stacked1m + b.pulled1m + b.hit1m,
                                           a.stacked1m + a.pulled1m + a.hit1m));
        if (levels.size() > topN) levels = new ArrayList<>(levels.subList(0, topN));
        return new BookDynamicsSnapshot(lastSeenNanos, Collections.unmodifiableList(levels), mboAvailable);
    }

    // ----------------- Pull/Stack snapshot (Engineered Analytics style) -----------------
    public PullStackSnapshot pullStackSnapshot() {
        long nowMs = nowMs();
        long w1m = nowMs - 60_000L;
        long w3m = nowMs - 180_000L;
        long w15m = nowMs - 900_000L;
        long bs1m=0, bp1m=0, as1m=0, ap1m=0;
        long bs3m=0, bp3m=0, as3m=0, ap3m=0;
        long bs15=0, bp15=0, as15=0, ap15=0;
        long bboBs, bboBp, bboAs, bboAp, bboLastReset, bboResetCount;
        int bbTick, baTick, bbSize=0, baSize=0;
        synchronized (pullStackLock) {
            bboBs = psBboBidStacked;
            bboBp = psBboBidPulled;
            bboAs = psBboAskStacked;
            bboAp = psBboAskPulled;
            bboLastReset = psBboLastResetMs;
            bboResetCount = psBboResetCount;
            // Sum per-minute buckets into 1m/3m/15m windows. The current
            // (in-progress) bucket is read first, then walk history backwards.
            java.util.function.BiConsumer<PsMinuteBucket, Integer> credit = (b, scope) -> {
                // scope: 0 = all (15m), 1 = 3m+15m, 2 = 1m+3m+15m
            };
            // Build a flat list: current bucket + history (newest first)
            ArrayList<PsMinuteBucket> all = new ArrayList<>(psHistory.size() + 1);
            if (psCurrentBucket != null) all.add(psCurrentBucket);
            Iterator<PsMinuteBucket> hit = psHistory.descendingIterator();
            while (hit.hasNext()) all.add(hit.next());
            for (PsMinuteBucket b : all) {
                long bucketEnd = b.minuteStartMs + 60_000L;
                // Skip buckets that are entirely older than the 15m horizon
                if (bucketEnd <= w15m) continue;
                // 15m window (everything we kept)
                bs15 += b.bidStacked; bp15 += b.bidPulled; as15 += b.askStacked; ap15 += b.askPulled;
                // 3m: any bucket whose end is past w3m
                if (bucketEnd > w3m) {
                    bs3m += b.bidStacked; bp3m += b.bidPulled; as3m += b.askStacked; ap3m += b.askPulled;
                }
                // 1m: any bucket whose end is past w1m (since minute alignment,
                // this is the current and previous bucket at most)
                if (bucketEnd > w1m) {
                    bs1m += b.bidStacked; bp1m += b.bidPulled; as1m += b.askStacked; ap1m += b.askPulled;
                }
            }
        }
        synchronized (depthLock) {
            if (!bids.isEmpty()) { Map.Entry<Integer, Integer> b = bids.firstEntry(); bbTick = b.getKey(); bbSize = b.getValue(); } else bbTick = Integer.MIN_VALUE;
            if (!asks.isEmpty()) { Map.Entry<Integer, Integer> a = asks.firstEntry(); baTick = a.getKey(); baSize = a.getValue(); } else baTick = Integer.MIN_VALUE;
        }
        double bbPrice = bbTick == Integer.MIN_VALUE ? Double.NaN : bbTick * pips;
        double baPrice = baTick == Integer.MIN_VALUE ? Double.NaN : baTick * pips;
        // Warm-up gating: how long have we been running?
        long psStart;
        synchronized (pullStackLock) { psStart = psFirstEventMs; }
        long runMs = psStart == 0L ? 0L : (nowMs - psStart);
        // BBO column: elapsed since last BBO reset
        long bboElapsedSec = bboLastReset == 0L ? Math.max(runMs / 1000L, 1L) : Math.max((nowMs - bboLastReset) / 1000L, 1L);
        long sec1m  = Math.min(runMs / 1000L, 60L);
        long sec3m  = Math.min(runMs / 1000L, 180L);
        long sec15m = Math.min(runMs / 1000L, 900L);
        String s1m  = sec1m  >= 60L  ? "FULL" : "WARMING";
        String s3m  = sec3m  >= 180L ? "FULL" : "WARMING";
        String s15m = sec15m >= 900L ? "FULL" : "WARMING";
        ArrayList<PullStackSnapshot.Window> windows = new ArrayList<>(4);
        PullStackSnapshot.Window winBbo = new PullStackSnapshot.Window("BBO", "BBO_RESET", 0L, bboResetCount, bboLastReset,
                bboBs, bboBp, bboAs, bboAp, "LIVE", bboElapsedSec);
        PullStackSnapshot.Window win1m  = new PullStackSnapshot.Window("1m",  "TIME", 60_000L,  0L, nowMs - 60_000L,  bs1m,  bp1m,  as1m,  ap1m,  s1m,  sec1m);
        PullStackSnapshot.Window win3m  = new PullStackSnapshot.Window("3m",  "TIME", 180_000L, 0L, nowMs - 180_000L, bs3m,  bp3m,  as3m,  ap3m,  s3m,  sec3m);
        PullStackSnapshot.Window win15m = new PullStackSnapshot.Window("15m", "TIME", 900_000L, 0L, nowMs - 900_000L, bs15,  bp15,  as15,  ap15,  s15m, sec15m);
        // Compute pulls-weighted score for each window and update its EWMA stats
        scoreWindow(winBbo, true);
        scoreWindow(win1m,  false);
        scoreWindow(win3m,  false);
        scoreWindow(win15m, false);
        windows.add(winBbo); windows.add(win1m); windows.add(win3m); windows.add(win15m);

        // Aggregate Z = weighted sum across windows (recency-favored)
        double aggZ = PS_W_BBO * winBbo.zScore
                    + PS_W_1M  * win1m.zScore
                    + PS_W_3M  * win3m.zScore
                    + PS_W_15M * win15m.zScore;

        // Classify into bias state
        String aggBias;
        double absZ = Math.abs(aggZ);
        if      (absZ < 0.5) aggBias = "QUIET";
        else if (absZ < 1.0) aggBias = aggZ > 0 ? "LEAN_BULL"    : "LEAN_BEAR";
        else if (absZ < 2.0) aggBias = aggZ > 0 ? "STRONG_BULL"  : "STRONG_BEAR";
        else if (absZ < 3.0) aggBias = aggZ > 0 ? "EXTREME_BULL" : "EXTREME_BEAR";
        else                 aggBias = aggZ > 0 ? "BLOWOFF_BULL" : "BLOWOFF_BEAR";

        // Rotation detection: short-term (BBO) sign differs from long-term (15m) AND BBO is significant
        String rot = "NONE";
        if (Math.abs(winBbo.zScore) >= 1.0
                && Math.signum(winBbo.zScore) != 0
                && Math.signum(win15m.zScore) != 0
                && Math.signum(winBbo.zScore) != Math.signum(win15m.zScore)) {
            rot = winBbo.zScore > 0 ? "ROTATION_UP" : "ROTATION_DN";
        }
        PullStackSnapshot snap = new PullStackSnapshot(lastSeenNanos, bbPrice, baPrice, bbSize, baSize, PS_DEPTH_LEVELS, Collections.unmodifiableList(windows));
        snap.aggregateZ = aggZ;
        snap.aggregateBias = aggBias;
        snap.rotation = rot;
        return snap;
    }

    /**
     * Compute the per-window pull/stack score, update its EWMA stats, and stamp z-score
     * + raw fields onto the Window in place. Score weighs pulls 1.3× stacks.
     */
    private void scoreWindow(PullStackSnapshot.Window w, boolean isBbo) {
        double bidNet = w.bidStacked - PS_PULL_WEIGHT * w.bidPulled;
        double askNet = w.askStacked - PS_PULL_WEIGHT * w.askPulled;
        double score = bidNet - askNet;
        w.score = score;
        // Update EWMA stats (Welford) for this window
        double mean, m2;
        long n;
        synchronized (psStatsLock) {
            // pick the right stats slot
            if (isBbo) {
                psBboN++;  double alpha = 1.0 / Math.min(psBboN, 60L);
                double delta = score - psBboMean;
                psBboMean += alpha * delta;
                psBboM2 = (1 - alpha) * psBboM2 + alpha * (delta * (score - psBboMean));
                mean = psBboMean; m2 = psBboM2; n = psBboN;
            } else if ("1m".equals(w.label)) {
                ps1mN++; double alpha = 1.0 / Math.min(ps1mN, 60L);
                double delta = score - ps1mMean;
                ps1mMean += alpha * delta;
                ps1mM2 = (1 - alpha) * ps1mM2 + alpha * (delta * (score - ps1mMean));
                mean = ps1mMean; m2 = ps1mM2; n = ps1mN;
            } else if ("3m".equals(w.label)) {
                ps3mN++; double alpha = 1.0 / Math.min(ps3mN, 60L);
                double delta = score - ps3mMean;
                ps3mMean += alpha * delta;
                ps3mM2 = (1 - alpha) * ps3mM2 + alpha * (delta * (score - ps3mMean));
                mean = ps3mMean; m2 = ps3mM2; n = ps3mN;
            } else {
                ps15mN++; double alpha = 1.0 / Math.min(ps15mN, 60L);
                double delta = score - ps15mMean;
                ps15mMean += alpha * delta;
                ps15mM2 = (1 - alpha) * ps15mM2 + alpha * (delta * (score - ps15mMean));
                mean = ps15mMean; m2 = ps15mM2; n = ps15mN;
            }
        }
        double std = Math.sqrt(Math.max(m2, 0.0));
        w.mean = mean;
        w.stddev = std;
        // Z-score: floor σ to avoid div-by-zero. While warming up (n < 10), keep z=0
        // so we don't fire fake bias signals from a 1-sample baseline.
        if (n < 10 || std < 1e-6) {
            w.zScore = 0.0;
        } else {
            w.zScore = (score - mean) / std;
        }
    }

    // ----------------- B: microstructure events snapshot -----------------
    public List<MicrostructureEvent> microstructureEvents(int max) {
        if (max <= 0) max = 50;
        synchronized (microEventLock) {
            ArrayList<MicrostructureEvent> out = new ArrayList<>(Math.min(max, microEvents.size()));
            Iterator<MicrostructureEvent> it = microEvents.descendingIterator();
            int n = 0;
            while (it.hasNext() && n < max) { out.add(it.next()); n++; }
            return out;
        }
    }

    // ----------------- existing public API (orders, position, etc.) -----------------
    public void onOrderUpdate(OrderInfoUpdate update) {
        orderEvents.increment();
        lastOrderEventNanos = lastSeenNanos;
        knownOrderSides.put(update.orderId, update.isBuy);
        OrderStatus status = update.status;
        if (status == OrderStatus.WORKING
                || status == OrderStatus.PENDING_SUBMIT
                || status == OrderStatus.PENDING_MODIFY
                || status == OrderStatus.PENDING_CANCEL) {
            workingOrders.put(update.orderId, toRecord(update));
        } else {
            workingOrders.remove(update.orderId);
        }
    }

    public void onExecution(ExecutionInfo exec) {
        executionEvents.increment();
        lastExecutionEventNanos = lastSeenNanos;
        Boolean isBuy = knownOrderSides.get(exec.orderId);
        String side = (isBuy == null) ? "unknown" : (isBuy ? "buy" : "sell");
        long execTimeMs = exec.time;
        if (execTimeMs > 100_000_000_000_000L) execTimeMs = execTimeMs / 1_000_000L;
        RecentExecution rec = new RecentExecution(
                exec.orderId, exec.executionId, side, exec.price, exec.size, execTimeMs, exec.isSimulated);
        synchronized (executionsLock) {
            if (recentExecutions.size() == FILLS_CAPACITY) recentExecutions.pollFirst();
            recentExecutions.addLast(rec);
        }
        int posBefore;
        double realizedBefore;
        synchronized (shadowLock) {
            posBefore = shadowPos;
            realizedBefore = shadowRealizedPnl;
        }
        if (isBuy != null) applyShadowFill(isBuy, exec.price, exec.size);
        int posAfter;
        double realizedAfter, avgAfter;
        synchronized (shadowLock) {
            posAfter = shadowPos;
            realizedAfter = shadowRealizedPnl;
            avgAfter = shadowAvg;
        }
        writeJournalRow(execTimeMs, side, exec.price, exec.size, exec.orderId, exec.isSimulated,
                posBefore, posAfter, avgAfter, realizedAfter - realizedBefore, realizedAfter);
    }

    private void writeJournalRow(long timeMs, String side, double price, int size,
                                 String orderId, boolean simulated,
                                 int posBefore, int posAfter, double avgAfter,
                                 double pnlDelta, double pnlAfter) {
        TradeJournal journal = BridgeRegistry.INSTANCE.journal();
        if (journal == null) return;
        VwapSnapshot vw = vwapRthSnapshot();
        double vwap = vw.vwap();
        double std  = vw.stddev();
        double sigmaDev = (Double.isFinite(vwap) && std > 0) ? (price - vwap) / std : Double.NaN;
        int bestBidTick = Integer.MIN_VALUE, bestAskTick = Integer.MIN_VALUE;
        int bestBidSize = 0, bestAskSize = 0;
        synchronized (depthLock) {
            if (!bids.isEmpty()) { bestBidTick = bids.firstKey(); bestBidSize = bids.firstEntry().getValue(); }
            if (!asks.isEmpty()) { bestAskTick = asks.firstKey(); bestAskSize = asks.firstEntry().getValue(); }
        }
        double mid = Double.NaN, micro = Double.NaN;
        if (bestBidTick != Integer.MIN_VALUE && bestAskTick != Integer.MIN_VALUE) {
            double bb = bestBidTick * pips, aa = bestAskTick * pips;
            mid = (bb + aa) / 2.0;
            int total = bestBidSize + bestAskSize;
            micro = total > 0 ? (bb * bestAskSize + aa * bestBidSize) / (double) total : mid;
        }
        journal.writeRow(new TradeJournal.JournalRow(
                timeMs, alias, side, price, size, orderId, simulated,
                posBefore, posAfter, avgAfter, pnlDelta, pnlAfter,
                vwap, std, sigmaDev, mid, micro));
    }

    private void applyShadowFill(boolean isBuy, double price, int size) {
        if (size <= 0 || !Double.isFinite(price)) return;
        synchronized (shadowLock) {
            int signed = isBuy ? size : -size;
            int newPos = shadowPos + signed;
            boolean sameDirection = shadowPos == 0
                    || (shadowPos > 0 && isBuy)
                    || (shadowPos < 0 && !isBuy);
            if (shadowPos == 0) {
                shadowAvg = price;
            } else if (sameDirection) {
                int oldAbs = Math.abs(shadowPos);
                int newAbs = Math.abs(newPos);
                shadowAvg = (oldAbs * shadowAvg + size * price) / (double) newAbs;
            } else {
                int closingSize = Math.min(size, Math.abs(shadowPos));
                double pnlPerContract = (shadowPos > 0) ? (price - shadowAvg) : (shadowAvg - price);
                shadowRealizedPnl += closingSize * pnlPerContract * multiplier;
                if (newPos == 0) shadowAvg = 0.0;
                else if (Math.signum(newPos) != Math.signum(shadowPos)) shadowAvg = price;
            }
            shadowPos = newPos;
        }
    }

    public void onPositionUpdate(StatusInfo status) {
        positionEvents.increment();
        lastPositionEventNanos = lastSeenNanos;
        this.position = new PositionSnapshot(
                status.position, status.averagePrice, status.unrealizedPnl, status.realizedPnl,
                status.currency, status.volume, status.workingBuys, status.workingSells);
    }

    public void onBalance(BalanceInfo info) {
        balanceEvents.increment();
        List<BalanceCurrency> currencies = new ArrayList<>();
        if (info.balancesInCurrency != null) {
            for (BalanceInCurrency b : info.balancesInCurrency) {
                currencies.add(new BalanceCurrency(
                        b.currency, b.balance, b.realizedPnl, b.unrealizedPnl,
                        b.previousDayBalance, b.netLiquidityValue, b.rateToBase));
            }
        }
        this.balance = new BalanceSnapshot(
                info.accountName == null ? "" : info.accountName,
                Collections.unmodifiableList(currencies));
    }

    private double currentMid() {
        synchronized (depthLock) {
            if (bids.isEmpty() || asks.isEmpty()) return Double.NaN;
            int bb = bids.firstKey();
            int aa = asks.firstKey();
            return ((bb + aa) / 2.0) * pips;
        }
    }

    public PositionSnapshot effectivePositionSnapshot() {
        if (positionEvents.sum() > 0) return position;
        synchronized (shadowLock) {
            double mid = currentMid();
            double uPnl = (Double.isFinite(mid) && shadowPos != 0)
                    ? shadowPos * (mid - shadowAvg) * multiplier : 0.0;
            int workingBuys = 0, workingSells = 0;
            for (WorkingOrderRecord w : workingOrders.values()) {
                if (w.isBuy()) workingBuys  += w.unfilled();
                else           workingSells += w.unfilled();
            }
            return new PositionSnapshot(
                    shadowPos, shadowPos == 0 ? 0.0 : shadowAvg,
                    uPnl, shadowRealizedPnl, "", 0, workingBuys, workingSells);
        }
    }

    public String positionSource() {
        if (positionEvents.sum() > 0) return "broker";
        if (executionEvents.sum() > 0) return "shadow";
        return "flat";
    }

    public Map<String, Long> eventCounts() {
        Map<String, Long> m = new LinkedHashMap<>();
        m.put("depthEvents", depthEvents.sum());
        m.put("tradeEvents", tradeEvents.sum());
        m.put("orderEvents", orderEvents.sum());
        m.put("executionEvents", executionEvents.sum());
        m.put("positionEvents", positionEvents.sum());
        m.put("balanceEvents", balanceEvents.sum());
        m.put("mboSendEvents", mboSendEvents.sum());
        m.put("mboCancelEvents", mboCancelEvents.sum());
        m.put("mboReplaceEvents", mboReplaceEvents.sum());
        m.put("lastOrderEventNanos", lastOrderEventNanos);
        m.put("lastExecutionEventNanos", lastExecutionEventNanos);
        m.put("lastPositionEventNanos", lastPositionEventNanos);
        return m;
    }

    public long orderEventCount()     { return orderEvents.sum(); }
    public long executionEventCount() { return executionEvents.sum(); }
    public long positionEventCount()  { return positionEvents.sum(); }
    public long balanceEventCount()   { return balanceEvents.sum(); }
    public long mboSendEventCount()   { return mboSendEvents.sum(); }
    public long mboCancelEventCount() { return mboCancelEvents.sum(); }

    public OrderbookSnapshot orderbookSnapshot(int depth) {
        if (depth <= 0) depth = 10;
        List<OrderbookLevel> bidLevels = new ArrayList<>(depth);
        List<OrderbookLevel> askLevels = new ArrayList<>(depth);
        synchronized (depthLock) {
            int n = 0;
            for (Map.Entry<Integer, Integer> e : bids.entrySet()) {
                if (n++ >= depth) break;
                bidLevels.add(new OrderbookLevel(e.getKey() * pips, e.getValue()));
            }
            n = 0;
            for (Map.Entry<Integer, Integer> e : asks.entrySet()) {
                if (n++ >= depth) break;
                askLevels.add(new OrderbookLevel(e.getKey() * pips, e.getValue()));
            }
        }
        double bestBid = bidLevels.isEmpty() ? Double.NaN : bidLevels.get(0).price();
        double bestAsk = askLevels.isEmpty() ? Double.NaN : askLevels.get(0).price();
        double mid = Double.NaN, spread = Double.NaN;
        if (!Double.isNaN(bestBid) && !Double.isNaN(bestAsk)) {
            mid = (bestBid + bestAsk) / 2.0;
            spread = bestAsk - bestBid;
        }
        return new OrderbookSnapshot(bidLevels, askLevels, bestBid, bestAsk, mid, spread, lastSeenNanos);
    }

    public List<TradeRecord> recentTradesSnapshot(int count) {
        if (count <= 0) count = 20;
        List<TradeRecord> out = new ArrayList<>(Math.min(count, TRADES_CAPACITY));
        synchronized (tradesLock) {
            Iterator<TradeRecord> it = recentTrades.descendingIterator();
            int n = 0;
            while (it.hasNext() && n < count) { out.add(it.next()); n++; }
        }
        return out;
    }

    public List<RecentExecution> recentExecutionsSnapshot(int count) {
        if (count <= 0) count = 20;
        List<RecentExecution> out = new ArrayList<>(Math.min(count, FILLS_CAPACITY));
        synchronized (executionsLock) {
            Iterator<RecentExecution> it = recentExecutions.descendingIterator();
            int n = 0;
            while (it.hasNext() && n < count) { out.add(it.next()); n++; }
        }
        return out;
    }

    public Collection<WorkingOrderRecord> workingOrdersSnapshot() {
        return Collections.unmodifiableCollection(new ArrayList<>(workingOrders.values()));
    }

    public PositionSnapshot positionSnapshot() { return position; }
    public BalanceSnapshot balanceSnapshot() { return balance; }

    public MomentumSnapshot momentumSnapshot(int[] windowSeconds) {
        if (windowSeconds == null || windowSeconds.length == 0) windowSeconds = new int[]{30, 120, 600};
        long now = lastSeenNanos;
        if (now <= 0L) now = System.nanoTime();
        List<MomentumSnapshot.Window> windows = new ArrayList<>(windowSeconds.length);
        List<TradeRecord> tradesCopy;
        synchronized (tradesLock) { tradesCopy = new ArrayList<>(recentTrades); }
        for (int sec : windowSeconds) {
            long horizonNanos = now - (long) sec * 1_000_000_000L;
            long count = 0L, buy = 0L, sell = 0L;
            for (int i = tradesCopy.size() - 1; i >= 0; i--) {
                TradeRecord t = tradesCopy.get(i);
                if (t.nanos() < horizonNanos) break;
                count++;
                if (t.bidAggressor()) sell += t.size();
                else                  buy  += t.size();
            }
            String label = (sec < 60) ? (sec + "s") : ((sec / 60) + "m");
            windows.add(new MomentumSnapshot.Window(label, sec, count, buy, sell));
        }
        int bestBidTick = Integer.MIN_VALUE, bestAskTick = Integer.MIN_VALUE;
        int bestBidSize = 0, bestAskSize = 0;
        long sumBid5 = 0, sumAsk5 = 0, sumBid25 = 0, sumAsk25 = 0;
        synchronized (depthLock) {
            int idx = 0;
            for (Map.Entry<Integer, Integer> e : bids.entrySet()) {
                if (idx == 0) { bestBidTick = e.getKey(); bestBidSize = e.getValue(); }
                if (idx < 5)  sumBid5  += e.getValue();
                if (idx < 25) sumBid25 += e.getValue();
                if (idx >= 25) break;
                idx++;
            }
            idx = 0;
            for (Map.Entry<Integer, Integer> e : asks.entrySet()) {
                if (idx == 0) { bestAskTick = e.getKey(); bestAskSize = e.getValue(); }
                if (idx < 5)  sumAsk5  += e.getValue();
                if (idx < 25) sumAsk25 += e.getValue();
                if (idx >= 25) break;
                idx++;
            }
        }
        double mid = Double.NaN, micro = Double.NaN, microMidTicks = Double.NaN;
        if (bestBidTick != Integer.MIN_VALUE && bestAskTick != Integer.MIN_VALUE) {
            double bb = bestBidTick * pips, aa = bestAskTick * pips;
            mid = (bb + aa) / 2.0;
            int totalTop = bestBidSize + bestAskSize;
            if (totalTop > 0) {
                micro = (bb * bestAskSize + aa * bestBidSize) / (double) totalTop;
                microMidTicks = (micro - mid) / pips;
            } else { micro = mid; microMidTicks = 0.0; }
        }
        double bookPressure5  = pressure(sumBid5,  sumAsk5);
        double bookPressure25 = pressure(sumBid25, sumAsk25);

        // Regime now comes from the adaptive FlowRegime classifier (Cont/Kukanov/Stoikov
        // OFI + CVD divergence + VPT absorption + bias trajectory, all Welford-EWMA
        // z-scored). Old heuristic regime block retired.
        FlowRegimeSnapshot fr = flowRegime.snapshot();
        return new MomentumSnapshot(
            now, windows, mid, micro, microMidTicks, bookPressure5, bookPressure25,
            fr.regime, fr.reason,
            fr.confidence, fr.biasScore, fr.biasTrajectory,
            fr.ofi, fr.ofiZ,
            fr.cvdDelta, fr.cvdDeltaZ,
            fr.vpt, fr.vptZ,
            fr.rvolTicks, fr.rvolZ
        );
    }

    private static double pressure(long bid, long ask) {
        long total = bid + ask;
        return total > 0 ? ((double)(bid - ask)) / total : 0.0;
    }
    private static String pct(double x) { return String.format("%+.0f%%", x * 100.0); }
    private static String signedTicks(double t) { return String.format("%+.1ft", t); }

    private static WorkingOrderRecord toRecord(OrderInfoUpdate u) {
        return new WorkingOrderRecord(
                u.orderId, u.isBuy,
                u.type == null ? "" : u.type.name(),
                u.status == null ? "" : u.status.name(),
                u.limitPrice, u.stopPrice, u.stopTriggered,
                u.filled, u.unfilled, u.averageFillPrice,
                u.duration == null ? "" : u.duration.name(),
                u.modificationUtcTime);
    }

    private static final class VwapBucket {
        long sessionStartMs = 0L;
        double sumPV = 0.0, sumV = 0.0, sumP2V = 0.0;
        long samples = 0L;
        void add(double price, int size, long anchorMs) {
            if (size <= 0 || !Double.isFinite(price)) return;
            if (anchorMs != sessionStartMs) reset(anchorMs);
            double pv = price * size;
            sumPV += pv; sumV += size; sumP2V += pv * price; samples++;
        }
        void maybeRoll(long anchorMs) { if (anchorMs != sessionStartMs) reset(anchorMs); }
        private void reset(long anchorMs) {
            sessionStartMs = anchorMs;
            sumPV = 0.0; sumV = 0.0; sumP2V = 0.0; samples = 0L;
        }
        VwapSnapshot snapshot() {
            if (samples == 0 || sumV <= 0.0) {
                return new VwapSnapshot(0L, sessionStartMs,
                        Double.NaN, Double.NaN, Double.NaN, Double.NaN,
                        Double.NaN, Double.NaN, Double.NaN, Double.NaN);
            }
            double vwap = sumPV / sumV;
            double meanP2 = sumP2V / sumV;
            double variance = meanP2 - vwap * vwap;
            double stddev = variance > 0.0 ? Math.sqrt(variance) : 0.0;
            return new VwapSnapshot(samples, sessionStartMs, vwap, stddev,
                    vwap + stddev, vwap - stddev,
                    vwap + 2 * stddev, vwap - 2 * stddev,
                    vwap + 3 * stddev, vwap - 3 * stddev);
        }
    }

    private static final class VolBucket {
        long sessionStartMs = 0L;
        final TreeMap<Integer, long[]> byTick = new TreeMap<>();
        long total = 0L;
        long samples = 0L;
        void add(int tick, int size, boolean bidAggressor, long anchorMs) {
            if (size <= 0) return;
            if (anchorMs != sessionStartMs) reset(anchorMs);
            long[] entry = byTick.get(tick);
            if (entry == null) { entry = new long[2]; byTick.put(tick, entry); }
            if (bidAggressor) entry[1] += size; else entry[0] += size;
            total += size; samples++;
        }
        void maybeRoll(long anchorMs) { if (anchorMs != sessionStartMs) reset(anchorMs); }
        private void reset(long anchorMs) {
            sessionStartMs = anchorMs;
            byTick.clear();
            total = 0L; samples = 0L;
        }
        VolumeProfileSnapshot snapshot(double pips) {
            if (byTick.isEmpty() || total == 0) {
                return new VolumeProfileSnapshot(sessionStartMs, 0L, 0L,
                        Double.NaN, Double.NaN, Double.NaN, 0.0,
                        Collections.emptyList());
            }
            ArrayList<VolumeProfileSnapshot.Level> levels = new ArrayList<>(byTick.size());
            int vpocTick = byTick.firstKey();
            long vpocVol = 0L;
            for (Map.Entry<Integer, long[]> e : byTick.entrySet()) {
                long[] v = e.getValue();
                long t = v[0] + v[1];
                levels.add(new VolumeProfileSnapshot.Level(e.getKey() * pips, v[0], v[1]));
                if (t > vpocVol) { vpocVol = t; vpocTick = e.getKey(); }
            }
            long target = (long) Math.ceil(total * 0.70);
            long enclosed = vpocVol;
            int highTick = vpocTick, lowTick = vpocTick;
            NavigableMap<Integer, long[]> above = byTick.tailMap(vpocTick, false);
            NavigableMap<Integer, long[]> below = byTick.headMap(vpocTick, false).descendingMap();
            Iterator<Map.Entry<Integer, long[]>> upIt = above.entrySet().iterator();
            Iterator<Map.Entry<Integer, long[]>> dnIt = below.entrySet().iterator();
            Map.Entry<Integer, long[]> peekUp = upIt.hasNext() ? upIt.next() : null;
            Map.Entry<Integer, long[]> peekDn = dnIt.hasNext() ? dnIt.next() : null;
            while (enclosed < target && (peekUp != null || peekDn != null)) {
                long upVol = peekUp == null ? -1L : (peekUp.getValue()[0] + peekUp.getValue()[1]);
                long dnVol = peekDn == null ? -1L : (peekDn.getValue()[0] + peekDn.getValue()[1]);
                if (upVol >= dnVol) {
                    if (peekUp == null) break;
                    enclosed += upVol;
                    highTick = peekUp.getKey();
                    peekUp = upIt.hasNext() ? upIt.next() : null;
                } else {
                    enclosed += dnVol;
                    lowTick = peekDn.getKey();
                    peekDn = dnIt.hasNext() ? dnIt.next() : null;
                }
            }
            return new VolumeProfileSnapshot(
                    sessionStartMs, total, samples,
                    vpocTick * pips, highTick * pips, lowTick * pips,
                    enclosed, Collections.unmodifiableList(levels));
        }
    }
}
