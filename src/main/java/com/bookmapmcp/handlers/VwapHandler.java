package com.bookmapmcp.handlers;

import java.io.IOException;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;
import com.bookmapmcp.state.VwapSnapshot;

/**
 * GET /vwap?alias=...
 *
 * <p>Returns the OR-session VWAP (anchored at the operator-configured
 * OpenRange start time; accumulates every trade until the next OR-open
 * crossing) plus a 17:00-CT-anchored informational overlay.
 *
 * <p>The "rth" sub-object is now the OR-session bucket (legacy field name
 * kept for ABI compat). Top-level fields mirror "rth" — this is what the
 * dashboard consumes for vwap_obj. The "eth" sub-object is purely
 * informational; it always rolls at 17:00 CT regardless of OR setting and
 * is NOT consumed by the dashboard or trading decisions.
 */
public final class VwapHandler implements HttpHandler {

    private static final DateTimeFormatter ISO_UTC = DateTimeFormatter.ISO_INSTANT;
    private static final DateTimeFormatter ISO_CT =
            DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ssXXX")
                    .withZone(ZoneId.of("America/Chicago"));

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
        VwapSnapshot rth = state.vwapRthSnapshot();
        VwapSnapshot eth = state.vwapEthSnapshot();
        double lastPrice = state.lastTradePrice();
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("lastTradePrice", lastPrice)
                // Top-level fields are RTH for backward compat with the existing dashboard band card.
                .prop("samples", rth.samples())
                .prop("sessionStartMs", rth.sessionStartMs())
                .prop("sessionStartUtc", isoUtc(rth.sessionStartMs()))
                .prop("sessionStartCt",  isoCt(rth.sessionStartMs()))
                .prop("vwap", rth.vwap())
                .prop("stddev", rth.stddev())
                .prop("upper1", rth.upper1()).prop("lower1", rth.lower1())
                .prop("upper2", rth.upper2()).prop("lower2", rth.lower2())
                .prop("upper3", rth.upper3()).prop("lower3", rth.lower3())
                // Per-session objects
                .rawProp("rth", wrap(rth, lastPrice))
                .rawProp("eth", wrap(eth, lastPrice))
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }

    private static String wrap(VwapSnapshot v, double last) {
        return new JsonWriter()
                .beginObject()
                .prop("samples", v.samples())
                .prop("sessionStartMs", v.sessionStartMs())
                .prop("sessionStartUtc", isoUtc(v.sessionStartMs()))
                .prop("sessionStartCt",  isoCt(v.sessionStartMs()))
                .prop("vwap", v.vwap())
                .prop("stddev", v.stddev())
                .prop("upper1", v.upper1()).prop("lower1", v.lower1())
                .prop("upper2", v.upper2()).prop("lower2", v.lower2())
                .prop("upper3", v.upper3()).prop("lower3", v.lower3())
                .prop("lastTradePrice", last)
                .endObject().build();
    }

    private static String isoUtc(long ms) {
        return ms > 0 ? ISO_UTC.format(Instant.ofEpochMilli(ms)) : null;
    }
    private static String isoCt(long ms) {
        return ms > 0 ? ISO_CT.format(Instant.ofEpochMilli(ms)) : null;
    }
}
