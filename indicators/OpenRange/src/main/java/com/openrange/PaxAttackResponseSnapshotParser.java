package com.openrange;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Recursive-descent JSON parser for the Pax AI
 * /api/pax/attack-response endpoint.
 *
 * <p>Safety defaults (these are load-bearing):</p>
 * <ul>
 *   <li>{@code health != "ok"} -> empty model (no rows render).</li>
 *   <li>{@code blocked.anchor=true} -> empty rows (anchor not LIVE).</li>
 *   <li>{@code blocked.stale=true} -> empty rows.</li>
 *   <li>Unknown state / bias enums -> the row is dropped, not rendered
 *       with a default direction. Defense-in-depth for a future Python
 *       schema change.</li>
 *   <li>Missing {@code proven_edge} -> defaults to FALSE so we can never
 *       accidentally promote a WATCH row to EDGE styling.</li>
 * </ul>
 */
final class PaxAttackResponseSnapshotParser {

    static final class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        ParseException(String message) { super(message); }
        ParseException(String message, Throwable cause) { super(message, cause); }
    }

    static PaxAttackResponseModel parse(String json, long fetchedAtMs) {
        if (json == null || json.isEmpty()) {
            throw new ParseException("empty JSON");
        }
        Object root;
        try {
            root = new Tokenizer(json).parseValue(true);
        } catch (RuntimeException e) {
            throw new ParseException("malformed JSON: " + e.getMessage(), e);
        }
        if (!(root instanceof Map)) {
            throw new ParseException("expected JSON object at root");
        }
        Map<?, ?> r = (Map<?, ?>) root;

        String alias = asString(r.get("alias"));
        long asOfMs = asLong(r.get("asOfMs"), 0L);
        String health = asString(r.get("health"));
        if (health == null) health = "";

        PaxAttackResponseModel.Blocked blocked = parseBlocked(r.get("blocked"));

        // Gate 1: health not "ok" -> no rows.
        if (!"ok".equalsIgnoreCase(health)) {
            return new PaxAttackResponseModel(alias, asOfMs, health,
                    new PaxAttackResponseModel.Blocked(true, blocked.stale, blocked.anchor),
                    java.util.Collections.<PaxAttackResponseModel.Row>emptyList(),
                    fetchedAtMs);
        }
        // Gate 2: anchor not LIVE -> no rows. (health stays "ok"; this is
        // the Python contract.)
        if (blocked.anchor || blocked.stale) {
            return new PaxAttackResponseModel(alias, asOfMs, health, blocked,
                    java.util.Collections.<PaxAttackResponseModel.Row>emptyList(),
                    fetchedAtMs);
        }

        List<PaxAttackResponseModel.Row> rows = new ArrayList<>();
        Object states = r.get("states");
        if (states instanceof List) {
            for (Object item : (List<?>) states) {
                if (!(item instanceof Map)) continue;
                PaxAttackResponseModel.Row row = parseRow((Map<?, ?>) item);
                if (row != null) rows.add(row);
            }
        }
        return new PaxAttackResponseModel(alias, asOfMs, health, blocked,
                rows, fetchedAtMs);
    }

    private static PaxAttackResponseModel.Blocked parseBlocked(Object o) {
        if (!(o instanceof Map)) return PaxAttackResponseModel.Blocked.NONE;
        Map<?, ?> b = (Map<?, ?>) o;
        boolean health = asBool(b.get("health"), false);
        boolean stale = asBool(b.get("stale"), false);
        boolean anchor = asBool(b.get("anchor"), false);
        return new PaxAttackResponseModel.Blocked(health, stale, anchor);
    }

    private static PaxAttackResponseModel.Row parseRow(Map<?, ?> m) {
        String id = asString(m.get("id"));
        String location = asString(m.get("location"));
        Double price = asDouble(m.get("level_price"));
        PaxAttackResponseModel.State state =
                PaxAttackResponseModel.State.of(asString(m.get("state")));
        // Drop rows with unknown state - no fallback rendering allowed.
        if (state == PaxAttackResponseModel.State.UNKNOWN) {
            return null;
        }
        PaxAttackResponseModel.Bias bias =
                PaxAttackResponseModel.Bias.of(asString(m.get("bias")));
        String attack = asString(m.get("attack"));
        String response = asString(m.get("response"));
        Double confidence = asDouble(m.get("confidence"));
        // proven_edge defaults to FALSE for safety. The Python emitter
        // ALWAYS stamps it; the default is a hedge against a partial
        // payload silently being treated as edge.
        boolean provenEdge = asBool(m.get("proven_edge"), false);
        long timestampMs = asLong(m.get("timestamp_ms"), 0L);
        List<String> drivers = asStringList(m.get("drivers"));

        return new PaxAttackResponseModel.Row(
                id, location,
                price == null ? Double.NaN : price.doubleValue(),
                state, bias, attack, response, drivers,
                confidence == null ? Double.NaN : confidence.doubleValue(),
                provenEdge, timestampMs);
    }

    // --- shape helpers ----------------------------------------------------

    private static String asString(Object o) {
        return (o instanceof String) ? (String) o : null;
    }

    private static Double asDouble(Object o) {
        if (o instanceof Number) {
            double v = ((Number) o).doubleValue();
            if (Double.isNaN(v) || Double.isInfinite(v)) return null;
            return v;
        }
        return null;
    }

    private static long asLong(Object o, long defaultValue) {
        if (o instanceof Number) {
            double v = ((Number) o).doubleValue();
            if (Double.isNaN(v) || Double.isInfinite(v)) return defaultValue;
            return (long) v;
        }
        return defaultValue;
    }

    private static boolean asBool(Object o, boolean defaultValue) {
        if (o instanceof Boolean) return (Boolean) o;
        return defaultValue;
    }

    private static List<String> asStringList(Object o) {
        if (!(o instanceof List)) return java.util.Collections.emptyList();
        List<?> raw = (List<?>) o;
        if (raw.isEmpty()) return java.util.Collections.emptyList();
        ArrayList<String> out = new ArrayList<>(raw.size());
        for (Object v : raw) {
            if (v instanceof String) out.add((String) v);
        }
        return out;
    }

    // --- tokenizer (same pattern as sibling parsers) ----------------------

    private static final class Tokenizer {
        private final String src;
        private int i;

        Tokenizer(String src) {
            this.src = src;
            this.i = 0;
        }

        Object parseValue(boolean expectEnd) {
            skipWs();
            Object v = readValue();
            if (expectEnd) {
                skipWs();
                if (i != src.length()) {
                    throw new RuntimeException("trailing chars at " + i);
                }
            }
            return v;
        }

        private Object readValue() {
            skipWs();
            if (i >= src.length()) throw new RuntimeException("eof");
            char c = src.charAt(i);
            if (c == '{') return readObject();
            if (c == '[') return readArray();
            if (c == '"') return readString();
            if (c == 't' || c == 'f') return readBool();
            if (c == 'n') return readNull();
            if (c == '-' || (c >= '0' && c <= '9')) return readNumber();
            throw new RuntimeException("unexpected char '" + c + "' at " + i);
        }

        private Map<String, Object> readObject() {
            expect('{');
            LinkedHashMap<String, Object> m = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') { i++; return m; }
            while (true) {
                skipWs();
                if (peek() != '"') throw new RuntimeException("key at " + i);
                String k = readString();
                skipWs();
                expect(':');
                Object v = readValue();
                m.put(k, v);
                skipWs();
                char c = peek();
                if (c == ',') { i++; continue; }
                if (c == '}') { i++; return m; }
                throw new RuntimeException("expected ',' or '}' at " + i);
            }
        }

        private List<Object> readArray() {
            expect('[');
            ArrayList<Object> list = new ArrayList<>();
            skipWs();
            if (peek() == ']') { i++; return list; }
            while (true) {
                Object v = readValue();
                list.add(v);
                skipWs();
                char c = peek();
                if (c == ',') { i++; continue; }
                if (c == ']') { i++; return list; }
                throw new RuntimeException("expected ',' or ']' at " + i);
            }
        }

        private String readString() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (i < src.length()) {
                char c = src.charAt(i++);
                if (c == '"') return sb.toString();
                if (c == '\\') {
                    if (i >= src.length()) throw new RuntimeException("bad escape");
                    char esc = src.charAt(i++);
                    switch (esc) {
                        case '"':  sb.append('"');  break;
                        case '\\': sb.append('\\'); break;
                        case '/':  sb.append('/');  break;
                        case 'b':  sb.append('\b'); break;
                        case 'f':  sb.append('\f'); break;
                        case 'n':  sb.append('\n'); break;
                        case 'r':  sb.append('\r'); break;
                        case 't':  sb.append('\t'); break;
                        case 'u':
                            if (i + 4 > src.length()) throw new RuntimeException("bad unicode");
                            int cp = Integer.parseInt(src.substring(i, i + 4), 16);
                            sb.append((char) cp);
                            i += 4;
                            break;
                        default: throw new RuntimeException("bad escape '\\" + esc + "'");
                    }
                } else {
                    sb.append(c);
                }
            }
            throw new RuntimeException("unterminated string");
        }

        private Object readBool() {
            if (src.startsWith("true", i))  { i += 4; return Boolean.TRUE; }
            if (src.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            throw new RuntimeException("expected bool at " + i);
        }

        private Object readNull() {
            if (src.startsWith("null", i)) { i += 4; return null; }
            throw new RuntimeException("expected null at " + i);
        }

        private Number readNumber() {
            int start = i;
            if (peek() == '-') i++;
            while (i < src.length() && isNumberChar(src.charAt(i))) i++;
            String s = src.substring(start, i);
            try {
                if (s.contains(".") || s.contains("e") || s.contains("E")) {
                    return Double.parseDouble(s);
                }
                return Long.parseLong(s);
            } catch (NumberFormatException e) {
                throw new RuntimeException("bad number '" + s + "'", e);
            }
        }

        private boolean isNumberChar(char c) {
            return c == '+' || c == '-' || c == '.'
                    || (c >= '0' && c <= '9') || c == 'e' || c == 'E';
        }

        private void expect(char want) {
            if (i >= src.length() || src.charAt(i) != want) {
                throw new RuntimeException("expected '" + want + "' at " + i);
            }
            i++;
        }

        private char peek() {
            if (i >= src.length()) throw new RuntimeException("eof");
            return src.charAt(i);
        }

        private void skipWs() {
            while (i < src.length()) {
                char c = src.charAt(i);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++;
                else break;
            }
        }
    }
}
