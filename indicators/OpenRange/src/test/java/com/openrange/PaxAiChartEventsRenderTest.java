package com.openrange;

/**
 * Unit tests for the source-aware render helpers + combined collision
 * allocator added in Phase 5. These exercise pure static methods on
 * {@link PaxOpeningRangeModule} so they do not need a live painter.
 *
 * Locked contracts:
 *  - chartEventRenderText returns "AI ..." / "LOC ..." / "CTX ..." based
 *    on evt.source, with a direction arrow + score appended.
 *  - chartEventCollisionKey produces the SAME key for AI and LOC events
 *    at the same time/price/side — they share a lane so they stack
 *    (don't overlap). Source is NOT part of the collision bucket.
 *  - collapseDenseCluster preserves ENTRY/EXIT (severityRank 0/1) over
 *    WATCH/INFO when more events than maxVisible.
 */
public class PaxAiChartEventsRenderTest {

    public static void main(String[] args) {
        aiAndLocalShareCollisionLane();
        farPricesDoNotShareLane();
        aiBadgeTextIsCompactAscii();
        locBadgeTextIsCompactAscii();
        ctxBadgeTextIsCompactAscii();
        renderTextIncludesConfidenceScore();
        chartEventIconIncludesCompactText();
        chartEventIconKeepsContextText();
        chartEventIconShowsCollapsedCount();
        collapseDisplayUsesOneBadgePerCollisionBucket();
        collapseDisplayKeepsHighestRankedEvent();
        chartEventOffsetClearsTradeBubbles();
        collapseDenseClusterKeepsEntryOverContext();
        emptyHistoryReturnsEmpty();
        belowCapPassThrough();
        filterByAliasKeepsMatching();
        filterByAliasDropsForeignAlias();
        filterByAliasKeepsEmptyAlias();
        filterByAliasEmptyOrNullStateAliasPassesAll();
        paxAiRenderSignatureDistinguishesOneForOneReplacement();
        paxAiRenderSignatureEmptyDiffersFromNonEmpty();
        paxAiRenderSignatureOrderIndependent();
        paxAiRenderSignatureStableAcrossRepeatedCalls();
        paxAiRenderSignatureNullAndEmptySame();
        System.out.println("PaxAiChartEventsRenderTest OK");
    }

    private static PaxInstitutionalChartEvent ev(String id, String source,
            String direction, double price, long ts, String label,
            String markerText, String severity, double confidence) {
        return new PaxInstitutionalChartEvent(id, "NQM6", label, price,
                direction.equals("SHORT") ? "below" : "above",
                "AI_ACCEPTANCE", direction, "PAY_FOR_TRADE",
                markerText, "#FF40D9", severity, ts, source, confidence);
    }

    // ---- Collision lane ----------------------------------------------------

    private static void aiAndLocalShareCollisionLane() {
        // User constraint: collision handling works across AI + local
        // markers TOGETHER. Identical price/time/side -> same bucket.
        PaxInstitutionalChartEvent ai = ev("idAi", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H", "AI ▲ OR-H 72", "ENTRY", 0.72);
        PaxInstitutionalChartEvent loc = ev("idLoc", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68);
        String aLane = PaxOpeningRangeModule.chartEventCollisionKey(ai, 0.25);
        String lLane = PaxOpeningRangeModule.chartEventCollisionKey(loc, 0.25);
        if (!aLane.equals(lLane)) {
            throw new AssertionError(
                "AI + LOC at same time/price/side MUST share collision lane "
                + "so the ordinal allocator stacks them; got "
                + aLane + " vs " + lLane);
        }
    }

    private static void farPricesDoNotShareLane() {
        PaxInstitutionalChartEvent near = ev("near", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H", "AI ▲ OR-H 72", "ENTRY", 0.72);
        PaxInstitutionalChartEvent far = ev("far", "pax_ai", "LONG",
                20004.0, 5_000L, "+1", "AI ▲ +1 72", "ENTRY", 0.72);
        String a = PaxOpeningRangeModule.chartEventCollisionKey(near, 0.25);
        String b = PaxOpeningRangeModule.chartEventCollisionKey(far, 0.25);
        if (a.equals(b)) {
            throw new AssertionError("distant prices must occupy different lanes");
        }
    }

    // ---- Source-aware render text -----------------------------------------

    private static void aiBadgeTextIsCompactAscii() {
        PaxInstitutionalChartEvent ai = ev("idAi", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H", "AI ▲ OR-H 72", "ENTRY", 0.72);
        String text = PaxOpeningRangeModule.chartEventRenderText(ai);
        if (!"AI^ORH72".equals(text)) {
            throw new AssertionError("AI marker must be compact ASCII; got " + text);
        }
        assertAscii(text);
    }

    private static void locBadgeTextIsCompactAscii() {
        PaxInstitutionalChartEvent loc = ev("idL", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68);
        String text = PaxOpeningRangeModule.chartEventRenderText(loc);
        if (!"L^ACC68".equals(text)) {
            throw new AssertionError("LOC marker must be compact ASCII; got " + text);
        }
        assertAscii(text);
    }

    private static void ctxBadgeTextIsCompactAscii() {
        PaxInstitutionalChartEvent ctx = ev("idC", "micro_events",
                "NONE", 20000.0, 5_000L, "OR-H", "STACK", "INFO", 0.44);
        String text = PaxOpeningRangeModule.chartEventRenderText(ctx);
        if (!"C.STK44".equals(text)) {
            throw new AssertionError("CTX marker must be compact ASCII; got " + text);
        }
        assertAscii(text);
    }

    private static void renderTextIncludesConfidenceScore() {
        PaxInstitutionalChartEvent loc = ev("idL", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.685);
        String text = PaxOpeningRangeModule.chartEventRenderText(loc);
        // 0.685 -> 69 (rounded).
        if (!text.endsWith(" 69") && !text.endsWith("69")) {
            throw new AssertionError("expected score 69 at end; got " + text);
        }
    }

    private static void chartEventIconIncludesCompactText() {
        PaxInstitutionalChartEvent loc = ev("idL", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68);
        velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage image =
                PaxOpeningRangeModule.chartEventIconImage(loc, loc.colorFromHint());
        int width = image.getReadOnlyImage().getWidth();
        if (width <= 34) {
            throw new AssertionError("chart-event marker must include readable compact text; width=" + width);
        }
        if (image.getReadOnlyImage().getHeight() != 17) {
            throw new AssertionError("marker height should stay compact");
        }
    }

    private static void chartEventIconKeepsContextText() {
        PaxInstitutionalChartEvent ctx = ev("idC", "micro_events",
                "NONE", 20000.0, 5_000L, "OR-H", "ABS-B", "WARNING", 0.44);
        velox.api.layer1.layers.strategies.interfaces.ScreenSpaceCanvas.PreparedImage image =
                PaxOpeningRangeModule.chartEventIconImage(ctx, ctx.colorFromHint());
        if (image.getReadOnlyImage().getWidth() <= 34) {
            throw new AssertionError("context markers must not render as anonymous dots");
        }
    }

    private static void chartEventIconShowsCollapsedCount() {
        PaxInstitutionalChartEvent loc = ev("idL", "institutional_thesis",
                "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68);
        int single = PaxOpeningRangeModule.chartEventIconImage(
                loc, loc.colorFromHint(), 1).getReadOnlyImage().getWidth();
        int cluster = PaxOpeningRangeModule.chartEventIconImage(
                loc, loc.colorFromHint(), 4).getReadOnlyImage().getWidth();
        if (cluster <= single) {
            throw new AssertionError("collapsed marker should include +N count");
        }
    }

    private static void collapseDisplayUsesOneBadgePerCollisionBucket() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(ev("a1", "pax_ai", "LONG", 20000.0, 5_000L, "OR-H", "AI ORH", "ENTRY", 0.72));
        in.add(ev("l1", "institutional_thesis", "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68));
        in.add(ev("c1", "micro_events", "LONG", 20000.25, 5_100L, "OR-H", "WATCH", "WATCH", 0.40));
        java.util.List<PaxOpeningRangeModule.ChartEventDisplay> out =
                PaxOpeningRangeModule.collapseChartEventsForDisplay(in, 0.25, 10);
        if (out.size() != 1) {
            throw new AssertionError("same collision bucket should draw one badge; got " + out.size());
        }
        if (out.get(0).clusterSize != 3) {
            throw new AssertionError("cluster count should preserve collapsed evidence count");
        }
    }

    private static void collapseDisplayKeepsHighestRankedEvent() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(ev("ctx", "micro_events", "LONG", 20000.0, 5_000L, "OR-H", "WATCH", "INFO", 0.90));
        in.add(ev("ai", "pax_ai", "LONG", 20000.0, 5_000L, "OR-H", "AI ORH", "ENTRY", 0.72));
        in.add(ev("loc", "institutional_thesis", "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.80));
        java.util.List<PaxOpeningRangeModule.ChartEventDisplay> out =
                PaxOpeningRangeModule.collapseChartEventsForDisplay(in, 0.25, 10);
        if (out.size() != 1) throw new AssertionError();
        if (!"ai".equals(out.get(0).event.id)) {
            throw new AssertionError("AI ENTRY should win over local/context in same bucket; got "
                    + out.get(0).event.id);
        }
    }

    private static void chartEventOffsetClearsTradeBubbles() {
        PaxInstitutionalChartEvent entry = ev("id", "pax_ai", "LONG",
                20000.0, 5_000L, "OR-H", "AI ORH", "ENTRY", 0.72);
        PaxInstitutionalChartEvent info = ev("id2", "micro_events", "NONE",
                20000.0, 5_000L, "OR-H", "WATCH", "INFO", 0.30);
        if (PaxOpeningRangeModule.chartEventOffsetTicks(entry) < 14) {
            throw new AssertionError("entry marker offset too tight");
        }
        if (PaxOpeningRangeModule.chartEventOffsetTicks(info)
                <= PaxOpeningRangeModule.chartEventOffsetTicks(entry)) {
            throw new AssertionError("lower-priority info/watch markers should sit farther from price");
        }
    }

    private static void assertAscii(String text) {
        for (int i = 0; i < text.length(); i++) {
            if (text.charAt(i) < 32 || text.charAt(i) > 126) {
                throw new AssertionError("non-ASCII chart badge text: " + text);
            }
        }
    }

    // ---- Dense-cluster collapse -------------------------------------------

    private static void collapseDenseClusterKeepsEntryOverContext() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(ev("c1", "micro_events",         "NONE", 20000.0, 5_000L, "OR-H", "WATCH", "INFO", 0.30));
        in.add(ev("c2", "micro_events",         "NONE", 20000.0, 5_000L, "OR-H", "STACK", "INFO", 0.30));
        in.add(ev("a1", "pax_ai",               "LONG", 20000.0, 5_000L, "OR-H", "AI ▲ OR-H 72", "ENTRY", 0.72));
        in.add(ev("l1", "institutional_thesis", "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.68));
        in.add(ev("a2", "pax_ai",               "LONG", 20000.0, 5_000L, "OR-H", "AI ▲ OR-H 80", "ENTRY", 0.80));
        in.add(ev("l2", "institutional_thesis", "LONG", 20000.0, 5_000L, "OR-H", "ACC-L", "ENTRY", 0.71));
        java.util.List<PaxInstitutionalChartEvent> kept =
                PaxOpeningRangeModule.collapseDenseCluster(in, 4);
        if (kept.size() != 4) {
            throw new AssertionError("expected 4 kept; got " + kept.size());
        }
        for (PaxInstitutionalChartEvent e : kept) {
            if (!"ENTRY".equals(e.severity)) {
                throw new AssertionError(
                    "dense-cluster collapse must keep ENTRY over INFO; saw " + e.severity);
            }
        }
    }

    private static void emptyHistoryReturnsEmpty() {
        java.util.List<PaxInstitutionalChartEvent> out =
                PaxOpeningRangeModule.collapseDenseCluster(
                    java.util.Collections.<PaxInstitutionalChartEvent>emptyList(), 4);
        if (!out.isEmpty()) throw new AssertionError();
        out = PaxOpeningRangeModule.collapseDenseCluster(null, 4);
        if (!out.isEmpty()) throw new AssertionError();
    }

    // ---- Alias filtering ---------------------------------------------------

    private static PaxInstitutionalChartEvent aliasEv(String alias) {
        return new PaxInstitutionalChartEvent("id-" + alias, alias, "OR-H",
                20000.0, "above", "AI_ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                "AI ▲ OR-H 72", "#FF40D9", "ENTRY", 1L, "pax_ai", 0.72);
    }

    private static void filterByAliasKeepsMatching() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(aliasEv("NQM6.CME@RITHMIC"));
        in.add(aliasEv("NQM6.CME@RITHMIC"));
        java.util.List<PaxInstitutionalChartEvent> out =
                PaxOpeningRangeModule.filterByAlias(in, "NQM6.CME@RITHMIC");
        if (out.size() != 2) throw new AssertionError("matching alias kept: " + out.size());
    }

    private static void filterByAliasDropsForeignAlias() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(aliasEv("NQM6.CME@RITHMIC"));
        in.add(aliasEv("ESM6.CME@RITHMIC"));
        java.util.List<PaxInstitutionalChartEvent> out =
                PaxOpeningRangeModule.filterByAlias(in, "NQM6.CME@RITHMIC");
        if (out.size() != 1) {
            throw new AssertionError("foreign alias must be dropped; size=" + out.size());
        }
        if (!"NQM6.CME@RITHMIC".equals(out.get(0).alias)) {
            throw new AssertionError("wrong alias retained");
        }
    }

    private static void filterByAliasKeepsEmptyAlias() {
        // Defensive: an event with empty/null alias is treated as
        // "unknown but probably valid" — kept rather than silently
        // dropped. The Python writer always sets alias, so this is a
        // hedge against malformed payloads.
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(aliasEv(""));
        in.add(aliasEv("NQM6.CME@RITHMIC"));
        java.util.List<PaxInstitutionalChartEvent> out =
                PaxOpeningRangeModule.filterByAlias(in, "NQM6.CME@RITHMIC");
        if (out.size() != 2) {
            throw new AssertionError("empty alias should pass through; size=" + out.size());
        }
    }

    private static void filterByAliasEmptyOrNullStateAliasPassesAll() {
        // If the painter's own alias is unknown, we can't decide — pass
        // all through (the merge call site is responsible for not
        // running with a blank alias in production, but the helper
        // mustn't filter to zero by accident).
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(aliasEv("NQM6.CME@RITHMIC"));
        in.add(aliasEv("ESM6.CME@RITHMIC"));
        if (PaxOpeningRangeModule.filterByAlias(in, "").size() != 2) {
            throw new AssertionError("empty state alias should pass all");
        }
        if (PaxOpeningRangeModule.filterByAlias(in, null).size() != 2) {
            throw new AssertionError("null state alias should pass all");
        }
    }

    // ---- paxAiRenderSignature (painter render-key dedup) -----------------
    //
    // F2 regression: replace-active-set means count alone can stay 1
    // while the active id swaps a1 -> a2. The painter renderKey MUST
    // include a sorted-id signature so the swap still forces a redraw.

    private static PaxInstitutionalChartEvent aiOne(String id) {
        return new PaxInstitutionalChartEvent(id, "NQM6", "OR-H", 20000.0,
                "above", "AI_ACCEPTANCE", "LONG", "PAY_FOR_TRADE",
                "AI ▲ OR-H 72", "#FF40D9", "ENTRY", 1L, "pax_ai", 0.72);
    }

    private static void paxAiRenderSignatureDistinguishesOneForOneReplacement() {
        // Active set [a1] (size 1) vs [a2] (size 1) -> different sig.
        java.util.List<PaxInstitutionalChartEvent> setA =
                java.util.Collections.singletonList(aiOne("a1"));
        java.util.List<PaxInstitutionalChartEvent> setB =
                java.util.Collections.singletonList(aiOne("a2"));
        String sigA = PaxOpeningRangeModule.paxAiRenderSignature(setA);
        String sigB = PaxOpeningRangeModule.paxAiRenderSignature(setB);
        if (sigA.equals(sigB)) {
            throw new AssertionError(
                "[a1] vs [a2] (count 1 each) MUST produce different signatures; got "
                + sigA + " vs " + sigB);
        }
    }

    private static void paxAiRenderSignatureEmptyDiffersFromNonEmpty() {
        String sigEmpty = PaxOpeningRangeModule.paxAiRenderSignature(
                java.util.Collections.<PaxInstitutionalChartEvent>emptyList());
        String sigOne = PaxOpeningRangeModule.paxAiRenderSignature(
                java.util.Collections.singletonList(aiOne("a1")));
        if (sigEmpty.equals(sigOne)) {
            throw new AssertionError(
                "empty active set must produce different signature than one event; "
                + "otherwise a TTL drop wouldn't redraw");
        }
    }

    private static void paxAiRenderSignatureOrderIndependent() {
        // The painter doesn't care about insertion order; ids alone
        // identify the active set.
        java.util.List<PaxInstitutionalChartEvent> orderA =
                java.util.Arrays.asList(aiOne("a"), aiOne("b"));
        java.util.List<PaxInstitutionalChartEvent> orderB =
                java.util.Arrays.asList(aiOne("b"), aiOne("a"));
        String sa = PaxOpeningRangeModule.paxAiRenderSignature(orderA);
        String sb = PaxOpeningRangeModule.paxAiRenderSignature(orderB);
        if (!sa.equals(sb)) {
            throw new AssertionError(
                "same ids in different order must produce the same signature; got "
                + sa + " vs " + sb);
        }
    }

    private static void paxAiRenderSignatureStableAcrossRepeatedCalls() {
        java.util.List<PaxInstitutionalChartEvent> set =
                java.util.Arrays.asList(aiOne("a"), aiOne("b"), aiOne("c"));
        String s1 = PaxOpeningRangeModule.paxAiRenderSignature(set);
        String s2 = PaxOpeningRangeModule.paxAiRenderSignature(set);
        if (!s1.equals(s2)) {
            throw new AssertionError("signature must be deterministic; got "
                    + s1 + " vs " + s2);
        }
    }

    private static void paxAiRenderSignatureNullAndEmptySame() {
        String sNull = PaxOpeningRangeModule.paxAiRenderSignature(null);
        String sEmpty = PaxOpeningRangeModule.paxAiRenderSignature(
                java.util.Collections.<PaxInstitutionalChartEvent>emptyList());
        if (!sNull.equals(sEmpty)) {
            throw new AssertionError("null and empty must hash identically");
        }
    }

    private static void belowCapPassThrough() {
        java.util.List<PaxInstitutionalChartEvent> in = new java.util.ArrayList<>();
        in.add(ev("a1", "pax_ai", "LONG", 20000.0, 5_000L, "OR-H",
                  "AI ▲ OR-H 72", "ENTRY", 0.72));
        in.add(ev("c1", "micro_events", "NONE", 20000.0, 5_000L, "OR-H",
                  "WATCH", "INFO", 0.30));
        java.util.List<PaxInstitutionalChartEvent> kept =
                PaxOpeningRangeModule.collapseDenseCluster(in, 4);
        if (kept.size() != 2) {
            throw new AssertionError("under-cap input must pass through unchanged");
        }
    }
}
