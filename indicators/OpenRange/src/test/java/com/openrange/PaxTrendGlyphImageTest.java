package com.openrange;

import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Verifies the font-glyph trend-triangle renderer
 * ({@link PaxOpeningRangeModule#trendGlyphImage}) — used in place of the
 * legacy small-polygon BufferedImage primitive because Bookmap 7.4's chart
 * canvas renders labelImage-style font glyphs reliably while small
 * alpha-blended polygon shapes occasionally fail to display.
 */
public class PaxTrendGlyphImageTest {

    public static void main(String[] args) {
        strongBullProducesGreenDominantGlyph();
        weakBullProducesSmallerGlyph();
        strongBearProducesRedDominantGlyph();
        weakBearProducesSmallerGlyph();
        noneProducesPlaceholder();
        glyphImageHasNonTransparentPixels();
        System.out.println("PaxTrendGlyphImageTest OK");
        System.exit(0);
    }

    private static void strongBullProducesGreenDominantGlyph() {
        PreparedImage img = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.STRONG_BULL,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getWidth() < 10 || b.getHeight() < 10)
            throw new AssertionError("strong bull glyph too small: "
                    + b.getWidth() + "x" + b.getHeight());
        // Sample a handful of interior pixels and require green dominates.
        int greenDominant = countDominant(b, true /*greenOverRed*/);
        if (greenDominant <= 0)
            throw new AssertionError(
                    "strong BULL glyph must produce at least one green-dominant pixel");
    }

    private static void weakBullProducesSmallerGlyph() {
        PreparedImage strong = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.STRONG_BULL,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        PreparedImage weak = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.WEAK_BULL,
                PaxOpeningRangeModule.TRIANGLE_FONT_WEAK);
        if (weak.getReadOnlyImage().getHeight() >= strong.getReadOnlyImage().getHeight())
            throw new AssertionError("weak bull must be SMALLER than strong: weak="
                    + weak.getReadOnlyImage().getHeight()
                    + " strong=" + strong.getReadOnlyImage().getHeight());
    }

    private static void strongBearProducesRedDominantGlyph() {
        PreparedImage img = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.STRONG_BEAR,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        BufferedImage b = img.getReadOnlyImage();
        int redDominant = countDominant(b, false /*greenOverRed = false → redOverGreen*/);
        if (redDominant <= 0)
            throw new AssertionError(
                    "strong BEAR glyph must produce at least one red-dominant pixel");
    }

    private static void weakBearProducesSmallerGlyph() {
        PreparedImage strong = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.STRONG_BEAR,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        PreparedImage weak = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.WEAK_BEAR,
                PaxOpeningRangeModule.TRIANGLE_FONT_WEAK);
        if (weak.getReadOnlyImage().getHeight() >= strong.getReadOnlyImage().getHeight())
            throw new AssertionError("weak bear must be SMALLER than strong");
    }

    private static void noneProducesPlaceholder() {
        PreparedImage img = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.NONE,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getWidth() != 1 || b.getHeight() != 1)
            throw new AssertionError("NONE must produce a 1x1 placeholder, got "
                    + b.getWidth() + "x" + b.getHeight());
    }

    private static void glyphImageHasNonTransparentPixels() {
        // Pin the "image actually has paint on it" invariant — guards against
        // a future refactor that accidentally renders an all-transparent glyph.
        PreparedImage img = PaxOpeningRangeModule.trendGlyphImage(
                PaxTrendSignalModel.Kind.STRONG_BULL,
                PaxOpeningRangeModule.TRIANGLE_FONT_STRONG);
        BufferedImage b = img.getReadOnlyImage();
        int opaque = 0;
        for (int x = 0; x < b.getWidth(); x++) {
            for (int y = 0; y < b.getHeight(); y++) {
                int alpha = (b.getRGB(x, y) >>> 24) & 0xFF;
                if (alpha >= 128) opaque++;
            }
        }
        if (opaque < 10)
            throw new AssertionError(
                    "strong glyph must have at least 10 opaque pixels, got " + opaque);
    }

    private static int countDominant(BufferedImage b, boolean greenOverRed) {
        int hits = 0;
        for (int x = 0; x < b.getWidth(); x++) {
            for (int y = 0; y < b.getHeight(); y++) {
                int rgb = b.getRGB(x, y);
                int alpha = (rgb >>> 24) & 0xFF;
                if (alpha < 200) continue;     // skip outline + transparent
                int red   = (rgb >>> 16) & 0xFF;
                int green = (rgb >>>  8) & 0xFF;
                if (greenOverRed && green > red + 30) hits++;
                if (!greenOverRed && red > green + 30) hits++;
            }
        }
        return hits;
    }
}
