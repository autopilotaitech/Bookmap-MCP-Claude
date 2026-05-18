package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.TapeBucketsSnapshot;

public final class TapeBucketsHandler implements HttpHandler {
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
        TapeBucketsSnapshot snap = state.tapeBucketsSnapshot();
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (TapeBucketsSnapshot.Bucket b : snap.buckets) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("label", b.label)
                    .prop("minSize", b.minSize)
                    .prop("maxSize", b.maxSize)
                    .prop("buyVol30s", b.buyVol30s)
                    .prop("sellVol30s", b.sellVol30s)
                    .prop("prints30s", b.prints30s)
                    .prop("imbalance30s", b.imbalance30s())
                    .prop("buyVol5m", b.buyVol5m)
                    .prop("sellVol5m", b.sellVol5m)
                    .prop("prints5m", b.prints5m)
                    .prop("imbalance5m", b.imbalance5m())
                    .endObject().build());
        }
        arr.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("asOfNanos", snap.asOfNanos)
                .rawProp("buckets", arr.toString())
                .endObject().build();
        Http.writeJson(exchange, body);
    }
}
