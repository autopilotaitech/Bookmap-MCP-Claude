package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.BookDynamicsSnapshot;
import com.bookmapmcp.state.InstrumentState;

public final class BookDynamicsHandler implements HttpHandler {
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
        int topN = Http.intQuery(q, "top", 12);
        BookDynamicsSnapshot snap = state.bookDynamicsSnapshot(topN);
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (BookDynamicsSnapshot.Level l : snap.topActiveLevels) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("price", l.price)
                    .prop("isBid", l.isBid)
                    .prop("stacked1m", l.stacked1m).prop("pulled1m", l.pulled1m).prop("hit1m", l.hit1m)
                    .prop("stacked3m", l.stacked3m).prop("pulled3m", l.pulled3m).prop("hit3m", l.hit3m)
                    .prop("stacked15m", l.stacked15m).prop("pulled15m", l.pulled15m).prop("hit15m", l.hit15m)
                    .endObject().build());
        }
        arr.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("asOfNanos", snap.asOfNanos)
                .prop("mboAvailable", snap.mboAvailable)
                .rawProp("levels", arr.toString())
                .endObject().build();
        Http.writeJson(exchange, body);
    }
}
