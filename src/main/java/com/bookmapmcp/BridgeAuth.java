package com.bookmapmcp;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

/**
 * Wraps an {@link HttpHandler} with shared-secret token authentication.
 * Token is compared in constant time. Requests without or with a wrong
 * token receive 401 with no body, so probes can't fingerprint endpoints.
 */
public final class BridgeAuth {

    public static final String HEADER = "X-Bookmap-MCP-Token";

    private final byte[] expectedToken;

    public BridgeAuth(String token) {
        this.expectedToken = Objects.requireNonNull(token, "token").getBytes(StandardCharsets.UTF_8);
    }

    public HttpHandler guard(HttpHandler delegate) {
        return exchange -> {
            String provided = exchange.getRequestHeaders().getFirst(HEADER);
            if (provided == null || !constantTimeEquals(provided.getBytes(StandardCharsets.UTF_8), expectedToken)) {
                respondEmpty(exchange, 401);
                return;
            }
            delegate.handle(exchange);
        };
    }

    private static boolean constantTimeEquals(byte[] a, byte[] b) {
        if (a.length != b.length) {
            return false;
        }
        int diff = 0;
        for (int i = 0; i < a.length; i++) {
            diff |= a[i] ^ b[i];
        }
        return diff == 0;
    }

    private static void respondEmpty(HttpExchange exchange, int status) throws IOException {
        exchange.sendResponseHeaders(status, -1);
        exchange.close();
    }
}
