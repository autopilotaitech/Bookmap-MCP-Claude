package com.openrange;

import java.awt.Color;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Renders one PaxLevelEdgeModel.Row into a compact 4-line PreparedImage.
 * Compact monospaced text, color from row.colorHint, direction shape
 * prefix from PaxChartPalette so the operator can read bull/bear from
 * shape alone (color-blind / monitor-calibration safe).
 *
 * Two render paths:
 *   render(row, fontSize)             -> actionable LONG/SHORT card.
 *                                        Returns null for WAIT / non-actionable.
 *   renderStandDown(reason, fontSize) -> gray "STAND DOWN" card with reason.
 *                                        Used when an upstream gate (anchor,
 *                                        news, health, low_confidence) blocks
 *                                        all rows.
 *
 * Both paths wrap the content in a 2px border in direction color so the
 * card visually dominates competing labels in the same price band.
 */
final class PaxLevelEdgePainter {

    private static final int PAD_X = 6;
    private static final int PAD_Y = 4;
    private static final int BORDER_PX = 2;

    private PaxLevelEdgePainter() {}

    /** Render a single actionable row. Returns null for rows that should
     *  not draw (defensive -- the callsite should already have filtered by
     *  row.actionable). */
    static PreparedImage render(PaxLevelEdgeModel.Row row, int fontSize) {
        if (row == null || !row.actionable) return null;
        if (row.direction == PaxLevelEdgeModel.Direction.WAIT) return null;
        if (row.price == null) return null;

        List<String> lines = new ArrayList<>(4);
        lines.add(formatHeadline(row));
        String setupLine = formatSetupLine(row);
        if (setupLine != null) lines.add(setupLine);
        String driversLine = formatDriversLine(row);
        if (driversLine != null) lines.add(driversLine);
        String stopLine = formatStopLine(row);
        if (stopLine != null) lines.add(stopLine);

        Color fg = PaxChartPalette.colorForDirection(row.direction);
        return renderCard(lines, fg, fontSize);
    }

    /** Render a STAND DOWN card. Used when the upstream model is blocked by
     *  a global gate (anchor, news, health, low_confidence). Reason is shown
     *  in dimmer text below the headline. Reason may be null/empty -- the
     *  card still renders with just "STAND DOWN". */
    static PreparedImage renderStandDown(String reason, int fontSize) {
        List<String> lines = new ArrayList<>(2);
        lines.add(PaxChartPalette.DirectionShape.SHIELD.text + " STAND DOWN");
        String r = formatStandDownReason(reason);
        if (r != null) lines.add(r);
        return renderCard(lines, PaxChartPalette.STAND_DOWN, fontSize);
    }

    /** Headline-text helper for the stand-down card. Public-visible for
     *  test inspection so we can pin the user-facing wording. */
    static String formatStandDownReason(String reason) {
        if (reason == null) return null;
        String r = reason.trim();
        if (r.isEmpty()) return null;
        switch (r) {
            case "anchor":         return "anchor not LIVE";
            case "news":           return "news blackout";
            case "health":         return "bridge offline";
            case "low_confidence": return "low confidence";
            case "stale":          return "stale snapshot";
            case "session":        return "out of session";
            case "thesis_gate":    return "thesis gate";
            case "size_tier":      return "size tier";
            default:
                // Sanitize: strip non-printable, cap at 24 chars.
                StringBuilder sb = new StringBuilder(Math.min(24, r.length()));
                for (int i = 0; i < r.length() && sb.length() < 24; i++) {
                    char c = r.charAt(i);
                    if (c >= 32 && c <= 126) sb.append(c);
                }
                return sb.length() == 0 ? null : sb.toString();
        }
    }

    /** Shared card-rendering body. Lines render in the given foreground
     *  color; the entire card has a BORDER_PX outline in the same color
     *  so it visually dominates competing labels. */
    private static PreparedImage renderCard(List<String> lines, Color fg, int fontSize) {
        int size = clampFontSize(fontSize);
        Font font = new Font(Font.MONOSPACED, Font.PLAIN, size);

        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        sg.setFont(font);
        FontMetrics m = sg.getFontMetrics();
        int wText = 0;
        for (String s : lines) {
            int sw = m.stringWidth(s);
            if (sw > wText) wText = sw;
        }
        int lineH = m.getHeight();
        sg.dispose();

        int contentW = wText + PAD_X * 2;
        int contentH = lineH * lines.size() + PAD_Y * 2;
        int w = contentW + BORDER_PX * 2;
        int h = contentH + BORDER_PX * 2;

        BufferedImage img = new BufferedImage(Math.max(1, w), Math.max(1, h),
                                                BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = img.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                           RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
        g.setColor(fg);
        g.fillRect(0, 0, w, h);
        g.setColor(PaxChartPalette.CARD_BG);
        g.fillRect(BORDER_PX, BORDER_PX, contentW, contentH);

        g.setColor(fg);
        g.setFont(font);
        int baseY = BORDER_PX + PAD_Y + m.getAscent();
        for (int i = 0; i < lines.size(); i++) {
            g.drawString(lines.get(i), BORDER_PX + PAD_X, baseY + lineH * i);
        }
        g.dispose();
        return new PreparedImage(img);
    }

    static Color colorFor(PaxLevelEdgeModel.Row row) {
        if (row == null) return PaxChartPalette.STAND_DOWN;
        return PaxChartPalette.colorForDirection(row.direction);
    }

    /** "^ LONG 0.72 R~1.15" / "v SHORT 0.68 R~1.02". Direction shape
     *  prefix is fixed: ^ for LONG, v for SHORT. Color is not the only
     *  cue. Confidence + score_R are optional; missing pieces are dropped
     *  from the line. */
    static String formatHeadline(PaxLevelEdgeModel.Row row) {
        String dir = row.direction == PaxLevelEdgeModel.Direction.LONG ? "LONG" : "SHORT";
        String shape = PaxChartPalette.shapeForDirection(row.direction).text;
        StringBuilder sb = new StringBuilder(28);
        sb.append(shape).append(' ').append(dir);
        if (row.confidence != null) {
            sb.append(' ').append(String.format(Locale.ROOT, "%.2f", row.confidence));
        }
        if (row.scoreR != null) {
            sb.append(' ').append(String.format(Locale.ROOT, "R~%.2f", row.scoreR));
        }
        return sb.toString();
    }

    /** Setup line per Slice 2 mapping:
     *    OR_BREAK_FOLLOW  -> "OR BREAK"
     *    LEVEL_FADE_LONG  -> "OR-L FADE" when label is "OR-L", else "FADE LONG"
     *    LEVEL_FADE_SHORT -> "OR-H FADE" when label is "OR-H", else "FADE SHORT"
     *    UNKNOWN_SETUP / null -> null (line omitted)
     */
    static String formatSetupLine(PaxLevelEdgeModel.Row row) {
        String setup = row.setup;
        if (setup == null) return null;
        if ("OR_BREAK_FOLLOW".equals(setup)) return "OR BREAK";
        if ("LEVEL_FADE_LONG".equals(setup)) {
            return "OR-L".equals(row.label) ? "OR-L FADE" : "FADE LONG";
        }
        if ("LEVEL_FADE_SHORT".equals(setup)) {
            return "OR-H".equals(row.label) ? "OR-H FADE" : "FADE SHORT";
        }
        return null;
    }

    /** "Pull + VWAP + Tape" -- max 3 normalized driver labels joined by
     *  " + ". Returns null when topDrivers is empty or yields no usable
     *  labels after normalization. */
    static String formatDriversLine(PaxLevelEdgeModel.Row row) {
        List<String> drivers = row.topDrivers;
        if (drivers == null || drivers.isEmpty()) return null;
        StringBuilder sb = new StringBuilder(32);
        int kept = 0;
        for (String name : drivers) {
            if (kept >= 3) break;
            String pretty = normalizeDriverName(name);
            if (pretty == null) continue;
            if (kept > 0) sb.append(" + ");
            sb.append(pretty);
            kept++;
        }
        if (kept == 0) return null;
        return sb.toString();
    }

    /** Closed-vocabulary normalization. Returns null when the input is
     *  unsuitable (null/empty/too long). Unknown names are title-cased
     *  safely up to 12 chars; longer or non-ASCII names are dropped. */
    static String normalizeDriverName(String raw) {
        if (raw == null) return null;
        String name = raw.trim();
        if (name.isEmpty()) return null;
        switch (name) {
            case "pull_stack":        return "Pull";
            case "tape":              return "Tape";
            case "vwap":              return "VWAP";
            case "micro":             return "Micro";
            case "orderbook":         return "Orderbook";
            case "volume_profile":    return "VP";
            case "session_conviction":return "Conviction";
            case "lt_liquidity":      return "LT";
            default:
                if (name.length() > 12) return null;
                StringBuilder sb = new StringBuilder(name.length());
                boolean upper = true;
                for (int i = 0; i < name.length(); i++) {
                    char c = name.charAt(i);
                    if (c == '_' || c == '-' || c == ' ') {
                        upper = true;
                        continue;
                    }
                    if (c < 32 || c > 126) return null;
                    sb.append(upper ? Character.toUpperCase(c) : Character.toLowerCase(c));
                    upper = false;
                }
                return sb.length() == 0 ? null : sb.toString();
        }
    }

    static String formatStopLine(PaxLevelEdgeModel.Row row) {
        if (row.stopPrice == null) return null;
        return String.format(Locale.ROOT, "STOP %.2f", row.stopPrice);
    }

    private static int clampFontSize(int requested) {
        if (requested < 9) return 9;
        if (requested > 18) return 18;
        return requested;
    }
}
