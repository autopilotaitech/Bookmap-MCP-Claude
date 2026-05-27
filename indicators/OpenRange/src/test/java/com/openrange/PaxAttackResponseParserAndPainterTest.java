package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

import velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage;

/**
 * Stage 7 tests: parser safety defaults + small label painter.
 *
 * Plan: reports/pax-ai-attack-response-plan-2026-05-27.md section 7.
 *
 * Invariants pinned:
 *  - parser drops rows with unknown state (no fallback rendering).
 *  - parser defaults missing proven_edge to FALSE.
 *  - health != "ok" yields zero rows.
 *  - blocked.anchor=true yields zero rows.
 *  - blocked.stale=true yields zero rows.
 *  - painter renders only renderable rows; UNKNOWN / NO_EDGE / NEUTRAL drop.
 *  - painter label says WATCH unless proven_edge=true.
 *  - painter color: BULL -> BULL green, BEAR -> BEAR red.
 *  - WATCH labels stay small (height <= 22 px, width bounded).
 */
public class PaxAttackResponseParserAndPainterTest {

    public static void main(String[] args) {
        parsesBullWatchRow();
        parsesBearWatchRow();
        parserDropsUnknownState();
        parserDefaultsProvenEdgeFalse();
        parserEmptyOnHealthOffline();
        parserEmptyOnAnchorBlocked();
        parserEmptyOnStaleBlocked();
        parserHandlesMissingStatesArray();
        parserRecursesAttackResponseAndDrivers();
        painterRendersBullWatchLabel();
        painterRendersBearWatchLabel();
        painterReturnsNullForNeutralRow();
        painterReturnsNullForNoEdgeRow();
        painterReturnsNullForUnrenderableRow();
        painterUsesGreenForBull();
        painterUsesRedForBear();
        labelTextSaysWatchWhenProvenEdgeFalse();
        labelTextSaysEdgeWhenProvenEdgeTrue();
        labelTextCompactsDrivers();
        labelHeightStaysSmall();
        System.out.println("PaxAttackResponseParserAndPainterTest OK");
    }

    // --- helpers --------------------------------------------------------

    private static PaxAttackResponseModel.Row row(
            String id, PaxAttackResponseModel.State state,
            PaxAttackResponseModel.Bias bias, double price,
            List<String> drivers, boolean provenEdge) {
        return new PaxAttackResponseModel.Row(
                id == null ? "" : id, "OR-L", price, state, bias,
                "SWEEP_LOW", "RECLAIMED", drivers,
                0.65, provenEdge, 1_000L);
    }

    // --- parser tests ---------------------------------------------------

    private static void parsesBullWatchRow() {
        String json =
            "{\"alias\":\"NQM6\",\"asOfMs\":1,\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":30145.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"attack\":\"SWEEP_LOW\",\"response\":\"RECLAIMED\","
          + "  \"drivers\":[\"sweep_low\",\"bid_iceberg\",\"bid_stack\"],"
          + "  \"confidence\":0.65,\"proven_edge\":false,"
          + "  \"timestamp_ms\":1700000000000"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 999L);
        if (m.rows.size() != 1) throw new AssertionError("expected 1 row");
        PaxAttackResponseModel.Row r = m.rows.get(0);
        if (r.state != PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM)
            throw new AssertionError("state");
        if (r.bias != PaxAttackResponseModel.Bias.BULL_WATCH)
            throw new AssertionError("bias");
        if (Math.abs(r.levelPrice - 30145.0) > 1e-6)
            throw new AssertionError("level_price");
        if (r.provenEdge) throw new AssertionError("provenEdge must be false");
        if (!r.isRenderable()) throw new AssertionError("must be renderable");
    }

    private static void parsesBearWatchRow() {
        String json =
            "{\"alias\":\"NQM6\",\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigB\",\"location\":\"OR-H\",\"level_price\":30192.0,"
          + "  \"state\":\"OR_H_SWEEP_FAIL\",\"bias\":\"BEAR_WATCH\","
          + "  \"attack\":\"SWEEP_HIGH\",\"response\":\"FAILED_CONTINUATION\","
          + "  \"drivers\":[\"sweep_high\",\"ask_iceberg\"],"
          + "  \"confidence\":0.60,\"proven_edge\":false,"
          + "  \"timestamp_ms\":1700000000500"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 999L);
        if (m.rows.size() != 1) throw new AssertionError("expected 1 row");
        if (m.rows.get(0).bias != PaxAttackResponseModel.Bias.BEAR_WATCH)
            throw new AssertionError("bias");
    }

    private static void parserDropsUnknownState() {
        // Unknown state strings (a future-Python schema typo) MUST be
        // dropped, never rendered with a fallback.
        String json =
            "{\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigZ\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_MYSTERY_NEW_STATE\",\"bias\":\"BULL_WATCH\","
          + "  \"proven_edge\":false,\"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) {
            throw new AssertionError("unknown state must be dropped, not rendered");
        }
    }

    private static void parserDefaultsProvenEdgeFalse() {
        String json =
            "{\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (m.rows.isEmpty()) throw new AssertionError("row expected");
        if (m.rows.get(0).provenEdge) {
            throw new AssertionError(
                "missing proven_edge MUST default to false; partial payload must NOT promote to EDGE");
        }
    }

    private static void parserEmptyOnHealthOffline() {
        String json =
            "{\"health\":\"offline\","
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) {
            throw new AssertionError("health=offline MUST yield zero rows");
        }
    }

    private static void parserEmptyOnAnchorBlocked() {
        String json =
            "{\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":true},"
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) {
            throw new AssertionError("blocked.anchor=true MUST yield zero rows");
        }
    }

    private static void parserEmptyOnStaleBlocked() {
        String json =
            "{\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":true,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) {
            throw new AssertionError("blocked.stale=true MUST yield zero rows");
        }
    }

    private static void parserHandlesMissingStatesArray() {
        String json = "{\"health\":\"ok\",\"blocked\":{}}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) throw new AssertionError("no states -> empty rows");
        if (!"ok".equals(m.health)) throw new AssertionError("health");
    }

    private static void parserRecursesAttackResponseAndDrivers() {
        String json =
            "{\"health\":\"ok\","
          + "\"blocked\":{\"health\":false,\"stale\":false,\"anchor\":false},"
          + "\"states\":[{"
          + "  \"id\":\"sigA\",\"location\":\"OR-L\",\"level_price\":1.0,"
          + "  \"state\":\"OR_L_SWEEP_RECLAIM\",\"bias\":\"BULL_WATCH\","
          + "  \"attack\":\"SWEEP_LOW\",\"response\":\"RECLAIMED\","
          + "  \"drivers\":[\"sweep_low\",\"bid_iceberg\",\"bid_stack\"],"
          + "  \"proven_edge\":false,\"timestamp_ms\":1"
          + "}]}";
        PaxAttackResponseModel m = PaxAttackResponseSnapshotParser.parse(json, 0L);
        PaxAttackResponseModel.Row r = m.rows.get(0);
        if (!"SWEEP_LOW".equals(r.attack)) throw new AssertionError();
        if (!"RECLAIMED".equals(r.response)) throw new AssertionError();
        if (r.drivers.size() != 3) throw new AssertionError();
        if (!r.drivers.contains("bid_iceberg")) throw new AssertionError();
    }

    // --- painter tests --------------------------------------------------

    private static void painterRendersBullWatchLabel() {
        PreparedImage img = PaxAttackResponseLabelPainter.render(
                row("a", PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                        PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                        Arrays.asList("sweep_low", "bid_iceberg", "bid_stack"),
                        false));
        if (img == null) throw new AssertionError("BULL_WATCH must render");
        if (img.getReadOnlyImage().getWidth() < 50)
            throw new AssertionError("label too narrow");
    }

    private static void painterRendersBearWatchLabel() {
        PreparedImage img = PaxAttackResponseLabelPainter.render(
                row("a", PaxAttackResponseModel.State.OR_H_SWEEP_FAIL,
                        PaxAttackResponseModel.Bias.BEAR_WATCH, 30192.0,
                        Arrays.asList("sweep_high", "ask_iceberg"), false));
        if (img == null) throw new AssertionError("BEAR_WATCH must render");
    }

    private static void painterReturnsNullForNeutralRow() {
        PreparedImage img = PaxAttackResponseLabelPainter.render(
                row("a", PaxAttackResponseModel.State.NO_EDGE,
                        PaxAttackResponseModel.Bias.NEUTRAL, 30145.0,
                        Collections.<String>emptyList(), false));
        if (img != null) {
            throw new AssertionError("NEUTRAL rows MUST NOT paint - they are absence of edge");
        }
    }

    private static void painterReturnsNullForNoEdgeRow() {
        PreparedImage img = PaxAttackResponseLabelPainter.render(
                row("a", PaxAttackResponseModel.State.NO_EDGE,
                        PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                        Collections.<String>emptyList(), false));
        if (img != null) {
            throw new AssertionError("NO_EDGE rows MUST NOT paint regardless of bias");
        }
    }

    private static void painterReturnsNullForUnrenderableRow() {
        // price = 0 -> not renderable
        PaxAttackResponseModel.Row bad = new PaxAttackResponseModel.Row(
                "bad", "OR-L", 0.0,
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, "SWEEP_LOW", "RECLAIMED",
                Collections.<String>emptyList(), 0.5, false, 1L);
        if (PaxAttackResponseLabelPainter.render(bad) != null)
            throw new AssertionError("unrenderable row must be skipped");
    }

    private static void painterUsesGreenForBull() {
        java.awt.Color c = PaxAttackResponseLabelPainter.colorFor(
                PaxAttackResponseModel.Bias.BULL_WATCH);
        if (c.getRed() != PaxChartPalette.BULL.getRed()
                || c.getGreen() != PaxChartPalette.BULL.getGreen()
                || c.getBlue() != PaxChartPalette.BULL.getBlue()) {
            throw new AssertionError("BULL_WATCH must use BULL palette color");
        }
    }

    private static void painterUsesRedForBear() {
        java.awt.Color c = PaxAttackResponseLabelPainter.colorFor(
                PaxAttackResponseModel.Bias.BEAR_WATCH);
        if (c.getRed() != PaxChartPalette.BEAR.getRed()
                || c.getGreen() != PaxChartPalette.BEAR.getGreen()
                || c.getBlue() != PaxChartPalette.BEAR.getBlue()) {
            throw new AssertionError("BEAR_WATCH must use BEAR palette color");
        }
    }

    private static void labelTextSaysWatchWhenProvenEdgeFalse() {
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                Arrays.asList("sweep_low"), false);
        String text = PaxAttackResponseLabelPainter.formatLabelText(r);
        if (!text.startsWith("WATCH ")) {
            throw new AssertionError("proven_edge=false MUST render WATCH; got " + text);
        }
        if (text.contains("EDGE")) {
            throw new AssertionError("WATCH label must NOT contain EDGE; got " + text);
        }
    }

    private static void labelTextSaysEdgeWhenProvenEdgeTrue() {
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                Arrays.asList("sweep_low"), true);
        String text = PaxAttackResponseLabelPainter.formatLabelText(r);
        if (!text.startsWith("EDGE ")) {
            throw new AssertionError("proven_edge=true label must start with EDGE; got " + text);
        }
    }

    private static void labelTextCompactsDrivers() {
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                Arrays.asList("sweep_low", "bid_iceberg", "bid_stack"), false);
        String text = PaxAttackResponseLabelPainter.formatLabelText(r);
        if (!text.contains("SL+BI+BS")) {
            throw new AssertionError("driver compaction failed; got " + text);
        }
    }

    private static void labelHeightStaysSmall() {
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 30145.0,
                Arrays.asList("sweep_low", "bid_iceberg", "bid_stack"), false);
        PreparedImage img = PaxAttackResponseLabelPainter.render(r);
        int h = img.getReadOnlyImage().getHeight();
        if (h > 22) throw new AssertionError("WATCH label too tall: " + h);
        int w = img.getReadOnlyImage().getWidth();
        if (w > 240) throw new AssertionError("WATCH label too wide (cap=240): " + w);
    }
}
