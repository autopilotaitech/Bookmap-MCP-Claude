package com.bookmapmcp.handlers;

import java.io.IOException;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;

public final class InstrumentsHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!Http.requireGet(exchange)) return;

        JsonWriter writer = new JsonWriter()
                .beginObject()
                .prop("count", BridgeRegistry.INSTANCE.size());

        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (InstrumentState s : BridgeRegistry.INSTANCE.all()) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter()
                    .beginObject()
                    .prop("alias", s.alias())
                    .prop("symbol", s.symbol())
                    .prop("fullName", s.fullName())
                    .prop("pips", s.pips())
                    .prop("multiplier", s.multiplier())
                    .prop("attachedAt", s.attachedAt().toString())
                    .prop("lastTradePrice", s.lastTradePrice())
                    .endObject()
                    .build());
        }
        arr.append(']');
        writer.rawProp("instruments", arr.toString());
        Http.writeJson(exchange, writer.endObject().build());
    }
}
