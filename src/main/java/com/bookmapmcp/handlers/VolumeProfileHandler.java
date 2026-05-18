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
import com.bookmapmcp.state.VolumeProfileSnapshot;

/**
 * GET /volume_profile?alias=...
 *
 * <p>Returns both RTH (08:30–15:00 CT, cleared each RTH open) and ETH
 * (17:00 CT prev → 17:00 CT, full trading day including overnight) volume
 * profiles. Top-level fields are RTH for backward compat.
 */
public final class VolumeProfileHandler implements HttpHandler {

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
        VolumeProfileSnapshot rth = state.volumeProfileRthSnapshot();
        VolumeProfileSnapshot eth = state.volumeProfileEthSnapshot();

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                // Top-level = RTH (backward compat)
                .prop("sessionStartMs", rth.sessionStartMs)
                .prop("sessionStartUtc", isoUtc(rth.sessionStartMs))
                .prop("sessionStartCt",  isoCt(rth.sessionStartMs))
                .prop("totalVolume", rth.totalVolume)
                .prop("samples", rth.sampleCount)
                .prop("vpoc", rth.vpoc)
                .prop("vah", rth.vah)
                .prop("val", rth.val)
                .prop("valueAreaVolume", rth.valueAreaVolume)
                .prop("valueAreaPct", rth.totalVolume > 0 ? ((double) rth.valueAreaVolume) / rth.totalVolume : 0.0)
                .rawProp("levels", levelsJson(rth))
                // Per-session
                .rawProp("rth", wrap(rth))
                .rawProp("eth", wrap(eth))
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }

    private static String wrap(VolumeProfileSnapshot vp) {
        return new JsonWriter()
                .beginObject()
                .prop("sessionStartMs", vp.sessionStartMs)
                .prop("sessionStartUtc", isoUtc(vp.sessionStartMs))
                .prop("sessionStartCt",  isoCt(vp.sessionStartMs))
                .prop("totalVolume", vp.totalVolume)
                .prop("samples", vp.sampleCount)
                .prop("vpoc", vp.vpoc)
                .prop("vah", vp.vah)
                .prop("val", vp.val)
                .prop("valueAreaVolume", vp.valueAreaVolume)
                .prop("valueAreaPct", vp.totalVolume > 0 ? ((double) vp.valueAreaVolume) / vp.totalVolume : 0.0)
                .rawProp("levels", levelsJson(vp))
                .endObject().build();
    }

    private static String levelsJson(VolumeProfileSnapshot vp) {
        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (VolumeProfileSnapshot.Level l : vp.levels) {
            if (!first) arr.append(',');
            first = false;
            arr.append(new JsonWriter().beginObject()
                    .prop("price", l.price())
                    .prop("volume", l.volume())
                    .prop("buyVolume", l.buyVolume())
                    .prop("sellVolume", l.sellVolume())
                    .endObject().build());
        }
        arr.append(']');
        return arr.toString();
    }

    private static String isoUtc(long ms) {
        return ms > 0 ? ISO_UTC.format(Instant.ofEpochMilli(ms)) : null;
    }
    private static String isoCt(long ms) {
        return ms > 0 ? ISO_CT.format(Instant.ofEpochMilli(ms)) : null;
    }
}
