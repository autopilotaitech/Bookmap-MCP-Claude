package com.openrange;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Minimal recursive-descent JSON parser for the Pax AI
 * /api/pax/levels/edge endpoint. Self-contained (Tokenizer is inlined) so
 * it does not couple to PaxHeatwaveSnapshotParser.
 *
 * Safety defaults: missing or malformed `actionable` -> false; missing
 * `direction` -> WAIT; missing `color_hint` -> NEUTRAL. Anything that
 * cannot be parsed as a number is null on the model. The painter then
 * skips non-actionable rows.
 */
final class PaxLevelEdgeSnapshotParser {

    static final class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        ParseException(String message) { super(message); }
        ParseException(String message, Throwable cause) { super(message, cause); }
    }

    static PaxLevelEdgeModel parse(String json, long fetchedAtMs) {
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
        long ageMs = asLong(r.get("ageMs"), 0L);
        boolean stale = asBool(r.get("stale"), false);
        Double mid = asDouble(r.get("mid"));
        String anchorMode = asString(r.get("anchorMode"));

        List<PaxLevelEdgeModel.Row> rows = new ArrayList<>();
        Object levels = r.get("levels");
        if (levels instanceof List) {
            for (Object item : (List<?>) levels) {
                if (item instanceof Map) {
                    PaxLevelEdgeModel.Row row = parseRow((Map<?, ?>) item);
                    if (row != null) rows.add(row);
                }
            }
        }
        return new PaxLevelEdgeModel(alias, asOfMs, ageMs, stale, mid, anchorMode,
                                       rows, fetchedAtMs);
    }

    private static PaxLevelEdgeModel.Row parseRow(Map<?, ?> m) {
        String label = asString(m.get("label"));
        Double price = asDouble(m.get("price"));
        PaxLevelEdgeModel.Direction direction =
                PaxLevelEdgeModel.Direction.of(asString(m.get("direction")));
        // Slice 2: raw_direction is the upstream LONG/SHORT classification
        // even when the chart-facing `direction` collapsed to WAIT (e.g. a
        // far-away level with a directional composite). Painter ignores
        // rawDirection; the outcome log (Slice 3) consumes it.
        PaxLevelEdgeModel.Direction rawDirection =
                PaxLevelEdgeModel.Direction.of(asString(m.get("raw_direction")));
        PaxLevelEdgeModel.ColorHint color =
                PaxLevelEdgeModel.ColorHint.of(asString(m.get("color_hint")));
        Double confidence = asDouble(m.get("confidence"));
        Double scoreR = asDouble(m.get("score_R"));
        Double stop = asDouble(m.get("stop_price"));
        String tier = asString(m.get("size_tier"));
        // Default actionable to FALSE for safety: a partial payload must
        // not render anything just because some other field happened to
        // be present.
        boolean actionable = asBool(m.get("actionable"), false);

        String setup = asString(m.get("setup"));
        if (setup == null || setup.isEmpty()) setup = "UNKNOWN_SETUP";
        List<String> topDrivers = asStringList(m.get("top_drivers"));
        String blockedReason = asString(m.get("blocked_reason"));
        Double distanceAbs = asDouble(m.get("distance_abs"));
        boolean levelRelevant = asBool(m.get("level_relevant"), false);

        return new PaxLevelEdgeModel.Row(label, price, direction, rawDirection,
                                           color, confidence, scoreR, stop, tier,
                                           actionable,
                                           setup, topDrivers, blockedReason,
                                           distanceAbs, levelRelevant);
    }

    private static List<String> asStringList(Object o) {
        if (!(o instanceof List)) return java.util.Collections.emptyList();
        List<?> raw = (List<?>) o;
        if (raw.isEmpty()) return java.util.Collections.emptyList();
        List<String> out = new ArrayList<>(raw.size());
        for (Object item : raw) {
            if (item instanceof String) {
                String s = (String) item;
                if (!s.isEmpty()) out.add(s);
            }
        }
        return out;
    }

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

    private static long asLong(Object o, long fallback) {
        if (o instanceof Number) return ((Number) o).longValue();
        return fallback;
    }

    private static boolean asBool(Object o, boolean fallback) {
        if (o instanceof Boolean) return (Boolean) o;
        return fallback;
    }

    private PaxLevelEdgeSnapshotParser() {}

    // -----------------------------------------------------------------
    // Inlined tokenizer — same shape as PaxHeatwaveSnapshotParser.Tokenizer
    // but self-contained so this parser has no cross-class dependency.
    // -----------------------------------------------------------------
    private static final class Tokenizer {
        private final String src;
        private int i;
        Tokenizer(String src) { this.src = src; this.i = 0; }

        Object parseValue(boolean expectEnd) {
            skipWs();
            Object v = readValue();
            if (expectEnd) {
                skipWs();
                if (i != src.length())
                    throw new RuntimeException("trailing characters at position " + i);
            }
            return v;
        }

        private Object readValue() {
            skipWs();
            if (i >= src.length()) throw new RuntimeException("unexpected end");
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
                if (peek() != '"') throw new RuntimeException("expected key at " + i);
                String key = readString();
                skipWs(); expect(':');
                Object value = readValue();
                m.put(key, value);
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
                list.add(readValue());
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
                    char e = src.charAt(i++);
                    switch (e) {
                        case '"': sb.append('"'); break;
                        case '\\': sb.append('\\'); break;
                        case '/': sb.append('/'); break;
                        case 'b': sb.append('\b'); break;
                        case 'f': sb.append('\f'); break;
                        case 'n': sb.append('\n'); break;
                        case 'r': sb.append('\r'); break;
                        case 't': sb.append('\t'); break;
                        case 'u':
                            if (i + 4 > src.length())
                                throw new RuntimeException("bad unicode escape at " + i);
                            String hex = src.substring(i, i + 4);
                            i += 4;
                            try { sb.append((char) Integer.parseInt(hex, 16)); }
                            catch (NumberFormatException nfe) {
                                throw new RuntimeException("bad unicode escape \\u" + hex);
                            }
                            break;
                        default: throw new RuntimeException("unknown escape \\" + e);
                    }
                } else {
                    sb.append(c);
                }
            }
            throw new RuntimeException("unterminated string");
        }

        private Object readNumber() {
            int start = i;
            if (peek() == '-') i++;
            while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') i++;
            boolean isFloat = false;
            if (i < src.length() && src.charAt(i) == '.') {
                isFloat = true; i++;
                while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') i++;
            }
            if (i < src.length() && (src.charAt(i) == 'e' || src.charAt(i) == 'E')) {
                isFloat = true; i++;
                if (i < src.length() && (src.charAt(i) == '+' || src.charAt(i) == '-')) i++;
                while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') i++;
            }
            String s = src.substring(start, i);
            try {
                if (isFloat) return Double.parseDouble(s);
                return (double) Long.parseLong(s);
            } catch (NumberFormatException nfe) {
                throw new RuntimeException("bad number '" + s + "'");
            }
        }

        private Boolean readBool() {
            if (src.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            if (src.startsWith("false", i)) { i += 5; return Boolean.FALSE; }
            throw new RuntimeException("expected true/false at " + i);
        }

        private Object readNull() {
            if (src.startsWith("null", i)) { i += 4; return null; }
            throw new RuntimeException("expected null at " + i);
        }

        private void skipWs() {
            while (i < src.length()) {
                char c = src.charAt(i);
                if (c == ' ' || c == '\t' || c == '\n' || c == '\r') i++;
                else break;
            }
        }

        private char peek() {
            if (i >= src.length()) throw new RuntimeException("unexpected end at " + i);
            return src.charAt(i);
        }

        private void expect(char c) {
            if (i >= src.length() || src.charAt(i) != c) {
                throw new RuntimeException("expected '" + c + "' at " + i);
            }
            i++;
        }
    }
}
