package com.openrange;

import java.util.List;

public class PaxLevelEdgeSnapshotParserTest {

    public static void main(String[] args) {
        parsesActionableLongRow();
        parsesActionableShortRow();
        skipsNonActionableRows();
        emptyLevelsListYieldsEmptyModel();
        missingActionableDefaultsToFalse();
        missingLevelsArrayYieldsEmptyModel();
        rejectsEmptyJson();
        rejectsMalformedJson();
        unknownDirectionFallsBackToWait();
        unknownColorHintFallsBackToNeutral();
        // Slice 2 additions:
        parsesSlice2ReasonFields();
        missingRawDirectionFallsBackToWait();
        missingSetupDefaultsToUnknownSetup();
        missingTopDriversYieldsEmptyList();
        missingBlockedReasonIsNull();
        missingDistanceAbsIsNull();
        missingLevelRelevantDefaultsFalse();
        malformedTopDriversYieldsEmptyList();
        unknownRawDirectionFallsBackToWait();
        System.out.println("PaxLevelEdgeSnapshotParserTest OK");
    }

    private static final String LONG_FIXTURE = ""
            + "{"
            + "\"alias\":\"NQM6.CME@RITHMIC\","
            + "\"asOfMs\":1748277123456,"
            + "\"ageMs\":412,"
            + "\"stale\":false,"
            + "\"mid\":20114.25,"
            + "\"anchorMode\":\"LIVE\","
            + "\"blocked\":{\"health_ok\":true,\"news\":false,\"session\":false,"
            + "\"stale\":false,\"anchor\":false},"
            + "\"levels\":[{"
            + "\"label\":\"OR-H\",\"price\":20112.00,\"side\":\"high\","
            + "\"distance\":2.25,\"proximity\":true,"
            + "\"direction\":\"LONG\",\"composite_dir\":\"FOLLOW_LONG\","
            + "\"confidence\":0.62,\"score_R\":1.44,\"stop_price\":20087.75,"
            + "\"size_tier\":\"HALF\",\"actionable\":true,\"color_hint\":\"positive\","
            + "\"reasons\":[\"composite_dir=FOLLOW_LONG\"]"
            + "}]"
            + "}";

    private static void parsesActionableLongRow() {
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(LONG_FIXTURE, 9_999L);
        if (!"NQM6.CME@RITHMIC".equals(m.alias)) throw new AssertionError("alias: " + m.alias);
        if (m.asOfMs != 1748277123456L) throw new AssertionError("asOfMs: " + m.asOfMs);
        if (m.ageMs != 412L) throw new AssertionError("ageMs: " + m.ageMs);
        if (m.stale) throw new AssertionError("stale should be false");
        if (m.mid == null || Math.abs(m.mid - 20114.25) > 1e-9) throw new AssertionError("mid: " + m.mid);
        if (!"LIVE".equals(m.anchorMode)) throw new AssertionError("anchorMode: " + m.anchorMode);
        if (m.fetchedAtMs != 9_999L) throw new AssertionError("fetchedAtMs: " + m.fetchedAtMs);
        List<PaxLevelEdgeModel.Row> rows = m.rows;
        if (rows.size() != 1) throw new AssertionError("rows: " + rows.size());
        PaxLevelEdgeModel.Row r = rows.get(0);
        if (!"OR-H".equals(r.label)) throw new AssertionError("label: " + r.label);
        if (r.price == null || Math.abs(r.price - 20112.00) > 1e-9) throw new AssertionError("price: " + r.price);
        if (r.direction != PaxLevelEdgeModel.Direction.LONG) throw new AssertionError("dir: " + r.direction);
        if (r.colorHint != PaxLevelEdgeModel.ColorHint.POSITIVE) throw new AssertionError("color: " + r.colorHint);
        if (r.confidence == null || Math.abs(r.confidence - 0.62) > 1e-9) throw new AssertionError("conf: " + r.confidence);
        if (r.scoreR == null || Math.abs(r.scoreR - 1.44) > 1e-9) throw new AssertionError("scoreR: " + r.scoreR);
        if (r.stopPrice == null || Math.abs(r.stopPrice - 20087.75) > 1e-9) throw new AssertionError("stop: " + r.stopPrice);
        if (!"HALF".equals(r.sizeTier)) throw new AssertionError("tier: " + r.sizeTier);
        if (!r.actionable) throw new AssertionError("actionable should be true");
        if (!m.hasActionable()) throw new AssertionError("hasActionable should be true");
    }

    private static void parsesActionableShortRow() {
        String json = ""
                + "{\"alias\":\"NQM6\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":30070.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-L\",\"price\":30068.0,"
                + "\"direction\":\"SHORT\",\"color_hint\":\"negative\","
                + "\"confidence\":0.68,\"score_R\":1.02,"
                + "\"stop_price\":30070.50,\"size_tier\":\"HALF\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        PaxLevelEdgeModel.Row r = m.rows.get(0);
        if (r.direction != PaxLevelEdgeModel.Direction.SHORT) throw new AssertionError("dir: " + r.direction);
        if (r.colorHint != PaxLevelEdgeModel.ColorHint.NEGATIVE) throw new AssertionError("color: " + r.colorHint);
        if (!r.actionable) throw new AssertionError("actionable");
    }

    private static void skipsNonActionableRows() {
        String json = ""
                + "{\"alias\":\"NQ\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":["
                + "{\"label\":\"OR-H\",\"price\":100.0,\"direction\":\"WAIT\","
                + "\"color_hint\":\"neutral\",\"actionable\":false},"
                + "{\"label\":\"OR-L\",\"price\":90.0,\"direction\":\"LONG\","
                + "\"color_hint\":\"positive\",\"actionable\":true}"
                + "]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.size() != 2) throw new AssertionError("expected 2 rows, got " + m.rows.size());
        if (!m.hasActionable()) throw new AssertionError("at least one row is actionable");
        // The parser keeps all rows; filtering is the painter's job.
        long actionable = m.rows.stream().filter(r -> r.actionable).count();
        if (actionable != 1) throw new AssertionError("actionable count: " + actionable);
    }

    private static void emptyLevelsListYieldsEmptyModel() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":true,"
                + "\"mid\":null,\"anchorMode\":\"LIVE\",\"levels\":[]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) throw new AssertionError("rows should be empty");
        if (m.hasActionable()) throw new AssertionError("hasActionable should be false");
    }

    private static void missingActionableDefaultsToFalse() {
        String json = ""
                + "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"score_R\":1.0,\"confidence\":0.6}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        PaxLevelEdgeModel.Row r = m.rows.get(0);
        if (r.actionable) throw new AssertionError("missing actionable must default to false");
        if (m.hasActionable()) throw new AssertionError("hasActionable should be false");
    }

    private static void missingLevelsArrayYieldsEmptyModel() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\"}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (!m.rows.isEmpty()) throw new AssertionError("rows should be empty when 'levels' missing");
    }

    private static void rejectsEmptyJson() {
        try {
            PaxLevelEdgeSnapshotParser.parse("", 0L);
            throw new AssertionError("expected ParseException for empty input");
        } catch (PaxLevelEdgeSnapshotParser.ParseException expected) { /* ok */ }
    }

    private static void rejectsMalformedJson() {
        try {
            PaxLevelEdgeSnapshotParser.parse("{not json", 0L);
            throw new AssertionError("expected ParseException for malformed JSON");
        } catch (PaxLevelEdgeSnapshotParser.ParseException expected) { /* ok */ }
    }

    private static void unknownDirectionFallsBackToWait() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"SIDEWAYS\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).direction != PaxLevelEdgeModel.Direction.WAIT)
            throw new AssertionError("unknown direction must fall back to WAIT");
    }

    private static void unknownColorHintFallsBackToNeutral() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"chartreuse\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).colorHint != PaxLevelEdgeModel.ColorHint.NEUTRAL)
            throw new AssertionError("unknown color_hint must fall back to NEUTRAL");
    }

    // ---------------------------------------------------------------------
    // Slice 2: parser must read raw_direction / setup / top_drivers /
    // blocked_reason / distance_abs / level_relevant from the enriched
    // /api/pax/levels/edge payload.
    // ---------------------------------------------------------------------

    private static final String SLICE2_FIXTURE = ""
            + "{\"alias\":\"NQM6\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
            + "\"mid\":30060.0,\"anchorMode\":\"LIVE\","
            + "\"levels\":[{"
            + "\"label\":\"OR-H\",\"price\":30060.00,"
            + "\"distance\":-1.25,\"distance_abs\":1.25,"
            + "\"proximity\":true,\"level_relevant\":true,"
            + "\"direction\":\"LONG\",\"raw_direction\":\"LONG\","
            + "\"composite_dir\":\"FOLLOW_LONG\","
            + "\"confidence\":0.72,\"score_R\":1.15,"
            + "\"stop_price\":30038.50,\"size_tier\":\"HALF\","
            + "\"actionable\":true,\"color_hint\":\"positive\","
            + "\"setup\":\"OR_BREAK_FOLLOW\","
            + "\"top_drivers\":[\"pull_stack\",\"vwap\",\"tape\"],"
            + "\"blocked_reason\":null"
            + "}]}";

    private static void parsesSlice2ReasonFields() {
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(SLICE2_FIXTURE, 0L);
        PaxLevelEdgeModel.Row r = m.rows.get(0);
        if (r.rawDirection != PaxLevelEdgeModel.Direction.LONG)
            throw new AssertionError("rawDirection: " + r.rawDirection);
        if (!"OR_BREAK_FOLLOW".equals(r.setup))
            throw new AssertionError("setup: " + r.setup);
        if (r.topDrivers.size() != 3)
            throw new AssertionError("topDrivers size: " + r.topDrivers.size());
        if (!"pull_stack".equals(r.topDrivers.get(0))
                || !"vwap".equals(r.topDrivers.get(1))
                || !"tape".equals(r.topDrivers.get(2)))
            throw new AssertionError("topDrivers order: " + r.topDrivers);
        if (r.blockedReason != null)
            throw new AssertionError("blockedReason: " + r.blockedReason);
        if (r.distanceAbs == null || Math.abs(r.distanceAbs - 1.25) > 1e-9)
            throw new AssertionError("distanceAbs: " + r.distanceAbs);
        if (!r.levelRelevant)
            throw new AssertionError("levelRelevant should be true");
    }

    private static void missingRawDirectionFallsBackToWait() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).rawDirection != PaxLevelEdgeModel.Direction.WAIT)
            throw new AssertionError("missing raw_direction must default to WAIT");
    }

    private static void missingSetupDefaultsToUnknownSetup() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (!"UNKNOWN_SETUP".equals(m.rows.get(0).setup))
            throw new AssertionError("missing setup must default to UNKNOWN_SETUP, got " + m.rows.get(0).setup);
    }

    private static void missingTopDriversYieldsEmptyList() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (!m.rows.get(0).topDrivers.isEmpty())
            throw new AssertionError("missing top_drivers must yield empty list");
    }

    private static void missingBlockedReasonIsNull() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).blockedReason != null)
            throw new AssertionError("missing blocked_reason must be null");
    }

    private static void missingDistanceAbsIsNull() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).distanceAbs != null)
            throw new AssertionError("missing distance_abs must be null");
    }

    private static void missingLevelRelevantDefaultsFalse() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).levelRelevant)
            throw new AssertionError("missing level_relevant must default to false");
    }

    private static void malformedTopDriversYieldsEmptyList() {
        // top_drivers is not an array -- parser must NOT crash, and the
        // list must be empty rather than partially populated.
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"color_hint\":\"positive\","
                + "\"top_drivers\":\"pull_stack\","
                + "\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (!m.rows.get(0).topDrivers.isEmpty())
            throw new AssertionError("malformed top_drivers must yield empty list");
    }

    private static void unknownRawDirectionFallsBackToWait() {
        String json = "{\"alias\":\"X\",\"asOfMs\":1,\"ageMs\":0,\"stale\":false,"
                + "\"mid\":1.0,\"anchorMode\":\"LIVE\","
                + "\"levels\":[{\"label\":\"OR-H\",\"price\":100.0,"
                + "\"direction\":\"LONG\",\"raw_direction\":\"SIDEWAYS\","
                + "\"color_hint\":\"positive\",\"actionable\":true}]}";
        PaxLevelEdgeModel m = PaxLevelEdgeSnapshotParser.parse(json, 0L);
        if (m.rows.get(0).rawDirection != PaxLevelEdgeModel.Direction.WAIT)
            throw new AssertionError("unknown raw_direction must default to WAIT");
    }
}
