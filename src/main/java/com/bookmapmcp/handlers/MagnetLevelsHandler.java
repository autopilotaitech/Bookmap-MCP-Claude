package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;

/**
 * POST /magnet_levels?alias=&levels=p1,p2,p3
 *
 * <p>Configures stop-sweep magnet levels for one instrument. Each level is a
 * display-currency price (NOT a tick). Pass an empty {@code levels} string
 * (or omit the param) to clear all levels.
 *
 * <p>Response: {@code {alias, count, levels:[...]}}.
 */
public final class MagnetLevelsHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            Http.writeJsonError(exchange, 405, "method_not_allowed",
                    "Magnet-levels endpoint requires POST.");
            return;
        }
        Map<String, String> q = Http.query(exchange.getRequestURI());
        String alias = q.get("alias");
        if (alias == null || alias.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_alias",
                    "Required query param 'alias' was not provided.");
            return;
        }
        InstrumentState state = BridgeRegistry.INSTANCE.get(alias);
        if (state == null) {
            Http.writeJsonError(exchange, 404, "unknown_alias",
                    "No instrument with alias '" + alias + "' is currently attached to the MCP bridge.");
            return;
        }
        String raw = q.getOrDefault("levels", "");
        List<Double> parsed = new ArrayList<>();
        if (!raw.isEmpty()) {
            for (String part : raw.split(",")) {
                String trimmed = part.trim();
                if (trimmed.isEmpty()) continue;
                double v;
                try {
                    v = Double.parseDouble(trimmed);
                } catch (NumberFormatException e) {
                    Http.writeJsonError(exchange, 400, "bad_level",
                            "Could not parse level '" + trimmed + "' as a number.");
                    return;
                }
                if (!Double.isFinite(v) || v <= 0.0) {
                    Http.writeJsonError(exchange, 400, "bad_level",
                            "Level must be a finite positive price; got '" + trimmed + "'.");
                    return;
                }
                parsed.add(v);
            }
        }
        double[] arr = new double[parsed.size()];
        for (int i = 0; i < arr.length; i++) arr[i] = parsed.get(i);
        state.setMagnetLevels(arr);

        StringBuilder lvls = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            if (i > 0) lvls.append(',');
            lvls.append(arr[i]);
        }
        lvls.append(']');
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("count", arr.length)
                .rawProp("levels", lvls.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
