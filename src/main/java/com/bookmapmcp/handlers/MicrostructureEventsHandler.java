package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.List;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.MicrostructureEvent;

public final class MicrostructureEventsHandler implements HttpHandler {
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
        int max = Http.intQuery(q, "max", 50);
        List<MicrostructureEvent> events = state.microstructureEvents(max);
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (MicrostructureEvent ev : events) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("kind", ev.kind.name())
                    .prop("timeMs", ev.timeMs)
                    .prop("price", ev.price)
                    .prop("size", ev.size)
                    .prop("isBid", ev.isBid)
                    .prop("reason", ev.reason)
                    .endObject().build());
        }
        arr.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", events.size())
                .rawProp("events", arr.toString())
                .endObject().build();
        Http.writeJson(exchange, body);
    }
}
