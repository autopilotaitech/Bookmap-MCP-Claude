package com.openrange;

import java.util.EnumSet;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

public class PaxChartLayerRegistryTest {

    public static void main(String[] args) {
        allIdsHaveEntries();
        idsAreUnique();
        everyEntryHasCategoryAndSurface();
        keysAreLowerSnakeAndUnique();
        priceLaneSubsetExcludesPureHud();
        diagnosticStatusCodesAreClassedAsHudNotPriceLane();
        levelEdgeIsTradeReadOnPriceLane();
        heatwaveBoxIsOnHud();
        layersInCategoryRoundTrips();
        System.out.println("PaxChartLayerRegistryTest OK");
        System.exit(0);
    }

    private static void allIdsHaveEntries() {
        for (PaxChartLayerRegistry.LayerId id : PaxChartLayerRegistry.LayerId.values()) {
            PaxChartLayerRegistry.Entry e = PaxChartLayerRegistry.get(id);
            if (e == null) throw new AssertionError("missing entry for " + id);
            if (e.id != id) throw new AssertionError("entry id mismatch for " + id);
        }
    }

    private static void idsAreUnique() {
        Set<PaxChartLayerRegistry.LayerId> seen = EnumSet.noneOf(PaxChartLayerRegistry.LayerId.class);
        for (PaxChartLayerRegistry.Entry e : PaxChartLayerRegistry.all()) {
            if (!seen.add(e.id)) throw new AssertionError("duplicate id " + e.id);
        }
    }

    private static void everyEntryHasCategoryAndSurface() {
        for (PaxChartLayerRegistry.Entry e : PaxChartLayerRegistry.all()) {
            if (e.category == null) throw new AssertionError("null category for " + e.id);
            if (e.surface == null)  throw new AssertionError("null surface for " + e.id);
            if (e.sourceClass == null || e.sourceClass.isEmpty())
                throw new AssertionError("missing source for " + e.id);
        }
    }

    private static void keysAreLowerSnakeAndUnique() {
        Set<String> seen = new HashSet<>();
        for (PaxChartLayerRegistry.LayerId id : PaxChartLayerRegistry.LayerId.values()) {
            String k = PaxChartLayerRegistry.key(id);
            if (k == null || k.isEmpty()) throw new AssertionError("empty key for " + id);
            for (int i = 0; i < k.length(); i++) {
                char c = k.charAt(i);
                boolean ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_';
                if (!ok) throw new AssertionError("bad key char in " + k);
            }
            if (!seen.add(k)) throw new AssertionError("duplicate key " + k);
        }
    }

    private static void priceLaneSubsetExcludesPureHud() {
        Set<PaxChartLayerRegistry.LayerId> price = PaxChartLayerRegistry.layersOn(
                PaxChartLayerRegistry.Surface.PRICE_LANE);
        if (price.contains(PaxChartLayerRegistry.LayerId.HEATWAVE_BOX))
            throw new AssertionError("HEATWAVE_BOX must be HUD, not PRICE_LANE");
        if (price.contains(PaxChartLayerRegistry.LayerId.STATUS_BANNER))
            throw new AssertionError("STATUS_BANNER must be HUD");
        if (price.contains(PaxChartLayerRegistry.LayerId.SIGNAL_BADGE))
            throw new AssertionError("SIGNAL_BADGE must be HUD");
    }

    private static void diagnosticStatusCodesAreClassedAsHudNotPriceLane() {
        PaxChartLayerRegistry.LayerId[] statusCodes = new PaxChartLayerRegistry.LayerId[] {
                PaxChartLayerRegistry.LayerId.WATCH_LABEL,
                PaxChartLayerRegistry.LayerId.STND_LABEL,
                PaxChartLayerRegistry.LayerId.ICE_LABEL,
                PaxChartLayerRegistry.LayerId.SPD_LABEL,
                PaxChartLayerRegistry.LayerId.SCR_LABEL,
        };
        for (PaxChartLayerRegistry.LayerId id : statusCodes) {
            PaxChartLayerRegistry.Entry e = PaxChartLayerRegistry.get(id);
            if (e.category != PaxChartLayerRegistry.Category.DIAGNOSTIC)
                throw new AssertionError(id + " must be DIAGNOSTIC, got " + e.category);
            if (e.surface != PaxChartLayerRegistry.Surface.HUD)
                throw new AssertionError(id + " must be on HUD surface, got " + e.surface);
        }
    }

    private static void levelEdgeIsTradeReadOnPriceLane() {
        PaxChartLayerRegistry.Entry e = PaxChartLayerRegistry.get(
                PaxChartLayerRegistry.LayerId.LEVEL_EDGE_GLYPH);
        if (e.category != PaxChartLayerRegistry.Category.TRADE_READ)
            throw new AssertionError("LEVEL_EDGE_GLYPH must be TRADE_READ");
        if (e.surface != PaxChartLayerRegistry.Surface.PRICE_LANE)
            throw new AssertionError("LEVEL_EDGE_GLYPH must be PRICE_LANE");
    }

    private static void heatwaveBoxIsOnHud() {
        PaxChartLayerRegistry.Entry e = PaxChartLayerRegistry.get(
                PaxChartLayerRegistry.LayerId.HEATWAVE_BOX);
        if (e.surface != PaxChartLayerRegistry.Surface.HUD)
            throw new AssertionError("HEATWAVE_BOX must be HUD");
    }

    private static void layersInCategoryRoundTrips() {
        for (PaxChartLayerRegistry.Category c : PaxChartLayerRegistry.Category.values()) {
            Set<PaxChartLayerRegistry.LayerId> got = PaxChartLayerRegistry.layersIn(c);
            for (PaxChartLayerRegistry.LayerId id : got) {
                if (PaxChartLayerRegistry.get(id).category != c)
                    throw new AssertionError("layersIn membership broken for " + id);
            }
        }
        List<PaxChartLayerRegistry.LayerId> all = PaxChartLayerRegistry.ids();
        if (all.size() != PaxChartLayerRegistry.LayerId.values().length)
            throw new AssertionError("ids() / values() length mismatch");
    }
}
