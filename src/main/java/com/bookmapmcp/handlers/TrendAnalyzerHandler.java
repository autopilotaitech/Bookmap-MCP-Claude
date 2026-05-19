package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.trend.TrendAnalyzerSnapshot;

/**
 * GET /trend_analyzer?alias=...
 *
 * <p>Per-alias stable-trend snapshot from the bridge-side
 * {@link com.bookmapmcp.trend.TimeBucketTrendAccumulator}. Returns the fast
 * (15s) and slow (60s) {@code StableTrendEngine} readings plus a blended
 * diagnostic score and reliability hint.</p>
 *
 * <p>Timestamp semantics — do NOT conflate:</p>
 * <ul>
 *   <li>{@code asOfNanos}: raw Bookmap event nanos if available, diagnostic only.</li>
 *   <li>{@code eventMs}: epoch ms of the most recent trade — used by chart anchoring.</li>
 *   <li>{@code updatedAtMs}: wall-clock at snapshot build time — used by the
 *       dashboard for staleness gating.</li>
 * </ul>
 */
public final class TrendAnalyzerHandler implements HttpHandler {

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
        TrendAnalyzerSnapshot snap = state.trendAnalyzerSnapshot();
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", snap.alias())
                .prop("asOfNanos", snap.asOfNanos())
                .prop("eventMs", snap.eventMs())
                .prop("updatedAtMs", snap.updatedAtMs())
                .prop("lastClose", snap.lastClose())
                .prop("warmedUp", snap.warmedUp())
                .prop("score", snap.score())
                .prop("reliabilityHint", snap.reliabilityHint())
                .rawProp("fast", legJson(snap.fast()))
                .rawProp("slow", legJson(snap.slow()))
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }

    private static String legJson(TrendAnalyzerSnapshot.Leg leg) {
        return new JsonWriter()
                .beginObject()
                .prop("direction", leg.direction() == null ? "NEUTRAL" : leg.direction().name())
                .prop("directionSign", leg.directionSign())
                .prop("confidence", leg.confidence())
                .prop("switched", leg.switched())
                .prop("chop", leg.chop())
                .prop("trendLine", leg.trendLine())
                .prop("candleIntervalMillis", leg.candleIntervalMillis())
                .prop("candleCount", leg.candleCount())
                .endObject()
                .build();
    }
}
