package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.MomentumSnapshot;

/**
 * GET /momentum?alias=...&windows=30,120,600
 *
 * <p>Time-windowed aggressor flow + microstructure (microprice, book pressure)
 * plus the adaptive flow-regime classifier:
 * TRENDING_UP / TRENDING_DOWN / ABSORPTION_BID / ABSORPTION_ASK /
 * EXHAUSTION_UP / EXHAUSTION_DOWN / BALANCED / QUIET / WARMUP.</p>
 *
 * <p>Built from Welford-EWMA z-scored OFI (Cont/Kukanov/Stoikov 2014), CVD
 * divergence, volume-per-tick absorption, and 3-bucket bias trajectory.</p>
 */
public final class MomentumHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!Http.requireGet(exchange)) return;

        Map<String, String> q = Http.query(exchange.getRequestURI());
        String alias = q.get("alias");
        if (alias == null || alias.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_alias",
                    "Required query param 'alias' was not provided.");
            return;
        }
        InstrumentState state = BridgeRegistry.INSTANCE.get(alias);
        if (state == null) {
            Http.writeJsonError(exchange, 404, "unknown_alias",
                    "No instrument with alias '" + alias + "' is currently attached to the MCP bridge.");
            return;
        }
        int[] windows = parseWindows(q.get("windows"));
        MomentumSnapshot snap = state.momentumSnapshot(windows);

        StringBuilder warr = new StringBuilder("[");
        boolean first = true;
        for (MomentumSnapshot.Window w : snap.windows) {
            if (!first) warr.append(',');
            first = false;
            warr.append(new JsonWriter().beginObject()
                    .prop("label", w.label())
                    .prop("windowSeconds", w.windowSeconds())
                    .prop("tradeCount", w.tradeCount())
                    .prop("buyVolume", w.buyVolume())
                    .prop("sellVolume", w.sellVolume())
                    .prop("imbalance", w.imbalance())
                    .prop("avgSize", w.avgSize())
                    .endObject().build());
        }
        warr.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("asOfNanos", snap.asOfNanos)
                .rawProp("windows", warr.toString())
                .prop("mid", snap.mid)
                .prop("microprice", snap.microprice)
                .prop("microMidTicks", snap.microMidTicks)
                .prop("bookPressureTop5",  snap.bookPressureTop5)
                .prop("bookPressureTop25", snap.bookPressureTop25)
                .prop("regime", snap.regime)
                .prop("regimeReason", snap.regimeReason)
                .prop("regimeConfidence", snap.regimeConfidence)
                .prop("biasScore", snap.biasScore)
                .prop("biasTrajectory", snap.biasTrajectory)
                .prop("ofi", snap.ofi)
                .prop("ofiZ", snap.ofiZ)
                .prop("cvdDelta", snap.cvdDelta)
                .prop("cvdDeltaZ", snap.cvdDeltaZ)
                .prop("vpt", snap.vpt)
                .prop("vptZ", snap.vptZ)
                .prop("rvolTicks", snap.rvolTicks)
                .prop("rvolZ", snap.rvolZ)
                .rawProp("vwapSlope", buildVwapSlope(state))
                .rawProp("ib", buildIb(state))
                .rawProp("avwap", buildAvwap(state))
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }

    private static int[] parseWindows(String csv) {
        if (csv == null || csv.isEmpty()) return new int[]{30, 120, 600};
        String[] parts = csv.split(",");
        int[] out = new int[parts.length];
        for (int i = 0; i < parts.length; i++) {
            try { out[i] = Integer.parseInt(parts[i].trim()); }
            catch (NumberFormatException e) { out[i] = 60; }
        }
        return out;
    }

    private static String buildVwapSlope(InstrumentState state) {
        com.bookmapmcp.state.VwapSlopeTracker.VwapSlopeSnapshot vs = state.vwapSlopeSnapshot();
        return new JsonWriter().beginObject()
                .prop("vwap", vs.vwap)
                .prop("samples", vs.samples)
                .prop("slopePerMin", vs.slopePerMin)
                .prop("slopeNorm", vs.slopeNorm)
                .prop("slopeZ", vs.slopeZ)
                .prop("label", vs.label)
                .endObject().build();
    }

    private static String buildIb(InstrumentState state) {
        com.bookmapmcp.state.InitialBalanceTracker.InitialBalanceSnapshot ib = state.ibSnapshot();
        StringBuilder eu = new StringBuilder("[");
        for (int i = 0; i < ib.extensionsUp.length; i++) {
            if (i > 0) eu.append(',');
            eu.append(Double.isNaN(ib.extensionsUp[i]) ? "null" : String.valueOf(ib.extensionsUp[i]));
        }
        eu.append(']');
        StringBuilder ed = new StringBuilder("[");
        for (int i = 0; i < ib.extensionsDown.length; i++) {
            if (i > 0) ed.append(',');
            ed.append(Double.isNaN(ib.extensionsDown[i]) ? "null" : String.valueOf(ib.extensionsDown[i]));
        }
        ed.append(']');
        return new JsonWriter().beginObject()
                .prop("sessionStartMs", ib.sessionStartMs)
                .prop("ibHigh", ib.ibHigh)
                .prop("ibLow", ib.ibLow)
                .prop("ibRange", ib.ibRange)
                .prop("ibComplete", ib.ibComplete)
                .prop("ibSizeTag", ib.ibSizeTag)
                .prop("avgIb", ib.avgIb)
                .prop("avgIbDays", ib.avgIbDays)
                .prop("sessionHigh", ib.sessionHigh)
                .prop("sessionLow", ib.sessionLow)
                .prop("sessionRange", ib.sessionRange)
                .prop("dayType", ib.dayType)
                .rawProp("extensionsUp", eu.toString())
                .rawProp("extensionsDown", ed.toString())
                .endObject().build();
    }

    private static String buildAvwap(InstrumentState state) {
        com.bookmapmcp.state.AnchoredVwapTracker.AnchoredVwapSnapshot a = state.anchoredVwapSnapshot();
        return new JsonWriter().beginObject()
                .prop("sessionAnchorMs", a.sessionAnchorMs)
                .prop("driveHigh", a.driveHigh)
                .prop("driveLow", a.driveLow)
                .prop("driveHighMs", a.driveHighMs)
                .prop("driveLowMs", a.driveLowMs)
                .prop("driveFrozen", a.driveFrozen)
                .prop("topAnchorMs", a.topAnchorMs)
                .prop("topVwap", a.topVwap)
                .prop("topVolume", a.topVolume)
                .prop("botAnchorMs", a.botAnchorMs)
                .prop("botVwap", a.botVwap)
                .prop("botVolume", a.botVolume)
                .endObject().build();
    }
}
