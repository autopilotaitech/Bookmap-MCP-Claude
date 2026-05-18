package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.PositionSnapshot;

public final class PositionHandler implements HttpHandler {

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
        PositionSnapshot p = state.effectivePositionSnapshot();
        String source = state.positionSource();
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("position", p.position())
                .prop("averagePrice", p.averagePrice())
                .prop("unrealizedPnl", p.unrealizedPnl())
                .prop("realizedPnl", p.realizedPnl())
                .prop("currency", p.currency())
                .prop("volume", p.volume())
                .prop("workingBuys", p.workingBuys())
                .prop("workingSells", p.workingSells())
                .prop("source", source)
                .prop("positionEvents", state.positionEventCount())
                .prop("orderEvents", state.orderEventCount())
                .prop("executionEvents", state.executionEventCount())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
