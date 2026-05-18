package com.bookmapmcp.handlers;

import java.io.IOException;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.JsonWriter;
import com.bookmapmcp.state.BalanceCurrency;
import com.bookmapmcp.state.BalanceSnapshot;
import com.bookmapmcp.state.InstrumentState;

/**
 * GET /balance?alias=...
 * Account balance + per-currency breakdown as last reported by the broker.
 */
public final class BalanceHandler implements HttpHandler {

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
        BalanceSnapshot bal = state.balanceSnapshot();

        StringBuilder arr = new StringBuilder("[");
        boolean first = true;
        for (BalanceCurrency c : bal.currencies()) {
            if (!first) arr.append(',');
            first = false;
            JsonWriter w = new JsonWriter().beginObject()
                    .prop("currency", c.currency())
                    .prop("balance", c.balance())
                    .prop("realizedPnl", c.realizedPnl())
                    .prop("unrealizedPnl", c.unrealizedPnl())
                    .prop("previousDayBalance", c.previousDayBalance())
                    .prop("netLiquidityValue", c.netLiquidityValue());
            if (c.rateToBase() != null) {
                w.prop("rateToBase", c.rateToBase());
            }
            arr.append(w.endObject().build());
        }
        arr.append(']');

        String body = new JsonWriter()
                .beginObject()
                .prop("alias", alias)
                .prop("accountName", bal.accountName())
                .rawProp("currencies", arr.toString())
                .endObject()
                .build();
        Http.writeJson(exchange, body);
    }
}
