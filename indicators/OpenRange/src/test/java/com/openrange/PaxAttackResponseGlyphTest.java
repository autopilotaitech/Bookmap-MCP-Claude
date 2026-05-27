package com.openrange;

import java.awt.Color;

/**
 * Stage 2 attack-response glyph vocabulary + palette guard.
 *
 * <p>Pins the bid/ask side inference, the closed glyph table
 * (SL/SH/BI/AI/BA/AA/BS/AS/BP/AP/AL/AC-S/RL/RS/W/T/SP/X), and the
 * three-color palette (BULL/BEAR/STAND_DOWN/SEVERITY_OUTLINE) returned
 * by {@link PaxOpeningRangeModule#attackResponseAccentColor}. Defends
 * the rule "sweep is evidence, not a direction" - sweep must NEVER
 * paint BULL or BEAR; it must paint SEVERITY_OUTLINE (amber).</p>
 *
 * <p>Plan: reports/pax-ai-attack-response-plan-2026-05-27.md section 6.</p>
 */
public class PaxAttackResponseGlyphTest {

    public static void main(String[] args) {
        sweepHighGlyph();
        sweepLowGlyph();
        bidIcebergFromMarkerSuffix();
        askIcebergFromMarkerSuffix();
        bidAbsorbFromMarkerSuffix();
        askAbsorbFromMarkerSuffix();
        bidStackingFromSideBelow();
        askStackingFromSideAbove();
        bidPullingFromSideBelow();
        askPullingFromSideAbove();
        acceptanceLong();
        acceptanceShortAvoidsCollisionWithAskStack();
        rejectionLong();
        rejectionShort();
        watchLevelIsContext();
        touchedLevelIsContext();
        spoofRiskIsContext();
        scratchIsContext();
        unknownEventTypeFallsToLegacyScan();
        accentSweepIsAmberNotDirectional();
        accentBidEvidenceIsBull();
        accentAskEvidenceIsBear();
        accentAcceptanceLongIsBull();
        accentAcceptanceShortIsBear();
        accentContextIsGray();
        accentUnknownEventTypeIsNullFallback();
        markerSuffixOverridesSideField();
        renderTextWiresNewCode();
        showInstitutionalChartEventsToggleStaysGate();
        System.out.println("PaxAttackResponseGlyphTest OK");
    }

    // ----- glyph codes --------------------------------------------------------

    private static void sweepHighGlyph() {
        PaxInstitutionalChartEvent ev = liquiditySweep("above");
        assertCode(ev, "SH", "LIQUIDITY_SWEEP side=above");
    }

    private static void sweepLowGlyph() {
        PaxInstitutionalChartEvent ev = liquiditySweep("below");
        assertCode(ev, "SL", "LIQUIDITY_SWEEP side=below");
    }

    private static void bidIcebergFromMarkerSuffix() {
        PaxInstitutionalChartEvent ev = event("ICEBERG_DEFENSE", "above",
                "NONE", "ICE-B");
        assertCode(ev, "BI",
                "ICE-B marker_text MUST override side=above");
    }

    private static void askIcebergFromMarkerSuffix() {
        PaxInstitutionalChartEvent ev = event("ICEBERG_DEFENSE", "below",
                "NONE", "ICE-A");
        assertCode(ev, "AI",
                "ICE-A marker_text MUST override side=below");
    }

    private static void bidAbsorbFromMarkerSuffix() {
        PaxInstitutionalChartEvent ev = event("ABSORPTION", "above",
                "NONE", "ABS-B");
        assertCode(ev, "BA",
                "ABS-B marker_text MUST override side=above");
    }

    private static void askAbsorbFromMarkerSuffix() {
        PaxInstitutionalChartEvent ev = event("ABSORPTION", "below",
                "NONE", "ABS-A");
        assertCode(ev, "AA",
                "ABS-A marker_text MUST override side=below");
    }

    private static void bidStackingFromSideBelow() {
        PaxInstitutionalChartEvent ev = event("STACKING", "below",
                "NONE", "STACK");
        assertCode(ev, "BS", "STACKING side=below");
    }

    private static void askStackingFromSideAbove() {
        PaxInstitutionalChartEvent ev = event("STACKING", "above",
                "NONE", "STACK");
        assertCode(ev, "AS", "STACKING side=above");
    }

    private static void bidPullingFromSideBelow() {
        PaxInstitutionalChartEvent ev = event("PULLING", "below",
                "NONE", "PULL");
        assertCode(ev, "BP", "PULLING side=below");
    }

    private static void askPullingFromSideAbove() {
        PaxInstitutionalChartEvent ev = event("PULLING", "above",
                "NONE", "PULL");
        assertCode(ev, "AP", "PULLING side=above");
    }

    private static void acceptanceLong() {
        PaxInstitutionalChartEvent ev = event("ACCEPTANCE", "above",
                "LONG", "ACC-L");
        assertCode(ev, "AL", "ACCEPTANCE direction=LONG");
    }

    private static void acceptanceShortAvoidsCollisionWithAskStack() {
        // Plain "AS" already means ASK_STACK. ACCEPTANCE short therefore
        // uses "AC-S" so the two are visually distinct.
        PaxInstitutionalChartEvent ev = event("ACCEPTANCE", "below",
                "SHORT", "ACC-S");
        String code = PaxOpeningRangeModule.compactChartEventCode(ev);
        if (!"AC-S".equals(code)) {
            throw new AssertionError(
                    "ACCEPTANCE SHORT must be AC-S to avoid AS collision; got " + code);
        }
        PaxInstitutionalChartEvent stack = event("STACKING", "above",
                "NONE", "STACK");
        String stackCode = PaxOpeningRangeModule.compactChartEventCode(stack);
        if (stackCode.equals(code)) {
            throw new AssertionError(
                    "ASK_STACK (" + stackCode + ") and ACCEPTANCE_SHORT (" + code
                            + ") must NOT collide");
        }
    }

    private static void rejectionLong() {
        PaxInstitutionalChartEvent ev = event("REJECTION", "below",
                "LONG", "REJ-L");
        assertCode(ev, "RL", "REJECTION direction=LONG");
    }

    private static void rejectionShort() {
        PaxInstitutionalChartEvent ev = event("REJECTION", "above",
                "SHORT", "REJ-S");
        assertCode(ev, "RS", "REJECTION direction=SHORT");
    }

    private static void watchLevelIsContext() {
        PaxInstitutionalChartEvent ev = event("WATCH_LEVEL", "above",
                "NONE", "WATCH");
        assertCode(ev, "W", "WATCH_LEVEL");
    }

    private static void touchedLevelIsContext() {
        PaxInstitutionalChartEvent ev = event("TOUCHED_LEVEL", "below",
                "NONE", "TCH");
        assertCode(ev, "T", "TOUCHED_LEVEL");
    }

    private static void spoofRiskIsContext() {
        PaxInstitutionalChartEvent ev = event("SPOOF_RISK", "above",
                "NONE", "SPD");
        assertCode(ev, "SP", "SPOOF_RISK");
    }

    private static void scratchIsContext() {
        PaxInstitutionalChartEvent ev = event("SCRATCH", "above",
                "NONE", "SCR");
        assertCode(ev, "X", "SCRATCH");
    }

    private static void unknownEventTypeFallsToLegacyScan() {
        // Legacy tests use event_type "AI_ACCEPTANCE" - this MUST not
        // get the new vocabulary applied, otherwise the existing
        // PaxAiChartEventsRenderTest exact-string contracts break.
        PaxInstitutionalChartEvent ev = event("AI_ACCEPTANCE", "above",
                "LONG", "ACC-L");
        String code = PaxOpeningRangeModule.compactChartEventCode(ev);
        if (!"ACC".equals(code)) {
            throw new AssertionError(
                    "unknown event_type must fall to legacy marker scan ('ACC'); got " + code);
        }
    }

    // ----- accent palette -----------------------------------------------------

    private static void accentSweepIsAmberNotDirectional() {
        PaxInstitutionalChartEvent sweepHi = liquiditySweep("above");
        PaxInstitutionalChartEvent sweepLo = liquiditySweep("below");
        Color hi = PaxOpeningRangeModule.attackResponseAccentColor(sweepHi);
        Color lo = PaxOpeningRangeModule.attackResponseAccentColor(sweepLo);
        if (!sameRgb(hi, PaxChartPalette.SEVERITY_OUTLINE)
                || !sameRgb(lo, PaxChartPalette.SEVERITY_OUTLINE)) {
            throw new AssertionError(
                    "Sweep MUST paint SEVERITY_OUTLINE (amber). Sweep is evidence, NOT a direction. "
                            + "Got high=" + hi + " low=" + lo);
        }
        if (sameRgb(hi, PaxChartPalette.BULL) || sameRgb(lo, PaxChartPalette.BEAR)) {
            throw new AssertionError("Sweep accidentally tinted as direction");
        }
    }

    private static void accentBidEvidenceIsBull() {
        check("ICE-B iceberg accent is BULL",
                PaxChartPalette.BULL,
                event("ICEBERG_DEFENSE", "above", "NONE", "ICE-B"));
        check("ABS-B absorption accent is BULL",
                PaxChartPalette.BULL,
                event("ABSORPTION", "above", "NONE", "ABS-B"));
        check("BID_STACK accent is BULL",
                PaxChartPalette.BULL,
                event("STACKING", "below", "NONE", "STACK"));
    }

    private static void accentAskEvidenceIsBear() {
        check("ICE-A iceberg accent is BEAR",
                PaxChartPalette.BEAR,
                event("ICEBERG_DEFENSE", "below", "NONE", "ICE-A"));
        check("ABS-A absorption accent is BEAR",
                PaxChartPalette.BEAR,
                event("ABSORPTION", "below", "NONE", "ABS-A"));
        check("ASK_STACK accent is BEAR",
                PaxChartPalette.BEAR,
                event("STACKING", "above", "NONE", "STACK"));
    }

    private static void accentAcceptanceLongIsBull() {
        check("ACCEPTANCE LONG accent",
                PaxChartPalette.BULL,
                event("ACCEPTANCE", "above", "LONG", "ACC-L"));
        check("REJECTION LONG accent",
                PaxChartPalette.BULL,
                event("REJECTION", "below", "LONG", "REJ-L"));
    }

    private static void accentAcceptanceShortIsBear() {
        check("ACCEPTANCE SHORT accent",
                PaxChartPalette.BEAR,
                event("ACCEPTANCE", "below", "SHORT", "ACC-S"));
        check("REJECTION SHORT accent",
                PaxChartPalette.BEAR,
                event("REJECTION", "above", "SHORT", "REJ-S"));
    }

    private static void accentContextIsGray() {
        check("PULLING accent is gray",
                PaxChartPalette.STAND_DOWN,
                event("PULLING", "above", "NONE", "PULL"));
        check("WATCH_LEVEL accent is gray",
                PaxChartPalette.STAND_DOWN,
                event("WATCH_LEVEL", "above", "NONE", "WATCH"));
        check("TOUCHED_LEVEL accent is gray",
                PaxChartPalette.STAND_DOWN,
                event("TOUCHED_LEVEL", "below", "NONE", "TCH"));
        check("SPOOF_RISK accent is gray",
                PaxChartPalette.STAND_DOWN,
                event("SPOOF_RISK", "above", "NONE", "SPD"));
        check("SCRATCH accent is gray",
                PaxChartPalette.STAND_DOWN,
                event("SCRATCH", "above", "NONE", "SCR"));
    }

    private static void accentUnknownEventTypeIsNullFallback() {
        // Legacy event_type -> null so painter falls back to colorFromHint.
        PaxInstitutionalChartEvent legacy = event("AI_ACCEPTANCE", "above",
                "LONG", "ACC-L");
        Color accent = PaxOpeningRangeModule.attackResponseAccentColor(legacy);
        if (accent != null) {
            throw new AssertionError("unknown event_type accent must be null; got " + accent);
        }
    }

    // ----- side inference -----------------------------------------------------

    private static void markerSuffixOverridesSideField() {
        // The Python emitter stamps "ICE-A" on an event whose side is the
        // OR level itself (could be "above" or "below"). The suffix is
        // the authoritative bid/ask source.
        PaxInstitutionalChartEvent contrarian = event("ICEBERG_DEFENSE",
                "below", "NONE", "ICE-A");
        if (!PaxOpeningRangeModule.isAboveContext(contrarian)) {
            throw new AssertionError("ICE-A suffix should pin upper context regardless of side");
        }
        assertCode(contrarian, "AI", "ICE-A on side=below");
    }

    // ----- end-to-end -------------------------------------------------------

    private static void renderTextWiresNewCode() {
        // Full render text includes the new short code. Sanity-check that
        // chartEventRenderText concatenates source + dir + new_code + conf.
        PaxInstitutionalChartEvent ev = event("ACCEPTANCE", "above",
                "LONG", "ACC-L");
        String text = PaxOpeningRangeModule.chartEventRenderText(ev);
        if (!text.contains("AL")) {
            throw new AssertionError("chartEventRenderText must surface 'AL' for ACCEPTANCE LONG; got " + text);
        }
        // Bearish counterpart.
        PaxInstitutionalChartEvent bear = event("REJECTION", "above",
                "SHORT", "REJ-S");
        String bearText = PaxOpeningRangeModule.chartEventRenderText(bear);
        if (!bearText.contains("RS")) {
            throw new AssertionError("chartEventRenderText must surface 'RS' for REJECTION SHORT; got " + bearText);
        }
    }

    private static void showInstitutionalChartEventsToggleStaysGate() {
        // The Stage 2 visual change does NOT introduce a new UI toggle.
        // showInstitutionalChartEvents continues to be the sole gate for
        // the raw-evidence layer. Verifying via trendFetcherShouldRun
        // helper - the fetcher only runs when chart events are enabled.
        if (PaxOpeningRangeModule.trendFetcherShouldRun(false, true) != true) {
            throw new AssertionError("chart-events toggle should keep fetcher running");
        }
        if (PaxOpeningRangeModule.trendFetcherShouldRun(false, false) != false) {
            throw new AssertionError("disabling chart-events MUST stop fetcher");
        }
    }

    // ----- helpers ------------------------------------------------------------

    private static PaxInstitutionalChartEvent liquiditySweep(String side) {
        return event("LIQUIDITY_SWEEP", side, "NONE",
                "above".equals(side) ? "SWP" : "SWP");
    }

    private static PaxInstitutionalChartEvent event(String eventType, String side,
            String direction, String markerText) {
        return new PaxInstitutionalChartEvent(
                "id-" + eventType + "-" + side + "-" + direction,
                "NQM6.CME@RITHMIC",
                "OR-H",
                20000.0,
                side,
                eventType,
                direction,
                "PAY_FOR_TRADE",
                markerText,
                "#9C9C9C",
                "WARNING",
                1_000L,
                "institutional_thesis",
                0.50);
    }

    private static void assertCode(PaxInstitutionalChartEvent evt,
            String expected, String label) {
        String got = PaxOpeningRangeModule.compactChartEventCode(evt);
        if (!expected.equals(got)) {
            throw new AssertionError(label + ": expected " + expected + ", got " + got);
        }
    }

    private static void check(String label, Color expected,
            PaxInstitutionalChartEvent evt) {
        Color got = PaxOpeningRangeModule.attackResponseAccentColor(evt);
        if (!sameRgb(got, expected)) {
            throw new AssertionError(label + ": expected " + expected + ", got " + got);
        }
    }

    private static boolean sameRgb(Color a, Color b) {
        if (a == null || b == null) return false;
        return a.getRed() == b.getRed()
                && a.getGreen() == b.getGreen()
                && a.getBlue() == b.getBlue();
    }
}
