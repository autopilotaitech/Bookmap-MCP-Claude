package com.openrange;

import java.awt.Color;
import java.awt.BasicStroke;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.Insets;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.nio.file.Path;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Deque;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import javax.swing.JCheckBox;
import javax.swing.JLabel;
import javax.swing.JSpinner;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.SpinnerNumberModel;

import velox.api.layer1.Layer1ApiAdminAdapter;
import velox.api.layer1.Layer1ApiDataAdapter;
import velox.api.layer1.Layer1ApiFinishable;
import velox.api.layer1.Layer1ApiInstrumentAdapter;
import velox.api.layer1.Layer1ApiInstrumentSpecificEnabledStateProvider;
import velox.api.layer1.Layer1ApiProvider;
import velox.api.layer1.Layer1CustomPanelsGetter;
import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1Attachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.common.ListenableHelper;
import velox.api.layer1.common.Log;
import velox.api.layer1.data.InstrumentInfo;
import velox.api.layer1.data.TradeInfo;
import velox.api.layer1.datastructure.events.TradeAggregationEvent;
import velox.api.layer1.layers.strategies.interfaces.CalculatedResultListener;
import velox.api.layer1.layers.strategies.interfaces.InvalidateInterface;
import velox.api.layer1.layers.strategies.interfaces.OnlineCalculatable;
import velox.api.layer1.layers.strategies.interfaces.OnlineCalculatable.Marker;
import velox.api.layer1.layers.strategies.interfaces.OnlineValueCalculatorAdapter;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.CanvasIcon;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.CompositeCoordinateBase;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.CompositeHorizontalCoordinate;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.CompositeVerticalCoordinate;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvasFactory;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvasFactory.ScreenSpaceCanvasType;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpacePainter;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpacePainterAdapter;
import velox.api.layer1.layers.strategies.interfaces.ScreenSpacePainterFactory;
import velox.api.layer1.messages.UserMessageLayersChainCreatedTargeted;
import velox.api.layer1.messages.indicators.DataStructureInterface;
import velox.api.layer1.messages.indicators.DataStructureInterface.StandardEvents;
import velox.api.layer1.messages.indicators.DataStructureInterface.TreeResponseInterval;
import velox.api.layer1.messages.indicators.IndicatorColorScheme;
import velox.api.layer1.messages.indicators.IndicatorColorScheme.ColorDescription;
import velox.api.layer1.messages.indicators.IndicatorColorScheme.ColorIntervalResponse;
import velox.api.layer1.messages.indicators.IndicatorLineStyle;
import velox.api.layer1.messages.indicators.Layer1ApiDataInterfaceRequestMessage;
import velox.api.layer1.messages.indicators.Layer1ApiUserMessageModifyIndicator;
import velox.api.layer1.messages.indicators.Layer1ApiUserMessageModifyIndicator.GraphType;
import velox.api.layer1.messages.indicators.Layer1ApiUserMessageModifyIndicator.LayerRenderPriority;
import velox.api.layer1.messages.indicators.Layer1ApiUserMessageModifyScreenSpacePainter;
import velox.api.layer1.messages.indicators.SettingsAccess;
import velox.api.layer1.settings.Layer1ConfigSettingsInterface;
import velox.gui.StrategyPanel;
import velox.gui.colors.ColorsConfigItem;

@Layer1Attachable
@Layer1StrategyName("OpenRange")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION2)
public class PaxOpeningRangeModule implements
        Layer1ApiFinishable,
        Layer1ApiAdminAdapter,
        Layer1ApiDataAdapter,
        Layer1ApiInstrumentAdapter,
        Layer1ApiInstrumentSpecificEnabledStateProvider,
        ScreenSpacePainterFactory,
        Layer1CustomPanelsGetter,
        Layer1ConfigSettingsInterface {

    private static final String INDICATOR_NAME = "OpenRange";
    private static final String SIGNAL_MARKER_INDICATOR_NAME = "OpenRange Signals";
    private static final String SIGNAL_MARKER_COLOR_NAME = "OpenRange signal markers";
    private static final ZoneId EXCHANGE_ZONE = ZoneId.of("America/Chicago");
    private static final int LABEL_X_OFFSET = 5;
    private static final int LABEL_Y_OFFSET = -8;
    private static final int SIGNAL_BADGE_LEFT_PADDING = 12;
    private static final int SIGNAL_BADGE_TOP_PADDING = 14;
    private static final String SETTINGS_KEY = "OpenRange Settings";
    private static final long REPAINT_THROTTLE_NANOS = 5_000_000_000L;
    private static final long DEPTH_SIGNAL_THROTTLE_NANOS = 50_000_000L;
    private static final int FEATURE_CACHE_HISTORY_SIZE = 1_024;
    /** How long the painter trusts a CSV-derived fallback before re-scanning.
     * Bounds disk hits per paint frame; short enough that a freshly-written
     * CSV becomes visible within the window. */
    private static final long FALLBACK_TTL_MS = 30_000L;

    /** Triangle font sizes for the strong/weak rendering paths. The trend
     *  triangle is painted as a font glyph (▲/▼) via the same labelImage
     *  primitive that paints the OR HIGH/LOW labels — Bookmap 7.4's chart
     *  canvas renders these reliably, where the legacy 18×18 BufferedImage
     *  Polygon could fail to display. Strong = full alpha + big glyph;
     *  Weak = lower alpha + smaller glyph. */
    static final int TRIANGLE_FONT_STRONG = 26;
    static final int TRIANGLE_FONT_WEAK   = 20;
    /** Throttle for triangle diagnostic Log lines so the loop doesn't spam
     *  Bookmap's log every poll. One emit + one skip-reason per minute is
     *  enough to audit live behavior. */
    private static final long TRIANGLE_LOG_THROTTLE_MS = 60_000L;
    private static volatile long lastTriangleEmitLogMs = 0L;
    private static volatile long lastTriangleSkipLogMs = 0L;
    /** Throttle for native trend marker emit/skip INFO logs. Same cadence as
     *  the triangle audit lines so the bridge log stays readable when
     *  multiple instruments are attached. */
    private static volatile long lastTrendMarkerEmitLogMs = 0L;
    private static volatile long lastTrendMarkerSkipLogMs = 0L;
    private static volatile long lastSignalMarkerEmitLogMs = 0L;
    private static volatile long lastSignalMarkerSkipLogMs = 0L;
    private static volatile long lastDegradationLogMs = 0L;
    /** Process-wide cache of trend-triangle PreparedImages. Each entry is
     *  immutable; ~8 entries cover STRONG/WEAK x BULL/BEAR x 2 font sizes.
     *  Replaces a per-redraw BufferedImage + Graphics2D allocation that
     *  could fire up to 8 times per dashboard poll. */
    private static final PaxTrendGlyphCache TREND_GLYPH_CACHE =
            new PaxTrendGlyphCache(PaxOpeningRangeModule::buildTrendGlyphImage);

    /** Sentinel values for the per-painter render-key memoization. A
     *  computed key from {@link #triangleRenderKey} or the overlay key
     *  computation can never collide with these because every code path
     *  there returns a non-empty, non-sentinel string. */
    static final String TRIANGLE_KEY_UNSET = "@unset";
    static final String OVERLAY_KEY_UNSET  = "@unset";

    /**
     * Fine-grained repaint dispatch.
     *
     * <p>The painter holds THREE shape buckets — persistent OR lines/labels
     * (chart-anchored, expensive), trend triangles (chart-anchored, can
     * advance independently when a new bucket fires), and volatile overlay
     * (Heatwave box, signal badge — pixel-anchored). Each bucket can be
     * rebuilt independently to eliminate flicker that would otherwise come
     * from rebuilding everything on every dashboard poll.
     *
     * <p>Production rule (Jane-Street-style: do exactly the work needed):
     * <ul>
     *   <li>persistent OR lines redraw <b>only</b> when the OR structure
     *       changes (new completed day, new dynamic level, fallback
     *       hydrated, high/low/mid drift), on {@code onMoveEnd}, or on
     *       explicit rebuild/backfill paths. <b>Never</b> on a market tick
     *       or dashboard poll by itself.</li>
     *   <li>dashboard conviction triangles redraw on {@code trendSignalDirty};
     *       OpenRange buy/sell markers use Bookmap's native Indicator API.</li>
     *   <li>volatile overlay redraws on {@code heatwaveDirty}.</li>
     * </ul>
     */
    static final class RepaintNeeds {
        final boolean persistent;
        final boolean triangles;
        final boolean overlay;
        static final RepaintNeeds NONE = new RepaintNeeds(false, false, false);
        RepaintNeeds(boolean persistent, boolean triangles, boolean overlay) {
            this.persistent = persistent;
            this.triangles  = triangles;
            this.overlay    = overlay;
        }
        boolean isNone() { return !persistent && !triangles && !overlay; }
        static RepaintNeeds full() { return new RepaintNeeds(true, true, true); }
    }

    static final int MAX_LIVE_TRIANGLES = 8;
    /** Max institutional markers held in the durable history. The history is
     *  append-only across polls — empty institutional_signals on a later
     *  poll does NOT remove plotted markers, so this cap is what eventually
     *  rolls the oldest off the chart. */
    static final int MAX_LIVE_INSTITUTIONAL_MARKERS = 30;
    /** Max chart-events markers (the broader evidence-trail layer) held in
     *  durable per-instrument history. Bigger cap than entry markers since
     *  WATCH / TCH / SWP / ABS / ICE / SPD / PULL / STACK accumulate
     *  faster than confirmed entries. */
    static final int MAX_LIVE_CHART_EVENT_MARKERS = 50;

    /** Decide whether the /api/snapshot fetcher worker should be running.
     *
     *  <p>Historically the fetcher was gated on {@code showTrendTriangles}
     *  alone. That broke the institutional-chart-events pipeline: if the
     *  operator toggled trend triangles off, the fetcher stopped polling
     *  and the new chart-events layer received no data either. Both UI
     *  layers consume the same /api/snapshot; either being on means the
     *  fetcher must run.</p>
     *
     *  Pure static so the OR logic is unit-testable without spinning up
     *  the full module / HttpClient.
     */
    static boolean trendFetcherShouldRun(boolean showTrendTriangles,
                                          boolean showInstitutionalChartEvents) {
        return showTrendTriangles || showInstitutionalChartEvents;
    }
    /** Stale-cliff for incoming trend_signal: keep existing triangles drawn,
     * but do not emit new ones if the fetcher's last successful HTTP receive
     * is older than this. */
    static final long TREND_STALE_AGE_MS = 30_000L;
    /** Triangle anchor offset in ticks. Strong sits further out so the two
     * sizes are distinguishable at the same price. */
    static final int TRIANGLE_OFFSET_TICKS_STRONG = 4;
    static final int TRIANGLE_OFFSET_TICKS_WEAK = 2;
    /** Chart-event labels collide visually when their prices are within a few
     *  ticks and their timestamps land on the same rendered chart column.
     *  Bucket by this many ticks so different marker texts at the same level
     *  stack instead of painting on top of each other. */
    static final int CHART_EVENT_COLLISION_PRICE_TICKS = 8;

    /** Immutable in-flight triangle event held by PaxPainter for redraw. */
    static final class TrendTriangleEvent {
        final PaxTrendSignalModel.Kind kind;
        final long bucketEnteredMs;
        final long eventMs;
        final double mid;
        TrendTriangleEvent(PaxTrendSignalModel.Kind kind, long bucketEnteredMs, long eventMs, double mid) {
            this.kind = kind;
            this.bucketEnteredMs = bucketEnteredMs;
            this.eventMs = eventMs;
            this.mid = mid;
        }
    }

    static PaxTrendSignalModel.Kind openingRangeMarkerKind(PaxOpeningRangeSignal signal) {
        if (!isOpeningRangeMarkerCandidate(signal)) {
            return PaxTrendSignalModel.Kind.NONE;
        }
        boolean strong = signal.confidence() == PaxOpeningRangeSignalConfidence.HIGH;
        if (signal.bias() == PaxOpeningRangeSignalBias.LONG) {
            return strong ? PaxTrendSignalModel.Kind.STRONG_BULL : PaxTrendSignalModel.Kind.WEAK_BULL;
        }
        if (signal.bias() == PaxOpeningRangeSignalBias.SHORT) {
            return strong ? PaxTrendSignalModel.Kind.STRONG_BEAR : PaxTrendSignalModel.Kind.WEAK_BEAR;
        }
        return PaxTrendSignalModel.Kind.NONE;
    }

    static boolean isOpeningRangeMarkerCandidate(PaxOpeningRangeSignal signal) {
        return signal != null
                && signal.action() == PaxOpeningRangeSignalAction.ALLOW_SIGNAL
                && (signal.bias() == PaxOpeningRangeSignalBias.LONG
                    || signal.bias() == PaxOpeningRangeSignalBias.SHORT);
    }

    static String openingRangeMarkerKey(String symbol, PaxOpeningRangeFeatureSnapshot snapshot) {
        if (snapshot == null || !isOpeningRangeMarkerCandidate(snapshot.signal())) {
            return "";
        }
        PaxOpeningRangeSignal signal = snapshot.signal();
        String safeSymbol = symbol == null ? "" : symbol;
        String evidence = signal.evidence() == null ? "" : signal.evidence();
        return safeSymbol + "|" + signal.bias() + "|" + signal.action() + "|"
                + signal.confidence() + "|" + signal.score() + "|" + evidence;
    }

    private final Layer1ApiProvider provider;
    private final Map<String, InstrumentState> instruments = new ConcurrentHashMap<>();
    private final Map<String, InstrumentInfo> knownInstruments = new ConcurrentHashMap<>();
    private final Map<String, PaxPainter> painters = new ConcurrentHashMap<>();
    private final Set<String> enabledAliases = ConcurrentHashMap.newKeySet();
    private final Map<String, String> indicatorsFullNameToUserName = new HashMap<>();
    private final Map<String, String> markerIndicatorFullNameToUserName = new HashMap<>();
    private final PaxOpeningRangeCrossMarketState crossMarketState = new PaxOpeningRangeCrossMarketState();
    private final NativeSignalMarkerIndicator nativeSignalMarkers = new NativeSignalMarkerIndicator();
    private final PaxHeatwaveFetcher heatwave;
    private final AtomicBoolean heatwaveDirty = new AtomicBoolean(false);
    private final PaxTrendSignalFetcher trendSignals;
    private final AtomicBoolean trendSignalDirty = new AtomicBoolean(false);
    private volatile DataStructureInterface dataStructureInterface;
    private volatile SettingsAccess settingsAccess;
    private volatile PaxOpeningRangeUiSettings uiSettings = new PaxOpeningRangeUiSettings();

    public PaxOpeningRangeModule(Layer1ApiProvider provider) {
        this.provider = provider;
        this.heatwave = new PaxHeatwaveFetcher(() -> heatwaveDirty.set(true));
        this.trendSignals = new PaxTrendSignalFetcher(() -> trendSignalDirty.set(true));
        ListenableHelper.addListeners(provider, this);
    }

    @Override
    public void finish() {
        heatwave.stop();
        trendSignals.stop();
        synchronized (indicatorsFullNameToUserName) {
            for (String userName : indicatorsFullNameToUserName.values()) {
                provider.sendUserMessage(Layer1ApiUserMessageModifyScreenSpacePainter
                        .builder(PaxOpeningRangeModule.class, userName)
                        .setIsAdd(false)
                        .build());
            }
            indicatorsFullNameToUserName.clear();
        }
        synchronized (markerIndicatorFullNameToUserName) {
            for (String userName : markerIndicatorFullNameToUserName.values()) {
                provider.sendUserMessage(new Layer1ApiUserMessageModifyIndicator(
                        PaxOpeningRangeModule.class, userName, false));
            }
            markerIndicatorFullNameToUserName.clear();
        }
        nativeSignalMarkers.clear();
        painters.values().forEach(PaxPainter::dispose);
        painters.clear();
        instruments.values().forEach(InstrumentState::dispose);
        instruments.clear();
        knownInstruments.clear();
        enabledAliases.clear();
    }

    @Override
    public void onStrategyCheckboxEnabled(String alias, boolean isEnabled) {
        if (alias == null) {
            return;
        }
        if (isEnabled) {
            enabledAliases.add(alias);
            InstrumentInfo info = knownInstruments.get(alias);
            if (info != null) {
                attachInstrumentIfEnabled(alias, info);
            }
        } else {
            enabledAliases.remove(alias);
            detachInstrument(alias);
        }
    }

    @Override
    public boolean isStrategyEnabled(String alias) {
        return alias != null && enabledAliases.contains(alias);
    }

    boolean consumeHeatwaveDirty() {
        return heatwaveDirty.compareAndSet(true, false);
    }

    boolean consumeTrendSignalDirty() {
        return trendSignalDirty.compareAndSet(true, false);
    }

    @Override
    public void onUserMessage(Object data) {
        if (data.getClass() == UserMessageLayersChainCreatedTargeted.class) {
            UserMessageLayersChainCreatedTargeted message = (UserMessageLayersChainCreatedTargeted) data;
            if (message.targetClass == getClass()) {
                provider.sendUserMessage(new Layer1ApiDataInterfaceRequestMessage(dataStructureInterface -> {
                    this.dataStructureInterface = dataStructureInterface;
                    backfillAll();
                }));
                addPainter();
                addNativeSignalMarkerIndicator();
            }
        }
    }

    @Override
    public void onInstrumentAdded(String alias, InstrumentInfo instrumentInfo) {
        knownInstruments.put(alias, instrumentInfo);
        attachInstrumentIfEnabled(alias, instrumentInfo);
    }

    private void attachInstrumentIfEnabled(String alias, InstrumentInfo instrumentInfo) {
        if (!isStrategyEnabled(alias) || instrumentInfo == null || instruments.containsKey(alias)) {
            return;
        }
        double pips = instrumentInfo.pips <= 0 ? 1 : instrumentInfo.pips;
        InstrumentState state = new InstrumentState(alias, instrumentInfo, pips, getCalculatorSettings());
        instruments.put(alias, state);
        backfill(alias);
        PaxPainter painter = painters.get(alias);
        if (painter != null) {
            painter.update();
        }
    }

    @Override
    public void onInstrumentRemoved(String alias) {
        knownInstruments.remove(alias);
        detachInstrument(alias);
    }

    private void detachInstrument(String alias) {
        InstrumentState state = instruments.remove(alias);
        if (state != null) {
            crossMarketState.remove(state.info.symbol);
            state.dispose();
        }
        PaxPainter painter = painters.remove(alias);
        if (painter != null) {
            painter.dispose();
        }
    }

    @Override
    public void onTrade(String alias, double price, int size, TradeInfo tradeInfo) {
        InstrumentState state = instruments.get(alias);
        if (state == null) {
            return;
        }

        long eventTime = provider.getCurrentTime();
        LocalDateTime localTime = toLocalDateTime(eventTime);
        double displayPrice = price * state.pips;
        synchronized (state.lock) {
            state.resetOrderFlowIfNewDay(localTime.toLocalDate());
            state.orderFlow.onTrade(displayPrice, size, tradeInfo != null && tradeInfo.isBidAggressor);
            state.calculator.onTrade(localTime, displayPrice);
            state.updateSignal(localTime);
            state.markDepthSignalUpdated(eventTime);
        }

        PaxPainter painter = painters.get(alias);
        if (painter != null) {
            painter.applyNeeds(state.nextRepaintNeeds(eventTime));
        }
    }

    @Override
    public void onDepth(String alias, boolean isBid, int price, int size) {
        InstrumentState state = instruments.get(alias);
        if (state == null) {
            return;
        }

        double displayPrice = price * state.pips;
        long eventTime = provider.getCurrentTime();
        LocalDateTime localTime = toLocalDateTime(eventTime);
        synchronized (state.lock) {
            state.resetOrderFlowIfNewDay(localTime.toLocalDate());
            state.orderFlow.onDepth(isBid, displayPrice, size);

            if (state.shouldUpdateSignalForDepth(eventTime)) {
                state.updateSignal(localTime);
            }
        }

        PaxPainter painter = painters.get(alias);
        if (painter != null) {
            painter.applyNeeds(state.nextRepaintNeeds(eventTime));
        }
    }

    @Override
    public ScreenSpacePainter createScreenSpacePainter(String indicatorName, String indicatorAlias,
            ScreenSpaceCanvasFactory screenSpaceCanvasFactory) {
        ScreenSpaceCanvas canvas = screenSpaceCanvasFactory.createCanvas(ScreenSpaceCanvasType.HEATMAP);
        PaxPainter painter = new PaxPainter(indicatorAlias, canvas);
        painters.put(indicatorAlias, painter);
        backfill(indicatorAlias);
        painter.update();
        return painter;
    }

    private void addPainter() {
        Layer1ApiUserMessageModifyScreenSpacePainter message = Layer1ApiUserMessageModifyScreenSpacePainter
                .builder(PaxOpeningRangeModule.class, INDICATOR_NAME)
                .setIsAdd(true)
                .setScreenSpacePainterFactory(this)
                .build();
        synchronized (indicatorsFullNameToUserName) {
            indicatorsFullNameToUserName.put(message.fullName, message.userName);
        }
        provider.sendUserMessage(message);
    }

    private void addNativeSignalMarkerIndicator() {
        Layer1ApiUserMessageModifyIndicator message = Layer1ApiUserMessageModifyIndicator
                .builder(PaxOpeningRangeModule.class, SIGNAL_MARKER_INDICATOR_NAME)
                .setIsAdd(true)
                .setIndicatorColorScheme(signalMarkerColorScheme())
                .setGraphType(GraphType.PRIMARY)
                .setIndicatorLineStyle(IndicatorLineStyle.NONE)
                .setOnlineCalculatable(nativeSignalMarkers)
                .setIconLayerRanderPriotity(LayerRenderPriority.ABSOLUTE_TOP)
                .setIsLineEnabledByDefault(true)
                .build();
        synchronized (markerIndicatorFullNameToUserName) {
            markerIndicatorFullNameToUserName.put(message.fullName, message.userName);
        }
        provider.sendUserMessage(message);
    }

    private static IndicatorColorScheme signalMarkerColorScheme() {
        return new IndicatorColorScheme() {
            @Override
            public ColorDescription[] getColors() {
                return new ColorDescription[] {
                        new ColorDescription(PaxOpeningRangeModule.class,
                                SIGNAL_MARKER_COLOR_NAME, Color.WHITE, false)
                };
            }

            @Override
            public String getColorFor(Double value) {
                return SIGNAL_MARKER_COLOR_NAME;
            }

            @Override
            public ColorIntervalResponse getColorIntervalsList(double valueFrom, double valueTo) {
                return new ColorIntervalResponse(new String[] { SIGNAL_MARKER_COLOR_NAME },
                        new double[] {});
            }
        };
    }

    private static LocalDateTime toLocalDateTime(long nanos) {
        if (nanos <= 0) {
            return LocalDateTime.now(EXCHANGE_ZONE);
        }
        return LocalDateTime.ofInstant(Instant.ofEpochSecond(0, nanos), EXCHANGE_ZONE);
    }

    private static long toNanos(LocalDateTime time) {
        Instant instant = time.atZone(EXCHANGE_ZONE).toInstant();
        return instant.getEpochSecond() * 1_000_000_000L + instant.getNano();
    }

    static LocalDateTime intervalEndTime(LocalDateTime start, long intervalNanos, int bucketIndex) {
        return start.plusNanos(intervalNanos * (bucketIndex + 1L));
    }

    private void backfillAll() {
        for (String alias : instruments.keySet()) {
            backfill(alias);
        }
    }

    private void backfill(String alias) {
        DataStructureInterface data = dataStructureInterface;
        InstrumentState state = instruments.get(alias);
        if (data == null || state == null) {
            return;
        }

        try {
            synchronized (state.lock) {
                state.calculator.reset();
                LocalDate currentDate = toLocalDateTime(provider.getCurrentTime()).toLocalDate();
                PaxOpeningRangeSettings settings = getCalculatorSettings();
                LocalDate firstDate = currentDate.minusDays(settings.daysToDisplay() - 1L);
                for (LocalDate date = firstDate; !date.isAfter(currentDate); date = date.plusDays(1)) {
                    backfillOpeningRange(data, alias, state, date);
                    backfillDynamicLevels(data, alias, state, date);
                }
            }
            PaxPainter painter = painters.get(alias);
            if (painter != null) {
                painter.update();
            }
        } catch (Exception e) {
            Log.warn("OpenRange historical backfill failed for " + alias, e);
        }
    }

    private void backfillOpeningRange(DataStructureInterface data, String alias, InstrumentState state, LocalDate date) {
        PaxOpeningRangeSettings settings = getCalculatorSettings();
        long t0 = toNanos(date.atTime(settings.rangeStart()));
        long interval = 1_000_000_000L;
        int intervals = settings.rangeSeconds();
        List<TreeResponseInterval> response = data.get(t0, interval, intervals, alias, new StandardEvents[] {StandardEvents.TRADE});
        for (int i = 1; i < response.size(); i++) {
            PriceRange range = getTradeRange(response.get(i), state.pips);
            if (range.hasTrades()) {
                state.calculator.onInterval(date.atTime(settings.rangeStart()).plusSeconds(i), range.high, range.low);
            }
        }
    }

    private void backfillDynamicLevels(DataStructureInterface data, String alias, InstrumentState state, LocalDate date) {
        PaxOpeningRangeSettings settings = getCalculatorSettings();
        LocalDateTime start = settings.rangeEndDateTime(date);
        LocalDateTime end = settings.lineEndDateTime(date);
        LocalDateTime now = toLocalDateTime(provider.getCurrentTime());
        if (date.equals(now.toLocalDate()) && now.isBefore(end)) {
            end = now;
        }
        if (!end.isAfter(start)) {
            return;
        }

        long interval = 30_000_000_000L;
        long duration = toNanos(end) - toNanos(start);
        int intervals = (int) Math.max(1, Math.ceil(duration / (double) interval));
        List<TreeResponseInterval> response = data.get(toNanos(start), interval, intervals, alias, new StandardEvents[] {StandardEvents.TRADE});
        for (int i = 1; i < response.size(); i++) {
            PriceRange range = getTradeRange(response.get(i), state.pips);
            if (range.hasTrades()) {
                state.calculator.onInterval(start.plusSeconds(30L * i), range.high, range.low);
            }
        }
    }

    private PriceRange getTradeRange(TreeResponseInterval interval, double pips) {
        Object event = interval.events.get(StandardEvents.TRADE.toString());
        if (!(event instanceof TradeAggregationEvent)) {
            return PriceRange.empty();
        }
        TradeAggregationEvent trades = (TradeAggregationEvent) event;
        PriceRange range = PriceRange.empty();
        for (Double price : trades.bidAggressorMap.keySet()) {
            range = range.include(price * pips);
        }
        for (Double price : trades.askAggressorMap.keySet()) {
            range = range.include(price * pips);
        }
        if (!Double.isNaN(trades.lastPrice)) {
            range = range.include(trades.lastPrice * pips);
        }
        return range;
    }

    private final class NativeSignalMarkerIndicator implements OnlineCalculatable {
        private final Map<String, Consumer<Object>> consumersByAlias = new ConcurrentHashMap<>();

        @Override
        public void calculateValuesInRange(String indicatorName, String alias, long t0,
                long intervalWidth, int intervalsNumber, CalculatedResultListener listener) {
            listener.setCompleted();
        }

        @Override
        public OnlineValueCalculatorAdapter createOnlineValueCalculator(String indicatorName,
                String indicatorAlias, long time, Consumer<Object> listener,
                InvalidateInterface invalidateInterface) {
            consumersByAlias.put(indicatorAlias, listener);
            return new OnlineValueCalculatorAdapter() {};
        }

        boolean publish(String symbol, double pips, PaxOpeningRangeFeatureSnapshot snapshot) {
            if (snapshot == null || snapshot.market() == null || snapshot.signal() == null) {
                return false;
            }
            Consumer<Object> consumer = consumersByAlias.get(symbol);
            if (consumer == null) {
                long nowMs = System.currentTimeMillis();
                if (nowMs - lastSignalMarkerSkipLogMs >= TRIANGLE_LOG_THROTTLE_MS) {
                    lastSignalMarkerSkipLogMs = nowMs;
                    try {
                        Log.info("OpenRange native signal marker skipped reason=no_consumer alias=" + symbol);
                    } catch (Throwable ignored) { /* Log unavailable in tests */ }
                }
                return false;
            }
            PaxTrendSignalModel.Kind kind = openingRangeMarkerKind(snapshot.signal());
            if (!kind.isRenderable() || !Double.isFinite(pips) || pips <= 0.0) {
                return false;
            }
            double price = snapshot.market().lastPrice();
            if (!Double.isFinite(price) || price <= 0.0) {
                return false;
            }
            BufferedImage icon = signalMarkerIcon(kind, price, "OR");
            int xOffset = -icon.getWidth() / 2;
            int yOffset = kind.isBull() ? 12 : -icon.getHeight() - 12;
            consumer.accept(new Marker(price / pips, xOffset, yOffset, icon));
            long nowMs = System.currentTimeMillis();
            if (nowMs - lastSignalMarkerEmitLogMs >= TRIANGLE_LOG_THROTTLE_MS) {
                lastSignalMarkerEmitLogMs = nowMs;
                try {
                    Log.info("OpenRange native signal marker emitted"
                            + " symbol=" + symbol
                            + " bias=" + snapshot.signal().bias()
                            + " confidence=" + snapshot.signal().confidence()
                            + " price=" + price
                            + " dataPrice=" + (price / pips));
                } catch (Throwable ignored) { /* Log unavailable in tests */ }
            }
            return true;
        }

        boolean publishTrend(String alias, double pips, PaxTrendSignalModel signal) {
            if (signal == null || signal.kind == null || !signal.kind.isRenderable()
                    || !Double.isFinite(pips) || pips <= 0.0
                    || !Double.isFinite(signal.mid) || signal.mid <= 0.0) {
                return false;
            }
            Consumer<Object> consumer = consumersByAlias.get(alias);
            if (consumer == null) {
                long nowMs = System.currentTimeMillis();
                if (nowMs - lastTrendMarkerSkipLogMs >= TRIANGLE_LOG_THROTTLE_MS) {
                    lastTrendMarkerSkipLogMs = nowMs;
                    try {
                        Log.info("OpenRange native trend marker skipped reason=no_consumer alias=" + alias);
                    } catch (Throwable ignored) { /* Log unavailable in tests */ }
                }
                return false;
            }
            String source;
            if (signal.eventMsSource != null && signal.eventMsSource.startsWith("institutional_signal")) {
                source = "INS";
            } else if (signal.eventMsSource != null && signal.eventMsSource.startsWith("pax_decision")) {
                source = "PAX";
            } else {
                source = "TRD";
            }
            BufferedImage icon = signalMarkerIcon(signal.kind, signal.mid, source);
            int xOffset = -icon.getWidth() / 2;
            int yOffset = signal.kind.isBull() ? 12 : -icon.getHeight() - 12;
            consumer.accept(new Marker(signal.mid / pips, xOffset, yOffset, icon));
            long nowMs = System.currentTimeMillis();
            if (nowMs - lastTrendMarkerEmitLogMs >= TRIANGLE_LOG_THROTTLE_MS) {
                lastTrendMarkerEmitLogMs = nowMs;
                try {
                    Log.info("OpenRange native trend marker emitted"
                            + " alias=" + alias
                            + " kind=" + signal.kind
                            + " mid=" + signal.mid
                            + " dataPrice=" + (signal.mid / pips));
                } catch (Throwable ignored) { /* Log unavailable in tests */ }
            }
            return true;
        }

        void clear() {
            consumersByAlias.clear();
        }
    }

    @Override
    public void acceptSettingsInterface(SettingsAccess settingsAccess) {
        this.settingsAccess = settingsAccess;
        PaxOpeningRangeUiSettings stored = (PaxOpeningRangeUiSettings) settingsAccess.getSettings(null, SETTINGS_KEY, PaxOpeningRangeUiSettings.class);
        if (stored != null) {
            uiSettings = stored;
            migrateLegacyDefaultsIfNeeded(settingsAccess);
        }
        // Publish operator-controlled OR settings to or-session-config.json
        // BEFORE any drift warning so downstream consumers (dashboard +
        // bridge) immediately see the effective anchor.
        PaxOpeningRangeSessionConfigWriter.publish(uiSettings);
        heatwave.applySettings(uiSettings);
        // The trend-signal fetcher feeds BOTH the legacy trend triangles
        // AND the new institutional chart events. Run it whenever EITHER
        // layer is enabled.
        trendSignals.applySettings(
                trendFetcherShouldRun(uiSettings.showTrendTriangles,
                                       uiSettings.showInstitutionalChartEvents),
                uiSettings.safeHeatwaveUrl(), uiSettings.clampedHeatwavePollMs());
    }

    /** V3 migration. Bookmap deserializes user settings overwriting fields
     *  set in the class. If we see the exact V1/V2 default tuple
     *  (start 09:30, end 17:00) AND no operator-supplied customization was
     *  detected on other fields, quietly migrate to canonical 08:30. We
     *  refuse to clobber an operator who explicitly chose 09:30 for some
     *  reason — that's why migratedToCanonical0830 acts as a breadcrumb. */
    private void migrateLegacyDefaultsIfNeeded(SettingsAccess access) {
        if (uiSettings.migratedToCanonical0830) {
            return;
        }
        boolean looksLikeV1V2Default = uiSettings.startHour == 9
                && uiSettings.startMinute == 30
                && uiSettings.startSecond == 0
                && uiSettings.endHour == 17
                && uiSettings.endMinute == 0
                && uiSettings.rangeSeconds == 30;
        if (looksLikeV1V2Default) {
            try {
                Log.info("OpenRange: migrating pre-canonical OR anchor (09:30 ET) → 08:30 CT.");
            } catch (Throwable ignored) { /* Log unavailable in tests */ }
            uiSettings.startHour   = 8;
            uiSettings.startMinute = 30;
            uiSettings.startSecond = 0;
            uiSettings.endHour     = 8;
            uiSettings.endMinute   = 30;
            uiSettings.migratedToCanonical0830 = true;
            try {
                access.setSettings(null, SETTINGS_KEY, uiSettings, PaxOpeningRangeUiSettings.class);
            } catch (Throwable t) {
                try {
                    Log.warn("OpenRange: failed to persist V3 migration: " + t);
                } catch (Throwable ignored) { }
            }
        } else {
            // Operator-customized — set the breadcrumb so we don't re-check
            // every restart. They keep their values.
            uiSettings.migratedToCanonical0830 = true;
            try {
                access.setSettings(null, SETTINGS_KEY, uiSettings, PaxOpeningRangeUiSettings.class);
            } catch (Throwable ignored) { }
        }
    }

    @Override
    public StrategyPanel[] getCustomGuiFor(String alias, String indicatorName) {
        PaxOpeningRangeUiSettings settings = loadSettings();
        StrategyPanel panel = new StrategyPanel("OpenRange", new GridBagLayout());
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(4, 4, 4, 4);
        c.fill = GridBagConstraints.HORIZONTAL;
        c.weightx = 1;

        int row = 0;
        JSpinner startHour = spinner(settings.startHour, 0, 23, 1);
        JSpinner startMinute = spinner(settings.startMinute, 0, 59, 1);
        JSpinner startSecond = spinner(settings.startSecond, 0, 59, 1);
        row = addRow(panel, c, row, "Start hour", startHour);
        row = addRow(panel, c, row, "Start minute", startMinute);
        row = addRow(panel, c, row, "Start second", startSecond);

        JSpinner rangeSeconds = spinner(settings.rangeSeconds, 1, 600, 1);
        row = addRow(panel, c, row, "Range seconds", rangeSeconds);

        JSpinner endHour = spinner(settings.endHour, 0, 23, 1);
        JSpinner endMinute = spinner(settings.endMinute, 0, 59, 1);
        row = addRow(panel, c, row, "Line end hour", endHour);
        row = addRow(panel, c, row, "Line end minute", endMinute);

        JSpinner days = spinner(settings.daysToDisplay, 1, 30, 1);
        row = addRow(panel, c, row, "Days to display", days);

        JSpinner cvdThreshold = spinner(settings.signalCvdThreshold, 0, 100000, 1);
        row = addRow(panel, c, row, "Signal CVD threshold", cvdThreshold);

        JSpinner depthThreshold = spinner(settings.signalDepthThreshold, 0, 100000, 1);
        row = addRow(panel, c, row, "Signal depth threshold", depthThreshold);

        JSpinner depthLevels = spinner(settings.signalDepthLevels, 1, 100, 1);
        row = addRow(panel, c, row, "Signal depth levels", depthLevels);

        JSpinner minBreakoutTicks = spinner(settings.signalMinBreakoutTicks, 0, 100, 1);
        row = addRow(panel, c, row, "Min breakout ticks", minBreakoutTicks);

        JSpinner maxBreakoutTicks = spinner(settings.signalMaxBreakoutTicks, 0, 1000, 1);
        row = addRow(panel, c, row, "Max breakout ticks", maxBreakoutTicks);

        JSpinner minScore = spinner(settings.signalMinScore, 1, 4, 1);
        row = addRow(panel, c, row, "Min signal score", minScore);

        JSpinner minCvdPercentile = spinner(settings.signalMinCvdPercentile, 0, 100, 1);
        row = addRow(panel, c, row, "Min CVD percentile", minCvdPercentile);

        JSpinner minPullingStackingPercentile = spinner(settings.signalMinPullingStackingPercentile, 0, 100, 1);
        row = addRow(panel, c, row, "Min PS percentile", minPullingStackingPercentile);

        JSpinner normalizationWindow = spinner(settings.normalizationWindowSeconds, 10, 600, 10);
        row = addRow(panel, c, row, "Norm window seconds", normalizationWindow);

        JTextField logDirectory = new JTextField(settings.logDirectory == null ? "build\\logs" : settings.logDirectory);
        row = addRow(panel, c, row, "CSV log directory", logDirectory);

        JTextField prefix = new JTextField(settings.labelPrefix == null ? "OpenRange" : settings.labelPrefix);
        row = addRow(panel, c, row, "Label prefix", prefix);

        JCheckBox blockCrossDivergence = new JCheckBox("Block ES/NQ divergence", settings.signalBlockCrossMarketDivergence);
        c.gridx = 0;
        c.gridy = row++;
        c.gridwidth = 4;
        panel.add(blockCrossDivergence, c);
        c.gridwidth = 1;

        JCheckBox showMid = new JCheckBox("Show mid line", settings.showMid);
        c.gridx = 0;
        c.gridy = row++;
        c.gridwidth = 4;
        panel.add(showMid, c);
        c.gridwidth = 1;

        ColorsConfigItem highColor = new ColorsConfigItem(settings.highColor, new Color(0, 191, 255), "High/upper", color -> {
            settings.highColor = color;
            saveSettings(null, settings);
            repaintAll();
        });
        row = addRow(panel, c, row, "High color", highColor);

        ColorsConfigItem lowColor = new ColorsConfigItem(settings.lowColor, new Color(255, 99, 71), "Low/lower", color -> {
            settings.lowColor = color;
            saveSettings(null, settings);
            repaintAll();
        });
        row = addRow(panel, c, row, "Low color", lowColor);

        ColorsConfigItem midColor = new ColorsConfigItem(settings.midColor, new Color(255, 215, 0), "Mid", color -> {
            settings.midColor = color;
            saveSettings(null, settings);
            repaintAll();
        });
        row = addRow(panel, c, row, "Mid color", midColor);

        JCheckBox showHeatwaveBox = new JCheckBox("Show Heatwave Quant Box", settings.showHeatwaveBox);
        c.gridx = 0;
        c.gridy = row++;
        c.gridwidth = 4;
        panel.add(showHeatwaveBox, c);
        c.gridwidth = 1;

        JCheckBox showInstitutionalChartEvents = new JCheckBox(
                "Show Institutional Chart Events",
                settings.showInstitutionalChartEvents);
        c.gridx = 0;
        c.gridy = row++;
        c.gridwidth = 4;
        panel.add(showInstitutionalChartEvents, c);
        c.gridwidth = 1;

        JCheckBox showTrendTriangles = new JCheckBox(
                "Show Legacy Trend Triangles (conviction)",
                settings.showTrendTriangles);
        c.gridx = 0;
        c.gridy = row++;
        c.gridwidth = 4;
        panel.add(showTrendTriangles, c);
        c.gridwidth = 1;

        JSpinner heatwaveBoxX = spinner(settings.clampedHeatwaveBoxX(), 0, 4000, 2);
        row = addRow(panel, c, row, "Heatwave box X", heatwaveBoxX);

        JSpinner heatwaveBoxY = spinner(settings.clampedHeatwaveBoxY(), 0, 4000, 2);
        row = addRow(panel, c, row, "Heatwave box Y", heatwaveBoxY);

        JSpinner heatwaveFontSize = spinner(settings.clampedHeatwaveFontSize(), 9, 16, 1);
        row = addRow(panel, c, row, "Heatwave font size", heatwaveFontSize);

        JSpinner heatwavePollMs = spinner(settings.clampedHeatwavePollMs(), 500, 3000, 100);
        row = addRow(panel, c, row, "Heatwave poll ms", heatwavePollMs);

        JTextField heatwaveUrl = new JTextField(settings.safeHeatwaveUrl());
        row = addRow(panel, c, row, "Heatwave URL", heatwaveUrl);

        JTextArea diagnostics = new JTextArea(diagnosticsText(alias));
        diagnostics.setEditable(false);
        diagnostics.setOpaque(false);
        row = addRow(panel, c, row, "Diagnostics", diagnostics);

        Runnable apply = () -> {
            settings.startHour = (Integer) startHour.getValue();
            settings.startMinute = (Integer) startMinute.getValue();
            settings.startSecond = (Integer) startSecond.getValue();
            settings.rangeSeconds = (Integer) rangeSeconds.getValue();
            settings.endHour = (Integer) endHour.getValue();
            settings.endMinute = (Integer) endMinute.getValue();
            settings.daysToDisplay = (Integer) days.getValue();
            settings.signalCvdThreshold = (Integer) cvdThreshold.getValue();
            settings.signalDepthThreshold = (Integer) depthThreshold.getValue();
            settings.signalDepthLevels = (Integer) depthLevels.getValue();
            settings.signalMinBreakoutTicks = (Integer) minBreakoutTicks.getValue();
            settings.signalMaxBreakoutTicks = (Integer) maxBreakoutTicks.getValue();
            settings.signalMinScore = (Integer) minScore.getValue();
            settings.signalMinCvdPercentile = (Integer) minCvdPercentile.getValue();
            settings.signalMinPullingStackingPercentile = (Integer) minPullingStackingPercentile.getValue();
            settings.normalizationWindowSeconds = (Integer) normalizationWindow.getValue();
            settings.logDirectory = logDirectory.getText();
            settings.labelPrefix = prefix.getText();
            settings.signalBlockCrossMarketDivergence = blockCrossDivergence.isSelected();
            settings.showMid = showMid.isSelected();
            settings.showHeatwaveBox = showHeatwaveBox.isSelected();
            settings.heatwaveBoxX = (Integer) heatwaveBoxX.getValue();
            settings.heatwaveBoxY = (Integer) heatwaveBoxY.getValue();
            settings.heatwaveFontSize = (Integer) heatwaveFontSize.getValue();
            settings.heatwavePollMs = (Integer) heatwavePollMs.getValue();
            settings.heatwaveUrl = heatwaveUrl.getText();
            settings.showTrendTriangles = showTrendTriangles.isSelected();
            settings.showInstitutionalChartEvents = showInstitutionalChartEvents.isSelected();
            saveSettings(null, settings);
            rebuildCalculators();
        };
        addChange(startHour, apply);
        addChange(startMinute, apply);
        addChange(startSecond, apply);
        addChange(rangeSeconds, apply);
        addChange(endHour, apply);
        addChange(endMinute, apply);
        addChange(days, apply);
        addChange(cvdThreshold, apply);
        addChange(depthThreshold, apply);
        addChange(depthLevels, apply);
        addChange(minBreakoutTicks, apply);
        addChange(maxBreakoutTicks, apply);
        addChange(minScore, apply);
        addChange(minCvdPercentile, apply);
        addChange(minPullingStackingPercentile, apply);
        addChange(normalizationWindow, apply);
        addChange(heatwaveBoxX, apply);
        addChange(heatwaveBoxY, apply);
        addChange(heatwaveFontSize, apply);
        addChange(heatwavePollMs, apply);
        logDirectory.addActionListener(e -> apply.run());
        prefix.addActionListener(e -> apply.run());
        blockCrossDivergence.addActionListener(e -> apply.run());
        showMid.addActionListener(e -> apply.run());
        showHeatwaveBox.addActionListener(e -> apply.run());
        showTrendTriangles.addActionListener(e -> apply.run());
        showInstitutionalChartEvents.addActionListener(e -> apply.run());
        heatwaveUrl.addActionListener(e -> apply.run());

        return new StrategyPanel[] {panel};
    }

    private PaxOpeningRangeSettings getCalculatorSettings() {
        return loadSettings().toCalculatorSettings();
    }

    private PaxOpeningRangeUiSettings loadSettings() {
        SettingsAccess access = settingsAccess;
        if (access != null) {
            PaxOpeningRangeUiSettings stored = (PaxOpeningRangeUiSettings) access.getSettings(null, SETTINGS_KEY, PaxOpeningRangeUiSettings.class);
            if (stored != null) {
                uiSettings = stored;
            }
        }
        return uiSettings;
    }

    private void saveSettings(String alias, PaxOpeningRangeUiSettings settings) {
        uiSettings = settings;
        SettingsAccess access = settingsAccess;
        if (access != null) {
            access.setSettings(alias, SETTINGS_KEY, settings, PaxOpeningRangeUiSettings.class);
        }
        // Re-publish OR config so dashboard + bridge see operator changes
        // within the next poll cycle.
        PaxOpeningRangeSessionConfigWriter.publish(settings);
        heatwave.applySettings(settings);
        trendSignals.applySettings(
                trendFetcherShouldRun(settings.showTrendTriangles,
                                       settings.showInstitutionalChartEvents),
                settings.safeHeatwaveUrl(), settings.clampedHeatwavePollMs());
    }

    private String diagnosticsText(String alias) {
        long nowNanos = safeCurrentTime();
        InstrumentState state = instruments.get(alias);
        String base = state == null
                ? PaxOpeningRangeDiagnostics.format(null, nowNanos, loadSettings().logDirectory)
                : PaxOpeningRangeDiagnostics.format(state.featureCache, nowNanos,
                        state.signalLogger.file().toString());
        return base + "\n\n" + chartEventsDiagnostics(state);
    }

    /** Operator-visible chart-events plumbing diagnostics. Surfaced inside
     *  the strategy panel so the operator can see whether the fetcher is
     *  running, what URL it points at, and how many events have flowed
     *  into the durable history. */
    private String chartEventsDiagnostics(InstrumentState state) {
        PaxOpeningRangeUiSettings ui = loadSettings();
        int latestChart = trendSignals.latestInstitutionalChartEvents().size();
        int latestInst = trendSignals.latestInstitutionalEvents().size();
        int historyChart = (state == null) ? 0 : state.chartEventMarkers.size();
        int historyInst = (state == null) ? 0 : state.institutionalMarkers.size();
        boolean fetcherWanted = trendFetcherShouldRun(
                ui.showTrendTriangles, ui.showInstitutionalChartEvents);
        StringBuilder sb = new StringBuilder(384);
        sb.append("Chart events plumbing\n");
        sb.append("  snapshot URL:                ").append(trendSignals.currentUrl()).append('\n');
        sb.append("  fetcher running:             ").append(trendSignals.isRunning()).append('\n');
        sb.append("  fetcher should run (OR):     ").append(fetcherWanted).append('\n');
        sb.append("  showInstitutionalChartEvents:").append(ui.showInstitutionalChartEvents).append('\n');
        sb.append("  showTrendTriangles (legacy): ").append(ui.showTrendTriangles).append('\n');
        sb.append("  latest chart_events count:   ").append(latestChart).append('\n');
        sb.append("  durable chart history count: ").append(historyChart).append('\n');
        sb.append("  latest signals count:        ").append(latestInst).append('\n');
        sb.append("  durable signals history:     ").append(historyInst).append('\n');
        sb.append("  consecutive fetch failures:  ").append(trendSignals.consecutiveFailures()).append('\n');
        sb.append("  last failure reason:         ").append(trendSignals.lastFailureReason());
        return sb.toString();
    }

    private long safeCurrentTime() {
        try {
            return provider.getCurrentTime();
        } catch (RuntimeException e) {
            return 0L;
        }
    }

    private void rebuildCalculators() {
        PaxOpeningRangeSettings calculatorSettings = getCalculatorSettings();
        PaxOpeningRangeSignalSettings signalSettings = loadSettings().toSignalSettings();
        for (InstrumentState state : instruments.values()) {
            state.resetCalculator(calculatorSettings, signalSettings);
        }
        backfillAll();
        repaintAll();
    }

    private void repaintAll() {
        for (PaxPainter painter : painters.values()) {
            painter.update();
        }
    }

    private static JSpinner spinner(int value, int min, int max, int step) {
        return new JSpinner(new SpinnerNumberModel(value, min, max, step));
    }

    private static void addChange(JSpinner spinner, Runnable action) {
        spinner.addChangeListener(e -> action.run());
    }

    private static int addRow(StrategyPanel panel, GridBagConstraints c, int row, String label, java.awt.Component... components) {
        c.gridy = row;
        c.gridx = 0;
        c.weightx = 0;
        panel.add(new JLabel(label), c);
        c.weightx = 1;
        for (int i = 0; i < components.length; i++) {
            c.gridx = i + 1;
            panel.add(components[i], c);
        }
        return row + 1;
    }

    private final class InstrumentState {
        final String alias;
        final InstrumentInfo info;
        final double pips;
        final Object lock = new Object();
        volatile PaxOpeningRangeCalculator calculator;
        volatile PaxOpeningRangeSignalEngine signalEngine;
        volatile PaxOpeningRangeSignalQualityGate qualityGate;
        volatile PaxOpeningRangeOrderFlowTracker orderFlow;
        volatile PaxOpeningRangeRollingStats cvdStats;
        volatile PaxOpeningRangeRollingStats pullingStackingStats;
        volatile PaxOpeningRangeRollingPercentile cvdPercentiles;
        volatile PaxOpeningRangeRollingPercentile pullingStackingPercentiles;
        volatile PaxOpeningRangeRollingPercentile rangeWidthPercentiles;
        final Set<LocalDate> sampledRangeDates = ConcurrentHashMap.newKeySet();
        final PaxOpeningRangeSignalCsvLogger signalLogger;
        final PaxOpeningRangeFeatureCache featureCache = new PaxOpeningRangeFeatureCache(FEATURE_CACHE_HISTORY_SIZE);
        LocalDate orderFlowDate;
        long lastRepaintTime;
        long lastDepthSignalTime;
        /** Signature of the persistent OR shape inputs (completed days,
         * high/low/mid, dynamic level counts + tips, fallback day, OR
         * settings hash). Persistent shapes are rebuilt only when this
         * signature changes — market ticks that do not alter OR structure
         * leave the chart shapes untouched. Empty string means "no
         * persistent shapes have been drawn yet". */
        String lastOrSignature = "";
        // CSV-backed chart-only fallback. Populated by PaxPainter.update() when
        // the live calculator has no completed day; cleared as soon as live
        // data shows up. Never read by trading/signal code.
        volatile PaxOpeningRangeDayState fallbackDay;
        volatile long fallbackLoadedAtMs;
        volatile String fallbackCsvPath = "";
        volatile String fallbackLoggedKey = "";
        // Triangle marker dedup + history. Mutated only by PaxPainter.update()
        // (Bookmap callback thread). The deque holds visible triangle events
        // so a full `clear()` + redraw on every repaint preserves the chart
        // across pans/zooms. Cap at 8 keeps the chart readable.
        final Deque<TrendTriangleEvent> liveTriangles = new ArrayDeque<>(MAX_LIVE_TRIANGLES + 1);
        /** Durable institutional-signal marker history. Append-only across
         *  polls; empty institutional_signals on a later poll does NOT
         *  remove already-plotted markers. Cleared only on instrument
         *  dispose / module restart. */
        final PaxInstitutionalSignalsHistory institutionalMarkers =
                new PaxInstitutionalSignalsHistory(MAX_LIVE_INSTITUTIONAL_MARKERS);
        /** Durable chart-events history (the full evidence trail). Same
         *  persistence contract as institutionalMarkers. */
        final PaxInstitutionalChartEventsHistory chartEventMarkers =
                new PaxInstitutionalChartEventsHistory(MAX_LIVE_CHART_EVENT_MARKERS);
        String lastEmittedKind = "";
        long lastEmittedBucketEnteredMs = 0L;
        String lastNativeSignalMarkerKey = "";

        InstrumentState(String alias, InstrumentInfo info, double pips, PaxOpeningRangeSettings settings) {
            this.alias = alias;
            this.info = info;
            this.pips = pips;
            this.calculator = new PaxOpeningRangeCalculator(settings, info.symbol, pips);
            PaxOpeningRangeSignalSettings signalSettings = loadSettings().toSignalSettings();
            this.signalEngine = new PaxOpeningRangeSignalEngine(signalSettings, pips);
            this.qualityGate = new PaxOpeningRangeSignalQualityGate(loadSettings().toQualitySettings());
            this.orderFlow = new PaxOpeningRangeOrderFlowTracker(pips, signalSettings.depthLevels());
            this.cvdStats = new PaxOpeningRangeRollingStats(loadSettings().normalizationWindowSeconds);
            this.pullingStackingStats = new PaxOpeningRangeRollingStats(loadSettings().normalizationWindowSeconds);
            this.cvdPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().normalizationWindowSeconds);
            this.pullingStackingPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().normalizationWindowSeconds);
            this.rangeWidthPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().daysToDisplay * 86_400);
            this.signalLogger = new PaxOpeningRangeSignalCsvLogger(PaxOpeningRangeLogPath.signalLogPath(loadSettings().logDirectory, info.symbol));
            setWaitingSignal();
        }

        void resetCalculator(PaxOpeningRangeSettings settings) {
            resetCalculator(settings, loadSettings().toSignalSettings());
        }

        void resetCalculator(PaxOpeningRangeSettings settings, PaxOpeningRangeSignalSettings signalSettings) {
            synchronized (lock) {
                this.calculator = new PaxOpeningRangeCalculator(settings, info.symbol, pips);
                this.signalEngine = new PaxOpeningRangeSignalEngine(signalSettings, pips);
                this.qualityGate = new PaxOpeningRangeSignalQualityGate(loadSettings().toQualitySettings());
                this.orderFlow = new PaxOpeningRangeOrderFlowTracker(pips, signalSettings.depthLevels());
                this.cvdStats = new PaxOpeningRangeRollingStats(loadSettings().normalizationWindowSeconds);
                this.pullingStackingStats = new PaxOpeningRangeRollingStats(loadSettings().normalizationWindowSeconds);
                this.cvdPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().normalizationWindowSeconds);
                this.pullingStackingPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().normalizationWindowSeconds);
                this.rangeWidthPercentiles = new PaxOpeningRangeRollingPercentile(loadSettings().daysToDisplay * 86_400);
                this.orderFlow.reset();
                this.cvdStats.reset();
                this.pullingStackingStats.reset();
                this.cvdPercentiles.reset();
                this.pullingStackingPercentiles.reset();
                this.rangeWidthPercentiles.reset();
                this.sampledRangeDates.clear();
                this.featureCache.reset();
                setWaitingSignal();
                this.orderFlowDate = null;
                this.lastRepaintTime = 0;
                this.lastNativeSignalMarkerKey = "";
            }
        }

        void resetOrderFlowIfNewDay(LocalDate date) {
            if (orderFlowDate == null || !orderFlowDate.equals(date)) {
                orderFlow.reset();
                orderFlowDate = date;
            }
        }

        void updateSignal(LocalDateTime time) {
            synchronized (lock) {
                resetOrderFlowIfNewDay(time.toLocalDate());
                PaxOpeningRangeDayState day = calculator.getDay(time.toLocalDate());
                PaxOpeningRangeMarketState market = orderFlow.snapshot();
                double cvdZ = cvdStats.zScore(market.cvdDelta());
                double psZ = pullingStackingStats.zScore(market.netDepthDelta());
                int cvdPercentile = cvdPercentiles.percentile(market.cvdDelta());
                int psPercentile = pullingStackingPercentiles.percentile(market.netDepthDelta());
                int rangePercentile = rangePercentile(day, time);
                PaxOpeningRangeSignal signal = withStats(signalEngine.evaluate(day, market), cvdZ, psZ, cvdPercentile, psPercentile,
                        PaxOpeningRangeRangeQuality.fromPercentile(rangePercentile), ageSeconds(day, time));
                long timeNanos = toNanos(time);
                signal = qualityGate.apply(signal, crossMarketState.statusFor(info.symbol, timeNanos, time.toLocalDate()));
                PaxOpeningRangeFeatureSnapshot snapshot = featureCache.update(timeNanos, market, signal);
                cvdStats.add(timeNanos, market.cvdDelta());
                pullingStackingStats.add(timeNanos, market.netDepthDelta());
                cvdPercentiles.add(timeNanos, market.cvdDelta());
                pullingStackingPercentiles.add(timeNanos, market.netDepthDelta());
                crossMarketState.update(info.symbol, snapshot.signal(), timeNanos, time.toLocalDate());
                if (day.isComplete()) {
                    signalLogger.logIfChanged(info.symbol, time, day.getHigh(), day.getLow(), snapshot.market(), snapshot.signal());
                }
                publishNativeSignalMarkerIfNeeded(snapshot);
            }
        }

        private void publishNativeSignalMarkerIfNeeded(PaxOpeningRangeFeatureSnapshot snapshot) {
            // Gate the legacy CVD/depth-driven native LONG/SHORT marker
            // publisher. Default ON — institutional_signals is the
            // authoritative buy/sell source on the chart. The UI flag is
            // reversible for diagnostics.
            PaxOpeningRangeUiSettings ui = uiSettings;
            if (ui != null && PaxNativeMarkerGate.shouldSuppress(ui.gateNativeMarkersOnInstitutional)) {
                return;
            }
            String key = openingRangeMarkerKey(alias, snapshot);
            if (key.isEmpty() || key.equals(lastNativeSignalMarkerKey)) {
                return;
            }
            if (nativeSignalMarkers.publish(alias, pips, snapshot)) {
                lastNativeSignalMarkerKey = key;
            }
        }

        private void setWaitingSignal() {
            featureCache.update(0, new PaxOpeningRangeMarketState(Double.NaN, 0, 0, 0, 0),
                    PaxOpeningRangeSignal.waitSignal("Waiting for live market data."));
        }

        private PaxOpeningRangeSignal withStats(PaxOpeningRangeSignal original, double cvdZ, double psZ,
                int cvdPercentile, int psPercentile, String rangeQuality, long ageSeconds) {
            return new PaxOpeningRangeSignal(original.action(), original.bias(), original.confidence(),
                    original.reason(), original.score(), original.maxScore(), original.evidence(),
                    original.location(), original.distanceTicks(), original.rangeWidth(), ageSeconds, original.cvdDelta(),
                    original.pullingStackingDelta(), cvdZ, psZ, cvdPercentile, psPercentile, rangeQuality);
        }

        private long ageSeconds(PaxOpeningRangeDayState day, LocalDateTime time) {
            if (day == null || day.getCompletedAt() == null || time == null || time.isBefore(day.getCompletedAt())) {
                return 0;
            }
            return java.time.Duration.between(day.getCompletedAt(), time).getSeconds();
        }

        private int rangePercentile(PaxOpeningRangeDayState day, LocalDateTime time) {
            if (day == null || !day.isComplete()) {
                return 0;
            }
            double rangeWidth = day.getHigh() - day.getLow();
            int percentile = rangeWidthPercentiles.percentile(rangeWidth);
            if (sampledRangeDates.add(day.getDate())) {
                rangeWidthPercentiles.add(toNanos(time), rangeWidth);
            }
            return percentile;
        }

        boolean shouldRepaint(long eventTime) {
            // Legacy boolean shim. Production code uses nextRepaintNeeds.
            return !nextRepaintNeeds(eventTime).isNone();
        }

        /**
         * Decide which shape buckets need rebuilding for this tick. Both
         * dirty bits are eagerly consumed (single-pipe — neither is
         * short-circuited).
         *
         * <p>Persistent OR shapes are rebuilt only when the OR structure
         * signature changes. Market ticks that don't change OR structure
         * never force a persistent rebuild. The legacy 5s throttle is now
         * informational only — it bounds how often the signature is
         * computed when a market tick fires, but no longer "expires" the
         * persistent shapes.
         */
        RepaintNeeds nextRepaintNeeds(long eventTime) {
            boolean heatwaveDirty = consumeHeatwaveDirty();
            boolean trendDirty    = consumeTrendSignalDirty();
            boolean persistent    = false;

            // Only compute the OR signature once per throttle window — it
            // walks the calculator's day map. Cheap, but no need to do it
            // on every NQ tick.
            if ((eventTime - lastRepaintTime) >= REPAINT_THROTTLE_NANOS) {
                lastRepaintTime = eventTime;
                String sig = computeOrSignature();
                if (!sig.equals(lastOrSignature)) {
                    lastOrSignature = sig;
                    persistent = true;
                }
            }
            return new RepaintNeeds(persistent, trendDirty, heatwaveDirty);
        }

        /**
         * Stable signature of every input that affects persistent OR shape
         * rendering. Lock-guarded read of the calculator + fallback state.
         * Any change here means the chart-anchored shapes need a rebuild.
         */
        String computeOrSignature() {
            StringBuilder sb = new StringBuilder(256);
            PaxOpeningRangeUiSettings ui = loadSettings();
            // Settings inputs that change line endpoints / level counts /
            // visual state.
            sb.append("S=").append(ui.startHour).append(':').append(ui.startMinute)
              .append(':').append(ui.startSecond)
              .append('/').append(ui.rangeSeconds)
              .append('/').append(ui.endHour).append(':').append(ui.endMinute)
              .append('/').append(ui.daysToDisplay)
              .append('/').append(ui.showMid)
              .append('/').append(ui.showHeatwaveBox)
              .append('/').append(ui.showTrendTriangles)
              .append(';');
            synchronized (lock) {
                for (PaxOpeningRangeDayState day : calculator.getDays()) {
                    if (!day.isComplete()) continue;
                    sb.append("D=").append(day.getDate())
                      .append('/').append(day.getHigh())
                      .append('/').append(day.getLow())
                      .append('/').append(day.getMid())
                      .append('/').append(day.getUpperLevels().size())
                      .append('/').append(day.getLowerLevels().size());
                    if (!day.getUpperLevels().isEmpty()) {
                        sb.append('/').append(day.getUpperLevels()
                                .get(day.getUpperLevels().size() - 1).price());
                    }
                    if (!day.getLowerLevels().isEmpty()) {
                        sb.append('/').append(day.getLowerLevels()
                                .get(day.getLowerLevels().size() - 1).price());
                    }
                    sb.append(';');
                }
            }
            PaxOpeningRangeDayState fb = fallbackDay;
            if (fb != null) {
                sb.append("FB=").append(fb.getDate())
                  .append('/').append(fb.getHigh())
                  .append('/').append(fb.getLow())
                  .append(';');
            }
            return sb.toString();
        }

        boolean shouldUpdateSignalForDepth(long eventTime) {
            if (eventTime - lastDepthSignalTime < DEPTH_SIGNAL_THROTTLE_NANOS) {
                return false;
            }
            lastDepthSignalTime = eventTime;
            return true;
        }

        void markDepthSignalUpdated(long eventTime) {
            lastDepthSignalTime = eventTime;
        }

        void dispose() {
            signalLogger.close();
        }
    }

    private static final class PriceRange {
        final double high;
        final double low;

        private PriceRange(double high, double low) {
            this.high = high;
            this.low = low;
        }

        static PriceRange empty() {
            return new PriceRange(Double.NaN, Double.NaN);
        }

        boolean hasTrades() {
            return !Double.isNaN(high) && !Double.isNaN(low);
        }

        PriceRange include(double price) {
            if (Double.isNaN(price)) {
                return this;
            }
            if (!hasTrades()) {
                return new PriceRange(price, price);
            }
            return new PriceRange(Math.max(high, price), Math.min(low, price));
        }
    }

    private final class PaxPainter implements ScreenSpacePainterAdapter {
        private final String alias;
        private final ScreenSpaceCanvas canvas;
        /**
         * Persistent chart-anchored OR shapes: high/low/mid lines, dynamic
         * extension lines, OR labels, "no OR" status text. Rebuilt only when
         * the OR structure signature changes, on onMoveEnd, or via explicit
         * rebuild/backfill paths.
         */
        private final List<CanvasIcon> persistentShapes = Collections.synchronizedList(new ArrayList<>());
        /**
         * Chart-anchored trend triangle shapes. Independent of persistent OR
         * shapes so a fresh eligible trend signal can render immediately
         * without re-drawing every OR line + label.
         */
        private final List<CanvasIcon> triangleShapes   = Collections.synchronizedList(new ArrayList<>());
        /**
         * Volatile pixel-anchored overlay shapes: Heatwave Quant Box,
         * signal badge. Rebuilt on every dashboard-poll dirty bit. Isolated
         * from persistentShapes so polling never flickers the OR lines.
         */
        private final List<CanvasIcon> volatileShapes   = Collections.synchronizedList(new ArrayList<>());
        /** Min-interval gap + emergency degradation guard. Bounded so a
         *  stalled Pax AI cannot trigger storms of triangle/overlay rebuilds
         *  on the chart callback thread. Persistent OR rebuilds bypass this
         *  guard - OR levels are load-bearing. */
        private final PaxRepaintGuard repaintGuard = new PaxRepaintGuard();
        /** Memoized last rendered triangle bucket key. When unchanged across
         *  consecutive ticks the entire clear+redraw is skipped - including
         *  the no-shape outcome (e.g. dashboard ineligible, signal null,
         *  showTrendTriangles=false). The sentinel TRIANGLE_KEY_UNSET marks
         *  "never rendered yet" so the first tick always renders. */
        private String lastTriangleRenderKey = TRIANGLE_KEY_UNSET;
        /** Memoized last rendered overlay key + cached PreparedImage. When
         *  the underlying model signature and font size are unchanged we
         *  reuse the prior image instead of allocating a fresh
         *  ~320x200 BufferedImage on every refresh. Sentinel OVERLAY_KEY_UNSET
         *  marks "never rendered yet". */
        private String lastOverlayKey = OVERLAY_KEY_UNSET;
        private PreparedImage lastOverlayImage;
        private int lastOverlayWidth;
        private int lastOverlayHeight;

        PaxPainter(String alias, ScreenSpaceCanvas canvas) {
            this.alias = alias;
            this.canvas = canvas;
        }

        @Override
        public void onMoveEnd() {
            // Chart pan/zoom — coordinate system effectively changed; rebuild
            // every bucket to be safe.
            update();
        }

        /** Dispatch by needs vector. Each bucket is independent. Overlay
         *  and triangle buckets pass through {@link #repaintGuard}: min
         *  interval gap + emergency degradation. Persistent OR shapes
         *  bypass the guard so OR levels never drop during a degraded
         *  window. The total wall-clock cost of this call is fed back into
         *  the guard so a slow paint triggers automatic backoff. */
        synchronized void applyNeeds(RepaintNeeds needs) {
            if (needs == null || needs.isNone()) return;
            long startNanos = System.nanoTime();
            long nowMs = System.currentTimeMillis();
            try {
                if (needs.persistent) updatePersistent();
                if (needs.triangles) {
                    if (repaintGuard.allowTriangle(nowMs, startNanos)) {
                        updateTriangles();
                    } else {
                        trendSignalDirty.set(true);
                        maybeLogDegradation(nowMs, "triangles");
                    }
                }
                if (needs.overlay) {
                    if (repaintGuard.allowOverlay(nowMs, startNanos)) {
                        updateOverlay();
                    } else {
                        heatwaveDirty.set(true);
                        maybeLogDegradation(nowMs, "overlay");
                    }
                }
            } finally {
                long durationNanos = System.nanoTime() - startNanos;
                repaintGuard.recordApplyDurationNanos(durationNanos, startNanos + durationNanos);
            }
        }

        /** Full repaint of every bucket. Used by onMoveEnd, init, and
         *  explicit rebuild paths. Force-syncs the InstrumentState's
         *  lastOrSignature so the next tick-driven check doesn't fire a
         *  redundant persistent rebuild. The guard's min-interval timers
         *  are reset here so onMoveEnd never gets short-circuited - the
         *  operator is actively interacting with the chart. The triangle
         *  and overlay buckets are invoked with force=true so the
         *  per-bucket render-key memoization cannot suppress the redraw
         *  (e.g. onMoveEnd needs to re-anchor the canvas regardless of
         *  whether the upstream model changed). The overlay image cache
         *  IS preserved by clearVolatile() so the forced repaint still
         *  reuses the prior PreparedImage when the model is unchanged. */
        synchronized void update() {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            repaintGuard.resetMinIntervals();
            updatePersistent();
            updateTriangles(true);
            updateOverlay(true);
            state.lastOrSignature = state.computeOrSignature();
        }

        /** Rate-limited heads-up that the guard tripped. Throttled across
         *  the whole module so multiple instruments don't multiply the
         *  Bookmap log line count. */
        private void maybeLogDegradation(long nowMs, String bucket) {
            if (!repaintGuard.isDegraded(System.nanoTime())) return;
            if (nowMs - lastDegradationLogMs < TRIANGLE_LOG_THROTTLE_MS) return;
            lastDegradationLogMs = nowMs;
            try {
                Log.info("OpenRange repaint degraded - skipping " + bucket
                        + " bucket alias=" + alias
                        + " lastDurationNanos=" + repaintGuard.lastDurationNanos()
                        + " budgetNanos=" + repaintGuard.overrunBudgetNanos()
                        + " degradationEntered=" + repaintGuard.degradationEnteredCount());
            } catch (Throwable ignored) { /* Log unavailable in tests */ }
        }

        /** Persistent OR shapes only — torn down and re-added from current
         *  calculator state. CSV fallback hydrate still happens here. */
        synchronized void updatePersistent() {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            clearPersistent();
            boolean drewDay = false;
            Set<LocalDate> drawnDates = new HashSet<>();
            List<PaxOpeningRangeDayState> daysSnapshot;
            synchronized (state.lock) {
                daysSnapshot = new ArrayList<>(state.calculator.getDays());
            }
            for (PaxOpeningRangeDayState day : daysSnapshot) {
                if (!day.isComplete()) continue;
                drawDay(state, day);
                drewDay = true;
                drawnDates.add(day.getDate());
            }
            PaxOpeningRangeDayState fallback = resolveFallback(state);
            if (fallback != null) {
                if (drawnDates.contains(fallback.getDate())) {
                    state.fallbackDay = null;
                    state.fallbackCsvPath = "";
                } else {
                    drawDay(state, fallback);
                    drewDay = true;
                }
            }
            if (!drewDay) {
                PaxOpeningRangeSettings settings = getCalculatorSettings();
                addStatus("OpenRange waiting: no completed OR. Set start time before a live " + settings.rangeSeconds() + "s window.");
            }
        }

        /** Dashboard conviction triangle shapes only. OpenRange buy/sell
         *  markers are emitted through the native Indicator API. Short-
         *  circuits when the computed render key matches the prior render
         *  - repeated dirty-bit fires with no semantic change cost zero
         *  canvas mutation. Crucially this is true even for the
         *  "no shapes intended" outcome (dashboard ineligible, signal
         *  null, showTrendTriangles=false); the previous gate required
         *  triangleShapes to be non-empty, which let those no-op states
         *  storm clear+redraw on every poll. */
        synchronized void updateTriangles() {
            updateTriangles(false);
        }

        /** Force overload used by {@link #update()} so onMoveEnd / explicit
         *  rebuild paths bypass the render-key memoization. The cached
         *  PreparedImage for the overlay survives independently via
         *  clearVolatile()'s deliberate non-clear of lastOverlayImage. */
        private void updateTriangles(boolean force) {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            PaxOpeningRangeUiSettings ui = loadSettings();
            boolean showLegacy = ui.showTrendTriangles;
            boolean showChartEvents = ui.showInstitutionalChartEvents;
            PaxTrendSignalModel signal = trendSignals.snapshot();
            // Merge unconditionally so new institutional ids land in history
            // even when the renderKey would otherwise short-circuit. New ids
            // bump sizes -> renderKey differs -> we fall through to
            // clearTriangles + redraw. Empty polls add nothing -> key stable
            // -> canvas preserved.
            int newInst = state.institutionalMarkers.merge(trendSignals.latestInstitutionalEvents());
            int newChart = state.chartEventMarkers.merge(trendSignals.latestInstitutionalChartEvents());
            int instSize = state.institutionalMarkers.size();
            int chartSize = state.chartEventMarkers.size();
            String renderKey = triangleRenderKey(showLegacy, signal,
                    state.lastEmittedKind, state.lastEmittedBucketEnteredMs,
                    state.liveTriangles.size())
                    + "|CE=" + showChartEvents
                    + "|INS=" + instSize
                    + (newInst > 0 ? "|+" + newInst : "")
                    + "|CHE=" + chartSize
                    + (newChart > 0 ? "|+" + newChart : "");
            if (!force && renderKey.equals(lastTriangleRenderKey)) {
                return;
            }
            clearTriangles();
            // Legacy trend triangles + institutional entry markers draw only
            // when the legacy layer is on. Institutional chart events draw
            // independently of the legacy layer — both gated by their own
            // UI flag.
            if (showLegacy) {
                addTrendTriangles(state);
                addInstitutionalMarkers(state);
            }
            if (showChartEvents) {
                addInstitutionalChartEvents(state);
            }
            lastTriangleRenderKey = renderKey;
        }

        /** Volatile overlay shapes only - Heatwave Quant Box and signal
         *  badge. Fired on heatwaveDirty at the 1Hz dashboard cadence.
         *  Short-circuits when the overlay key (model semantic signature +
         *  font size + age bucket) is unchanged so we don't rebuild a
         *  ~320x200 BufferedImage on every fetcher repaint. When the key
         *  changes but the cached image survives (e.g. onMoveEnd rebuilt
         *  shapes without the underlying model changing) the cached
         *  PreparedImage is reused too. The previous gate required
         *  volatileShapes to be non-empty, which storm-fired no-op
         *  paths (badge hidden + no heatwave + idle ticks). */
        synchronized void updateOverlay() {
            updateOverlay(false);
        }

        private void updateOverlay(boolean force) {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            PaxOpeningRangeUiSettings ui = loadSettings();
            String overlayKey = computeOverlayKey(ui, state);
            if (!force && overlayKey.equals(lastOverlayKey)) {
                return;
            }
            clearVolatile();
            if (ui.showHeatwaveBox) {
                boolean cacheHit = lastOverlayImage != null
                        && overlayKey.equals(lastOverlayKey);
                addHeatwaveBox(ui, cacheHit);
            } else {
                addSignalStatus(state.featureCache.latest());
            }
            lastOverlayKey = overlayKey;
        }

        /** Stable key for the overlay bucket. Heatwave path: model semantic
         *  signature + font size + ~1s age bucket so the age tick advances
         *  the cache without rebuilding more often than that. Badge path:
         *  badge text + color state. */
        private String computeOverlayKey(PaxOpeningRangeUiSettings ui, InstrumentState state) {
            if (ui.showHeatwaveBox) {
                long nowMs = System.currentTimeMillis();
                PaxHeatwaveModel model = heatwave.effectiveModel(nowMs);
                if (model == null) {
                    model = PaxHeatwaveModel.noData(nowMs);
                }
                long ageBucket = (nowMs - model.fetchedAtMs) / 1000L;
                return "HW|" + ui.clampedHeatwaveFontSize() + "|"
                        + ui.clampedHeatwaveBoxX() + "|" + ui.clampedHeatwaveBoxY() + "|"
                        + ageBucket + "|" + heatwaveModelKey(model);
            }
            PaxOpeningRangeFeatureSnapshot snap = state.featureCache.latest();
            if (snap == null || snap.signal() == null) return "BADGE|EMPTY";
            return "BADGE|" + snap.colorState() + "|" + snap.badgeText();
        }

        private String heatwaveModelKey(PaxHeatwaveModel model) {
            return PaxOpeningRangeModule.heatwaveModelKey(model);
        }

        /**
         * CSV-backed chart-only fallback. Returns the most recently cached
         * DayState (within FALLBACK_TTL_MS); otherwise scans
         * <logDir>/openrange-signals-*.csv for the newest row matching
         * state.info.symbol, caches it, and returns it. Returns null when no
         * valid row exists. Never writes into the trading calculator.
         */
        private PaxOpeningRangeDayState resolveFallback(InstrumentState state) {
            long nowMs = System.currentTimeMillis();
            PaxOpeningRangeDayState cached = state.fallbackDay;
            if (cached != null && nowMs - state.fallbackLoadedAtMs < FALLBACK_TTL_MS) {
                return cached;
            }
            PaxOpeningRangeUiSettings ui = loadSettings();
            String logDir = ui.logDirectory == null || ui.logDirectory.isBlank()
                    ? "build\\logs" : ui.logDirectory;
            List<Path> logDirs = new ArrayList<>();
            logDirs.add(Path.of(logDir));
            logDirs.add(Path.of("D:\\BookmapLogs"));
            java.util.Optional<PaxOpeningRangeChartFallback.Result> res =
                    PaxOpeningRangeChartFallback.loadLatest(
                            logDirs, state.info.symbol, state.pips);
            state.fallbackLoadedAtMs = nowMs;
            if (res.isEmpty()) {
                state.fallbackDay = null;
                state.fallbackCsvPath = "";
                return null;
            }
            PaxOpeningRangeChartFallback.Result r = res.get();
            r.day.setLastUpdateTime(getCalculatorSettings().lineEndDateTime(r.day.getDate()));
            state.fallbackDay = r.day;
            String csvPath = r.csvPath.toString();
            state.fallbackCsvPath = csvPath;
            String key = state.info.symbol + "|" + csvPath;
            if (!key.equals(state.fallbackLoggedKey)) {
                Log.info("OpenRange CSV chart fallback hydrated"
                        + " symbol=" + state.info.symbol
                        + " sessionDate=" + r.day.getDate()
                        + " rowTime=" + r.rowTime
                        + " high=" + r.day.getHigh()
                        + " low=" + r.day.getLow()
                        + " csv=" + csvPath);
                state.fallbackLoggedKey = key;
            }
            return r.day;
        }

        /**
         * Dedup + render trend triangles. Runs only on the Bookmap callback
         * thread (PaxPainter.update is synchronized). Mutates
         * state.liveTriangles, state.lastEmittedKind, state.lastEmittedBucketEnteredMs.
         */
        private void addTrendTriangles(InstrumentState state) {
            PaxTrendSignalModel signal = trendSignals.snapshot();
            long nowMs = System.currentTimeMillis();
            // Emit-new logic: PaxTrendTriangleDedup.shouldEmit applies the
            // full gate (eligible / fresh / renderable / valid mid / valid
            // tick / dedup tuple). Centralising the rule there means we
            // don't duplicate stale/eligibility checks at the call site.
            boolean emitted = false;
            if (PaxTrendTriangleDedup.shouldEmit(signal, nowMs, TREND_STALE_AGE_MS,
                    state.lastEmittedKind, state.lastEmittedBucketEnteredMs,
                    state.pips)) {
                TrendTriangleEvent evt = new TrendTriangleEvent(
                        signal.kind, signal.bucketEnteredMs, signal.eventMs, signal.mid);
                state.liveTriangles.addLast(evt);
                while (state.liveTriangles.size() > MAX_LIVE_TRIANGLES) {
                    state.liveTriangles.pollFirst();
                }
                state.lastEmittedKind = signal.kind.name();
                state.lastEmittedBucketEnteredMs = signal.bucketEnteredMs;
                emitted = true;
                // No native consumer.accept here — Bookmap's native Marker
                // path is one-shot and gets cleared by the chart layer on
                // subsequent polls. Persistence comes from the painter
                // redrawing state.liveTriangles + state.institutionalMarkers
                // every cycle from durable in-memory history.
            }
            // Redraw every live triangle. update() called clear() at top,
            // so each refresh re-adds the deque contents.
            for (TrendTriangleEvent evt : state.liveTriangles) {
                drawTrendTriangle(state, evt);
            }
            logTriangleDiagnostics(state, signal, nowMs, emitted);
        }

        /** Throttled audit lines so the operator can see in Bookmap's log
         *  whether the triangle pipeline is emitting, why it is skipping,
         *  and how many shapes are currently on the canvas. One emit + one
         *  skip-reason per minute is enough; spamming every poll would
         *  drown the bridge logs. */
        private void logTriangleDiagnostics(InstrumentState state,
                                            PaxTrendSignalModel signal,
                                            long nowMs,
                                            boolean emitted) {
            int liveCount;
            synchronized (state.liveTriangles) {
                liveCount = state.liveTriangles.size();
            }
            if (emitted) {
                if (nowMs - lastTriangleEmitLogMs >= TRIANGLE_LOG_THROTTLE_MS) {
                    lastTriangleEmitLogMs = nowMs;
                    try {
                        Log.info("OpenRange trend triangle emitted"
                                + " kind="   + (signal != null ? signal.kind : "null")
                                + " bucket=" + (signal != null ? signal.bucketEnteredMs : 0L)
                                + " mid="    + (signal != null ? signal.mid : Double.NaN)
                                + " eventMs=" + (signal != null ? signal.eventMs : 0L)
                                + " liveCount=" + liveCount);
                    } catch (Throwable ignored) { /* Log unavailable in tests */ }
                }
                return;
            }
            if (nowMs - lastTriangleSkipLogMs < TRIANGLE_LOG_THROTTLE_MS) return;
            // Classify the skip reason so the operator can audit live state.
            String reason;
            if (signal == null)                                            reason = "no_snapshot_yet";
            else if (!signal.eligible)                                     reason = "dashboard_ineligible:" + safeString(signal.blockedReason);
            else if (signal.isStale(nowMs, TREND_STALE_AGE_MS))            reason = "stale_age_ms=" + (nowMs - signal.fetchedAtMs);
            else if (!signal.kind.isRenderable())                          reason = "non_renderable_kind:" + signal.kind;
            else if (signal.eventMs <= 0L)                                 reason = "invalid_event_ms";
            else if (!Double.isFinite(signal.mid) || signal.mid <= 0.0)    reason = "invalid_mid:" + signal.mid;
            else if (!Double.isFinite(state.pips) || state.pips <= 0.0)    reason = "invalid_tick_size:" + state.pips;
            else                                                            reason = "duplicate_bucket";
            lastTriangleSkipLogMs = nowMs;
            try {
                Log.info("OpenRange trend triangle skipped"
                        + " reason=" + reason
                        + " liveCount=" + liveCount);
            } catch (Throwable ignored) { /* Log unavailable in tests */ }
        }

        private static String safeString(String s) {
            return s == null || s.isEmpty() ? "?" : s;
        }

        /** Merge the latest fetched institutional_signals into the durable
         *  per-instrument history and redraw EVERY event currently in history.
         *
         *  <p>Persistence contract: the latest fetched array adds new markers
         *  by id; it never defines the whole render state. Empty
         *  institutional_signals on a poll adds nothing and removes nothing.
         *  The full history is redrawn every cycle so clearTriangles() at
         *  the top of updateTriangles() doesn't lose previously-plotted
         *  markers.</p>
         */
        private void addInstitutionalMarkers(InstrumentState state) {
            // History merge already happened in updateTriangles() before the
            // renderKey check; here we just redraw every event in the durable
            // deque. Empty institutional_signals on the latest poll added
            // nothing — prior history survives untouched.
            for (PaxInstitutionalSignalEvent evt : state.institutionalMarkers.snapshot()) {
                drawInstitutionalMarker(state, evt);
            }
        }

        /** Render one institutional signal event as a labelImage marker
         *  anchored at the event's level price + event's timestamp.
         *
         *  <p>Distinct glyphs per execution_read class so PAY entries don't
         *  look the same as WAIT / STAND_DOWN / SCRATCH non-entry markers.
         *  Uses the labelImage primitive (known reliable on Bookmap 7.4) and
         *  the triangleShapes clear list — meaning the shape is removed and
         *  re-added on every refresh from the durable history deque.</p>
         */
        /** Redraw the full evidence-trail history of institutional chart
         *  events. Stagger y-offset by severity rank so overlapping events
         *  at the same level/time don't cover each other. */
        private void addInstitutionalChartEvents(InstrumentState state) {
            java.util.List<PaxInstitutionalChartEvent> history = state.chartEventMarkers.snapshot();
            // Group by rendered chart-time + side + nearby price to apply a
            // per-group stagger. Do not include label/marker text: WATCH,
            // TCH, SWP, ACC, etc. can share one visual spot and must stack.
            // Drawing in insertion order; the per-event stagger is computed
            // from severityRank + a small per-event ordinal within the same
            // collision bucket.
            java.util.HashMap<String, Integer> bucketOrdinal = new java.util.HashMap<>();
            for (PaxInstitutionalChartEvent evt : history) {
                String bucketKey = chartEventCollisionKey(evt, state.pips);
                int ord = bucketOrdinal.getOrDefault(bucketKey, 0);
                bucketOrdinal.put(bucketKey, ord + 1);
                drawInstitutionalChartEvent(state, evt, ord);
            }
        }

        /** Render one chart event as a labelImage marker anchored at
         *  event.price + event.timestamp_ms.
         *
         *  Stagger semantics: the {@code bucketOrdinal} positions the label
         *  N rows away from the level so multiple events at the same
         *  (label, timestamp) don't overlap. The severity rank biases
         *  ENTRY/EXIT to sit closer to the level; INFO further away. */
        private void drawInstitutionalChartEvent(InstrumentState state,
                                                  PaxInstitutionalChartEvent evt,
                                                  int bucketOrdinal) {
            if (evt == null || !evt.isRenderable()) return;
            double tickSize = state.pips;
            if (!Double.isFinite(tickSize) || tickSize <= 0.0) return;

            java.awt.Color color = evt.colorFromHint();
            PreparedImage image = labelImage(evt.markerText, color, TRIANGLE_FONT_WEAK);
            int w = image.getReadOnlyImage().getWidth();
            int h = image.getReadOnlyImage().getHeight();
            long xNanos = PaxChartTimeCoords.epochMsToChartNanos(evt.timestampMs);

            // Offset in ticks: ENTRY/EXIT closer to the level, WATCH/INFO
            // further out. Stagger ordinal adds h-pixels of separation
            // within the same (label, ts) bucket.
            int baseTicks = TRIANGLE_OFFSET_TICKS_WEAK;
            int severityShift = evt.severityRank();  // 0..5
            int offsetTicks = baseTicks + severityShift;

            boolean placeBelow = isPlaceBelow(evt);
            double anchorPrice;
            int yPxTop, yPxBottom;
            int stagger = bucketOrdinal * h;
            if (placeBelow) {
                anchorPrice = (evt.price - offsetTicks * tickSize) / tickSize;
                yPxTop = stagger;
                yPxBottom = h + stagger;
            } else {
                anchorPrice = (evt.price + offsetTicks * tickSize) / tickSize;
                yPxTop = -h - stagger;
                yPxBottom = -stagger;
            }
            addTriangleShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, -w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxTop, anchorPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxBottom, anchorPrice));
        }

        /** Bull-side levels (above mid) get markers below the level by
         *  default; bear-side levels get markers above. Bearish-direction
         *  entries (ACC-S, REJ-S) flip to ABOVE so they read like sells. */
        private static boolean isPlaceBelow(PaxInstitutionalChartEvent evt) {
            return PaxOpeningRangeModule.chartEventPlaceBelow(evt);
        }

        private static String chartEventCollisionKey(PaxInstitutionalChartEvent evt, double tickSize) {
            return PaxOpeningRangeModule.chartEventCollisionKey(evt, tickSize);
        }

        private void drawInstitutionalMarker(InstrumentState state, PaxInstitutionalSignalEvent evt) {
            if (evt == null || !evt.isRenderable()) return;
            double tickSize = state.pips;
            if (!Double.isFinite(tickSize) || tickSize <= 0.0) return;
            String text;
            Color color;
            boolean bullish;
            switch (evt.executionRead) {
                case "PAY_FOR_TRADE":
                    if ("LONG".equals(evt.direction)) {
                        text = "INS-L"; color = PaxHeatwaveColors.BULL; bullish = true;
                    } else if ("SHORT".equals(evt.direction)) {
                        text = "INS-S"; color = PaxHeatwaveColors.BEAR; bullish = false;
                    } else {
                        // PAY_FOR_TRADE with direction NONE shouldn't happen
                        // per the Python contract; render as WATCH for safety.
                        text = "WATCH"; color = new Color(220, 200, 80); bullish = true;
                    }
                    break;
                case "STAND_DOWN":
                    if ("ICEBERG_DEFENSE".equals(evt.signalType)) {
                        text = "ICE";
                    } else if ("SPOOF_STAND_DOWN".equals(evt.signalType)) {
                        text = "SPD";
                    } else {
                        text = "STND";
                    }
                    color = new Color(255, 153, 0); bullish = true;
                    break;
                case "SCRATCH_READY":
                    text = "SCR"; color = new Color(176, 176, 176); bullish = true;
                    break;
                case "WAIT_FOR_CONFIRM":
                default:
                    text = "WATCH"; color = new Color(220, 200, 80); bullish = true;
                    break;
            }
            PreparedImage image = labelImage(text, color, TRIANGLE_FONT_WEAK);
            int w = image.getReadOnlyImage().getWidth();
            int h = image.getReadOnlyImage().getHeight();
            long xNanos = PaxChartTimeCoords.epochMsToChartNanos(evt.timestampMs);
            // Anchor at event.price. PAY-LONG and WATCH sit BELOW the level
            // (label drops down so the level line is visible); PAY-SHORT sits
            // ABOVE. STAND_DOWN/SCRATCH overlay the level itself.
            double anchorPrice;
            int yPxTop, yPxBottom;
            int offsetTicks = TRIANGLE_OFFSET_TICKS_WEAK;
            if (bullish) {
                anchorPrice = (evt.price - offsetTicks * tickSize) / tickSize;
                yPxTop = 0;
                yPxBottom = h;
            } else {
                anchorPrice = (evt.price + offsetTicks * tickSize) / tickSize;
                yPxTop = -h;
                yPxBottom = 0;
            }
            addTriangleShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, -w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxTop, anchorPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxBottom, anchorPrice));
        }

        private void drawTrendTriangle(InstrumentState state, TrendTriangleEvent evt) {
            // Render the triangle as a font glyph (▲ / ▼) via the proven
            // labelImage primitive. Earlier builds used a small custom
            // BufferedImage Polygon (PaxTrendTrianglePainter); on Bookmap 7.4
            // that primitive could fail to display on the chart canvas while
            // OR labels (font glyphs) and OR lines (solidPixel) rendered
            // reliably. Reusing labelImage eliminates that fragility.
            int fontSize = evt.kind.isStrong() ? TRIANGLE_FONT_STRONG : TRIANGLE_FONT_WEAK;
            PreparedImage image = trendGlyphImage(evt.kind, fontSize);
            int w = image.getReadOnlyImage().getWidth();
            int h = image.getReadOnlyImage().getHeight();
            long xNanos = PaxChartTimeCoords.epochMsToChartNanos(evt.eventMs);
            int offsetTicks = evt.kind.isStrong()
                    ? TRIANGLE_OFFSET_TICKS_STRONG
                    : TRIANGLE_OFFSET_TICKS_WEAK;
            double tickSize = state.pips;
            double anchorPrice;
            int yPxTop, yPxBottom;
            if (evt.kind.isBull()) {
                // Bullish: glyph BELOW price (anchor offset down by N ticks).
                anchorPrice = (evt.mid - offsetTicks * tickSize) / tickSize;
                // Image top edge at anchor; extends DOWN (screen +y) by h px.
                yPxTop = 0;
                yPxBottom = h;
            } else {
                // Bearish: glyph ABOVE price (anchor offset up by N ticks).
                anchorPrice = (evt.mid + offsetTicks * tickSize) / tickSize;
                // Image bottom edge at anchor; extends UP (screen -y) by h px.
                yPxTop = -h;
                yPxBottom = 0;
            }
            addTriangleShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, -w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxTop, anchorPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxBottom, anchorPrice));
        }

        private void addHeatwaveBox(PaxOpeningRangeUiSettings ui, boolean reuseCache) {
            long now = System.currentTimeMillis();
            PreparedImage image;
            int w, h;
            if (reuseCache && lastOverlayImage != null) {
                image = lastOverlayImage;
                w = lastOverlayWidth;
                h = lastOverlayHeight;
            } else {
                // effectiveModel synthesizes a DASHBOARD_OFFLINE carrier when
                // the fetcher has never had a successful response. Otherwise
                // it returns the last parsed model (which may itself be a
                // BRIDGE_OFFLINE carrier if the dashboard said so).
                PaxHeatwaveModel model = heatwave.effectiveModel(now);
                if (model == null) {
                    model = PaxHeatwaveModel.noData(now);
                }
                image = PaxHeatwavePainter.render(model, now, ui.clampedHeatwaveFontSize());
                w = image.getReadOnlyImage().getWidth();
                h = image.getReadOnlyImage().getHeight();
                lastOverlayImage = image;
                lastOverlayWidth = w;
                lastOverlayHeight = h;
            }
            int x = ui.clampedHeatwaveBoxX();
            int y = ui.clampedHeatwaveBoxY();
            addVolatileShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, x, 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, y, 0),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, x + w, 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, y + h, 0));
        }

        private void drawDay(InstrumentState state, PaxOpeningRangeDayState day) {
            PaxOpeningRangeUiSettings ui = loadSettings();
            PaxOpeningRangeSettings settings = ui.toCalculatorSettings();
            LocalDateTime lineStart = settings.rangeEndDateTime(day.getDate());
            // Lines anchor at canonical session boundaries — rangeEnd today
            // → next session anchor (lineEndDateTime). No per-tick clamp to
            // lastUpdateTime; that legacy clamp forced a persistent rebuild
            // on every tick to advance the right edge. With a fixed maxEnd
            // anchor the chart shapes are stable until the OR structure
            // itself changes.
            LocalDateTime lineEnd = settings.lineEndDateTime(day.getDate());
            if (lineEnd.isBefore(lineStart)) {
                lineEnd = lineStart;
            }

            addLine(lineStart, lineEnd, day.getHigh() / state.pips, ui.highColor, ui.mainLineWidth, false);
            addLabel(lineEnd, day.getHigh() / state.pips, formatLabel(state, day.getHigh()), ui.highColor);

            addLine(lineStart, lineEnd, day.getLow() / state.pips, ui.lowColor, ui.mainLineWidth, false);
            addLabel(lineEnd, day.getLow() / state.pips, formatLabel(state, day.getLow()), ui.lowColor);

            if (settings.showMid()) {
                addLine(lineStart, lineEnd, day.getMid() / state.pips, ui.midColor, ui.mainLineWidth, false);
                addLabel(lineEnd, day.getMid() / state.pips, settings.labelPrefix() + " MID " + formatPrice(state, day.getMid()), ui.midColor);
            }

            for (PaxOpeningRangeLevel level : day.getUpperLevels()) {
                LocalDateTime start = level.startTime();
                addLine(start, lineEnd, level.price() / state.pips, ui.highColor, ui.levelLineWidth, false);
                addLabel(lineEnd, level.price() / state.pips, formatLabel(state, level.price()), ui.highColor);
            }

            for (PaxOpeningRangeLevel level : day.getLowerLevels()) {
                LocalDateTime start = level.startTime();
                addLine(start, lineEnd, level.price() / state.pips, ui.lowColor, ui.levelLineWidth, false);
                addLabel(lineEnd, level.price() / state.pips, formatLabel(state, level.price()), ui.lowColor);
            }
        }

        private String formatLabel(InstrumentState state, double displayPrice) {
            return getCalculatorSettings().labelPrefix() + " " + formatPrice(state, displayPrice);
        }

        private String formatPrice(InstrumentState state, double displayPrice) {
            try {
                return provider.formatPrice(alias, displayPrice);
            } catch (Exception e) {
                Log.warn("OpenRange price formatting failed", e);
                return String.format("%.2f", displayPrice);
            }
        }

        private void addLine(LocalDateTime start, LocalDateTime end, double dataPrice, Color color, int width, boolean dashed) {
            if (dashed) {
                addDashedLine(start, end, dataPrice, color, width);
                return;
            }
            addShape(solidPixel(color),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, 0, toNanos(start)),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, -width / 2, dataPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, 0, toNanos(end)),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, width - width / 2, dataPrice));
        }

        private void addDashedLine(LocalDateTime start, LocalDateTime end, double dataPrice, Color color, int width) {
            long startNanos = toNanos(start);
            long endNanos = toNanos(end);
            long dash = 2_500_000_000L;
            long gap = 1_500_000_000L;
            for (long x = startNanos; x < endNanos; x += dash + gap) {
                long x2 = Math.min(x + dash, endNanos);
                addShape(solidPixel(color),
                        new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, 0, x),
                        new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, -width / 2, dataPrice),
                        new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, 0, x2),
                        new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, width - width / 2, dataPrice));
            }
        }

        private void addLabel(LocalDateTime atTime, double dataPrice, String text, Color color) {
            PreparedImage image = labelImage(text, color, loadSettings().fontSize);
            addShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, LABEL_X_OFFSET, toNanos(atTime)),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, LABEL_Y_OFFSET, dataPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, LABEL_X_OFFSET + image.getReadOnlyImage().getWidth(), toNanos(atTime)),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, LABEL_Y_OFFSET + image.getReadOnlyImage().getHeight(), dataPrice));
        }

        private void addStatus(String text) {
            PreparedImage image = labelImage(text, Color.WHITE, 14);
            addShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, 12, 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, 20, 0),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, 12 + image.getReadOnlyImage().getWidth(), 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, 20 + image.getReadOnlyImage().getHeight(), 0));
        }

        private void addSignalStatus(PaxOpeningRangeFeatureSnapshot snapshot) {
            if (snapshot == null || snapshot.signal() == null) {
                return;
            }
            Color color = signalColor(snapshot.colorState());
            PreparedImage image = badgeImage(signalText(snapshot), color, 13);
            addVolatileShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, SIGNAL_BADGE_LEFT_PADDING, 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, SIGNAL_BADGE_TOP_PADDING, 0),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, SIGNAL_BADGE_LEFT_PADDING + image.getReadOnlyImage().getWidth(), 0),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.PIXEL_ZERO, SIGNAL_BADGE_TOP_PADDING + image.getReadOnlyImage().getHeight(), 0));
        }

        private Color signalColor(PaxOpeningRangeSignalColorState state) {
            if (state == PaxOpeningRangeSignalColorState.BULLISH) {
                return new Color(0, 220, 120);
            }
            if (state == PaxOpeningRangeSignalColorState.BEARISH) {
                return new Color(255, 80, 80);
            }
            if (state == PaxOpeningRangeSignalColorState.DIVERGENT) {
                return new Color(255, 215, 0);
            }
            return new Color(180, 190, 200);
        }

        private String signalText(PaxOpeningRangeFeatureSnapshot snapshot) {
            String text = snapshot.badgeText();
            PaxOpeningRangeCrossMarketStatus status = crossMarketState.statusFor(alias);
            if (status == PaxOpeningRangeCrossMarketStatus.CONFIRM) {
                return appendToFirstLine(text, " | X CONF");
            }
            if (status == PaxOpeningRangeCrossMarketStatus.DIVERGE) {
                return appendToFirstLine(text, " | X DIV");
            }
            return text;
        }

        private String appendToFirstLine(String text, String suffix) {
            int newline = text.indexOf('\n');
            if (newline < 0) {
                return text + suffix;
            }
            return text.substring(0, newline) + suffix + text.substring(newline);
        }

        private void addShape(PreparedImage image, CompositeHorizontalCoordinate x1, CompositeVerticalCoordinate y1,
                CompositeHorizontalCoordinate x2, CompositeVerticalCoordinate y2) {
            CanvasIcon icon = new CanvasIcon(image, x1, y1, x2, y2);
            persistentShapes.add(icon);
            canvas.addShape(icon);
        }

        private void addTriangleShape(PreparedImage image, CompositeHorizontalCoordinate x1, CompositeVerticalCoordinate y1,
                CompositeHorizontalCoordinate x2, CompositeVerticalCoordinate y2) {
            CanvasIcon icon = new CanvasIcon(image, x1, y1, x2, y2);
            triangleShapes.add(icon);
            canvas.addShape(icon);
        }

        private void addVolatileShape(PreparedImage image, CompositeHorizontalCoordinate x1, CompositeVerticalCoordinate y1,
                CompositeHorizontalCoordinate x2, CompositeVerticalCoordinate y2) {
            CanvasIcon icon = new CanvasIcon(image, x1, y1, x2, y2);
            volatileShapes.add(icon);
            canvas.addShape(icon);
        }

        private void clearPersistent() {
            for (CanvasIcon shape : persistentShapes) canvas.removeShape(shape);
            persistentShapes.clear();
        }

        private void clearTriangles() {
            for (CanvasIcon shape : triangleShapes) canvas.removeShape(shape);
            triangleShapes.clear();
            lastTriangleRenderKey = "";
        }

        private void clearVolatile() {
            for (CanvasIcon shape : volatileShapes) canvas.removeShape(shape);
            volatileShapes.clear();
            // Note: lastOverlayKey + lastOverlayImage deliberately NOT
            // cleared. The cached PreparedImage outlives one clear/add
            // cycle (e.g. update() forced a full repaint on onMoveEnd
            // while the underlying model is unchanged) so we never pay
            // the Graphics2D allocation cost twice for the same model.
        }

        @Override
        public void dispose() {
            clearVolatile();
            clearTriangles();
            clearPersistent();
            canvas.dispose();
        }
    }

    /** Stable cache key for the triangle bucket. Includes the upstream
     *  signal identity (kind + bucketEnteredMs + eligibility + alias)
     *  plus the last-emitted state - so a fresh successful fetch with
     *  no semantic change produces the same key, and so the "no shapes
     *  intended" outcomes (showTrendTriangles=false, signal null) also
     *  produce stable keys that short-circuit on the next tick. */
    static String triangleRenderKey(boolean show,
                                      PaxTrendSignalModel signal,
                                      String lastEmittedKind,
                                      long lastEmittedBucketEnteredMs,
                                      int liveTriangleCount) {
        if (!show) return "OFF";
        if (signal == null) return "NULL";
        StringBuilder sb = new StringBuilder(96);
        sb.append(signal.kind).append('|')
          .append(signal.bucketEnteredMs).append('|')
          .append(signal.eligible).append('|')
          .append(signal.alias).append('|')
          .append(lastEmittedKind).append('|')
          .append(lastEmittedBucketEnteredMs).append('|')
          .append(liveTriangleCount);
        return sb.toString();
    }

    static String chartEventCollisionKey(PaxInstitutionalChartEvent evt, double tickSize) {
        if (evt == null) return "NULL";
        long timeBucket = evt.timestampMs / 1000L;
        boolean placeBelow = chartEventPlaceBelow(evt);
        long priceTicks;
        if (Double.isFinite(evt.price) && evt.price > 0.0
                && Double.isFinite(tickSize) && tickSize > 0.0) {
            priceTicks = Math.round(evt.price / tickSize);
        } else {
            priceTicks = Long.MIN_VALUE;
        }
        long priceBucket = priceTicks == Long.MIN_VALUE
                ? Long.MIN_VALUE
                : Math.floorDiv(priceTicks, CHART_EVENT_COLLISION_PRICE_TICKS);
        return timeBucket + "|" + (placeBelow ? "B" : "A") + "|" + priceBucket;
    }

    static boolean chartEventPlaceBelow(PaxInstitutionalChartEvent evt) {
        if (evt == null) return true;
        if ("SHORT".equals(evt.direction)) return false;
        if ("LONG".equals(evt.direction)) return true;
        return "above".equals(evt.side);
    }

    /** Stable cache key for the heatwave model semantic content. Used by
     *  the overlay-render memoization to detect when the model content
     *  (state, verdict, scoreText, all rows) is unchanged so the cached
     *  PreparedImage can be reused. Pure function of the model - no
     *  reads against instance fields - so it is directly unit-testable. */
    static String heatwaveModelKey(PaxHeatwaveModel model) {
        if (model == null) return "NULL";
        StringBuilder sb = new StringBuilder(160);
        sb.append(model.state).append('|')
          .append(model.verdict).append('|')
          .append(model.verdictTone).append('|')
          .append(model.scoreText).append('|')
          .append(model.ok).append('|')
          .append(model.offlineReason);
        if (model.rows != null) {
            for (PaxHeatwaveModel.Row row : model.rows) {
                if (row == null) {
                    sb.append("|<null>");
                } else {
                    sb.append('|').append(row.label).append(':')
                      .append(row.scoreText).append(':')
                      .append(row.tone).append(':').append(row.hint);
                }
            }
        }
        return sb.toString();
    }

    private static PreparedImage solidPixel(Color color) {
        BufferedImage image = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        image.setRGB(0, 0, color.getRGB());
        return new PreparedImage(image);
    }

    /**
     * Build a {@link PreparedImage} containing the ▲ / ▼ trend-triangle glyph
     * at the given font size, in the bull/bear color, with weak/strong alpha.
     *
     * <p>Cached: glyphs are immutable, so repeated calls with the same
     * {@code (kind, fontSize)} return the same {@link PreparedImage} from
     * {@link #TREND_GLYPH_CACHE}. The cache short-circuits the Graphics2D
     * allocation that previously fired up to 8 times per dashboard poll.</p>
     *
     * <p>Uses {@link Graphics2D#drawString} on a transparent ARGB scratch
     * image — identical primitive class to {@code labelImage} which paints
     * the OR HIGH/LOW labels reliably on Bookmap 7.4. Strong = full alpha,
     * heavier outline; weak = ~70% alpha, lighter outline. NONE / null →
     * 1×1 transparent placeholder.
     */
    static PreparedImage trendGlyphImage(PaxTrendSignalModel.Kind kind, int fontSize) {
        return TREND_GLYPH_CACHE.get(kind, fontSize);
    }

    /** Uncached underlying renderer used by the cache. */
    static PreparedImage buildTrendGlyphImage(PaxTrendSignalModel.Kind kind, int fontSize) {
        if (kind == null || kind == PaxTrendSignalModel.Kind.NONE) {
            BufferedImage placeholder = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
            return new PreparedImage(placeholder);
        }
        String glyph = kind.isBull() ? "▲" : "▼";   // ▲ / ▼
        Color base = kind.isBull() ? PaxHeatwaveColors.BULL : PaxHeatwaveColors.BEAR;
        int alpha = kind.isStrong() ? 255 : 178;
        Color fill = new Color(base.getRed(), base.getGreen(), base.getBlue(), alpha);
        int safeSize = Math.max(10, Math.min(64, fontSize));
        Font font = new Font(Font.SANS_SERIF, Font.BOLD, safeSize);

        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D scratchGraphics = scratch.createGraphics();
        scratchGraphics.setFont(font);
        FontMetrics metrics = scratchGraphics.getFontMetrics();
        int width  = Math.max(1, metrics.stringWidth(glyph) + 4);
        int height = Math.max(1, metrics.getHeight() + 2);
        scratchGraphics.dispose();

        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D graphics = image.createGraphics();
        graphics.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        graphics.setRenderingHint(RenderingHints.KEY_ANTIALIASING,
                RenderingHints.VALUE_ANTIALIAS_ON);
        graphics.setFont(font);
        // Dark outline pass so the glyph stays visible on any chart background.
        Color outline = new Color(8, 11, 15, kind.isStrong() ? 235 : 178);
        graphics.setColor(outline);
        int baselineY = metrics.getAscent() + 1;
        for (int dx = -1; dx <= 1; dx++) {
            for (int dy = -1; dy <= 1; dy++) {
                if (dx == 0 && dy == 0) continue;
                graphics.drawString(glyph, 2 + dx, baselineY + dy);
            }
        }
        graphics.setColor(fill);
        graphics.drawString(glyph, 2, baselineY);
        graphics.dispose();
        return new PreparedImage(image);
    }

    static BufferedImage signalMarkerIcon(PaxTrendSignalModel.Kind kind) {
        return signalMarkerIcon(kind, Double.NaN, "SIG");
    }

    static BufferedImage signalMarkerIcon(PaxTrendSignalModel.Kind kind, double price, String source) {
        if (kind == null || kind == PaxTrendSignalModel.Kind.NONE) {
            return new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        }
        String safeSource = source == null || source.isBlank() ? "SIG" : source.trim();
        if (safeSource.length() > 4) {
            safeSource = safeSource.substring(0, 4);
        }
        String priceText = Double.isFinite(price) && price > 0.0
                ? markerPriceText(price)
                : "";
        String header = safeSource + (kind.isStrong() ? "!" : "");

        Font headerFont = new Font(Font.SANS_SERIF, Font.BOLD, 10);
        Font priceFont = new Font(Font.SANS_SERIF, Font.BOLD, 11);
        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        sg.setFont(headerFont);
        FontMetrics headerMetrics = sg.getFontMetrics();
        int headerWidth = headerMetrics.stringWidth(header);
        sg.setFont(priceFont);
        FontMetrics priceMetrics = sg.getFontMetrics();
        int priceWidth = priceMetrics.stringWidth(priceText);
        sg.dispose();

        int iconBox = 16;
        int gap = 4;
        int padX = 6;
        int padY = 4;
        int textWidth = Math.max(headerWidth, priceWidth);
        int width = Math.max(54, padX * 2 + iconBox + gap + textWidth);
        int height = 32;
        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = image.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);

        Color accent = kind.isBull()
                ? new Color(0, 210, 255, kind.isStrong() ? 255 : 220)
                : new Color(255, 155, 36, kind.isStrong() ? 255 : 220);
        Color background = new Color(6, 10, 14, 218);
        Color border = new Color(accent.getRed(), accent.getGreen(), accent.getBlue(), 230);
        g.setColor(background);
        g.fillRoundRect(0, 0, width - 1, height - 1, 7, 7);
        g.setStroke(new BasicStroke(kind.isStrong() ? 2.0f : 1.4f));
        g.setColor(border);
        g.drawRoundRect(1, 1, width - 3, height - 3, 7, 7);

        int cx = padX + iconBox / 2;
        int top = 8;
        int bottom = 22;
        g.setColor(accent);
        if (kind.isBull()) {
            g.fillPolygon(new int[] {cx, cx - 7, cx + 7}, new int[] {top, bottom, bottom}, 3);
        } else {
            g.fillPolygon(new int[] {cx - 7, cx + 7, cx}, new int[] {top, top, bottom}, 3);
        }

        int textX = padX + iconBox + gap;
        g.setFont(headerFont);
        g.setColor(accent);
        g.drawString(header, textX, 12);
        if (!priceText.isEmpty()) {
            g.setFont(priceFont);
            g.setColor(new Color(232, 238, 243, 245));
            g.drawString(priceText, textX, 25);
        }
        g.dispose();
        return image;
    }

    private static String markerPriceText(double price) {
        if (Math.abs(price) >= 1000.0) {
            return String.format(java.util.Locale.US, "%.2f", price);
        }
        return String.format(java.util.Locale.US, "%.4f", price);
    }

    private static PreparedImage labelImage(String text, Color color, int fontSize) {
        Font font = new Font("Arial", Font.PLAIN, Math.max(8, Math.min(36, fontSize)));
        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D scratchGraphics = scratch.createGraphics();
        scratchGraphics.setFont(font);
        FontMetrics metrics = scratchGraphics.getFontMetrics();
        int width = Math.max(1, metrics.stringWidth(text) + 4);
        int height = Math.max(1, metrics.getHeight() + 2);
        scratchGraphics.dispose();

        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D graphics = image.createGraphics();
        graphics.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        graphics.setFont(font);
        graphics.setColor(color);
        graphics.drawString(text, 2, metrics.getAscent() + 1);
        graphics.dispose();
        return new PreparedImage(image);
    }

    private static PreparedImage badgeImage(String text, Color accent, int fontSize) {
        Font font = new Font("Arial", Font.BOLD, Math.max(8, Math.min(24, fontSize)));
        String[] lines = text.split("\\R", -1);
        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D scratchGraphics = scratch.createGraphics();
        scratchGraphics.setFont(font);
        FontMetrics metrics = scratchGraphics.getFontMetrics();
        int textWidth = 0;
        for (String line : lines) {
            textWidth = Math.max(textWidth, metrics.stringWidth(line));
        }
        int width = Math.max(1, textWidth + 24);
        int height = Math.max(1, (metrics.getHeight() * lines.length) + 10);
        scratchGraphics.dispose();

        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D graphics = image.createGraphics();
        graphics.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        graphics.setColor(new Color(
                Math.max(0, accent.getRed() / 5),
                Math.max(0, accent.getGreen() / 5),
                Math.max(0, accent.getBlue() / 5),
                235));
        graphics.fillRect(0, 0, width, height);
        graphics.setColor(accent);
        graphics.fillRect(0, height - 3, width, 3);
        graphics.setColor(new Color(accent.getRed(), accent.getGreen(), accent.getBlue(), 150));
        graphics.drawRect(0, 0, width - 1, height - 1);
        graphics.setFont(font);
        graphics.setColor(accent);
        int y = metrics.getAscent() + 5;
        for (String line : lines) {
            graphics.drawString(line, 11, y);
            y += metrics.getHeight();
        }
        graphics.dispose();
        return new PreparedImage(image);
    }

}
