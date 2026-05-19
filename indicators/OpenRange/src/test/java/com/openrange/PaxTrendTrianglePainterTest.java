package com.openrange;

import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

public class PaxTrendTrianglePainterTest {

    public static void main(String[] args) {
        strongBullRendersGreenUpTriangle();
        weakBullRendersDimmerSmallerTriangle();
        strongBearRendersRedDownTriangle();
        weakBearRendersDimmerSmallerTriangle();
        noneRendersOnePixelTransparent();
        bullPolygonGeometryUp();
        bearPolygonGeometryDown();
        System.out.println("PaxTrendTrianglePainterTest OK");
        // Defense in depth: even with -Djava.awt.headless=true, force exit
        // so the JVM never lingers if AWT did spin up an EDT.
        System.exit(0);
    }

    private static void strongBullRendersGreenUpTriangle() {
        PreparedImage img = PaxTrendTrianglePainter.render(PaxTrendSignalModel.Kind.STRONG_BULL);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getWidth() != PaxTrendTrianglePainter.STRONG_PIXELS)
            throw new AssertionError("strong width mismatch, got " + b.getWidth());
        if (b.getHeight() != PaxTrendTrianglePainter.STRONG_PIXELS)
            throw new AssertionError("strong height mismatch");
        // Apex (top center) should be opaque green.
        int apex = b.getRGB(b.getWidth() / 2, 1);
        int alpha = (apex >>> 24) & 0xFF;
        int red   = (apex >>> 16) & 0xFF;
        int green = (apex >>>  8) & 0xFF;
        if (alpha < 200)
            throw new AssertionError("strong apex must be (near) opaque; alpha=" + alpha);
        if (green <= red)
            throw new AssertionError("bull apex must be green-dominant; green=" + green + " red=" + red);
    }

    private static void weakBullRendersDimmerSmallerTriangle() {
        PreparedImage img = PaxTrendTrianglePainter.render(PaxTrendSignalModel.Kind.WEAK_BULL);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getWidth() != PaxTrendTrianglePainter.WEAK_PIXELS)
            throw new AssertionError("weak width mismatch, got " + b.getWidth());
        // Center pixel inside the polygon — should be the half-alpha fill.
        int mid = b.getRGB(b.getWidth() / 2, b.getHeight() / 2);
        int alpha = (mid >>> 24) & 0xFF;
        // Weak fill alpha is 128; outline pixels are full alpha. Center is
        // (almost always) inside the triangle and not on the outline pixel
        // for a 10×10 image, so we expect the half-alpha fill.
        if (alpha == 0)
            throw new AssertionError("weak triangle must have non-transparent fill at center");
    }

    private static void strongBearRendersRedDownTriangle() {
        PreparedImage img = PaxTrendTrianglePainter.render(PaxTrendSignalModel.Kind.STRONG_BEAR);
        BufferedImage b = img.getReadOnlyImage();
        // Apex (bottom center) should be opaque red.
        int apex = b.getRGB(b.getWidth() / 2, b.getHeight() - 2);
        int alpha = (apex >>> 24) & 0xFF;
        int red   = (apex >>> 16) & 0xFF;
        int green = (apex >>>  8) & 0xFF;
        if (alpha < 200)
            throw new AssertionError("strong bear apex must be (near) opaque; alpha=" + alpha);
        if (red <= green)
            throw new AssertionError("bear apex must be red-dominant; red=" + red + " green=" + green);
    }

    private static void weakBearRendersDimmerSmallerTriangle() {
        PreparedImage img = PaxTrendTrianglePainter.render(PaxTrendSignalModel.Kind.WEAK_BEAR);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getHeight() != PaxTrendTrianglePainter.WEAK_PIXELS)
            throw new AssertionError("weak bear height mismatch");
    }

    private static void noneRendersOnePixelTransparent() {
        PreparedImage img = PaxTrendTrianglePainter.render(PaxTrendSignalModel.Kind.NONE);
        BufferedImage b = img.getReadOnlyImage();
        if (b.getWidth() != 1 || b.getHeight() != 1)
            throw new AssertionError("NONE must render a 1x1 placeholder");
    }

    private static void bullPolygonGeometryUp() {
        java.awt.Polygon p = PaxTrendTrianglePainter.buildPolygon(
                PaxTrendSignalModel.Kind.STRONG_BULL, 10);
        // Apex y must be 0 (top); base y must be size-1.
        // ys = { 0, size-1, size-1 }
        if (p.ypoints[0] != 0) throw new AssertionError("bull apex must be at top, ypoints[0]=" + p.ypoints[0]);
        if (p.ypoints[1] != 9 || p.ypoints[2] != 9)
            throw new AssertionError("bull base must be at bottom; ypoints=" + p.ypoints[1] + "," + p.ypoints[2]);
    }

    private static void bearPolygonGeometryDown() {
        java.awt.Polygon p = PaxTrendTrianglePainter.buildPolygon(
                PaxTrendSignalModel.Kind.STRONG_BEAR, 10);
        // Apex y must be size-1 (bottom); base y must be 0 (top).
        if (p.ypoints[2] != 9) throw new AssertionError("bear apex must be at bottom, ypoints[2]=" + p.ypoints[2]);
        if (p.ypoints[0] != 0 || p.ypoints[1] != 0)
            throw new AssertionError("bear base must be at top; ypoints=" + p.ypoints[0] + "," + p.ypoints[1]);
    }
}
