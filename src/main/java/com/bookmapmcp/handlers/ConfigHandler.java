package com.bookmapmcp.handlers;

import java.io.IOException;
import java.time.LocalTime;
import java.time.format.DateTimeParseException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.InstrumentState;

/**
 * GET  /config           — return current VWAP/VP runtime config.
 * POST /config           — update one or more of:
 *   ?rth_open=HH:MM
 *   ?rth_close=HH:MM
 *   ?eth_open=HH:MM
 *   ?vp_value_area_pct=0.70  (must be in (0, 1])
 *
 * <p>All times are in America/Chicago, matching the rest of the bridge.
 * Any param omitted is left unchanged. Mutations are atomic via
 * InstrumentState.applyConfig(). Changing an anchor time causes the
 * next VWAP/VP snapshot to roll into a fresh session.
 */
public final class ConfigHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        String method = exchange.getRequestMethod();
        if ("GET".equalsIgnoreCase(method)) {
            writeCurrent(exchange);
            return;
        }
        if (!"POST".equalsIgnoreCase(method)) {
            Http.writeJsonError(exchange, 405, "method_not_allowed",
                    "Config endpoint accepts GET or POST.");
            return;
        }
        Map<String, String> q = Http.query(exchange.getRequestURI());

        LocalTime rthOpen, rthClose, ethOpen;
        Double vpPct;
        try {
            rthOpen  = parseOptionalTime(q.get("rth_open"));
            rthClose = parseOptionalTime(q.get("rth_close"));
            ethOpen  = parseOptionalTime(q.get("eth_open"));
            vpPct    = parseOptionalDouble(q.get("vp_value_area_pct"));
        } catch (IllegalArgumentException e) {
            Http.writeJsonError(exchange, 400, "bad_param", e.getMessage());
            return;
        }

        // Validate combined constraints (RTH open < RTH close on the same day).
        LocalTime effectiveRthOpen  = rthOpen  != null ? rthOpen  : InstrumentState.configRthOpen();
        LocalTime effectiveRthClose = rthClose != null ? rthClose : InstrumentState.configRthClose();
        if (!effectiveRthOpen.isBefore(effectiveRthClose)) {
            Http.writeJsonError(exchange, 400, "bad_window",
                    "rth_open must be strictly earlier than rth_close.");
            return;
        }
        if (vpPct != null && (!Double.isFinite(vpPct) || vpPct <= 0.0 || vpPct > 1.0)) {
            Http.writeJsonError(exchange, 400, "bad_value_area_pct",
                    "vp_value_area_pct must be a number in (0, 1].");
            return;
        }
        try {
            InstrumentState.applyConfig(rthOpen, rthClose, ethOpen, vpPct);
        } catch (IllegalArgumentException e) {
            Http.writeJsonError(exchange, 400, "bad_config", e.getMessage());
            return;
        }
        writeCurrent(exchange);
    }

    private static void writeCurrent(HttpExchange exchange) throws IOException {
        String body = new JsonWriter()
                .beginObject()
                .prop("rth_open",          InstrumentState.configRthOpen().toString())
                .prop("rth_close",         InstrumentState.configRthClose().toString())
                .prop("eth_open",          InstrumentState.configEthOpen().toString())
                .prop("vp_value_area_pct", InstrumentState.configVpValueAreaPct())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }

    private static LocalTime parseOptionalTime(String raw) {
        if (raw == null || raw.isEmpty()) return null;
        try {
            return LocalTime.parse(raw);
        } catch (DateTimeParseException e) {
            throw new IllegalArgumentException("expected HH:MM, got '" + raw + "'");
        }
    }

    private static Double parseOptionalDouble(String raw) {
        if (raw == null || raw.isEmpty()) return null;
        try {
            return Double.parseDouble(raw);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("expected number, got '" + raw + "'");
        }
    }
}
