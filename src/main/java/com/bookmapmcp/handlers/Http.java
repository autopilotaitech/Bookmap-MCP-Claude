package com.bookmapmcp.handlers;

import java.io.IOException;
import java.io.OutputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.Map;

import com.sun.net.httpserver.HttpExchange;

import com.bookmapmcp.JsonWriter;

/**
 * Tiny helpers shared by every HTTP handler.
 */
final class Http {

    private Http() {}

    /** Returns true if the method is GET; otherwise writes 405 + closes. */
    static boolean requireGet(HttpExchange exchange) throws IOException {
        if ("GET".equalsIgnoreCase(exchange.getRequestMethod())) return true;
        exchange.sendResponseHeaders(405, -1);
        exchange.close();
        return false;
    }

    static void writeJson(HttpExchange exchange, String json) throws IOException {
        byte[] bytes = json.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(200, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    static void writeJsonError(HttpExchange exchange, int status, String code, String message) throws IOException {
        String body = new JsonWriter()
                .beginObject()
                .prop("error", code)
                .prop("message", message)
                .endObject()
                .build();
        byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
        exchange.sendResponseHeaders(status, bytes.length);
        try (OutputStream os = exchange.getResponseBody()) {
            os.write(bytes);
        }
    }

    /**
     * Parse the query string into a name->value map. Last-write-wins for duplicate keys.
     * Returns an empty map if there's no query string.
     */
    static Map<String, String> query(URI uri) {
        Map<String, String> out = new LinkedHashMap<>();
        String q = uri.getRawQuery();
        if (q == null || q.isEmpty()) return out;
        for (String pair : q.split("&")) {
            if (pair.isEmpty()) continue;
            int eq = pair.indexOf('=');
            String key, value;
            if (eq < 0) { key = pair; value = ""; }
            else { key = pair.substring(0, eq); value = pair.substring(eq + 1); }
            out.put(urlDecode(key), urlDecode(value));
        }
        return out;
    }

    static int intQuery(Map<String, String> q, String key, int defaultValue) {
        String v = q.get(key);
        if (v == null || v.isEmpty()) return defaultValue;
        try { return Integer.parseInt(v); }
        catch (NumberFormatException e) { return defaultValue; }
    }

    private static String urlDecode(String s) {
        // Minimal URL decoder. Java's URLDecoder.decode requires charset on older JDKs
        // and pulls in java.net dependencies; this is sufficient for our query params.
        StringBuilder out = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '+') {
                out.append(' ');
            } else if (c == '%' && i + 2 < s.length()) {
                int hi = Character.digit(s.charAt(i + 1), 16);
                int lo = Character.digit(s.charAt(i + 2), 16);
                if (hi >= 0 && lo >= 0) {
                    out.append((char) ((hi << 4) + lo));
                    i += 2;
                } else {
                    out.append(c);
                }
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    /** Convenience map-builder so handlers can build small objects ergonomically. */
    static Map<String, String> map() {
        return new HashMap<>();
    }
}
