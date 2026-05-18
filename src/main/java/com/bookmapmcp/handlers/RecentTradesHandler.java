package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.TradeRecord;

/**
 * GET /recent_trades?alias=...&count=N
 *
 * <p>Returns the most recent N trades (default 20, max 500) in newest-first order.
 */
public final class RecentTradesHandler implements HttpHandler {

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
        int count = Http.intQuery(q, "count", 20);
        if (count < 1) count = 1;
        if (count > InstrumentState.TRADES_CAPACITY) count = InstrumentState.TRADES_CAPACITY;

        List<TradeRecord> trades = state.recentTradesSnapshot(count);
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (TradeRecord t : trades) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("price", t.price())
                    .prop("size", t.size())
                    .prop("side", t.bidAggressor() ? "buy" : "sell")
                    .prop("nanos", t.nanos())
                    .endObject().build());
        }
        arr.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", trades.size())
                .rawProp("trades", arr.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
