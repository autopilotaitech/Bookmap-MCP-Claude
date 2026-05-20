package com.openrange;

import java.awt.image.BufferedImage;

public class PaxNativeSignalMarkerPolicyTest {

    public static void main(String[] args) {
        allowLongHighMapsToStrongBull();
        allowShortMediumMapsToWeakBear();
        blockedOrNeutralDoesNotRender();
        markerKeyDedupsLikeSignalLogger();
        markerIconHasPixels();
        markerIconIncludesPriceBadge();
        System.out.println("PaxNativeSignalMarkerPolicyTest OK");
        System.exit(0);
    }

    private static void allowLongHighMapsToStrongBull() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG, PaxOpeningRangeSignalConfidence.HIGH, 4, "CVD+ BID+");
        assertEquals(PaxTrendSignalModel.Kind.STRONG_BULL,
                PaxOpeningRangeModule.openingRangeMarkerKind(signal), "long high");
    }

    private static void allowShortMediumMapsToWeakBear() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.SHORT, PaxOpeningRangeSignalConfidence.MEDIUM, 3, "CVD- ASK+");
        assertEquals(PaxTrendSignalModel.Kind.WEAK_BEAR,
                PaxOpeningRangeModule.openingRangeMarkerKind(signal), "short medium");
    }

    private static void blockedOrNeutralDoesNotRender() {
        PaxOpeningRangeSignal blocked = signal(PaxOpeningRangeSignalAction.BLOCK_SIGNAL,
                PaxOpeningRangeSignalBias.LONG, PaxOpeningRangeSignalConfidence.LOW, 1, "blocked");
        PaxOpeningRangeSignal neutral = signal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.NEUTRAL, PaxOpeningRangeSignalConfidence.HIGH, 4, "neutral");
        assertEquals(PaxTrendSignalModel.Kind.NONE,
                PaxOpeningRangeModule.openingRangeMarkerKind(blocked), "blocked");
        assertEquals(PaxTrendSignalModel.Kind.NONE,
                PaxOpeningRangeModule.openingRangeMarkerKind(neutral), "neutral");
    }

    private static void markerKeyDedupsLikeSignalLogger() {
        PaxOpeningRangeSignal signal = signal(PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG, PaxOpeningRangeSignalConfidence.HIGH, 4, "CVD+ BID+");
        PaxOpeningRangeFeatureSnapshot snapshot = new PaxOpeningRangeFeatureSnapshot(
                1_700_000_000_000_000_000L,
                new PaxOpeningRangeMarketState(21800.25, 1, 1, -1, 1),
                signal, "badge", PaxOpeningRangeSignalColorState.BULLISH);
        String key = PaxOpeningRangeModule.openingRangeMarkerKey("NQM6", snapshot);
        if (!key.contains("NQM6|LONG|ALLOW_SIGNAL|HIGH|4|CVD+ BID+")) {
            throw new AssertionError("unexpected key: " + key);
        }
    }

    private static void markerIconHasPixels() {
        BufferedImage image = PaxOpeningRangeModule.signalMarkerIcon(PaxTrendSignalModel.Kind.STRONG_BULL);
        int opaque = 0;
        for (int x = 0; x < image.getWidth(); x++) {
            for (int y = 0; y < image.getHeight(); y++) {
                if (((image.getRGB(x, y) >>> 24) & 0xFF) >= 128) {
                    opaque++;
                }
            }
        }
        if (opaque < 10) {
            throw new AssertionError("native marker icon is transparent");
        }
    }

    private static void markerIconIncludesPriceBadge() {
        BufferedImage bull = PaxOpeningRangeModule.signalMarkerIcon(
                PaxTrendSignalModel.Kind.STRONG_BULL, 28909.25, "TRD");
        BufferedImage bear = PaxOpeningRangeModule.signalMarkerIcon(
                PaxTrendSignalModel.Kind.WEAK_BEAR, 28909.25, "OR");
        if (bull.getWidth() < 60 || bull.getHeight() < 28) {
            throw new AssertionError("bull marker badge too small for source+price");
        }
        if (bear.getWidth() < 60 || bear.getHeight() < 28) {
            throw new AssertionError("bear marker badge too small for source+price");
        }
        int bullCyan = countColorDominant(bull, true);
        int bearOrange = countOrangeDominant(bear);
        if (bullCyan <= 0) {
            throw new AssertionError("bull marker must contain cyan-dominant pixels");
        }
        if (bearOrange <= 0) {
            throw new AssertionError("bear marker must contain orange-dominant pixels");
        }
    }

    private static int countColorDominant(BufferedImage image, boolean cyan) {
        int hits = 0;
        for (int x = 0; x < image.getWidth(); x++) {
            for (int y = 0; y < image.getHeight(); y++) {
                int rgb = image.getRGB(x, y);
                int alpha = (rgb >>> 24) & 0xFF;
                int red = (rgb >>> 16) & 0xFF;
                int green = (rgb >>> 8) & 0xFF;
                int blue = rgb & 0xFF;
                if (alpha >= 180 && cyan && blue > red + 60 && green > red + 40) {
                    hits++;
                }
            }
        }
        return hits;
    }

    private static int countOrangeDominant(BufferedImage image) {
        int hits = 0;
        for (int x = 0; x < image.getWidth(); x++) {
            for (int y = 0; y < image.getHeight(); y++) {
                int rgb = image.getRGB(x, y);
                int alpha = (rgb >>> 24) & 0xFF;
                int red = (rgb >>> 16) & 0xFF;
                int green = (rgb >>> 8) & 0xFF;
                int blue = rgb & 0xFF;
                if (alpha >= 180 && red > blue + 80 && green > blue + 40) {
                    hits++;
                }
            }
        }
        return hits;
    }

    private static PaxOpeningRangeSignal signal(PaxOpeningRangeSignalAction action,
            PaxOpeningRangeSignalBias bias, PaxOpeningRangeSignalConfidence confidence,
            int score, String evidence) {
        return new PaxOpeningRangeSignal(action, bias, confidence, "reason",
                score, 4, evidence, "ORH", 2, 10.0, 60L,
                5.0, 5.0, 1.0, 1.0, 80, 80, "NORMAL");
    }

    private static void assertEquals(Object expected, Object actual, String label) {
        if (!expected.equals(actual)) {
            throw new AssertionError(label + " expected " + expected + " got " + actual);
        }
    }
}
