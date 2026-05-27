package com.openrange;

import java.util.Arrays;
import java.util.Collections;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Single source of truth for every visible chart layer the OpenRange addon
 * draws. Documented in reports/pax-ai-chart-layer-map-2026-05-27.md.
 *
 * Pure data class. No painters, no canvas. Used as the spec for tests that
 * pin uniqueness + category coverage, and as the central registry consumed
 * by future visibility / pin / replay slices.
 *
 * Categories (see report):
 *   CONTEXT     - always-on trading frame (OR lines, extensions).
 *   RAW_EVENT   - microstructure event (sweep, absorption, pull/stack).
 *   TRADE_READ  - directional read for entry. Dominant.
 *   DIAGNOSTIC  - process / status state. Belongs in HUD, not price lane.
 *
 * Surfaces:
 *   PRICE_LANE  - chart-anchored shape (DATA_ZERO coords).
 *   HUD         - pixel-anchored overlay (PIXEL_ZERO coords).
 */
final class PaxChartLayerRegistry {

    enum Category { CONTEXT, RAW_EVENT, TRADE_READ, DIAGNOSTIC }

    enum Surface { PRICE_LANE, HUD }

    enum LayerId {
        OR_HIGH_LINE,
        OR_HIGH_LABEL,
        OR_LOW_LINE,
        OR_LOW_LABEL,
        OR_MID_LINE,
        OR_MID_LABEL,
        OR_UPPER_EXTENSION_LINES,
        OR_LOWER_EXTENSION_LINES,
        NATIVE_SIGNAL_MARKER,
        INS_L_MARKER,
        INS_S_MARKER,
        WATCH_LABEL,
        STND_LABEL,
        ICE_LABEL,
        SPD_LABEL,
        SCR_LABEL,
        CHART_EVENT_TRAIL,
        LEGACY_TREND_TRIANGLES,
        HEATWAVE_BOX,
        LEVEL_EDGE_GLYPH,
        SIGNAL_BADGE,
        STATUS_BANNER
    }

    static final class Entry {
        final LayerId id;
        final Category category;
        final Surface surface;
        final String sourceClass;

        Entry(LayerId id, Category category, Surface surface, String sourceClass) {
            this.id = id;
            this.category = category;
            this.surface = surface;
            this.sourceClass = sourceClass;
        }
    }

    private static final Map<LayerId, Entry> ENTRIES;

    static {
        Map<LayerId, Entry> m = new LinkedHashMap<>();
        add(m, LayerId.OR_HIGH_LINE,              Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_HIGH_LABEL,             Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_LOW_LINE,               Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_LOW_LABEL,              Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_MID_LINE,               Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_MID_LABEL,              Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_UPPER_EXTENSION_LINES,  Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.OR_LOWER_EXTENSION_LINES,  Category.CONTEXT,    Surface.PRICE_LANE, "PaxOpeningRangeModule#drawDay");
        add(m, LayerId.NATIVE_SIGNAL_MARKER,      Category.TRADE_READ, Surface.PRICE_LANE, "PaxOpeningRangeModule#addNativeSignalMarkerIndicator");
        add(m, LayerId.INS_L_MARKER,              Category.TRADE_READ, Surface.PRICE_LANE, "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.INS_S_MARKER,              Category.TRADE_READ, Surface.PRICE_LANE, "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.WATCH_LABEL,               Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.STND_LABEL,                Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.ICE_LABEL,                 Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.SPD_LABEL,                 Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.SCR_LABEL,                 Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#drawInstitutionalMarker");
        add(m, LayerId.CHART_EVENT_TRAIL,         Category.RAW_EVENT,  Surface.PRICE_LANE, "PaxOpeningRangeModule#drawInstitutionalChartEvent");
        add(m, LayerId.LEGACY_TREND_TRIANGLES,    Category.TRADE_READ, Surface.PRICE_LANE, "PaxOpeningRangeModule#drawTrendTriangle");
        add(m, LayerId.HEATWAVE_BOX,              Category.DIAGNOSTIC, Surface.HUD,        "PaxHeatwavePainter#render");
        add(m, LayerId.LEVEL_EDGE_GLYPH,          Category.TRADE_READ, Surface.PRICE_LANE, "PaxLevelEdgePainter#render");
        add(m, LayerId.SIGNAL_BADGE,              Category.TRADE_READ, Surface.HUD,        "PaxOpeningRangeModule#addSignalStatus");
        add(m, LayerId.STATUS_BANNER,             Category.DIAGNOSTIC, Surface.HUD,        "PaxOpeningRangeModule#addStatus");
        ENTRIES = Collections.unmodifiableMap(m);
    }

    private static void add(Map<LayerId, Entry> m, LayerId id, Category cat, Surface surf, String src) {
        m.put(id, new Entry(id, cat, surf, src));
    }

    private PaxChartLayerRegistry() {}

    static Entry get(LayerId id) {
        if (id == null) return null;
        return ENTRIES.get(id);
    }

    static List<Entry> all() {
        return Collections.unmodifiableList(new java.util.ArrayList<>(ENTRIES.values()));
    }

    static Set<LayerId> layersIn(Category category) {
        EnumSet<LayerId> out = EnumSet.noneOf(LayerId.class);
        for (Entry e : ENTRIES.values()) {
            if (e.category == category) out.add(e.id);
        }
        return out;
    }

    static Set<LayerId> layersOn(Surface surface) {
        EnumSet<LayerId> out = EnumSet.noneOf(LayerId.class);
        for (Entry e : ENTRIES.values()) {
            if (e.surface == surface) out.add(e.id);
        }
        return out;
    }

    /** Returns the canonical, lower-snake-case layer key. Stable for logging
     *  and any future config that wants to reference layers by string. */
    static String key(LayerId id) {
        if (id == null) return "";
        return id.name().toLowerCase(Locale.ROOT);
    }

    static List<LayerId> ids() {
        return Arrays.asList(LayerId.values());
    }
}
