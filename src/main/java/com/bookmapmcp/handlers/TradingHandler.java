package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import velox.api.layer1.data.OrderCancelParameters;
import velox.api.layer1.data.OrderDuration;
import velox.api.layer1.data.SimpleOrderSendParameters;
import velox.api.layer1.simplified.Api;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;

/**
 * POST /place_limit_order?alias=&side=buy|sell&size=N&price=P[&duration=DAY|GTC]
 * POST /cancel_order?alias=&orderId=
 *
 * Gated behind the BOOKMAP_ALLOW_TRADING=1 environment variable. The user
 * remains responsible for whether they're connected to a sim or live broker
 * — that's decided at Bookmap login.
 */
public final class TradingHandler implements HttpHandler {

    public enum Op { PLACE_LIMIT, CANCEL }

    private final Op op;

    public TradingHandler(Op op) {
        this.op = op;
    }

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            Http.writeJsonError(exchange, 405, "method_not_allowed", "Trading endpoints require POST.");
            return;
        }
        if (!"1".equals(System.getenv("BOOKMAP_ALLOW_TRADING"))) {
            Http.writeJsonError(exchange, 403, "trading_disabled",
                    "Set BOOKMAP_ALLOW_TRADING=1 in the environment Bookmap runs under to enable order placement.");
            return;
        }
        Map<String, String> q = Http.query(exchange.getRequestURI());
        String alias = q.get("alias");
        if (alias == null || alias.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_alias", "Required query param 'alias' was not provided.");
            return;
        }
        InstrumentState state = BridgeRegistry.INSTANCE.get(alias);
        if (state == null) {
            Http.writeJsonError(exchange, 404, "unknown_alias",
                    "No instrument with alias '" + alias + "'.");
            return;
        }
        Api api = state.api();
        if (api == null) {
            Http.writeJsonError(exchange, 503, "no_api",
                    "Bookmap Api handle for this instrument is not available. Re-attach the MCP Bridge add-on.");
            return;
        }
        switch (op) {
            case PLACE_LIMIT -> placeLimit(exchange, q, alias, api);
            case CANCEL      -> cancel(exchange, q, api);
        }
    }

    private static void placeLimit(HttpExchange exchange, Map<String, String> q, String alias, Api api)
            throws IOException {
        String side = q.get("side");
        if (side == null || (!side.equalsIgnoreCase("buy") && !side.equalsIgnoreCase("sell"))) {
            Http.writeJsonError(exchange, 400, "bad_side", "side must be 'buy' or 'sell'.");
            return;
        }
        boolean isBuy = side.equalsIgnoreCase("buy");
        int size = Http.intQuery(q, "size", 0);
        if (size < 1) {
            Http.writeJsonError(exchange, 400, "bad_size", "size must be a positive integer.");
            return;
        }
        double price = parsePrice(q.get("price"));
        if (Double.isNaN(price)) {
            Http.writeJsonError(exchange, 400, "bad_price",
                    "price must be a finite positive number.");
            return;
        }
        OrderDuration duration;
        try {
            duration = OrderDuration.valueOf(q.getOrDefault("duration", "DAY").toUpperCase());
        } catch (IllegalArgumentException e) {
            Http.writeJsonError(exchange, 400, "bad_duration",
                    "duration must be one of DAY, GTC, IOC, FOK, etc.");
            return;
        }
        SimpleOrderSendParameters params =
                new SimpleOrderSendParameters(alias, isBuy, size, duration, Double.NaN, price);
        try {
            api.sendOrder(params);
        } catch (RuntimeException e) {
            Http.writeJsonError(exchange, 500, "send_order_failed", e.getMessage());
            return;
        }
        String body = new JsonWriter().beginObject()
                .prop("status", "submitted")
                .prop("alias", alias)
                .prop("side", side)
                .prop("size", size)
                .prop("price", price)
                .prop("duration", duration.name())
                .endObject().build();
        Http.writeJson(exchange, body);
    }

    private static void cancel(HttpExchange exchange, Map<String, String> q, Api api) throws IOException {
        String orderId = q.get("orderId");
        if (orderId == null || orderId.isEmpty()) {
            Http.writeJsonError(exchange, 400, "missing_orderId", "Required query param 'orderId' was not provided.");
            return;
        }
        try {
            api.updateOrder(new OrderCancelParameters(orderId));
        } catch (RuntimeException e) {
            Http.writeJsonError(exchange, 500, "cancel_failed", e.getMessage());
            return;
        }
        String body = new JsonWriter().beginObject()
                .prop("status", "cancel_submitted")
                .prop("orderId", orderId)
                .endObject().build();
        Http.writeJson(exchange, body);
    }
    /** Parse a price string. Returns NaN for missing/bad/non-finite/non-positive
     *  values so callers can emit a uniform 400 bad_price response. Isolated
     *  from Bookmap runtime so it can be unit-tested standalone. */
    static double parsePrice(String s) {
        if (s == null || s.isEmpty()) return Double.NaN;
        double v;
        try { v = Double.parseDouble(s); }
        catch (NumberFormatException e) { return Double.NaN; }
        if (!Double.isFinite(v)) return Double.NaN;
        if (v <= 0.0)            return Double.NaN;
        return v;
    }
}
