package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.OrderbookLevel;
import com.bookmapmcp.state.OrderbookSnapshot;

/**
 * GET /orderbook?alias=...&depth=N
 *
 * <p>Returns the top-N bid and ask levels (default N=10) at the time of the
 * call, plus best bid/ask, mid, and spread for quick reading.
 */
public final class OrderbookHandler implements HttpHandler {

    public static final int MAX_DEPTH = 500;

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
        int depth = Http.intQuery(q, "depth", 10);
        if (depth < 1) depth = 1;
        if (depth > MAX_DEPTH) depth = MAX_DEPTH;

        OrderbookSnapshot snap = state.orderbookSnapshot(depth);

        StringBuilder bids = new StringBuilder("[");
        boolean first = true;
        for (OrderbookLevel l : snap.bids()) {
            if (!first) bids.append(',');
            first = false;
            bids.append(new JsonWriter().beginObject()
                    .prop("price", l.price())
                    .prop("size", l.size())
                    .endObject().build());
        }
        bids.append(']');

        StringBuilder asks = new StringBuilder("[");
        first = true;
        for (OrderbookLevel l : snap.asks()) {
            if (!first) asks.append(',');
            first = false;
            asks.append(new JsonWriter().beginObject()
                    .prop("price", l.price())
                    .prop("size", l.size())
                    .endObject().build());
        }
        asks.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("depth", depth)
                .prop("bestBid", snap.bestBid())
                .prop("bestAsk", snap.bestAsk())
                .prop("mid", snap.mid())
                .prop("spread", snap.spread())
                .prop("generatedNanos", snap.generatedNanos())
                .rawProp("bids", bids.toString())
                .rawProp("asks", asks.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
