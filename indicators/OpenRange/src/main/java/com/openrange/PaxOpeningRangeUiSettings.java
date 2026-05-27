package com.openrange;

import java.awt.Color;
import java.time.LocalTime;

import velox.api.layer1.settings.StrategySettingsVersion;

@StrategySettingsVersion(currentVersion = 3, compatibleVersions = {1, 2})
public class PaxOpeningRangeUiSettings {
    // Canonical institutional OR anchor: 08:30 America/Chicago. The default
    // line end matches the anchor — PaxOpeningRangeSettings.lineEndDateTime
    // interprets lineEnd <= rangeEnd as "extend to next session's open",
    // giving a 24h line span (drawDay anchors at maxEnd).
    public int startHour   = 8;
    public int startMinute = 30;
    public int startSecond = 0;
    public int rangeSeconds = 30;
    public int endHour   = 8;
    public int endMinute = 30;
    /** Migration breadcrumb. Old V1/V2 stored settings have this implicit
     *  false; the module's acceptSettingsInterface uses that signal plus
     *  the exact old-default tuple to upgrade quietly to canonical 08:30. */
    public boolean migratedToCanonical0830 = false;
    public int daysToDisplay = 8;
    public boolean showMid = false;
    public String labelPrefix = "OpenRange";
    public Color highColor = new Color(0, 191, 255);
    public Color lowColor = new Color(255, 99, 71);
    public Color midColor = new Color(255, 215, 0);
    public int mainLineWidth = 3;
    public int levelLineWidth = 2;
    public int fontSize = 16;
    public int signalCvdThreshold = 25;
    public int signalDepthThreshold = 25;
    public int signalDepthLevels = 10;
    public int signalMinBreakoutTicks = 2;
    public int signalMaxBreakoutTicks = 80;
    public int signalMinScore = 4;
    public int signalMinCvdPercentile = 80;
    public int signalMinPullingStackingPercentile = 80;
    public boolean signalBlockCrossMarketDivergence = true;
    public int normalizationWindowSeconds = 120;
    public String logDirectory = "build\\logs";

    public boolean showHeatwaveBox = true;
    public int heatwaveBoxX = 12;
    public int heatwaveBoxY = 14;
    public int heatwaveFontSize = 11;
    public boolean heatwaveCompact = true;
    public int heatwavePollMs = 1000;
    public String heatwaveUrl = "http://127.0.0.1:18888/api/snapshot";

    /** Show institutional-signal triangles on the chart. Triangles are now
     * driven by snap["institutional_signals"] PAY_FOR_TRADE entries
     * (level-anchored at OR-H/OR-L/extensions). Legacy trend_signal and
     * pax.decision sources are no longer consulted for entry markers — the
     * parser returns NONE when no institutional PAY_FOR_TRADE signal exists.
     * Set false to disable the institutional triangle layer entirely. */
    public boolean showTrendTriangles = false;

    /** Suppress the native OR breakout marker publisher (CVD/depth-driven
     * LONG/SHORT engine markers). Default ON — native markers are the
     * "buy in the middle of the OR" source the institutional pipeline
     * replaces. Set false to restore legacy behavior for debugging. */
    public boolean gateNativeMarkersOnInstitutional = true;

    /** Show the full institutional chart-events evidence trail on the
     * Bookmap chart: sweeps (SWP), absorption (ABS), iceberg defense
     * (ICE), spoof risk (SPD), pull/stack context (PULL/STACK),
     * watch/touch (WATCH/TCH), and confirmed acceptance/rejection
     * entries (ACC/REJ). Driven by snap["institutional_chart_events"];
     * spec: docs/superpowers/specs/institutional-chart-markers.md. */
    public boolean showInstitutionalChartEvents = true;

    /** Show small attack-response WATCH labels anchored at each
     * qualifying OR / extension level. Driven by Pax AI server's
     * {@code /api/pax/attack-response} endpoint (PaxAttackResponseFetcher
     * -> PaxAttackResponseSnapshotParser -> PaxAttackResponseLabelPainter).
     * INDEPENDENT of {@link #showInstitutionalChartEvents}: raw-event
     * glyphs and these state labels are toggled separately.
     * WATCH-only - never auto-promotes to EDGE styling without explicit
     * {@code proven_edge=true} from the endpoint. Default ON. */
    public boolean showAttackResponseLabels = true;

    public PaxOpeningRangeSettings toCalculatorSettings() {
        return new PaxOpeningRangeSettings(
                LocalTime.of(clamp(startHour, 0, 23), clamp(startMinute, 0, 59), clamp(startSecond, 0, 59)),
                clamp(rangeSeconds, 1, 600),
                LocalTime.of(clamp(endHour, 0, 23), clamp(endMinute, 0, 59)),
                clamp(daysToDisplay, 1, 30),
                showMid,
                labelPrefix == null || labelPrefix.isBlank() ? "OpenRange" : labelPrefix);
    }

    public PaxOpeningRangeSignalSettings toSignalSettings() {
        return new PaxOpeningRangeSignalSettings(
                clamp(signalCvdThreshold, 0, 100000),
                clamp(signalDepthThreshold, 0, 100000),
                clamp(signalDepthLevels, 1, 100),
                clamp(signalMinBreakoutTicks, 0, 100),
                clamp(signalMaxBreakoutTicks, 0, 1000),
                clamp(signalMinScore, 1, 4));
    }

    public PaxOpeningRangeSignalQualitySettings toQualitySettings() {
        return new PaxOpeningRangeSignalQualitySettings(
                clamp(signalMinCvdPercentile, 0, 100),
                clamp(signalMinPullingStackingPercentile, 0, 100),
                signalBlockCrossMarketDivergence);
    }

    public int clampedHeatwaveFontSize() {
        return clamp(heatwaveFontSize, 9, 16);
    }

    public int clampedHeatwavePollMs() {
        return clamp(heatwavePollMs, 500, 3000);
    }

    public int clampedHeatwaveBoxX() {
        return clamp(heatwaveBoxX, 0, 4000);
    }

    public int clampedHeatwaveBoxY() {
        return clamp(heatwaveBoxY, 0, 4000);
    }

    public String safeHeatwaveUrl() {
        if (heatwaveUrl == null || heatwaveUrl.isBlank()) {
            return PaxHeatwaveFetcher.DEFAULT_URL;
        }
        return heatwaveUrl;
    }

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }
}
