package com.openrange;

import java.awt.Color;
import java.awt.Graphics2D;
import java.awt.Polygon;
import java.awt.RenderingHints;
import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Renders a single weak/strong bull/bear triangle as a transparent
 * {@link PreparedImage}. The triangle is painted inside a square canvas
 * sized exactly to the kind's dimensions; transparency around the triangle
 * lets the wrapping {@code CanvasIcon} compose cleanly over chart pixels.
 *
 * <p>Strong = full alpha, large; Weak = half alpha, small. Colors reuse
 * {@link PaxHeatwaveColors} so the heat-box and triangle palette agree.</p>
 *
 * <p>Geometry:</p>
 * <ul>
 *   <li>Bull (UP): apex at top center, base on bottom — points up.</li>
 *   <li>Bear (DOWN): apex at bottom center, base on top — points down.</li>
 * </ul>
 */
final class PaxTrendTrianglePainter {

    static final int STRONG_PIXELS = 18;
    static final int WEAK_PIXELS = 10;
    static final int WEAK_ALPHA = 128;
    static final int STRONG_ALPHA = 255;

    private PaxTrendTrianglePainter() {}

    static int pixelHeight(PaxTrendSignalModel.Kind kind) {
        return kind.isStrong() ? STRONG_PIXELS : WEAK_PIXELS;
    }

    static int pixelWidth(PaxTrendSignalModel.Kind kind) {
        return pixelHeight(kind);   // square bbox; visually correct for equilateral-style apex
    }

    static PreparedImage render(PaxTrendSignalModel.Kind kind) {
        if (kind == null || kind == PaxTrendSignalModel.Kind.NONE) {
            // Degenerate transparent 1x1 — caller should not invoke for NONE,
            // but never throw from a painter.
            BufferedImage img = new BufferedImage(1, 1, BufferedImage.TYPE_INT_ARGB);
            return new PreparedImage(img);
        }
        int size = pixelHeight(kind);
        BufferedImage img = new BufferedImage(size, size, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = img.createGraphics();
        try {
            g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
            Color fill = toFill(kind);
            Polygon p = buildPolygon(kind, size);
            g.setColor(fill);
            g.fillPolygon(p);
            // Crisp outline at same color, full alpha, so the shape is visible
            // even when the half-alpha fill blends into a busy background.
            g.setColor(toOutline(kind));
            g.drawPolygon(p);
        } finally {
            g.dispose();
        }
        return new PreparedImage(img);
    }

    /** Build the triangle in image-pixel coordinates. */
    static Polygon buildPolygon(PaxTrendSignalModel.Kind kind, int size) {
        if (kind.isBull()) {
            // Apex top-center, base on bottom.
            int[] xs = { size / 2, size - 1, 0 };
            int[] ys = { 0,        size - 1, size - 1 };
            return new Polygon(xs, ys, 3);
        }
        // Bear: apex bottom-center, base on top.
        int[] xs = { 0, size - 1, size / 2 };
        int[] ys = { 0, 0,        size - 1 };
        return new Polygon(xs, ys, 3);
    }

    private static Color toFill(PaxTrendSignalModel.Kind kind) {
        Color base = kind.isBull() ? PaxHeatwaveColors.BULL : PaxHeatwaveColors.BEAR;
        int alpha = kind.isStrong() ? STRONG_ALPHA : WEAK_ALPHA;
        return new Color(base.getRed(), base.getGreen(), base.getBlue(), alpha);
    }

    private static Color toOutline(PaxTrendSignalModel.Kind kind) {
        Color base = kind.isBull() ? PaxHeatwaveColors.BULL : PaxHeatwaveColors.BEAR;
        return new Color(base.getRed(), base.getGreen(), base.getBlue(), STRONG_ALPHA);
    }
}
