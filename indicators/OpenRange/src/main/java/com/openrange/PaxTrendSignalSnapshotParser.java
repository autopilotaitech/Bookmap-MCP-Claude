package com.openrange;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Parses the dashboard's {@code /api/snapshot} JSON and extracts the
 * {@code trend_signal} block. Self-contained tokenizer (no third-party
 * JSON dependency), same pattern as {@link PaxHeatwaveSnapshotParser}.
 *
 * <p>Missing / null / unknown-kind values resolve to {@link PaxTrendSignalModel.Kind#NONE}
 * — the dashboard side is the single source of truth for trend mapping;
 * the painter must never invent a kind on its own.</p>
 */
final class PaxTrendSignalSnapshotParser {

    static final class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;
        ParseException(String message) { super(message); }
        ParseException(String message, Throwable cause) { super(message, cause); }
    }

    private PaxTrendSignalSnapshotParser() {}

    static PaxTrendSignalModel parse(String json, long fetchedAtMs) {
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
        return distill((Map<?, ?>) root, fetchedAtMs);
    }

    private static PaxTrendSignalModel distill(Map<?, ?> root, long fetchedAtMs) {
        // Hard gate: only emit a renderable kind when the dashboard says
        // health=ok. If the dashboard returned an offline payload, the
        // trend_signal block is absent OR stale — refuse to render.
        Object healthObj = root.get("health");
        if (healthObj instanceof String && !"ok".equalsIgnoreCase((String) healthObj)) {
            return PaxTrendSignalModel.none(fetchedAtMs);
        }
        PaxTrendSignalModel paxMarker = paxDecisionMarker(root, fetchedAtMs);
        if (paxMarker != null) {
            return paxMarker;
        }
        Map<?, ?> ts = asMap(root.get("trend_signal"));
        if (ts == null) {
            // Missing entirely (older dashboard, error path) → NONE.
            return PaxTrendSignalModel.none(fetchedAtMs);
        }
        String kindStr = asString(ts.get("kind"));
        PaxTrendSignalModel.Kind kind = PaxTrendSignalModel.Kind.from(kindStr);
        String alias = asString(ts.get("alias"));
        Double midObj = asDouble(ts.get("mid"));
        double mid = (midObj == null) ? Double.NaN : midObj.doubleValue();
        long eventMs = asLong(ts.get("eventMs"), 0L);
        long asOfMs = asLong(ts.get("asOfMs"), 0L);
        long bucketEnteredMs = asLong(ts.get("bucketEnteredMs"), 0L);
        boolean changed = Boolean.TRUE.equals(ts.get("changedSinceLastTick"));

        // New plot-eligibility fields. Missing `eligible` defaults to FALSE
        // for safety — an old or partial dashboard payload that omits the
        // field cannot accidentally trigger a triangle emit. The painter
        // gates on this in PaxTrendTriangleDedup.shouldEmit.
        boolean eligible = Boolean.TRUE.equals(ts.get("eligible"));
        String blockedReason = asString(ts.get("blockedReason"));
        String eventMsSource = asString(ts.get("eventMsSource"));
        return new PaxTrendSignalModel(kind, alias, mid, eventMs, asOfMs,
                bucketEnteredMs, changed, fetchedAtMs,
                eligible, blockedReason, eventMsSource);
    }

    // ─── Shape helpers ─────────────────────────────────────────────────────

    private static PaxTrendSignalModel paxDecisionMarker(Map<?, ?> root, long fetchedAtMs) {
        Map<?, ?> pax = asMap(root.get("pax"));
        if (pax == null) {
            return null;
        }
        String decision = asString(pax.get("decision"));
        if (decision == null || !decision.startsWith("ENTER_")) {
            return null;
        }
        String sizeTier = asString(pax.get("size_tier"));
        if (!"FULL".equals(sizeTier) && !"HALF".equals(sizeTier)) {
            return null;
        }
        Double entry = asDouble(pax.get("entry"));
        if (entry == null || entry.doubleValue() <= 0.0) {
            return null;
        }
        boolean isLong = decision.contains("LONG");
        boolean isShort = decision.contains("SHORT");
        if (!isLong && !isShort) {
            return null;
        }
        boolean strong = "FULL".equals(sizeTier);
        PaxTrendSignalModel.Kind kind = isLong
                ? (strong ? PaxTrendSignalModel.Kind.STRONG_BULL : PaxTrendSignalModel.Kind.WEAK_BULL)
                : (strong ? PaxTrendSignalModel.Kind.STRONG_BEAR : PaxTrendSignalModel.Kind.WEAK_BEAR);
        String alias = asString(root.get("alias"));
        String level = asString(pax.get("level_label"));
        String key = decision + "|" + (level == null ? "" : level) + "|" + sizeTier;
        long bucket = Integer.toUnsignedLong(key.hashCode());
        return new PaxTrendSignalModel(kind, alias, entry.doubleValue(),
                fetchedAtMs, fetchedAtMs, bucket, true, fetchedAtMs,
                true, decision, "pax_decision");
    }

    private static Map<?, ?> asMap(Object o) {
        return (o instanceof Map) ? (Map<?, ?>) o : null;
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

    private static long asLong(Object o, long defaultValue) {
        if (o instanceof Number) {
            double v = ((Number) o).doubleValue();
            if (Double.isNaN(v) || Double.isInfinite(v)) return defaultValue;
            return (long) v;
        }
        return defaultValue;
    }

    // ─── Tokenizer (recursive descent, embedded — same pattern as Heatwave) ───

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
                    throw new RuntimeException("trailing characters at position " + i);
                }
            }
            return v;
        }

        private Object readValue() {
            skipWs();
            if (i >= src.length()) {
                throw new RuntimeException("unexpected end of input");
            }
            char c = src.charAt(i);
            if (c == '{') return readObject();
            if (c == '[') return readArray();
            if (c == '"') return readString();
            if (c == 't' || c == 'f') return readBool();
            if (c == 'n') return readNull();
            if (c == '-' || (c >= '0' && c <= '9')) return readNumber();
            throw new RuntimeException("unexpected char '" + c + "' at position " + i);
        }

        private Map<String, Object> readObject() {
            expect('{');
            LinkedHashMap<String, Object> m = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') { i++; return m; }
            while (true) {
                skipWs();
                if (peek() != '"') {
                    throw new RuntimeException("expected object key at position " + i);
                }
                String key = readString();
                skipWs();
                expect(':');
                Object value = readValue();
                m.put(key, value);
                skipWs();
                char c = peek();
                if (c == ',') { i++; continue; }
                if (c == '}') { i++; return m; }
                throw new RuntimeException("expected ',' or '}' at position " + i);
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
                throw new RuntimeException("expected ',' or ']' at position " + i);
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
                            if (i + 4 > src.length()) throw new RuntimeException("bad unicode escape");
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
            if (src.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
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
                long l = Long.parseLong(s);
                return l;
            } catch (NumberFormatException e) {
                throw new RuntimeException("bad number '" + s + "'", e);
            }
        }

        private boolean isNumberChar(char c) {
            return c == '+' || c == '-' || c == '.' || (c >= '0' && c <= '9') || c == 'e' || c == 'E';
        }

        private void expect(char want) {
            if (i >= src.length() || src.charAt(i) != want) {
                throw new RuntimeException("expected '" + want + "' at position " + i);
            }
            i++;
        }

        private char peek() {
            if (i >= src.length()) throw new RuntimeException("unexpected end of input");
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
