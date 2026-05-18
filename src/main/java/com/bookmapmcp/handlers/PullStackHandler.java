package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.PullStackSnapshot;

public final class PullStackHandler implements HttpHandler {
    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!Http.requireGet(exchange)) return;
        Map<String, String> q = Http.query(exchange.getRequestURI());
        String alias = q.get("alias");
        if (alias == null || alias.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_alias", "Required query param 'alias' was not provided.");
            return;
        }
        InstrumentState state = BridgeRegistry.INSTANCE.get(alias);
        if (state == null) {
            Http.writeJsonError(exchange, 404, "unknown_alias",
                    "No instrument with alias '" + alias + "' is currently attached to the MCP bridge.");
            return;
        }
        PullStackSnapshot snap = state.pullStackSnapshot();
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (PullStackSnapshot.Window w : snap.windows) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("label", w.label)
                    .prop("resetMode", w.resetMode)
                    .prop("resetWindowMs", w.resetWindowMs)
                    .prop("resetCount", w.resetCount)
                    .prop("lastResetMs", w.lastResetMs)
                    .prop("bidStacked", w.bidStacked)
                    .prop("bidPulled", w.bidPulled)
                    .prop("askStacked", w.askStacked)
                    .prop("askPulled", w.askPulled)
                    .prop("bidStackedPerMin", w.bidStackedPerMin)
                    .prop("bidPulledPerMin", w.bidPulledPerMin)
                    .prop("askStackedPerMin", w.askStackedPerMin)
                    .prop("askPulledPerMin", w.askPulledPerMin)
                    .prop("status", w.status)
                    .prop("elapsedSec", w.elapsedSec)
                    .prop("bias", w.bias)
                    .prop("biasReason", w.biasReason)
                    .prop("score", w.score)
                    .prop("mean", w.mean)
                    .prop("stddev", w.stddev)
                    .prop("zScore", w.zScore)
                    .endObject().build());
        }
        arr.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("asOfNanos", snap.asOfNanos)
                .prop("bestBid", snap.bestBid)
                .prop("bestAsk", snap.bestAsk)
                .prop("bestBidSize", snap.bestBidSize)
                .prop("bestAskSize", snap.bestAskSize)
                .prop("depthLevels", snap.depthLevels)
                .prop("aggregateZ", snap.aggregateZ)
                .prop("aggregateBias", snap.aggregateBias)
                .prop("rotation", snap.rotation)
                .rawProp("windows", arr.toString())
                .endObject().build();
        Http.writeJson(exchange, body);
    }
}
