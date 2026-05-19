package com.openrange;

public class PaxHeatwaveSnapshotParserTest {

    public static void main(String[] args) {
        richFixtureBuildsElevenRows();
        richFixturePicksPaxDecisionFirst();
        richFixtureFlowGroupedWeightedAverage();
        richFixtureOrPicksNearestByAbsDistance();
        richFixtureOrFormatsDistanceInPoints();
        richFixtureHeaderScoreFormatted();
        richFixtureTapeHintLowercased();
        sparseFixtureProducesNeutralRows();
        sparseFixtureFallsBackToTradeDecision();
        zeroReliabilityProducesNeutralRow();
        malformedFixtureThrowsParseException();
        realisticFixtureReadsVwapComponents();
        realisticFixtureReadsVpComponents();
        realisticFixtureFlowUsesFlowRegime();
        realisticFixtureCvdHintBuyOrSellNotRising();
        realisticFixtureFallsBackToDirectVwapKeysWhenComponentsAbsent();
    }

    private static final String RICH = ""
            + "{"
            + "\"ts\":\"2026-05-19T11:42:17\","
            + "\"conviction\":{"
            + "\"score\":0.42,"
            + "\"trend\":\"TREND_UP\","
            + "\"sourceScores\":{"
            + "\"flow_ofi\":0.44,\"flow_cvd\":0.27,\"bias_score\":0.30,\"regime\":0.32,"
            + "\"vwap_dislocation\":0.18,\"vwap_slope\":0.22,\"vwap_or_gate\":0.10,"
            + "\"anchored_vwap_opening_drive\":0.20,"
            + "\"volume_profile\":0.25,\"ib_context\":0.15,"
            + "\"pull_stack\":0.55,\"tape_large_lot\":0.41,"
            + "\"orderbook\":-0.10,\"lt_liquidity\":-0.05,"
            + "\"micro_events\":0.30,\"level_reaction\":-0.10"
            + "},"
            + "\"effectiveWeights\":{"
            + "\"flow_ofi\":0.14,\"flow_cvd\":0.12,\"bias_score\":0.08,\"regime\":0.08,"
            + "\"vwap_dislocation\":0.08,\"vwap_slope\":0.08,\"vwap_or_gate\":0.04,"
            + "\"anchored_vwap_opening_drive\":0.08,"
            + "\"volume_profile\":0.08,\"ib_context\":0.04,"
            + "\"pull_stack\":0.10,\"tape_large_lot\":0.06,"
            + "\"orderbook\":0.05,\"lt_liquidity\":0.04,"
            + "\"micro_events\":0.04,\"level_reaction\":0.08"
            + "},"
            + "\"sourceReliability\":{"
            + "\"flow_ofi\":0.82,\"flow_cvd\":0.80,\"bias_score\":0.70,\"regime\":0.70,"
            + "\"vwap_dislocation\":0.80,\"vwap_slope\":0.80,\"vwap_or_gate\":0.50,"
            + "\"anchored_vwap_opening_drive\":0.75,"
            + "\"volume_profile\":0.75,\"ib_context\":0.40,"
            + "\"pull_stack\":0.85,\"tape_large_lot\":0.70,"
            + "\"orderbook\":0.60,\"lt_liquidity\":0.50,"
            + "\"micro_events\":0.55,\"level_reaction\":0.65"
            + "}"
            + "},"
            + "\"pax\":{\"decision\":\"ENTER_LONG\",\"reason\":\"OR follow\"},"
            + "\"decision\":{\"decision\":\"WAIT\"},"
            + "\"or_levels\":{\"levels\":["
            + "{\"label\":\"ORH\",\"price\":20125.00,\"side\":\"UPPER\",\"distance\":12.50,"
            + "\"decision\":\"FOLLOW_LONG\",\"confidence\":0.68},"
            + "{\"label\":\"EXT_+1\",\"price\":20150.00,\"side\":\"UPPER\",\"distance\":37.50,"
            + "\"decision\":\"WAIT\",\"confidence\":0.30}"
            + "]},"
            + "\"tape_flow\":{\"deltaLabel\":\"BUY BLOCK\",\"deltaScore\":0.55},"
            + "\"flow\":{\"regime\":\"TREND_UP\",\"biasTrajectory\":\"RISING_STRONG\",\"biasScore\":0.35},"
            + "\"vwap_bias\":{\"label\":\"BULLISH\",\"score\":0.45,"
            + "\"components\":{\"sigma_z\":1.8,\"regime\":\"MEAN_REVERT\"}},"
            + "\"vp_bias\":{\"label\":\"BULLISH\",\"score\":0.22,"
            + "\"components\":{\"va_state\":\"HVN NEAR\",\"hvn_count\":2,\"lvn_count\":1}}"
            + "}";

    private static final String REALISTIC = ""
            + "{"
            + "\"ts\":\"2026-05-19T11:42:17\","
            + "\"conviction\":{"
            + "\"score\":0.20,"
            + "\"trend\":\"TREND_UP\","
            + "\"sourceScores\":{"
            + "\"flow_ofi\":0.30,\"flow_cvd\":-0.40,\"bias_score\":0.20,\"regime\":0.10,"
            + "\"vwap_dislocation\":0.10,\"vwap_slope\":0.10,\"vwap_or_gate\":0.05,"
            + "\"anchored_vwap_opening_drive\":0.10,"
            + "\"volume_profile\":0.05,\"ib_context\":0.05,"
            + "\"pull_stack\":0.20,\"tape_large_lot\":0.10,"
            + "\"orderbook\":0.00,\"lt_liquidity\":0.00,"
            + "\"micro_events\":0.10,\"level_reaction\":0.00"
            + "},"
            + "\"effectiveWeights\":{"
            + "\"flow_ofi\":0.14,\"flow_cvd\":0.12,\"bias_score\":0.08,\"regime\":0.08,"
            + "\"vwap_dislocation\":0.08,\"vwap_slope\":0.08,\"vwap_or_gate\":0.04,"
            + "\"anchored_vwap_opening_drive\":0.08,"
            + "\"volume_profile\":0.08,\"ib_context\":0.04,"
            + "\"pull_stack\":0.10,\"tape_large_lot\":0.06,"
            + "\"orderbook\":0.05,\"lt_liquidity\":0.04,"
            + "\"micro_events\":0.04,\"level_reaction\":0.08"
            + "},"
            + "\"sourceReliability\":{"
            + "\"flow_ofi\":0.8,\"flow_cvd\":0.7,\"bias_score\":0.6,\"regime\":0.6,"
            + "\"vwap_dislocation\":0.7,\"vwap_slope\":0.7,\"vwap_or_gate\":0.5,"
            + "\"anchored_vwap_opening_drive\":0.7,"
            + "\"volume_profile\":0.6,\"ib_context\":0.4,"
            + "\"pull_stack\":0.8,\"tape_large_lot\":0.6,"
            + "\"orderbook\":0.5,\"lt_liquidity\":0.4,"
            + "\"micro_events\":0.5,\"level_reaction\":0.6"
            + "}"
            + "},"
            + "\"decision\":{\"decision\":\"WAIT\"},"
            + "\"or_levels\":{\"levels\":["
            + "{\"label\":\"ORL\",\"price\":20100.00,\"distance\":-4.00,"
            + "\"decision\":\"FADE_LONG\",\"confidence\":0.55}"
            + "]},"
            + "\"flow\":{\"regime\":\"DISTRIBUTION\",\"biasTrajectory\":\"FALLING\",\"biasScore\":-0.12},"
            + "\"tape_flow\":{\"deltaLabel\":\"BUY MILD\"},"
            + "\"vwap_bias\":{\"label\":\"BEARISH\",\"score\":-0.30,"
            + "\"components\":{\"sigma_z\":-2.4,\"regime\":\"STRETCHED_REVERT\"}},"
            + "\"vp_bias\":{\"label\":\"NEUTRAL\",\"score\":0.05,"
            + "\"components\":{\"va_state\":\"INSIDE_VA\",\"hvn_count\":3,\"lvn_count\":2}}"
            + "}";

    private static final String DIRECT_VWAP_KEYS = ""
            + "{"
            + "\"conviction\":{\"score\":0.0,"
            + "\"sourceScores\":{\"vwap_slope\":0.10},"
            + "\"effectiveWeights\":{\"vwap_slope\":0.08},"
            + "\"sourceReliability\":{\"vwap_slope\":0.5}},"
            + "\"decision\":{\"decision\":\"WAIT\"},"
            + "\"vwap_bias\":{\"sigma_z\":1.2,\"regime\":\"MEAN_REVERT\"}"
            + "}";

    private static final String SPARSE = ""
            + "{"
            + "\"ts\":\"2026-05-19T11:42:17\","
            + "\"conviction\":{\"score\":0.0,\"sourceScores\":{},\"effectiveWeights\":{},\"sourceReliability\":{}},"
            + "\"decision\":{\"decision\":\"WAIT\"}"
            + "}";

    private static final String ZERO_RELIAB = ""
            + "{"
            + "\"conviction\":{\"score\":0.0,"
            + "\"sourceScores\":{\"flow_ofi\":0.99},"
            + "\"effectiveWeights\":{\"flow_ofi\":0.20},"
            + "\"sourceReliability\":{\"flow_ofi\":0.0}"
            + "},"
            + "\"decision\":{\"decision\":\"WAIT\"}"
            + "}";

    private static final String MALFORMED = "{not valid json,";

    private static void richFixtureBuildsElevenRows() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        assertEquals(11, m.rows.length, "row count");
        String[] expected = {"OR ", "FLOW", "OFI", "CVD", "ABSORB", "VWAP", "VP", "PS", "TAPE", "BOOK", "MICRO"};
        for (int i = 0; i < 11; i++) {
            if (i == 0) {
                if (!m.rows[i].label.startsWith("OR")) {
                    throw new AssertionError("row 0 should start with OR; got " + m.rows[i].label);
                }
            } else {
                assertEquals(expected[i], m.rows[i].label, "row " + i + " label");
            }
        }
    }

    private static void richFixturePicksPaxDecisionFirst() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        assertEquals("ENTER_LONG", m.verdict, "verdict");
        assertEquals(PaxHeatwaveModel.Tone.BULL, m.verdictTone, "verdict tone");
    }

    private static void richFixtureFlowGroupedWeightedAverage() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        PaxHeatwaveModel.Row flow = m.rows[1];
        // (regime*0.08 + bias_score*0.08) / 0.16 = (0.32 + 0.30)/2 = 0.31
        assertEquals("+0.31", flow.scoreText, "flow score");
        assertEquals(PaxHeatwaveModel.Tone.BULL, flow.tone, "flow tone");
        assertEquals("TREND_UP", flow.hint, "flow hint");

        PaxHeatwaveModel.Row vwap = m.rows[5];
        // (0.18*0.08 + 0.22*0.08 + 0.10*0.04 + 0.20*0.08) / 0.28
        // = (0.0144 + 0.0176 + 0.004 + 0.016) / 0.28 = 0.052 / 0.28 = 0.18571
        if (!vwap.scoreText.equals("+0.19") && !vwap.scoreText.equals("+0.18")) {
            throw new AssertionError("vwap score should be ~+0.19, got " + vwap.scoreText);
        }
    }

    private static void richFixtureOrPicksNearestByAbsDistance() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        PaxHeatwaveModel.Row or = m.rows[0];
        // nearest is ORH (distance 12.50) vs EXT (37.50)
        if (!or.label.contains("H")) {
            throw new AssertionError("OR row should reference high (ORH), got " + or.label);
        }
        assertEquals(PaxHeatwaveModel.Tone.BULL, or.tone, "or tone for FOLLOW_LONG");
    }

    private static void richFixtureOrFormatsDistanceInPoints() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        PaxHeatwaveModel.Row or = m.rows[0];
        if (!or.hint.contains("+12.50p")) {
            throw new AssertionError("hint should contain '+12.50p', got: " + or.hint);
        }
        if (!or.hint.contains("20125.00")) {
            throw new AssertionError("hint should contain price '20125.00', got: " + or.hint);
        }
        if (!or.hint.contains("68%")) {
            throw new AssertionError("hint should contain '68%', got: " + or.hint);
        }
    }

    private static void richFixtureHeaderScoreFormatted() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        assertEquals("+42", m.scoreText, "header score");
    }

    private static void richFixtureTapeHintLowercased() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(RICH, 1000L);
        PaxHeatwaveModel.Row tape = m.rows[8];
        assertEquals("buy block", tape.hint, "tape hint");
    }

    private static void sparseFixtureProducesNeutralRows() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(SPARSE, 1000L);
        assertEquals(11, m.rows.length, "row count");
        for (int i = 1; i < 11; i++) {
            assertEquals("--", m.rows[i].scoreText, "row " + i + " score should be -- when source missing");
            assertEquals(PaxHeatwaveModel.Tone.NEUTRAL, m.rows[i].tone, "row " + i + " tone");
        }
        assertEquals("--", m.rows[0].scoreText, "OR row should be -- with no levels");
    }

    private static void sparseFixtureFallsBackToTradeDecision() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(SPARSE, 1000L);
        assertEquals("WAIT", m.verdict, "fallback verdict");
    }

    private static void zeroReliabilityProducesNeutralRow() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(ZERO_RELIAB, 1000L);
        PaxHeatwaveModel.Row ofi = m.rows[2];
        assertEquals("--", ofi.scoreText, "OFI row with zero reliability should be --");
        assertEquals(PaxHeatwaveModel.Tone.NEUTRAL, ofi.tone, "OFI tone");
    }

    private static void malformedFixtureThrowsParseException() {
        try {
            PaxHeatwaveSnapshotParser.parse(MALFORMED, 1000L);
            throw new AssertionError("expected ParseException for malformed JSON");
        } catch (PaxHeatwaveSnapshotParser.ParseException ex) {
            // expected
        }
    }

    private static void realisticFixtureReadsVwapComponents() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(REALISTIC, 1000L);
        PaxHeatwaveModel.Row vwap = m.rows[5];
        if (!vwap.hint.contains("-2.4s")) {
            throw new AssertionError("vwap hint should contain '-2.4s' from components.sigma_z; got: " + vwap.hint);
        }
        if (!vwap.hint.contains("STRETCHED_REVERT")) {
            throw new AssertionError("vwap hint should contain components.regime; got: " + vwap.hint);
        }
    }

    private static void realisticFixtureReadsVpComponents() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(REALISTIC, 1000L);
        PaxHeatwaveModel.Row vp = m.rows[6];
        assertEquals("INSIDE_VA", vp.hint, "vp hint should come from components.va_state");
    }

    private static void realisticFixtureFlowUsesFlowRegime() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(REALISTIC, 1000L);
        PaxHeatwaveModel.Row flow = m.rows[1];
        assertEquals("DISTRIBUTION", flow.hint, "flow hint should prefer snap.flow.regime");
    }

    private static void realisticFixtureCvdHintBuyOrSellNotRising() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(REALISTIC, 1000L);
        PaxHeatwaveModel.Row cvd = m.rows[3];
        if (cvd.hint.contains("rising") || cvd.hint.contains("falling")) {
            throw new AssertionError("cvd hint must not say rising/falling; got: " + cvd.hint);
        }
        assertEquals("sell", cvd.hint, "negative cvd should say 'sell'");
    }

    private static void realisticFixtureFallsBackToDirectVwapKeysWhenComponentsAbsent() {
        PaxHeatwaveModel m = PaxHeatwaveSnapshotParser.parse(DIRECT_VWAP_KEYS, 1000L);
        PaxHeatwaveModel.Row vwap = m.rows[5];
        if (!vwap.hint.contains("+1.2s")) {
            throw new AssertionError("vwap hint should fall back to top-level sigma_z; got: " + vwap.hint);
        }
        if (!vwap.hint.contains("MEAN_REVERT")) {
            throw new AssertionError("vwap hint should fall back to top-level regime; got: " + vwap.hint);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (expected == null ? actual != null : !expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertEquals(int expected, int actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
