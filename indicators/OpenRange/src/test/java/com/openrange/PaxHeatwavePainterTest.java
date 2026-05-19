package com.openrange;

import java.awt.image.BufferedImage;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

public class PaxHeatwavePainterTest {

    public static void main(String[] args) {
        rendersWithinExpectedBox();
        noDataModelRendersAmberHeader();
        differentTonesProduceDifferentRowTints();
        ageBeyondStaleThresholdShowsStale();
    }

    private static void rendersWithinExpectedBox() {
        PaxHeatwaveModel model = sampleBull();
        PreparedImage img = PaxHeatwavePainter.render(model, 1000L, 11);
        BufferedImage bi = img.getReadOnlyImage();
        if (bi.getWidth() < 260 || bi.getWidth() > 320) {
            throw new AssertionError("box width out of [260,320]: " + bi.getWidth());
        }
        if (bi.getHeight() < 100) {
            throw new AssertionError("box height unexpectedly small: " + bi.getHeight());
        }
        int opaqueCount = countOpaquePixels(bi);
        if (opaqueCount < 1000) {
            throw new AssertionError("box looks empty (only " + opaqueCount + " opaque pixels)");
        }
        // verify background is not pure transparent
        int corner = bi.getRGB(2, 2);
        int alpha = (corner >>> 24) & 0xFF;
        if (alpha < 200) {
            throw new AssertionError("background corner should be opaque, got alpha=" + alpha);
        }
    }

    private static void noDataModelRendersAmberHeader() {
        PaxHeatwaveModel model = PaxHeatwaveModel.noData(1000L);
        PreparedImage img = PaxHeatwavePainter.render(model, 1000L, 11);
        BufferedImage bi = img.getReadOnlyImage();
        int opaqueCount = countOpaquePixels(bi);
        if (opaqueCount < 500) {
            throw new AssertionError("NO_DATA box should still render header/labels");
        }
    }

    private static void differentTonesProduceDifferentRowTints() {
        PaxHeatwaveModel.Row[] bull = sampleRows(PaxHeatwaveModel.Tone.BULL);
        PaxHeatwaveModel.Row[] bear = sampleRows(PaxHeatwaveModel.Tone.BEAR);
        PaxHeatwaveModel modelBull = new PaxHeatwaveModel("ENTER_LONG", PaxHeatwaveModel.Tone.BULL,
                "+42", bull, 1000L, true);
        PaxHeatwaveModel modelBear = new PaxHeatwaveModel("ENTER_SHORT", PaxHeatwaveModel.Tone.BEAR,
                "-42", bear, 1000L, true);
        BufferedImage a = PaxHeatwavePainter.render(modelBull, 1000L, 11).getReadOnlyImage();
        BufferedImage b = PaxHeatwavePainter.render(modelBear, 1000L, 11).getReadOnlyImage();
        // Compare a vertical column at the same x in row band; they must differ in pixel content.
        int diffs = countDiffs(a, b);
        if (diffs == 0) {
            throw new AssertionError("BULL and BEAR renders should differ in pixel content");
        }
    }

    private static void ageBeyondStaleThresholdShowsStale() {
        PaxHeatwaveModel.Row[] rows = sampleRows(PaxHeatwaveModel.Tone.BULL);
        PaxHeatwaveModel model = new PaxHeatwaveModel("WAIT", PaxHeatwaveModel.Tone.NEUTRAL,
                "+00", rows, 0L, true);
        long now = 60_000L; // 60s after fetch
        PaxHeatwaveModel.AgeState st = model.ageState(now, 5_000L, 30_000L);
        if (st != PaxHeatwaveModel.AgeState.STALE) {
            throw new AssertionError("expected STALE age state, got " + st);
        }
        String text = model.ageText(now, 30_000L);
        if (!text.equals("STALE")) {
            throw new AssertionError("expected ageText STALE, got " + text);
        }
    }

    private static PaxHeatwaveModel sampleBull() {
        PaxHeatwaveModel.Row[] rows = sampleRows(PaxHeatwaveModel.Tone.BULL);
        return new PaxHeatwaveModel("ENTER_LONG", PaxHeatwaveModel.Tone.BULL, "+42", rows, 1000L, true);
    }

    private static PaxHeatwaveModel.Row[] sampleRows(PaxHeatwaveModel.Tone primary) {
        PaxHeatwaveModel.Row[] r = new PaxHeatwaveModel.Row[PaxHeatwaveModel.ROW_COUNT];
        for (int i = 0; i < r.length; i++) {
            String score = primary == PaxHeatwaveModel.Tone.BEAR ? "-0.40" : "+0.40";
            r[i] = new PaxHeatwaveModel.Row(PaxHeatwaveModel.LABELS[i], score, primary, "hint");
        }
        return r;
    }

    private static int countOpaquePixels(BufferedImage bi) {
        int n = 0;
        int w = bi.getWidth();
        int h = bi.getHeight();
        for (int y = 0; y < h; y += 2) {
            for (int x = 0; x < w; x += 2) {
                int rgb = bi.getRGB(x, y);
                if (((rgb >>> 24) & 0xFF) > 0) {
                    n++;
                }
            }
        }
        return n;
    }

    private static int countDiffs(BufferedImage a, BufferedImage b) {
        if (a.getWidth() != b.getWidth() || a.getHeight() != b.getHeight()) {
            return Integer.MAX_VALUE;
        }
        int n = 0;
        int w = a.getWidth();
        int h = a.getHeight();
        for (int y = 0; y < h; y += 2) {
            for (int x = 0; x < w; x += 2) {
                if (a.getRGB(x, y) != b.getRGB(x, y)) {
                    n++;
                }
            }
        }
        return n;
    }
}
