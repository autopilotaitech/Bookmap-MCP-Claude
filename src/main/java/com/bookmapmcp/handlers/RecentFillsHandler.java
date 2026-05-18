package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.RecentExecution;

/**
 * GET /recent_fills?alias=...&count=N
 * Last N fills (executions) on the instrument, newest first.
 */
public final class RecentFillsHandler implements HttpHandler {

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
        if (count > InstrumentState.FILLS_CAPACITY) count = InstrumentState.FILLS_CAPACITY;

        List<RecentExecution> fills = state.recentExecutionsSnapshot(count);
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (RecentExecution f : fills) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("orderId", f.orderId())
                    .prop("executionId", f.executionId())
                    .prop("side", f.side())
                    .prop("price", f.price())
                    .prop("size", f.size())
                    .prop("timeMillis", f.timeMillis())
                    .prop("simulated", f.simulated())
                    .endObject().build());
        }
        arr.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", fills.size())
                .rawProp("fills", arr.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
