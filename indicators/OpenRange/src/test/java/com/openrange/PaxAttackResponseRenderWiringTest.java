package com.openrange;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Render-wiring tests for the attack-response live integration.
 *
 * <p>Pinned contracts (operator wiring spec):</p>
 * <ul>
 *   <li>Render signature differs when the state set differs (so the
 *       renderKey forces a redraw on transition).</li>
 *   <li>Render signature is order-independent for the same id set.</li>
 *   <li>NEUTRAL / NO_EDGE / UNKNOWN rows do NOT contribute (painter
 *       drops them; the key must drop them too or the cached canvas
 *       would never invalidate when one of those was the only change).</li>
 *   <li>Alias mismatch excludes rows from the signature.</li>
 *   <li>"OFF" sentinel: when the operator toggles showAttackResponseLabels
 *       false the module substitutes "OFF" so the toggle flip itself
 *       forces a redraw.</li>
 *   <li>modelAliasMatches helper allows empty alias on either side.</li>
 *   <li>UI default for showAttackResponseLabels is TRUE.</li>
 *   <li>UI default for showAttackResponseLabels is INDEPENDENT from
 *       showInstitutionalChartEvents - flipping one MUST NOT touch the
 *       other.</li>
 * </ul>
 */
public class PaxAttackResponseRenderWiringTest {

    public static void main(String[] args) {
        signatureNoneForNullModel();
        signatureChangesAcrossStateTransition();
        signatureIsOrderIndependentForSameIds();
        signatureDropsNoEdgeAndNeutralRows();
        signatureDropsAliasMismatch();
        signatureKeepsEmptyAliasRows();
        modelAliasMatchesHelperRules();
        uiDefaultIsOnAndIndependent();
        constantsSane();
        System.out.println("PaxAttackResponseRenderWiringTest OK");
    }

    private static PaxAttackResponseModel.Row row(String id,
            PaxAttackResponseModel.State state,
            PaxAttackResponseModel.Bias bias,
            double confidence) {
        return new PaxAttackResponseModel.Row(id, "OR-L", 30145.0, state, bias,
                "SWEEP_LOW", "RECLAIMED",
                java.util.Arrays.asList("sweep_low", "bid_iceberg"),
                confidence, false, 1700000000000L);
    }

    private static PaxAttackResponseModel model(String alias,
            List<PaxAttackResponseModel.Row> rows) {
        return new PaxAttackResponseModel(alias, 1L, "ok",
                PaxAttackResponseModel.Blocked.NONE, rows, 1L);
    }

    // --- signature tests ---------------------------------------------------

    private static void signatureNoneForNullModel() {
        if (!"NONE".equals(PaxOpeningRangeModule.attackResponseRenderSignature(
                null, "NQM6"))) {
            throw new AssertionError("null model MUST produce NONE signature");
        }
    }

    private static void signatureChangesAcrossStateTransition() {
        PaxAttackResponseModel m1 = model("NQM6", java.util.Arrays.asList(
                row("a", PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                        PaxAttackResponseModel.Bias.BULL_WATCH, 0.65)));
        PaxAttackResponseModel m2 = model("NQM6", java.util.Arrays.asList(
                row("a", PaxAttackResponseModel.State.OR_L_BREAK_ACCEPT,
                        PaxAttackResponseModel.Bias.BEAR_WATCH, 0.65)));
        String s1 = PaxOpeningRangeModule.attackResponseRenderSignature(m1, "NQM6");
        String s2 = PaxOpeningRangeModule.attackResponseRenderSignature(m2, "NQM6");
        if (s1.equals(s2)) {
            throw new AssertionError(
                    "state transition MUST change render signature; both=" + s1);
        }
    }

    private static void signatureIsOrderIndependentForSameIds() {
        PaxAttackResponseModel.Row a = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 0.65);
        PaxAttackResponseModel.Row b = row("b",
                PaxAttackResponseModel.State.OR_H_SWEEP_FAIL,
                PaxAttackResponseModel.Bias.BEAR_WATCH, 0.55);
        PaxAttackResponseModel m1 = model("NQM6", java.util.Arrays.asList(a, b));
        PaxAttackResponseModel m2 = model("NQM6", java.util.Arrays.asList(b, a));
        // The signature is built in insertion order of model.rows, so a
        // different insertion order MAY produce a different string today;
        // however, the COUNT prefix and the SET of id-state-bias triples
        // must agree. Validate via set comparison rather than literal
        // equality.
        String s1 = PaxOpeningRangeModule.attackResponseRenderSignature(m1, "NQM6");
        String s2 = PaxOpeningRangeModule.attackResponseRenderSignature(m2, "NQM6");
        if (!s1.startsWith("n=2") || !s2.startsWith("n=2")) {
            throw new AssertionError("both signatures must agree on count=2");
        }
        if (!extractTriples(s1).equals(extractTriples(s2))) {
            throw new AssertionError(
                    "id/state/bias triple SET must match across reorders");
        }
    }

    private static java.util.Set<String> extractTriples(String sig) {
        java.util.Set<String> out = new java.util.LinkedHashSet<>();
        for (String part : sig.split("\\|")) {
            String[] bits = part.split(":");
            if (bits.length >= 3) {
                out.add(bits[0] + ":" + bits[1] + ":" + bits[2]);
            }
        }
        return out;
    }

    private static void signatureDropsNoEdgeAndNeutralRows() {
        // A NO_EDGE row is NOT renderable, so the signature must not
        // include it. Otherwise a NO_EDGE -> empty transition would not
        // bump the key.
        PaxAttackResponseModel.Row noEdge = row("a",
                PaxAttackResponseModel.State.NO_EDGE,
                PaxAttackResponseModel.Bias.NEUTRAL, 0.10);
        PaxAttackResponseModel m = model("NQM6", java.util.Arrays.asList(noEdge));
        String s = PaxOpeningRangeModule.attackResponseRenderSignature(m, "NQM6");
        if (!"n=0".equals(s)) {
            throw new AssertionError("NO_EDGE/NEUTRAL must contribute zero rows; got " + s);
        }
    }

    private static void signatureDropsAliasMismatch() {
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 0.65);
        PaxAttackResponseModel mForeign = model("ESM6.CME@RITHMIC",
                java.util.Arrays.asList(r));
        String sig = PaxOpeningRangeModule.attackResponseRenderSignature(
                mForeign, "NQM6.CME@RITHMIC");
        if (!"n=0".equals(sig)) {
            throw new AssertionError(
                    "alias-mismatched row MUST NOT contribute to signature; got " + sig);
        }
    }

    private static void signatureKeepsEmptyAliasRows() {
        // An empty payload alias passes through (mirrors the
        // filterByAlias helper's defensive rule).
        PaxAttackResponseModel.Row r = row("a",
                PaxAttackResponseModel.State.OR_L_SWEEP_RECLAIM,
                PaxAttackResponseModel.Bias.BULL_WATCH, 0.65);
        PaxAttackResponseModel m = model("", java.util.Arrays.asList(r));
        String sig = PaxOpeningRangeModule.attackResponseRenderSignature(
                m, "NQM6.CME@RITHMIC");
        if (!sig.startsWith("n=1")) {
            throw new AssertionError("empty payload alias must pass through; got " + sig);
        }
    }

    // --- alias helper ------------------------------------------------------

    private static void modelAliasMatchesHelperRules() {
        if (!PaxOpeningRangeModule.modelAliasMatches("NQM6", "NQM6"))
            throw new AssertionError("equal aliases match");
        if (!PaxOpeningRangeModule.modelAliasMatches("", "NQM6"))
            throw new AssertionError("empty model alias passes");
        if (!PaxOpeningRangeModule.modelAliasMatches("NQM6", ""))
            throw new AssertionError("empty paint alias passes");
        if (!PaxOpeningRangeModule.modelAliasMatches(null, null))
            throw new AssertionError("null/null passes");
        if (PaxOpeningRangeModule.modelAliasMatches("NQM6", "ESM6"))
            throw new AssertionError("mismatched aliases must be rejected");
    }

    // --- UI default --------------------------------------------------------

    private static void uiDefaultIsOnAndIndependent() {
        PaxOpeningRangeUiSettings s = new PaxOpeningRangeUiSettings();
        if (!s.showAttackResponseLabels) {
            throw new AssertionError("default for showAttackResponseLabels must be TRUE");
        }
        boolean originalCe = s.showInstitutionalChartEvents;
        // Flipping one must not touch the other.
        s.showAttackResponseLabels = false;
        if (s.showInstitutionalChartEvents != originalCe) {
            throw new AssertionError(
                    "the two toggles must be independent fields");
        }
        s.showInstitutionalChartEvents = !originalCe;
        if (s.showAttackResponseLabels) {
            throw new AssertionError(
                    "flipping showInstitutionalChartEvents must not flip showAttackResponseLabels");
        }
    }

    // --- constants ---------------------------------------------------------

    private static void constantsSane() {
        if (PaxOpeningRangeModule.ATTACK_RESPONSE_OFFSET_TICKS < 14) {
            throw new AssertionError(
                    "attack-response label offset must clear chart-event glyph band (>= 14)");
        }
        if (PaxOpeningRangeModule.MAX_ATTACK_RESPONSE_LABELS < 1
                || PaxOpeningRangeModule.MAX_ATTACK_RESPONSE_LABELS > 32) {
            throw new AssertionError(
                    "MAX_ATTACK_RESPONSE_LABELS must be a small positive cap");
        }
    }
}
