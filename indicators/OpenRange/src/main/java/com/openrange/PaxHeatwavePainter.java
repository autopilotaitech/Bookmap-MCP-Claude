package com.openrange;

import java.awt.Color;
import java.awt.Font;
import java.awt.FontMetrics;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

final class PaxHeatwavePainter {

    static final long WARN_AGE_MS = 5_000L;
    static final long STALE_AGE_MS = 30_000L;

    private static final int PAD_X = 8;
    private static final int PAD_Y = 6;
    private static final int HEADER_GAP = 4;
    private static final int LABEL_COL_CHARS = 7;
    private static final int SCORE_COL_CHARS = 7;
    private static final int HINT_MAX_CHARS = 16;
    private static final int COL_GUTTER = 2;

    private PaxHeatwavePainter() {
    }

    static PreparedImage render(PaxHeatwaveModel model, long nowMs, int fontSize) {
        if (model == null) {
            model = PaxHeatwaveModel.noData(nowMs);
        }
        int safeFont = Math.max(9, Math.min(16, fontSize));
        Font font = monoFont(safeFont);

        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        sg.setFont(font);
        FontMetrics fm = sg.getFontMetrics();
        int charW = Math.max(1, fm.charWidth('M'));
        int rowH = fm.getHeight() + 2;
        int ascent = fm.getAscent();
        sg.dispose();

        String headerLine = headerText(model, nowMs);
        int headerW = fm.stringWidth(headerLine);

        String[] rowLines = new String[PaxHeatwaveModel.ROW_COUNT];
        int maxRowW = 0;
        for (int i = 0; i < PaxHeatwaveModel.ROW_COUNT; i++) {
            rowLines[i] = formatRowLine(model.rows[i]);
            maxRowW = Math.max(maxRowW, fm.stringWidth(rowLines[i]));
        }

        int contentW = Math.max(headerW, maxRowW);
        int boxW = clampBoxWidth(contentW + 2 * PAD_X);
        int boxH = PAD_Y + rowH + HEADER_GAP + rowH * PaxHeatwaveModel.ROW_COUNT + PAD_Y;

        BufferedImage img = new BufferedImage(boxW, boxH, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = img.createGraphics();
        try {
            g.setRenderingHint(RenderingHints.KEY_TEXT_ANTIALIASING, RenderingHints.VALUE_TEXT_ANTIALIAS_ON);
            g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_OFF);
            g.setFont(font);

            g.setColor(PaxHeatwaveColors.BG_OUTER);
            g.fillRect(0, 0, boxW, boxH);
            g.setColor(PaxHeatwaveColors.BG_INNER);
            g.fillRect(1, 1, boxW - 2, boxH - 2);
            g.setColor(PaxHeatwaveColors.BORDER);
            g.drawRect(0, 0, boxW - 1, boxH - 1);

            int y = PAD_Y;
            paintHeader(g, model, nowMs, fm, ascent, charW, boxW, y);
            y += rowH;
            g.setColor(PaxHeatwaveColors.SEP);
            g.drawLine(PAD_X, y + HEADER_GAP / 2, boxW - PAD_X - 1, y + HEADER_GAP / 2);
            y += HEADER_GAP;

            int labelX = PAD_X;
            int scoreX = labelX + charW * (LABEL_COL_CHARS + COL_GUTTER);
            int hintX = scoreX + charW * (SCORE_COL_CHARS + COL_GUTTER);

            for (int i = 0; i < PaxHeatwaveModel.ROW_COUNT; i++) {
                PaxHeatwaveModel.Row row = model.rows[i];
                g.setColor(PaxHeatwaveColors.toneTint(row.tone));
                g.fillRect(PAD_X / 2, y, boxW - PAD_X, rowH);

                g.setColor(PaxHeatwaveColors.TEXT_MUTED);
                g.drawString(padRight(row.label, LABEL_COL_CHARS), labelX, y + ascent);

                g.setColor(PaxHeatwaveColors.toneText(row.tone));
                g.drawString(padLeft(row.scoreText, SCORE_COL_CHARS), scoreX, y + ascent);

                g.setColor(PaxHeatwaveColors.TEXT_DIM);
                g.drawString(truncate(row.hint, HINT_MAX_CHARS), hintX, y + ascent);

                y += rowH;
            }
        } finally {
            g.dispose();
        }
        return new PreparedImage(img);
    }

    static int preferredWidth(PaxHeatwaveModel model, long nowMs, int fontSize) {
        Font font = monoFont(Math.max(9, Math.min(16, fontSize)));
        BufferedImage scratch = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
        Graphics2D sg = scratch.createGraphics();
        sg.setFont(font);
        FontMetrics fm = sg.getFontMetrics();
        int w = fm.stringWidth(headerText(model, nowMs));
        for (int i = 0; i < PaxHeatwaveModel.ROW_COUNT; i++) {
            w = Math.max(w, fm.stringWidth(formatRowLine(model.rows[i])));
        }
        sg.dispose();
        return clampBoxWidth(w + 2 * PAD_X);
    }

    private static int clampBoxWidth(int raw) {
        return Math.max(260, Math.min(320, raw));
    }

    private static void paintHeader(Graphics2D g, PaxHeatwaveModel model, long nowMs,
            FontMetrics fm, int ascent, int charW, int boxW, int y) {
        String title = "PAX HEAT";
        g.setColor(PaxHeatwaveColors.TEXT);
        g.drawString(title, PAD_X, y + ascent);
        int x = PAD_X + fm.stringWidth(title) + charW;

        g.setColor(scoreColor(model.scoreText));
        g.drawString(model.scoreText, x, y + ascent);
        x += fm.stringWidth(model.scoreText) + charW;

        g.setColor(PaxHeatwaveColors.toneText(model.verdictTone));
        g.drawString(model.verdict, x, y + ascent);

        String ageText = model.ageText(nowMs, STALE_AGE_MS);
        int aw = fm.stringWidth(ageText);
        g.setColor(ageColor(model.ageState(nowMs, WARN_AGE_MS, STALE_AGE_MS)));
        g.drawString(ageText, boxW - PAD_X - aw, y + ascent);
    }

    private static String headerText(PaxHeatwaveModel model, long nowMs) {
        return "PAX HEAT " + model.scoreText + "  " + model.verdict + "    " + model.ageText(nowMs, STALE_AGE_MS);
    }

    private static String formatRowLine(PaxHeatwaveModel.Row row) {
        return padRight(row.label, LABEL_COL_CHARS) + "  "
                + padLeft(row.scoreText, SCORE_COL_CHARS) + "  "
                + truncate(row.hint, HINT_MAX_CHARS);
    }

    private static Color scoreColor(String scoreText) {
        if (scoreText == null || scoreText.isEmpty() || scoreText.equals("--")) {
            return PaxHeatwaveColors.NEUTRAL;
        }
        char first = scoreText.charAt(0);
        if (first == '+') {
            return PaxHeatwaveColors.BULL;
        }
        if (first == '-') {
            return PaxHeatwaveColors.BEAR;
        }
        return PaxHeatwaveColors.NEUTRAL;
    }

    private static Color ageColor(PaxHeatwaveModel.AgeState state) {
        if (state == PaxHeatwaveModel.AgeState.FRESH) {
            return PaxHeatwaveColors.AGE_FRESH;
        }
        if (state == PaxHeatwaveModel.AgeState.WARN) {
            return PaxHeatwaveColors.AGE_WARN;
        }
        return PaxHeatwaveColors.AGE_DEAD;
    }

    private static Font monoFont(int size) {
        Font candidate = new Font("Consolas", Font.PLAIN, size);
        if ("Consolas".equalsIgnoreCase(candidate.getFamily())) {
            return candidate;
        }
        return new Font(Font.MONOSPACED, Font.PLAIN, size);
    }

    static String padRight(String s, int n) {
        if (s == null) {
            s = "";
        }
        if (s.length() >= n) {
            return s.substring(0, n);
        }
        StringBuilder sb = new StringBuilder(n);
        sb.append(s);
        while (sb.length() < n) {
            sb.append(' ');
        }
        return sb.toString();
    }

    static String padLeft(String s, int n) {
        if (s == null) {
            s = "";
        }
        if (s.length() >= n) {
            return s.substring(s.length() - n);
        }
        StringBuilder sb = new StringBuilder(n);
        while (sb.length() < n - s.length()) {
            sb.append(' ');
        }
        sb.append(s);
        return sb.toString();
    }

    static String truncate(String s, int n) {
        if (s == null) {
            return "";
        }
        if (s.length() <= n) {
            return s;
        }
        return s.substring(0, n);
    }
}
