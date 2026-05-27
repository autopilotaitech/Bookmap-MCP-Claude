package com.openrange;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Immutable carrier for one /api/pax/levels/edge response. Renders ONLY
 * actionable rows -- the painter must skip rows where actionable is false
 * and draw NOTHING when actionable.isEmpty(). See
 * reports/pax-ai-level-edge-audit-2026-05-26.md.
 *
 * The `score_R` number on each Row is renamed from edge_calculus.expected_R:
 * confidence * hand-tuned directional_R constant. NOT measured EV. Painter
 * displays it as `R~x.xx` to keep the honesty contract visible.
 *
 * Slice 2 (2026-05-26): rows now carry the Python-side reason fields so the
 * chart can label its glyphs with setup + driver tags:
 *   rawDirection, setup, topDrivers, blockedReason, distanceAbs, levelRelevant
 * The painter still draws nothing unless actionable && direction != WAIT &&
 * price != null. blockedReason is intentionally NOT rendered on the chart in
 * Slice 2 -- it is for the outcome log and operator inspection only.
 */
final class PaxLevelEdgeModel {

    enum ColorHint {
        POSITIVE, NEGATIVE, NEUTRAL;

        static ColorHint of(String raw) {
            if (raw == null) return NEUTRAL;
            String s = raw.trim().toLowerCase();
            if ("positive".equals(s)) return POSITIVE;
            if ("negative".equals(s)) return NEGATIVE;
            return NEUTRAL;
        }
    }

    enum Direction {
        LONG, SHORT, WAIT;

        static Direction of(String raw) {
            if (raw == null) return WAIT;
            String s = raw.trim().toUpperCase();
            if ("LONG".equals(s)) return LONG;
            if ("SHORT".equals(s)) return SHORT;
            return WAIT;
        }
    }

    static final class Row {
        final String label;
        final Double price;
        final Direction direction;
        final Direction rawDirection;
        final ColorHint colorHint;
        final Double confidence;
        final Double scoreR;
        final Double stopPrice;
        final String sizeTier;
        final boolean actionable;
        final String setup;
        final List<String> topDrivers;
        final String blockedReason;
        final Double distanceAbs;
        final boolean levelRelevant;

        Row(String label, Double price,
             Direction direction, Direction rawDirection, ColorHint colorHint,
             Double confidence, Double scoreR, Double stopPrice, String sizeTier,
             boolean actionable,
             String setup, List<String> topDrivers, String blockedReason,
             Double distanceAbs, boolean levelRelevant) {
            this.label = label;
            this.price = price;
            this.direction = direction;
            this.rawDirection = rawDirection == null ? Direction.WAIT : rawDirection;
            this.colorHint = colorHint;
            this.confidence = confidence;
            this.scoreR = scoreR;
            this.stopPrice = stopPrice;
            this.sizeTier = sizeTier;
            this.actionable = actionable;
            this.setup = setup == null ? "UNKNOWN_SETUP" : setup;
            this.topDrivers = (topDrivers == null || topDrivers.isEmpty())
                    ? Collections.<String>emptyList()
                    : Collections.unmodifiableList(new ArrayList<>(topDrivers));
            this.blockedReason = blockedReason;
            this.distanceAbs = distanceAbs;
            this.levelRelevant = levelRelevant;
        }

        /** Legacy constructor preserved for existing tests / callers that
         *  predate Slice 2. Defaults the new reason fields safely: setup
         *  UNKNOWN_SETUP, no drivers, no blocked reason, no distance, not
         *  relevant, and raw_direction == direction. */
        Row(String label, Double price, Direction direction, ColorHint colorHint,
             Double confidence, Double scoreR, Double stopPrice, String sizeTier,
             boolean actionable) {
            this(label, price, direction, direction, colorHint,
                 confidence, scoreR, stopPrice, sizeTier, actionable,
                 "UNKNOWN_SETUP", Collections.<String>emptyList(), null,
                 null, false);
        }
    }

    final String alias;
    final long asOfMs;
    final long ageMs;
    final boolean stale;
    final Double mid;
    final String anchorMode;
    final List<Row> rows;
    /** Wall-clock ms when this model was fetched. Used by the painter to
     *  decide whether the rows are still trustworthy. */
    final long fetchedAtMs;

    PaxLevelEdgeModel(String alias, long asOfMs, long ageMs, boolean stale,
                      Double mid, String anchorMode, List<Row> rows,
                      long fetchedAtMs) {
        this.alias = alias;
        this.asOfMs = asOfMs;
        this.ageMs = ageMs;
        this.stale = stale;
        this.mid = mid;
        this.anchorMode = anchorMode;
        this.rows = rows == null ? Collections.emptyList()
                                  : Collections.unmodifiableList(rows);
        this.fetchedAtMs = fetchedAtMs;
    }

    /** Empty model -- no rows. Used as a degenerate after-failure state. */
    static PaxLevelEdgeModel empty(long fetchedAtMs) {
        return new PaxLevelEdgeModel(null, 0L, 0L, true, null, null,
                                       Collections.emptyList(), fetchedAtMs);
    }

    /** True when there is at least one actionable row to draw. */
    boolean hasActionable() {
        for (Row r : rows) {
            if (r != null && r.actionable) return true;
        }
        return false;
    }
}
