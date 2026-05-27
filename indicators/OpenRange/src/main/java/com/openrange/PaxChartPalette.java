package com.openrange;

import java.awt.Color;

/**
 * Closed visual vocabulary for the Pax chart UI. Documented in
 * reports/pax-ai-chart-ui-readability-audit-2026-05-27.md.
 *
 * Trade-read direction colors are fixed and additive to the existing
 * PaxHeatwaveColors palette (which keeps its own slightly-different RGB
 * for backwards compatibility with the Heatwave box). New Layer C
 * (trade-read) code paths use this palette so the operator gets one
 * consistent green/red/gray/yellow language across the chart.
 *
 * <ul>
 *   <li>BULL = #3CDC7D (60, 220, 125) — LONG / bullish</li>
 *   <li>BEAR = #EB6464 (235, 100, 100) — SHORT / bearish</li>
 *   <li>STAND_DOWN = #9C9C9C (156, 156, 156) — blocked / stand down</li>
 *   <li>SEVERITY = #FFD040 (255, 208, 64) — outline only; never a
 *       direction</li>
 * </ul>
 *
 * Color is not the only cue. Shape (▲ / ▼ / ⛨) and text (LONG / SHORT /
 * STAND DOWN) confirm direction independently of color.
 */
final class PaxChartPalette {

    static final Color BULL = new Color(0x3C, 0xDC, 0x7D);
    static final Color BEAR = new Color(0xEB, 0x64, 0x64);
    static final Color STAND_DOWN = new Color(0x9C, 0x9C, 0x9C);
    static final Color SEVERITY_OUTLINE = new Color(0xFF, 0xD0, 0x40);

    /** Layer-C card background (very dark, slightly translucent). Shared
     *  across LEVEL_EDGE_GLYPH render paths so LONG / SHORT / STAND DOWN
     *  cards have identical chrome and differ only in border + text. */
    static final Color CARD_BG = new Color(8, 12, 16, 235);

    /** Direction shape prefix for the trade-read headline. Closed set:
     *  LONG  -> ASCII up-arrow ("^")
     *  SHORT -> ASCII down-arrow ("v")
     *  STAND DOWN -> shield ("#") -- ASCII placeholder; renders as a
     *               square shield-like glyph in the monospaced font. */
    enum DirectionShape {
        UP_ARROW("^"), DOWN_ARROW("v"), SHIELD("#");

        final String text;

        DirectionShape(String text) { this.text = text; }
    }

    static DirectionShape shapeForDirection(PaxLevelEdgeModel.Direction dir) {
        if (dir == PaxLevelEdgeModel.Direction.LONG) return DirectionShape.UP_ARROW;
        if (dir == PaxLevelEdgeModel.Direction.SHORT) return DirectionShape.DOWN_ARROW;
        return DirectionShape.SHIELD;
    }

    static Color colorForDirection(PaxLevelEdgeModel.Direction dir) {
        if (dir == PaxLevelEdgeModel.Direction.LONG) return BULL;
        if (dir == PaxLevelEdgeModel.Direction.SHORT) return BEAR;
        return STAND_DOWN;
    }

    /** Returns true when the given AWT color is an authorized palette member.
     *  Used by tests as the closed-vocabulary guard. */
    static boolean isAuthorized(Color c) {
        if (c == null) return false;
        return rgbEquals(c, BULL) || rgbEquals(c, BEAR)
                || rgbEquals(c, STAND_DOWN) || rgbEquals(c, SEVERITY_OUTLINE);
    }

    private static boolean rgbEquals(Color a, Color b) {
        return a.getRed() == b.getRed()
                && a.getGreen() == b.getGreen()
                && a.getBlue() == b.getBlue();
    }

    private PaxChartPalette() {}
}
