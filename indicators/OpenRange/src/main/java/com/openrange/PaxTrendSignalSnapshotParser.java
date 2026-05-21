package com.openrange;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Parses the dashboard's {@code /api/snapshot} JSON and extracts the
 * authoritative buy/sell entry source: {@code snap["institutional_signals"]}.
 *
 * <p>The parser walks the institutional-signal array and emits a
 * {@link PaxTrendSignalModel} only when a {@code PAY_FOR_TRADE} signal with
 * direction {@code LONG} or {@code SHORT} exists. Mapping:</p>
 * <ul>
 *   <li>LONG + (FULL or confidence&gt;=0.70) -&gt; STRONG_BULL; else WEAK_BULL.</li>
 *   <li>SHORT + (FULL or confidence&gt;=0.70) -&gt; STRONG_BEAR; else WEAK_BEAR.</li>
 *   <li>{@code signal.price} -&gt; model {@code mid} (chart anchor at the level).</li>
 *   <li>{@code signal.timestamp_ms} -&gt; model {@code eventMs}.</li>
 *   <li>{@code signal.id} -&gt; bucketEnteredMs (hash) for dedup.</li>
 *   <li>{@code eventMsSource} = {@code "institutional_signal"}.</li>
 * </ul>
 *
 * <p>No fallback to {@code trend_signal} or {@code pax.decision} for entry
 * markers — those are generic trend bias, not level-anchored institutional
 * signals. If no PAY_FOR_TRADE signal is present the parser returns
 * {@link PaxTrendSignalModel#none(long)}.</p>
 *
 * <p>Self-contained tokenizer (no third-party JSON dependency), same pattern
 * as {@link PaxHeatwaveSnapshotParser}.</p>
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

    /** Parse the full evidence-trail array from
     *  {@code snap["institutional_chart_events"]}.
     *
     *  <p>Returns empty list on missing key / malformed JSON / non-ok
     *  health. Never throws — chart painter must continue rendering prior
     *  history even on a bad payload.</p>
     */
    static List<PaxInstitutionalChartEvent> parseChartEvents(String json) {
        if (json == null || json.isEmpty()) {
            return java.util.Collections.emptyList();
        }
        Object root;
        try {
            root = new Tokenizer(json).parseValue(true);
        } catch (RuntimeException e) {
            return java.util.Collections.emptyList();
        }
        if (!(root instanceof Map)) {
            return java.util.Collections.emptyList();
        }
        Map<?, ?> rootMap = (Map<?, ?>) root;
        Object healthObj = rootMap.get("health");
        if (healthObj instanceof String && !"ok".equalsIgnoreCase((String) healthObj)) {
            return java.util.Collections.emptyList();
        }
        Object evsObj = rootMap.get("institutional_chart_events");
        if (!(evsObj instanceof List)) {
            return java.util.Collections.emptyList();
        }
        List<?> evs = (List<?>) evsObj;
        ArrayList<PaxInstitutionalChartEvent> out = new ArrayList<>(evs.size());
        for (Object o : evs) {
            if (!(o instanceof Map)) continue;
            Map<?, ?> e = (Map<?, ?>) o;
            String id = asString(e.get("id"));
            String alias = asString(e.get("alias"));
            String label = asString(e.get("label"));
            Double priceObj = asDouble(e.get("price"));
            String side = asString(e.get("side"));
            String eventType = asString(e.get("event_type"));
            String direction = asString(e.get("direction"));
            String executionRead = asString(e.get("execution_read"));
            String markerText = asString(e.get("marker_text"));
            String markerColorHint = asString(e.get("marker_color_hint"));
            String severity = asString(e.get("severity"));
            long timestampMs = asLong(e.get("timestamp_ms"), 0L);
            String source = asString(e.get("source"));
            Double confObj = asDouble(e.get("confidence"));
            double price = priceObj == null ? Double.NaN : priceObj.doubleValue();
            double confidence = confObj == null ? Double.NaN : confObj.doubleValue();
            out.add(new PaxInstitutionalChartEvent(
                    id, alias, label, price, side, eventType, direction,
                    executionRead, markerText, markerColorHint, severity,
                    timestampMs, source, confidence));
        }
        return out;
    }

    /** Parse ALL institutional signal events from {@code snap["institutional_signals"]}.
     *
     * <p>Returns an empty list when {@code institutional_signals} is missing,
     * empty, malformed, or when the dashboard reports a non-ok health. The
     * fetcher feeds this list to {@link PaxInstitutionalSignalsHistory#merge}
     * on every poll; the history layer is what survives empty polls — the
     * parser itself is stateless.</p>
     *
     * <p>This method does NOT throw on missing fields; it simply skips
     * malformed entries. The painter then drops entries that aren't
     * renderable ({@link PaxInstitutionalSignalEvent#isRenderable()}).</p>
     */
    static List<PaxInstitutionalSignalEvent> parseInstitutionalEvents(String json) {
        if (json == null || json.isEmpty()) {
            return java.util.Collections.emptyList();
        }
        Object root;
        try {
            root = new Tokenizer(json).parseValue(true);
        } catch (RuntimeException e) {
            return java.util.Collections.emptyList();
        }
        if (!(root instanceof Map)) {
            return java.util.Collections.emptyList();
        }
        Map<?, ?> rootMap = (Map<?, ?>) root;
        Object healthObj = rootMap.get("health");
        if (healthObj instanceof String && !"ok".equalsIgnoreCase((String) healthObj)) {
            return java.util.Collections.emptyList();
        }
        Object sigsObj = rootMap.get("institutional_signals");
        if (!(sigsObj instanceof List)) {
            return java.util.Collections.emptyList();
        }
        List<?> sigs = (List<?>) sigsObj;
        ArrayList<PaxInstitutionalSignalEvent> out = new ArrayList<>(sigs.size());
        for (Object o : sigs) {
            if (!(o instanceof Map)) continue;
            Map<?, ?> s = (Map<?, ?>) o;
            String id = asString(s.get("id"));
            String signalType = asString(s.get("signal_type"));
            String direction = asString(s.get("direction"));
            String executionRead = asString(s.get("execution_read"));
            String label = asString(s.get("label"));
            Double priceObj = asDouble(s.get("price"));
            Double confObj = asDouble(s.get("confidence"));
            long timestampMs = asLong(s.get("timestamp_ms"), 0L);
            double price = priceObj == null ? Double.NaN : priceObj.doubleValue();
            double confidence = confObj == null ? Double.NaN : confObj.doubleValue();
            out.add(new PaxInstitutionalSignalEvent(
                    id, signalType, direction, executionRead,
                    price, timestampMs, label, confidence));
        }
        return out;
    }

    private static PaxTrendSignalModel distill(Map<?, ?> root, long fetchedAtMs) {
        // Hard gate: only emit a renderable kind when the dashboard says
        // health=ok. If the dashboard returned an offline payload, refuse
        // to render.
        Object healthObj = root.get("health");
        if (healthObj instanceof String && !"ok".equalsIgnoreCase((String) healthObj)) {
            return PaxTrendSignalModel.none(fetchedAtMs);
        }
        // Authoritative source: snap["institutional_signals"]. No fallback
        // to trend_signal or pax.decision for entry markers — those are
        // generic trend bias, not level-anchored institutional signals.
        PaxTrendSignalModel ins = institutionalSignalMarker(root, fetchedAtMs);
        if (ins != null) {
            return ins;
        }
        return PaxTrendSignalModel.none(fetchedAtMs);
    }

    /** Walk snap["institutional_signals"] and pick the most recent
     *  PAY_FOR_TRADE LONG/SHORT entry. Returns null when no such signal
     *  exists, the array is absent / malformed, or the chosen signal has
     *  no usable price / timestamp. */
    private static PaxTrendSignalModel institutionalSignalMarker(Map<?, ?> root, long fetchedAtMs) {
        Object sigsObj = root.get("institutional_signals");
        if (!(sigsObj instanceof List)) {
            return null;
        }
        List<?> sigs = (List<?>) sigsObj;
        Map<?, ?> best = null;
        long bestTs = Long.MIN_VALUE;
        for (Object o : sigs) {
            if (!(o instanceof Map)) continue;
            Map<?, ?> s = (Map<?, ?>) o;
            if (!"PAY_FOR_TRADE".equals(asString(s.get("execution_read")))) continue;
            String dir = asString(s.get("direction"));
            if (!"LONG".equals(dir) && !"SHORT".equals(dir)) continue;
            long ts = asLong(s.get("timestamp_ms"), 0L);
            if (ts >= bestTs) {
                bestTs = ts;
                best = s;
            }
        }
        if (best == null) {
            return null;
        }
        String dir = asString(best.get("direction"));
        String sizeTier = asString(best.get("size_tier"));
        Double conf = asDouble(best.get("confidence"));
        boolean strong = "FULL".equals(sizeTier)
                || (conf != null && conf.doubleValue() >= 0.70);
        PaxTrendSignalModel.Kind kind;
        if ("LONG".equals(dir)) {
            kind = strong ? PaxTrendSignalModel.Kind.STRONG_BULL
                          : PaxTrendSignalModel.Kind.WEAK_BULL;
        } else {
            kind = strong ? PaxTrendSignalModel.Kind.STRONG_BEAR
                          : PaxTrendSignalModel.Kind.WEAK_BEAR;
        }
        Double price = asDouble(best.get("price"));
        if (price == null || price.doubleValue() <= 0.0) {
            return null;
        }
        String alias = asString(root.get("alias"));
        if (alias == null) {
            alias = asString(best.get("alias"));
        }
        String id = asString(best.get("id"));
        long bucketEnteredMs = (id == null || id.isEmpty())
                ? bestTs
                : Integer.toUnsignedLong(id.hashCode());
        long eventMs = bestTs > 0 ? bestTs : fetchedAtMs;
        return new PaxTrendSignalModel(kind, alias == null ? "" : alias,
                price.doubleValue(), eventMs, fetchedAtMs, bucketEnteredMs,
                true, fetchedAtMs,
                /*eligible=*/true,
                /*blockedReason=*/dir,
                /*eventMsSource=*/"institutional_signal");
    }

    // ─── Shape helpers ─────────────────────────────────────────────────────

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
