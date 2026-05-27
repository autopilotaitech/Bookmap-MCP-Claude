package com.openrange;

import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Renders one PaxAttackResponseModel.Row into a tiny one-line label
 * image, e.g. {@code "WATCH BULL OR-L OR_L_SWEEP_RECLAIM SL+BI+BS"}.
 *
 * <p>Hard contracts (Stage 7 of
 * reports/pax-ai-attack-response-plan-2026-05-27.md):</p>
 * <ul>
 *   <li>Renders ONLY rows where {@code isRenderable() == true}. State
 *       UNKNOWN / NO_EDGE and bias NEUTRAL never paint - the parser
 *       drops UNKNOWN rows, and this layer drops NO_EDGE / NEUTRAL
 *       defensively.</li>
 *   <li>If {@code provenEdge == false} the label says {@code WATCH}.
 *       Only when the endpoint says {@code provenEdge=true} does the
 *       label switch to {@code EDGE}. EDGE styling is gated; today this
 *       Java path never sees a true value.</li>
 *   <li>Width is bounded - the image is small, not a HUD card. No
 *       reason text, no per-driver breakdown beyond the compact tags.</li>
 *   <li>Color: BULL_WATCH -> BULL green, BEAR_WATCH -> BEAR red. WATCH
 *       border is 1px so the operator can distinguish it from a future
 *       EDGE label (which would use a 2px border).</li>
 * </ul>
 */
final class PaxAttackResponseLabelPainter {

    private static final int PAD_X = 5;
    private static final int PAD_Y = 2;
    private static final int FONT_SIZE = 10;

    private PaxAttackResponseLabelPainter() {}

    /** Composed label text. Public for unit-test pinning. */
    static String formatLabelText(PaxAttackResponseModel.Row row) {
        if (row == null) return "";
        String biasShort = row.bias == PaxAttackResponseModel.Bias.BULL_WATCH
                ? "BULL"
                : (row.bias == PaxAttackResponseModel.Bias.BEAR_WATCH ? "BEAR" : "NEUTRAL");
        String tier = row.provenEdge ? "EDGE" : "WATCH";
        String driverTags = row.compactDriverTags();
        StringBuilder sb = new StringBuilder(48);
        sb.append(tier).append(' ').append(biasShort)
          .append(' ').append(row.location)
          .append(' ').append(row.state.name());
        if (!driverTags.isEmpty()) {
            sb.append(' ').append(driverTags);
        }
        return sb.toString();
    }

    /** Render a single attack-response row as a small label image. Returns
     *  null for non-renderable rows (defense in depth - caller should
     *  already have filtered). */
    static PreparedImage render(PaxAttackResponseModel.Row row) {
        if (row == null || !row.isRenderable()) return null;
        return renderInternal(row, FONT_SIZE);
    }

    /** Same as {@link #render} but lets tests vary the font size. */
    static PreparedImage render(PaxAttackResponseModel.Row row, int fontSize) {
        if (row == null || !row.isRenderable()) return null;
        return renderInternal(row, fontSize);
    }

    private static PreparedImage renderInternal(PaxAttackResponseModel.Row row,
            int fontSize) {
        String text = formatLabelText(row);
        Color accent = colorFor(row.bias);
        int safeFont = Math.max(8, Math.min(16, fontSize));
        Font font = new Font(Font.MONOSPACED, Font.BOLD, safeFont);
        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        sg.setFont(font);
        FontMetrics fm = sg.getFontMetrics();
        int textWidth = fm.stringWidth(text);
        int textHeight = fm.getHeight();
        sg.dispose();

        int width = textWidth + PAD_X * 2;
        int height = textHeight + PAD_Y * 2;
        // Cap the label to a small footprint so the operator's chart doesn't
        // pick up a wide block. WATCH labels stay small.
        if (width > 240) width = 240;

        BufferedImage image = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = image.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING,
                RenderingHints.VALUE_TEXT_ANTIALIAS_ON);

        g.setColor(PaxChartPalette.CARD_BG);
        g.fillRoundRect(0, 0, width - 1, height - 1, 4, 4);
        g.setStroke(new BasicStroke(row.provenEdge ? 2f : 1f));
        g.setColor(accent);
        g.drawRoundRect(0, 0, width - 1, height - 1, 4, 4);

        g.setFont(font);
        g.setColor(accent);
        g.drawString(text, PAD_X, PAD_Y + fm.getAscent());

        g.dispose();
        return new PreparedImage(image);
    }

    /** Color mapping. NEUTRAL rows never reach here (filtered by
     *  isRenderable). Defense-in-depth STAND_DOWN gray fallback. */
    static Color colorFor(PaxAttackResponseModel.Bias bias) {
        if (bias == PaxAttackResponseModel.Bias.BULL_WATCH) return PaxChartPalette.BULL;
        if (bias == PaxAttackResponseModel.Bias.BEAR_WATCH) return PaxChartPalette.BEAR;
        return PaxChartPalette.STAND_DOWN;
    }
}
