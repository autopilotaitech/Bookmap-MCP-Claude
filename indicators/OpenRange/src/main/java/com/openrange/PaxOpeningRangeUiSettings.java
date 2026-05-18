package com.openrange;

import java.awt.Color;
import java.time.LocalTime;

import velox.api.layer1.settings.StrategySettingsVersion;

@StrategySettingsVersion(currentVersion = 1, compatibleVersions = {})
public class PaxOpeningRangeUiSettings {
    public int startHour = 9;
    public int startMinute = 30;
    public int startSecond = 0;
    public int rangeSeconds = 30;
    public int endHour = 17;
    public int endMinute = 0;
    public int daysToDisplay = 8;
    public boolean showMid = false;
    public String labelPrefix = "OpenRange";
    public Color highColor = new Color(0, 191, 255);
    public Color lowColor = new Color(255, 99, 71);
    public Color midColor = new Color(255, 215, 0);
    public int mainLineWidth = 3;
    public int levelLineWidth = 2;
    public int fontSize = 16;
    public int signalCvdThreshold = 1;
    public int signalDepthThreshold = 1;
    public int signalDepthLevels = 10;
    public int signalMinBreakoutTicks = 0;
    public int signalMaxBreakoutTicks = 0;
    public int signalMinScore = 3;
    public int signalMinCvdPercentile = 70;
    public int signalMinPullingStackingPercentile = 70;
    public boolean signalBlockCrossMarketDivergence = true;
    public int normalizationWindowSeconds = 120;
    public String logDirectory = "build\\logs";

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

    private static int clamp(int value, int min, int max) {
        return Math.max(min, Math.min(max, value));
    }
}
