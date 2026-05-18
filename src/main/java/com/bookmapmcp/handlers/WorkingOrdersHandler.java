package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Collection;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.WorkingOrderRecord;

public final class WorkingOrdersHandler implements HttpHandler {

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

        Collection<WorkingOrderRecord> orders = state.workingOrdersSnapshot();
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (WorkingOrderRecord o : orders) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("orderId", o.orderId())
                    .prop("side", o.isBuy() ? "buy" : "sell")
                    .prop("type", o.type())
                    .prop("status", o.status())
                    .prop("limitPrice", o.limitPrice())
                    .prop("stopPrice", o.stopPrice())
                    .prop("stopTriggered", o.stopTriggered())
                    .prop("filled", o.filled())
                    .prop("unfilled", o.unfilled())
                    .prop("averageFillPrice", o.averageFillPrice())
                    .prop("duration", o.duration())
                    .prop("modificationUtcMillis", o.modificationUtcMillis())
                    .endObject().build());
        }
        arr.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", orders.size())
                .prop("orderEvents", state.orderEventCount())
                .prop("executionEvents", state.executionEventCount())
                .rawProp("orders", arr.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
