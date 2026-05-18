package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.LtLiquiditySnapshot;

public final class LtLiquidityHandler implements HttpHandler {
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
        LtLiquiditySnapshot lt = state.ltLiquiditySnapshot();
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("asOfNanos", lt.asOfNanos)
                .prop("bestBid", lt.bestBid)
                .prop("bestAsk", lt.bestAsk)
                .prop("bestBidSize", lt.bestBidSize)
                .prop("bestAskSize", lt.bestAskSize)
                .prop("ltBidSize", lt.ltBidSize)
                .prop("ltAskSize", lt.ltAskSize)
                .prop("ratio", lt.ratio)
                .prop("halfLifeMillis", lt.halfLifeMillis)
                .endObject().build();
        Http.writeJson(exchange, body);
    }
}
