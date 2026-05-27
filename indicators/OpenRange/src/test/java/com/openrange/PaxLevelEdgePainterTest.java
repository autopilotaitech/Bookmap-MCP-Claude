package com.openrange;

import java.awt.image.BufferedImage;
import java.util.Arrays;
import java.util.Collections;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

public class PaxLevelEdgePainterTest {

    public static void main(String[] args) {
        rendersLongRowAsNonEmptyGlyph();
        rendersShortRowAsNonEmptyGlyph();
        nonActionableRowReturnsNull();
        waitDirectionReturnsNull();
        nullRowReturnsNull();
        longAndShortGlyphsDifferInPixelContent();
        missingStopOnlyProducesTwoLines();
        // Slice 2 additions:
        headlineIncludesConfidenceAndScoreR();
        headlineSkipsMissingPieces();
        setupLineOrBreakFollow();
        setupLineFadeLongAtOrL();
        setupLineFadeLongElsewhere();
        setupLineFadeShortAtOrH();
        setupLineFadeShortElsewhere();
        setupLineUnknownReturnsNull();
        driversLineNormalizesAllKnownNames();
        driversLineCapsAtThree();
        driversLineEmptyReturnsNull();
        driversLineDropsOverlyLongUnknown();
        nonActionableWithRawLongReturnsNull();
        actionableWithSetupAndDriversIsTallerThanLegacy();
        glyphDoesNotIncludeBlockedReasonText();
        levelEdgeKeyIncludesSetup();
        levelEdgeKeyIncludesTopDrivers();
        levelEdgeKeyNullModelIsNoneSentinel();
        levelEdgeKeyExcludesNonActionableRows();
        // Phase 2 (chart readability) additions:
        headlineIncludesDirectionShapePrefix();
        renderUsesPaletteGreenForLong();
        renderUsesPaletteRedForShort();
        cardHasBorderInDirectionColor();
        standDownRendersWithoutActionableRow();
        standDownRendersGrayWithKnownReason();
        standDownRendersWithUnknownReason();
        standDownReasonNullProducesOneLine();
        standDownReasonEmptyProducesOneLine();
        formatStandDownReasonMapsCanonicalCodes();
        formatStandDownReasonStripsBadChars();
        System.out.println("PaxLevelEdgePainterTest OK");
        System.exit(0);
    }

    private static PaxLevelEdgeModel.Row longRow() {
        return new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.72, 1.15, 30061.25, "HALF", true);
    }

    private static PaxLevelEdgeModel.Row shortRow() {
        return new PaxLevelEdgeModel.Row(
                "OR-L", 30068.0,
                PaxLevelEdgeModel.Direction.SHORT,
                PaxLevelEdgeModel.ColorHint.NEGATIVE,
                0.68, 1.02, 30070.50, "HALF", true);
    }

    private static void rendersLongRowAsNonEmptyGlyph() {
        PreparedImage img = PaxLevelEdgePainter.render(longRow(), 11);
        if (img == null) throw new AssertionError("expected non-null image");
        BufferedImage bi = img.getReadOnlyImage();
        if (bi.getWidth() < 30 || bi.getWidth() > 200)
            throw new AssertionError("unexpected width " + bi.getWidth());
        if (bi.getHeight() < 20 || bi.getHeight() > 120)
            throw new AssertionError("unexpected height " + bi.getHeight());
        if (countOpaquePixels(bi) < 30)
            throw new AssertionError("glyph looks empty");
    }

    private static void rendersShortRowAsNonEmptyGlyph() {
        PreparedImage img = PaxLevelEdgePainter.render(shortRow(), 11);
        if (img == null) throw new AssertionError("expected non-null image");
        BufferedImage bi = img.getReadOnlyImage();
        if (countOpaquePixels(bi) < 30)
            throw new AssertionError("short glyph looks empty");
    }

    private static void nonActionableRowReturnsNull() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 1.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 0.5, "HALF",
                /*actionable*/ false);
        if (PaxLevelEdgePainter.render(r, 11) != null)
            throw new AssertionError("non-actionable row must not render");
    }

    private static void waitDirectionReturnsNull() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 1.0,
                PaxLevelEdgeModel.Direction.WAIT,
                PaxLevelEdgeModel.ColorHint.NEUTRAL,
                0.5, 1.0, 0.5, "HALF", true);
        if (PaxLevelEdgePainter.render(r, 11) != null)
            throw new AssertionError("WAIT direction must not render");
    }

    private static void nullRowReturnsNull() {
        if (PaxLevelEdgePainter.render(null, 11) != null)
            throw new AssertionError("null row must not render");
    }

    private static void longAndShortGlyphsDifferInPixelContent() {
        PreparedImage longImg = PaxLevelEdgePainter.render(longRow(), 11);
        PreparedImage shortImg = PaxLevelEdgePainter.render(shortRow(), 11);
        if (longImg == null || shortImg == null) throw new AssertionError("renders");
        BufferedImage lb = longImg.getReadOnlyImage();
        BufferedImage sb = shortImg.getReadOnlyImage();
        // Different text content -> different pixel patterns. Compare by
        // counting non-background pixels of the row's color channel.
        int longGreen = 0, shortRed = 0;
        for (int y = 0; y < lb.getHeight(); y++) {
            for (int x = 0; x < lb.getWidth(); x++) {
                int argb = lb.getRGB(x, y);
                int g = (argb >>> 8) & 0xFF;
                int r = (argb >>> 16) & 0xFF;
                if (g > 150 && g > r + 30) longGreen++;
            }
        }
        for (int y = 0; y < sb.getHeight(); y++) {
            for (int x = 0; x < sb.getWidth(); x++) {
                int argb = sb.getRGB(x, y);
                int r = (argb >>> 16) & 0xFF;
                int g = (argb >>> 8) & 0xFF;
                if (r > 150 && r > g + 30) shortRed++;
            }
        }
        if (longGreen < 10) throw new AssertionError("LONG should have green text: " + longGreen);
        if (shortRed < 10) throw new AssertionError("SHORT should have red text: " + shortRed);
    }

    private static void missingStopOnlyProducesTwoLines() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 1.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, /*stop*/ null, "HALF", true);
        PreparedImage img = PaxLevelEdgePainter.render(r, 11);
        if (img == null) throw new AssertionError("rendered");
        BufferedImage with = PaxLevelEdgePainter.render(longRow(), 11).getReadOnlyImage();
        BufferedImage without = img.getReadOnlyImage();
        if (without.getHeight() >= with.getHeight())
            throw new AssertionError("no-stop glyph should be shorter (got "
                    + without.getHeight() + " vs " + with.getHeight() + ")");
    }

    private static int countOpaquePixels(BufferedImage bi) {
        int n = 0;
        for (int y = 0; y < bi.getHeight(); y++) {
            for (int x = 0; x < bi.getWidth(); x++) {
                int alpha = (bi.getRGB(x, y) >>> 24) & 0xFF;
                if (alpha > 200) n++;
            }
        }
        return n;
    }

    // ---------------------------------------------------------------------
    // Slice 2 helpers and tests
    // ---------------------------------------------------------------------

    private static PaxLevelEdgeModel.Row slice2LongRow() {
        return new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.72, 1.15, 30061.25, "HALF",
                /*actionable*/ true,
                "OR_BREAK_FOLLOW",
                Arrays.asList("pull_stack", "vwap", "tape"),
                /*blockedReason*/ null,
                /*distanceAbs*/ 1.25,
                /*levelRelevant*/ true);
    }

    private static PaxLevelEdgeModel.Row slice2ShortRow() {
        return new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.SHORT,
                PaxLevelEdgeModel.Direction.SHORT,
                PaxLevelEdgeModel.ColorHint.NEGATIVE,
                0.68, 1.02, 30070.50, "HALF",
                /*actionable*/ true,
                "LEVEL_FADE_SHORT",
                Arrays.asList("tape", "vwap", "orderbook"),
                null, 1.50, true);
    }

    private static PaxLevelEdgeModel.Row nonActionableRawLongRow() {
        // raw_direction=LONG, direction=WAIT (chart-facing), actionable=false
        // -- the upstream composite resolved a direction but a downstream
        // gate (not_near_level / low_confidence / thesis_gate) suppressed it.
        return new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.WAIT,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.NEUTRAL,
                0.72, 1.15, 30061.25, "HALF",
                /*actionable*/ false,
                "OR_BREAK_FOLLOW",
                Arrays.asList("pull_stack", "vwap"),
                "not_near_level",
                10.5, false);
    }

    private static void headlineIncludesConfidenceAndScoreR() {
        String h = PaxLevelEdgePainter.formatHeadline(slice2LongRow());
        if (!"^ LONG 0.72 R~1.15".equals(h))
            throw new AssertionError("headline: '" + h + "'");
        String s = PaxLevelEdgePainter.formatHeadline(slice2ShortRow());
        if (!"v SHORT 0.68 R~1.02".equals(s))
            throw new AssertionError("short headline: '" + s + "'");
    }

    private static void headlineSkipsMissingPieces() {
        PaxLevelEdgeModel.Row noConf = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                /*conf*/ null, 1.15, 30061.25, "HALF",
                true, "OR_BREAK_FOLLOW", Collections.<String>emptyList(),
                null, null, true);
        String h = PaxLevelEdgePainter.formatHeadline(noConf);
        if (!"^ LONG R~1.15".equals(h))
            throw new AssertionError("missing conf headline: '" + h + "'");
        PaxLevelEdgeModel.Row noScore = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.72, /*scoreR*/ null, 30061.25, "HALF",
                true, "OR_BREAK_FOLLOW", Collections.<String>emptyList(),
                null, null, true);
        h = PaxLevelEdgePainter.formatHeadline(noScore);
        if (!"^ LONG 0.72".equals(h))
            throw new AssertionError("missing scoreR headline: '" + h + "'");
    }

    private static void setupLineOrBreakFollow() {
        String s = PaxLevelEdgePainter.formatSetupLine(slice2LongRow());
        if (!"OR BREAK".equals(s))
            throw new AssertionError("OR_BREAK_FOLLOW -> '" + s + "'");
    }

    private static void setupLineFadeLongAtOrL() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-L", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 30058.75, "HALF", true,
                "LEVEL_FADE_LONG", Collections.<String>emptyList(),
                null, 1.0, true);
        String s = PaxLevelEdgePainter.formatSetupLine(r);
        if (!"OR-L FADE".equals(s))
            throw new AssertionError("LEVEL_FADE_LONG at OR-L -> '" + s + "'");
    }

    private static void setupLineFadeLongElsewhere() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "-1", 30000.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 29998.75, "HALF", true,
                "LEVEL_FADE_LONG", Collections.<String>emptyList(),
                null, 1.0, true);
        String s = PaxLevelEdgePainter.formatSetupLine(r);
        if (!"FADE LONG".equals(s))
            throw new AssertionError("LEVEL_FADE_LONG at -1 -> '" + s + "'");
    }

    private static void setupLineFadeShortAtOrH() {
        String s = PaxLevelEdgePainter.formatSetupLine(slice2ShortRow());
        if (!"OR-H FADE".equals(s))
            throw new AssertionError("LEVEL_FADE_SHORT at OR-H -> '" + s + "'");
    }

    private static void setupLineFadeShortElsewhere() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "+1", 30100.0,
                PaxLevelEdgeModel.Direction.SHORT,
                PaxLevelEdgeModel.Direction.SHORT,
                PaxLevelEdgeModel.ColorHint.NEGATIVE,
                0.5, 1.0, 30110.0, "HALF", true,
                "LEVEL_FADE_SHORT", Collections.<String>emptyList(),
                null, 1.0, true);
        String s = PaxLevelEdgePainter.formatSetupLine(r);
        if (!"FADE SHORT".equals(s))
            throw new AssertionError("LEVEL_FADE_SHORT at +1 -> '" + s + "'");
    }

    private static void setupLineUnknownReturnsNull() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 30058.75, "HALF", true,
                "UNKNOWN_SETUP", Collections.<String>emptyList(),
                null, null, true);
        String s = PaxLevelEdgePainter.formatSetupLine(r);
        if (s != null)
            throw new AssertionError("UNKNOWN_SETUP should omit setup line, got '" + s + "'");
    }

    private static void driversLineNormalizesAllKnownNames() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 30058.75, "HALF", true,
                "OR_BREAK_FOLLOW",
                Arrays.asList("pull_stack", "tape", "vwap"),
                null, null, true);
        String d = PaxLevelEdgePainter.formatDriversLine(r);
        if (!"Pull + Tape + VWAP".equals(d))
            throw new AssertionError("drivers normalization: '" + d + "'");
        // Cover the remaining mappings via the normalizer directly.
        String[][] pairs = {
                {"pull_stack", "Pull"},
                {"tape", "Tape"},
                {"vwap", "VWAP"},
                {"micro", "Micro"},
                {"orderbook", "Orderbook"},
                {"volume_profile", "VP"},
                {"session_conviction", "Conviction"},
                {"lt_liquidity", "LT"},
        };
        for (String[] p : pairs) {
            String got = PaxLevelEdgePainter.normalizeDriverName(p[0]);
            if (!p[1].equals(got))
                throw new AssertionError("normalize " + p[0] + " -> '" + got + "' (expected " + p[1] + ")");
        }
    }

    private static void driversLineCapsAtThree() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 30058.75, "HALF", true,
                "OR_BREAK_FOLLOW",
                Arrays.asList("pull_stack", "tape", "vwap", "orderbook", "micro"),
                null, null, true);
        String d = PaxLevelEdgePainter.formatDriversLine(r);
        if (!"Pull + Tape + VWAP".equals(d))
            throw new AssertionError("drivers cap-3: '" + d + "'");
    }

    private static void driversLineEmptyReturnsNull() {
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.5, 1.0, 30058.75, "HALF", true,
                "OR_BREAK_FOLLOW", Collections.<String>emptyList(),
                null, null, true);
        if (PaxLevelEdgePainter.formatDriversLine(r) != null)
            throw new AssertionError("empty drivers list must yield null line");
    }

    private static void driversLineDropsOverlyLongUnknown() {
        String longName = "this_name_is_way_too_long_for_chart";
        String norm = PaxLevelEdgePainter.normalizeDriverName(longName);
        if (norm != null)
            throw new AssertionError("over-long unknown driver should be dropped, got '" + norm + "'");
    }

    private static void nonActionableWithRawLongReturnsNull() {
        PaxLevelEdgeModel.Row r = nonActionableRawLongRow();
        if (r.rawDirection != PaxLevelEdgeModel.Direction.LONG)
            throw new AssertionError("fixture invariant: rawDirection should be LONG");
        PreparedImage img = PaxLevelEdgePainter.render(r, 11);
        if (img != null)
            throw new AssertionError("non-actionable row with raw LONG must NOT render");
    }

    private static void actionableWithSetupAndDriversIsTallerThanLegacy() {
        // Slice 2 row renders 4 lines (headline + setup + drivers + stop).
        // Legacy longRow() renders 2 lines (headline + stop). The Slice 2
        // glyph must therefore be strictly taller -- a quick sanity check
        // that the reason lines were actually drawn.
        PreparedImage slice2 = PaxLevelEdgePainter.render(slice2LongRow(), 11);
        PreparedImage legacy = PaxLevelEdgePainter.render(longRow(), 11);
        if (slice2 == null || legacy == null)
            throw new AssertionError("both rows should render");
        BufferedImage s = slice2.getReadOnlyImage();
        BufferedImage l = legacy.getReadOnlyImage();
        if (s.getHeight() <= l.getHeight())
            throw new AssertionError("Slice 2 glyph must be taller than legacy: "
                    + s.getHeight() + " vs " + l.getHeight());
        if (countOpaquePixels(s) <= countOpaquePixels(l))
            throw new AssertionError("Slice 2 glyph must have more text than legacy");
    }

    private static void glyphDoesNotIncludeBlockedReasonText() {
        // The painter must NOT render blockedReason. We cannot inspect
        // pixel text directly, but we can confirm the lines list never
        // includes any of the canonical blocked_reason values when the
        // model carries one (here exercised via a non-actionable row, but
        // the contract is enforced by the format helpers themselves).
        PaxLevelEdgeModel.Row r = new PaxLevelEdgeModel.Row(
                "OR-H", 30060.0,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.Direction.LONG,
                PaxLevelEdgeModel.ColorHint.POSITIVE,
                0.72, 1.15, 30061.25, "HALF", true,
                "OR_BREAK_FOLLOW",
                Arrays.asList("pull_stack", "vwap"),
                /*blockedReason*/ "not_near_level",
                1.0, true);
        // Format each line individually and assert no line contains the
        // blocked reason string.
        String[] candidates = new String[] {
                PaxLevelEdgePainter.formatHeadline(r),
                PaxLevelEdgePainter.formatSetupLine(r),
                PaxLevelEdgePainter.formatDriversLine(r),
                PaxLevelEdgePainter.formatStopLine(r),
        };
        for (String line : candidates) {
            if (line == null) continue;
            if (line.contains("not_near_level")
                    || line.contains("BLOCKED")
                    || line.contains("blocked")) {
                throw new AssertionError("painter must not include blocked reason text: '" + line + "'");
            }
        }
    }

    // -- Phase 2 (chart readability) tests --------------------------------

    private static void headlineIncludesDirectionShapePrefix() {
        String h = PaxLevelEdgePainter.formatHeadline(slice2LongRow());
        if (!h.startsWith("^ "))
            throw new AssertionError("LONG headline must start with shape prefix '^ ', got: '" + h + "'");
        String s = PaxLevelEdgePainter.formatHeadline(slice2ShortRow());
        if (!s.startsWith("v "))
            throw new AssertionError("SHORT headline must start with shape prefix 'v ', got: '" + s + "'");
    }

    private static void renderUsesPaletteGreenForLong() {
        PreparedImage img = PaxLevelEdgePainter.render(slice2LongRow(), 11);
        if (img == null) throw new AssertionError("expected glyph");
        BufferedImage bi = img.getReadOnlyImage();
        int paletteR = PaxChartPalette.BULL.getRed();
        int paletteG = PaxChartPalette.BULL.getGreen();
        int paletteB = PaxChartPalette.BULL.getBlue();
        int hits = countPaletteHits(bi, paletteR, paletteG, paletteB, 8);
        if (hits < 50)
            throw new AssertionError("LONG glyph should contain palette green pixels, got " + hits);
    }

    private static void renderUsesPaletteRedForShort() {
        PreparedImage img = PaxLevelEdgePainter.render(slice2ShortRow(), 11);
        if (img == null) throw new AssertionError("expected glyph");
        BufferedImage bi = img.getReadOnlyImage();
        int paletteR = PaxChartPalette.BEAR.getRed();
        int paletteG = PaxChartPalette.BEAR.getGreen();
        int paletteB = PaxChartPalette.BEAR.getBlue();
        int hits = countPaletteHits(bi, paletteR, paletteG, paletteB, 8);
        if (hits < 50)
            throw new AssertionError("SHORT glyph should contain palette red pixels, got " + hits);
    }

    private static void cardHasBorderInDirectionColor() {
        // Top-left corner of the card is part of the border. Sample 4 pixels
        // along each edge and verify at least one matches the direction
        // color (palette BULL for the LONG card).
        PreparedImage img = PaxLevelEdgePainter.render(slice2LongRow(), 11);
        BufferedImage bi = img.getReadOnlyImage();
        int paletteR = PaxChartPalette.BULL.getRed();
        int paletteG = PaxChartPalette.BULL.getGreen();
        int paletteB = PaxChartPalette.BULL.getBlue();
        boolean topEdge = false;
        for (int x = 0; x < bi.getWidth() && !topEdge; x++) {
            topEdge = pixelMatchesPalette(bi, x, 0, paletteR, paletteG, paletteB, 8);
        }
        boolean leftEdge = false;
        for (int y = 0; y < bi.getHeight() && !leftEdge; y++) {
            leftEdge = pixelMatchesPalette(bi, 0, y, paletteR, paletteG, paletteB, 8);
        }
        if (!topEdge)  throw new AssertionError("top border should be palette green");
        if (!leftEdge) throw new AssertionError("left border should be palette green");
    }

    private static void standDownRendersWithoutActionableRow() {
        PreparedImage img = PaxLevelEdgePainter.renderStandDown("anchor", 11);
        if (img == null) throw new AssertionError("stand-down card should render");
        BufferedImage bi = img.getReadOnlyImage();
        if (countOpaquePixels(bi) < 30)
            throw new AssertionError("stand-down card looks empty");
    }

    private static void standDownRendersGrayWithKnownReason() {
        PreparedImage img = PaxLevelEdgePainter.renderStandDown("news", 11);
        BufferedImage bi = img.getReadOnlyImage();
        int gR = PaxChartPalette.STAND_DOWN.getRed();
        int gG = PaxChartPalette.STAND_DOWN.getGreen();
        int gB = PaxChartPalette.STAND_DOWN.getBlue();
        int hits = countPaletteHits(bi, gR, gG, gB, 8);
        if (hits < 30)
            throw new AssertionError("stand-down card should contain palette gray, got " + hits);
        // Confirm it's NOT green or red (would mean a direction snuck in)
        int greenHits = countPaletteHits(bi,
                PaxChartPalette.BULL.getRed(),
                PaxChartPalette.BULL.getGreen(),
                PaxChartPalette.BULL.getBlue(), 8);
        if (greenHits > 5)
            throw new AssertionError("stand-down card must not contain BULL green, got " + greenHits);
    }

    private static void standDownRendersWithUnknownReason() {
        PreparedImage img = PaxLevelEdgePainter.renderStandDown("custom_reason", 11);
        if (img == null) throw new AssertionError("unknown reason should still render a card");
        BufferedImage bi = img.getReadOnlyImage();
        if (countOpaquePixels(bi) < 30)
            throw new AssertionError("unknown-reason card looks empty");
    }

    private static void standDownReasonNullProducesOneLine() {
        PreparedImage img = PaxLevelEdgePainter.renderStandDown(null, 11);
        if (img == null) throw new AssertionError("null reason should still render");
        BufferedImage bi = img.getReadOnlyImage();
        BufferedImage withReason = PaxLevelEdgePainter.renderStandDown("anchor", 11).getReadOnlyImage();
        if (bi.getHeight() >= withReason.getHeight())
            throw new AssertionError("null-reason card should be shorter than reason card");
    }

    private static void standDownReasonEmptyProducesOneLine() {
        PreparedImage img = PaxLevelEdgePainter.renderStandDown("   ", 11);
        if (img == null) throw new AssertionError("empty reason should still render");
        BufferedImage bi = img.getReadOnlyImage();
        BufferedImage withReason = PaxLevelEdgePainter.renderStandDown("anchor", 11).getReadOnlyImage();
        if (bi.getHeight() >= withReason.getHeight())
            throw new AssertionError("blank-reason card should be shorter than reason card");
    }

    private static void formatStandDownReasonMapsCanonicalCodes() {
        String[][] pairs = {
                {"anchor",         "anchor not LIVE"},
                {"news",           "news blackout"},
                {"health",         "bridge offline"},
                {"low_confidence", "low confidence"},
                {"stale",          "stale snapshot"},
                {"session",        "out of session"},
                {"thesis_gate",    "thesis gate"},
                {"size_tier",      "size tier"},
        };
        for (String[] p : pairs) {
            String got = PaxLevelEdgePainter.formatStandDownReason(p[0]);
            if (!p[1].equals(got))
                throw new AssertionError("reason " + p[0] + " -> '" + got + "' expected '" + p[1] + "'");
        }
        if (PaxLevelEdgePainter.formatStandDownReason(null) != null)
            throw new AssertionError("null reason must return null");
        if (PaxLevelEdgePainter.formatStandDownReason("") != null)
            throw new AssertionError("empty reason must return null");
        if (PaxLevelEdgePainter.formatStandDownReason("   ") != null)
            throw new AssertionError("blank reason must return null");
    }

    private static void formatStandDownReasonStripsBadChars() {
        // Non-printable + long input must be sanitized + capped at 24 chars.
        String evil = "abc defghi" + "z".repeat(40);
        String got = PaxLevelEdgePainter.formatStandDownReason(evil);
        if (got == null) throw new AssertionError("sanitized reason should not be null");
        if (got.length() > 24) throw new AssertionError("sanitized reason too long: " + got.length());
        for (int i = 0; i < got.length(); i++) {
            char c = got.charAt(i);
            if (c < 32 || c > 126)
                throw new AssertionError("non-printable char survived sanitization");
        }
    }

    private static int countPaletteHits(BufferedImage bi, int r, int g, int b, int tolerance) {
        int n = 0;
        for (int y = 0; y < bi.getHeight(); y++) {
            for (int x = 0; x < bi.getWidth(); x++) {
                if (pixelMatchesPalette(bi, x, y, r, g, b, tolerance)) n++;
            }
        }
        return n;
    }

    private static boolean pixelMatchesPalette(BufferedImage bi, int x, int y,
                                                int r, int g, int b, int tolerance) {
        int argb = bi.getRGB(x, y);
        int alpha = (argb >>> 24) & 0xFF;
        if (alpha < 200) return false;
        int rr = (argb >>> 16) & 0xFF;
        int gg = (argb >>> 8)  & 0xFF;
        int bb = argb          & 0xFF;
        return Math.abs(rr - r) <= tolerance
                && Math.abs(gg - g) <= tolerance
                && Math.abs(bb - b) <= tolerance;
    }

    // -- Render key tests -------------------------------------------------

    private static PaxLevelEdgeModel modelWith(PaxLevelEdgeModel.Row row) {
        return new PaxLevelEdgeModel(
                "NQM6", 1L, 0L, false, 30060.0, "LIVE",
                Arrays.asList(row), 1L);
    }

    private static void levelEdgeKeyIncludesSetup() {
        PaxLevelEdgeModel.Row a = slice2LongRow();
        PaxLevelEdgeModel.Row b = new PaxLevelEdgeModel.Row(
                a.label, a.price, a.direction, a.rawDirection, a.colorHint,
                a.confidence, a.scoreR, a.stopPrice, a.sizeTier, a.actionable,
                "LEVEL_FADE_LONG", a.topDrivers, a.blockedReason,
                a.distanceAbs, a.levelRelevant);
        String kA = PaxOpeningRangeModule.levelEdgeKey(modelWith(a));
        String kB = PaxOpeningRangeModule.levelEdgeKey(modelWith(b));
        if (kA.equals(kB))
            throw new AssertionError("setup change must invalidate the level-edge key");
    }

    private static void levelEdgeKeyIncludesTopDrivers() {
        PaxLevelEdgeModel.Row a = slice2LongRow();
        PaxLevelEdgeModel.Row b = new PaxLevelEdgeModel.Row(
                a.label, a.price, a.direction, a.rawDirection, a.colorHint,
                a.confidence, a.scoreR, a.stopPrice, a.sizeTier, a.actionable,
                a.setup, Arrays.asList("tape", "vwap", "orderbook"),
                a.blockedReason, a.distanceAbs, a.levelRelevant);
        String kA = PaxOpeningRangeModule.levelEdgeKey(modelWith(a));
        String kB = PaxOpeningRangeModule.levelEdgeKey(modelWith(b));
        if (kA.equals(kB))
            throw new AssertionError("top_drivers change must invalidate the level-edge key");
    }

    private static void levelEdgeKeyNullModelIsNoneSentinel() {
        String k = PaxOpeningRangeModule.levelEdgeKey(null);
        if (!"NONE".equals(k))
            throw new AssertionError("null model must yield NONE sentinel; got " + k);
    }

    private static void levelEdgeKeyExcludesNonActionableRows() {
        PaxLevelEdgeModel.Row actionable = slice2LongRow();
        PaxLevelEdgeModel.Row nonAct = nonActionableRawLongRow();
        PaxLevelEdgeModel onlyActionable = modelWith(actionable);
        PaxLevelEdgeModel withBoth = new PaxLevelEdgeModel(
                "NQM6", 1L, 0L, false, 30060.0, "LIVE",
                Arrays.asList(nonAct, actionable, nonAct), 1L);
        String kOnly = PaxOpeningRangeModule.levelEdgeKey(onlyActionable);
        String kBoth = PaxOpeningRangeModule.levelEdgeKey(withBoth);
        if (!kOnly.equals(kBoth))
            throw new AssertionError(
                    "non-actionable rows must not influence the key: kOnly='"
                            + kOnly + "' kBoth='" + kBoth + "'");
    }
}
