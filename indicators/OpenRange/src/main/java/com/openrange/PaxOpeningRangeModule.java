package com.openrange;

import java.awt.Color;
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
import velox.api.layer1.messages.indicators.Layer1ApiDataInterfaceRequestMessage;
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
        ScreenSpacePainterFactory,
        Layer1CustomPanelsGetter,
        Layer1ConfigSettingsInterface {

    private static final String INDICATOR_NAME = "OpenRange";
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
     *   <li>trend triangles redraw on {@code trendSignalDirty}.</li>
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
    /** Stale-cliff for incoming trend_signal: keep existing triangles drawn,
     * but do not emit new ones if the fetcher's last successful HTTP receive
     * is older than this. */
    static final long TREND_STALE_AGE_MS = 30_000L;
    /** Triangle anchor offset in ticks. Strong sits further out so the two
     * sizes are distinguishable at the same price. */
    static final int TRIANGLE_OFFSET_TICKS_STRONG = 4;
    static final int TRIANGLE_OFFSET_TICKS_WEAK = 2;

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

    private final Layer1ApiProvider provider;
    private final Map<String, InstrumentState> instruments = new ConcurrentHashMap<>();
    private final Map<String, PaxPainter> painters = new ConcurrentHashMap<>();
    private final Map<String, String> indicatorsFullNameToUserName = new HashMap<>();
    private final PaxOpeningRangeCrossMarketState crossMarketState = new PaxOpeningRangeCrossMarketState();
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
        painters.values().forEach(PaxPainter::dispose);
        painters.clear();
        instruments.values().forEach(InstrumentState::dispose);
        instruments.clear();
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
            }
        }
    }

    @Override
    public void onInstrumentAdded(String alias, InstrumentInfo instrumentInfo) {
        double pips = instrumentInfo.pips <= 0 ? 1 : instrumentInfo.pips;
        InstrumentState state = new InstrumentState(instrumentInfo, pips, getCalculatorSettings());
        instruments.put(alias, state);
        backfill(alias);
        PaxPainter painter = painters.get(alias);
        if (painter != null) {
            painter.update();
        }
    }

    @Override
    public void onInstrumentRemoved(String alias) {
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
        // Trend triangles share the dashboard URL with the heatwave box —
        // single source for the polled /api/snapshot endpoint.
        trendSignals.applySettings(uiSettings.showTrendTriangles,
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

        JCheckBox showTrendTriangles = new JCheckBox("Show Trend Triangles (conviction)", settings.showTrendTriangles);
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
        trendSignals.applySettings(settings.showTrendTriangles,
                settings.safeHeatwaveUrl(), settings.clampedHeatwavePollMs());
    }

    private String diagnosticsText(String alias) {
        long nowNanos = safeCurrentTime();
        InstrumentState state = instruments.get(alias);
        if (state == null) {
            return PaxOpeningRangeDiagnostics.format(null, nowNanos, loadSettings().logDirectory);
        }
        return PaxOpeningRangeDiagnostics.format(state.featureCache, nowNanos,
                state.signalLogger.file().toString());
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
        // Trend triangle dedup + history. Mutated only by PaxPainter.update()
        // (Bookmap callback thread). The deque holds visible triangle events
        // so a full `clear()` + redraw on every repaint preserves the chart
        // across pans/zooms. Cap at 8 keeps the chart readable.
        final Deque<TrendTriangleEvent> liveTriangles = new ArrayDeque<>(MAX_LIVE_TRIANGLES + 1);
        String lastEmittedKind = "";
        long lastEmittedBucketEnteredMs = 0L;

        InstrumentState(InstrumentInfo info, double pips, PaxOpeningRangeSettings settings) {
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

        /** Dispatch by needs vector. Each bucket is independent. */
        synchronized void applyNeeds(RepaintNeeds needs) {
            if (needs == null || needs.isNone()) return;
            if (needs.persistent) updatePersistent();
            if (needs.triangles)  updateTriangles();
            if (needs.overlay)    updateOverlay();
        }

        /** Full repaint of every bucket. Used by onMoveEnd, init, and
         *  explicit rebuild paths. Force-syncs the InstrumentState's
         *  lastOrSignature so the next tick-driven check doesn't fire a
         *  redundant persistent rebuild. */
        synchronized void update() {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            updatePersistent();
            updateTriangles();
            updateOverlay();
            state.lastOrSignature = state.computeOrSignature();
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

        /** Trend triangle shapes only. Fired on trendSignalDirty so a fresh
         *  eligible signal renders immediately without rebuilding OR lines. */
        synchronized void updateTriangles() {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            clearTriangles();
            if (loadSettings().showTrendTriangles) {
                addTrendTriangles(state);
            }
        }

        /** Volatile overlay shapes only — Heatwave Quant Box and signal
         *  badge. Fired on heatwaveDirty at the 1Hz dashboard cadence. */
        synchronized void updateOverlay() {
            InstrumentState state = instruments.get(alias);
            if (state == null) return;
            clearVolatile();
            PaxOpeningRangeUiSettings ui = loadSettings();
            if (ui.showHeatwaveBox) {
                addHeatwaveBox(ui);
            } else {
                addSignalStatus(state.featureCache.latest());
            }
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
            }
            // Redraw every live triangle. update() called clear() at top,
            // so each refresh re-adds the deque contents.
            for (TrendTriangleEvent evt : state.liveTriangles) {
                drawTrendTriangle(state, evt);
            }
        }

        private void drawTrendTriangle(InstrumentState state, TrendTriangleEvent evt) {
            int w = PaxTrendTrianglePainter.pixelWidth(evt.kind);
            int h = PaxTrendTrianglePainter.pixelHeight(evt.kind);
            PreparedImage image = PaxTrendTrianglePainter.render(evt.kind);
            long xNanos = PaxChartTimeCoords.epochMsToChartNanos(evt.eventMs);
            int offsetTicks = evt.kind.isStrong()
                    ? TRIANGLE_OFFSET_TICKS_STRONG
                    : TRIANGLE_OFFSET_TICKS_WEAK;
            double tickSize = state.pips;
            double anchorPrice;
            int yPxTop, yPxBottom;
            if (evt.kind.isBull()) {
                // Bullish: triangle BELOW price (anchor offset down).
                anchorPrice = (evt.mid - offsetTicks * tickSize) / tickSize;
                // y-pixels: image extends downward from anchor.
                yPxTop = 0;
                yPxBottom = h;
            } else {
                // Bearish: triangle ABOVE price (anchor offset up).
                anchorPrice = (evt.mid + offsetTicks * tickSize) / tickSize;
                // y-pixels: image extends upward from anchor.
                yPxTop = -h;
                yPxBottom = 0;
            }
            addTriangleShape(image,
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, -w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxTop, anchorPrice),
                    new CompositeHorizontalCoordinate(CompositeCoordinateBase.DATA_ZERO, w / 2, xNanos),
                    new CompositeVerticalCoordinate(CompositeCoordinateBase.DATA_ZERO, yPxBottom, anchorPrice));
        }

        private void addHeatwaveBox(PaxOpeningRangeUiSettings ui) {
            long now = System.currentTimeMillis();
            // effectiveModel synthesizes a DASHBOARD_OFFLINE carrier when the
            // fetcher has never had a successful response. Otherwise it
            // returns the last parsed model (which may itself be a
            // BRIDGE_OFFLINE carrier if the dashboard said so).
            PaxHeatwaveModel model = heatwave.effectiveModel(now);
            if (model == null) {
                model = PaxHeatwaveModel.noData(now);
            }
            PreparedImage image = PaxHeatwavePainter.render(model, now, ui.clampedHeatwaveFontSize());
            int x = ui.clampedHeatwaveBoxX();
            int y = ui.clampedHeatwaveBoxY();
            int w = image.getReadOnlyImage().getWidth();
            int h = image.getReadOnlyImage().getHeight();
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
        }

        private void clearVolatile() {
            for (CanvasIcon shape : volatileShapes) canvas.removeShape(shape);
            volatileShapes.clear();
        }

        @Override
        public void dispose() {
            clearVolatile();
            clearTriangles();
            clearPersistent();
            canvas.dispose();
        }
    }

    private static PreparedImage solidPixel(Color color) {
        BufferedImage image = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        image.setRGB(0, 0, color.getRGB());
        return new PreparedImage(image);
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
