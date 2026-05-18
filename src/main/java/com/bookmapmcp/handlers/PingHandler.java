package com.bookmapmcp.handlers;

import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;

public final class PingHandler implements HttpHandler {

    public static final String BRIDGE_VERSION = "0.1.0";
    public static final String BOOKMAP_API_VERSION = "7.4.0.21";

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!"GET".equalsIgnoreCase(exchange.getRequestMethod())) {
            exchange.sendResponseHeaders(405, -1);
            exchange.close();
            return;
        }
        String body = new JsonWriter()
                .beginObject()
                .prop("ok", true)
                .prop("bridgeVersion", BRIDGE_VERSION)
                .prop("bookmapApi", BOOKMAP_API_VERSION)
                .prop("attachedInstruments", BridgeRegistry.INSTANCE.size())
                .prop("serverTime", Instant.now().toString())
                .endObject()
                .build();
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(200, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }
}
