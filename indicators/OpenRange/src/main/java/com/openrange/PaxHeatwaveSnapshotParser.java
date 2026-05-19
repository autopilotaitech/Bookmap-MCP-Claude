package com.openrange;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.openrange.PaxHeatwaveModel.Row;
import com.openrange.PaxHeatwaveModel.Tone;

final class PaxHeatwaveSnapshotParser {

    static final class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        ParseException(String message) {
            super(message);
        }

        ParseException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    static PaxHeatwaveModel parse(String json, long fetchedAtMs) {
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

    private static PaxHeatwaveModel distill(Map<?, ?> root, long fetchedAtMs) {
        String verdict = pickVerdict(root);
        Tone verdictTone = verdictTone(verdict);

        Map<?, ?> conviction = asMap(root.get("conviction"));
        Map<?, ?> sources = asMap(conviction == null ? null : conviction.get("sourceScores"));
        Map<?, ?> weights = asMap(conviction == null ? null : conviction.get("effectiveWeights"));
        Map<?, ?> reliab = asMap(conviction == null ? null : conviction.get("sourceReliability"));
        Double convScore = asDouble(conviction == null ? null : conviction.get("score"));

        String scoreText = formatHeaderScore(convScore);

        Row[] rows = new Row[PaxHeatwaveModel.ROW_COUNT];
        Map<?, ?> flow = asMap(root.get("flow"));
        rows[0] = orRow(asMap(root.get("or_levels")));
        rows[1] = groupedRow("FLOW", new String[]{"regime", "bias_score"}, sources, weights, reliab,
                flowHint(flow, conviction));
        rows[2] = singleRow("OFI", "flow_ofi", sources, reliab, reliabilityHint(reliab, "flow_ofi"));
        rows[3] = singleRow("CVD", "flow_cvd", sources, reliab, cvdHint(sources));
        rows[4] = singleRow("ABSORB", "level_reaction", sources, reliab, absorbHint(sources, reliab));
        rows[5] = groupedRow("VWAP",
                new String[]{"vwap_dislocation", "vwap_slope", "vwap_or_gate", "anchored_vwap_opening_drive"},
                sources, weights, reliab, vwapHint(asMap(root.get("vwap_bias"))));
        rows[6] = groupedRow("VP", new String[]{"volume_profile", "ib_context"},
                sources, weights, reliab, vpHint(asMap(root.get("vp_bias"))));
        rows[7] = singleRow("PS", "pull_stack", sources, reliab, psHint(sources));
        rows[8] = singleRow("TAPE", "tape_large_lot", sources, reliab, tapeHint(asMap(root.get("tape_flow"))));
        rows[9] = groupedRow("BOOK", new String[]{"orderbook", "lt_liquidity"},
                sources, weights, reliab, bookHint(sources));
        rows[10] = singleRow("MICRO", "micro_events", sources, reliab, microHint(root));

        return new PaxHeatwaveModel(verdict, verdictTone, scoreText, rows, fetchedAtMs, true);
    }

    private static String pickVerdict(Map<?, ?> root) {
        Map<?, ?> pax = asMap(root.get("pax"));
        String fromPax = asString(pax == null ? null : pax.get("decision"));
        if (fromPax != null && !fromPax.isEmpty()) {
            return fromPax;
        }
        Map<?, ?> decision = asMap(root.get("decision"));
        String fromDecision = asString(decision == null ? null : decision.get("decision"));
        if (fromDecision != null && !fromDecision.isEmpty()) {
            return fromDecision;
        }
        return "WAIT";
    }

    private static Tone verdictTone(String verdict) {
        if (verdict == null) {
            return Tone.NEUTRAL;
        }
        String v = verdict.toUpperCase(Locale.ROOT);
        if (v.contains("ENTER_LONG") || v.contains("FOLLOW_LONG") || v.equals("LONG")) {
            return Tone.BULL;
        }
        if (v.contains("ENTER_SHORT") || v.contains("FOLLOW_SHORT") || v.equals("SHORT")) {
            return Tone.BEAR;
        }
        if (v.contains("FADE")) {
            return Tone.AMBER;
        }
        if (v.contains("STAND_DOWN") || v.equals("NO DATA")) {
            return Tone.AMBER;
        }
        return Tone.NEUTRAL;
    }

    private static String formatHeaderScore(Double score) {
        if (score == null || Double.isNaN(score)) {
            return "--";
        }
        int pct = (int) Math.round(score * 100.0);
        if (pct >= 0) {
            return String.format(Locale.ROOT, "+%02d", pct);
        }
        return String.format(Locale.ROOT, "-%02d", -pct);
    }

    private static Row orRow(Map<?, ?> orLevels) {
        if (orLevels == null) {
            return new Row("OR", "--", Tone.NEUTRAL, "no OR");
        }
        Object levelsObj = orLevels.get("levels");
        if (!(levelsObj instanceof List)) {
            return new Row("OR", "--", Tone.NEUTRAL, "no OR");
        }
        List<?> levels = (List<?>) levelsObj;
        Map<?, ?> nearest = null;
        double nearestAbs = Double.POSITIVE_INFINITY;
        for (Object o : levels) {
            if (!(o instanceof Map)) {
                continue;
            }
            Map<?, ?> lvl = (Map<?, ?>) o;
            Double d = asDouble(lvl.get("distance"));
            if (d == null) {
                continue;
            }
            double abs = Math.abs(d);
            if (abs < nearestAbs) {
                nearestAbs = abs;
                nearest = lvl;
            }
        }
        if (nearest == null) {
            return new Row("OR", "--", Tone.NEUTRAL, "no OR");
        }
        String label = compactLevelLabel(asString(nearest.get("label")));
        Double price = asDouble(nearest.get("price"));
        Double dist = asDouble(nearest.get("distance"));
        Double conf = asDouble(nearest.get("confidence"));
        String decision = asString(nearest.get("decision"));

        String priceText = price == null ? "------" : String.format(Locale.ROOT, "%.2f", price);
        String distText = dist == null ? "--p" : (dist >= 0
                ? String.format(Locale.ROOT, "+%.2fp", dist)
                : String.format(Locale.ROOT, "%.2fp", dist));
        String confText = conf == null ? "" : (Math.round(conf * 100.0)) + "%";

        String hint = (priceText + "  " + distText + (confText.isEmpty() ? "" : "  " + confText)).trim();
        String score = decision == null ? "WAIT" : compactDecision(decision);
        return new Row("OR " + label, score, decisionTone(decision), hint);
    }

    private static String compactLevelLabel(String raw) {
        if (raw == null || raw.isEmpty()) {
            return "??";
        }
        String u = raw.toUpperCase(Locale.ROOT);
        if (u.contains("EXT") && u.contains("+")) {
            return "EXT+" + extractInt(u);
        }
        if (u.contains("EXT") && u.contains("-")) {
            return "EXT-" + extractInt(u);
        }
        if (u.contains("HIGH") || u.endsWith("H") || u.contains("ORH")) {
            return "H";
        }
        if (u.contains("LOW") || u.endsWith("L") || u.contains("ORL")) {
            return "L";
        }
        if (u.contains("MID")) {
            return "M";
        }
        return raw.length() > 6 ? raw.substring(0, 6) : raw;
    }

    private static String extractInt(String s) {
        StringBuilder sb = new StringBuilder();
        boolean seenDigit = false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (Character.isDigit(c)) {
                sb.append(c);
                seenDigit = true;
            } else if (seenDigit) {
                break;
            }
        }
        return sb.length() == 0 ? "?" : sb.toString();
    }

    private static String compactDecision(String d) {
        if (d == null) {
            return "WAIT";
        }
        String u = d.toUpperCase(Locale.ROOT);
        if (u.contains("FOLLOW_LONG")) {
            return "FOL L";
        }
        if (u.contains("FOLLOW_SHORT")) {
            return "FOL S";
        }
        if (u.contains("FADE_LONG")) {
            return "FADE L";
        }
        if (u.contains("FADE_SHORT")) {
            return "FADE S";
        }
        if (u.contains("WAIT")) {
            return "WAIT";
        }
        return u.length() > 6 ? u.substring(0, 6) : u;
    }

    private static Tone decisionTone(String d) {
        if (d == null) {
            return Tone.NEUTRAL;
        }
        String u = d.toUpperCase(Locale.ROOT);
        if (u.contains("FOLLOW_LONG")) {
            return Tone.BULL;
        }
        if (u.contains("FOLLOW_SHORT")) {
            return Tone.BEAR;
        }
        if (u.contains("FADE")) {
            return Tone.AMBER;
        }
        return Tone.NEUTRAL;
    }

    private static Row groupedRow(String label, String[] keys, Map<?, ?> sources, Map<?, ?> weights,
            Map<?, ?> reliab, String hint) {
        if (sources == null || weights == null) {
            return new Row(label, "--", Tone.NEUTRAL, hint == null ? "" : hint);
        }
        double sumWS = 0.0;
        double sumW = 0.0;
        int contributing = 0;
        for (String k : keys) {
            Double s = asDouble(sources.get(k));
            Double w = asDouble(weights.get(k));
            Double r = reliab == null ? null : asDouble(reliab.get(k));
            if (s == null || w == null) {
                continue;
            }
            if (r != null && r <= 0.0) {
                continue;
            }
            sumWS += s * w;
            sumW += w;
            contributing++;
        }
        if (contributing == 0 || sumW <= 0.0) {
            return new Row(label, "--", Tone.NEUTRAL, hint == null ? "" : hint);
        }
        double w = sumWS / sumW;
        return new Row(label, formatScore(w), toneForScore(w), hint == null ? "" : hint);
    }

    private static Row singleRow(String label, String key, Map<?, ?> sources, Map<?, ?> reliab, String hint) {
        if (sources == null) {
            return new Row(label, "--", Tone.NEUTRAL, hint == null ? "" : hint);
        }
        Double s = asDouble(sources.get(key));
        Double r = reliab == null ? null : asDouble(reliab.get(key));
        if (s == null) {
            return new Row(label, "--", Tone.NEUTRAL, hint == null ? "" : hint);
        }
        if (r != null && r <= 0.0) {
            return new Row(label, "--", Tone.NEUTRAL, hint == null ? "" : hint);
        }
        return new Row(label, formatScore(s), toneForScore(s), hint == null ? "" : hint);
    }

    private static String formatScore(double v) {
        if (v >= 0) {
            return String.format(Locale.ROOT, "+%.2f", v);
        }
        return String.format(Locale.ROOT, "%.2f", v);
    }

    private static Tone toneForScore(double v) {
        if (v > 0.20) {
            return Tone.BULL;
        }
        if (v < -0.20) {
            return Tone.BEAR;
        }
        return Tone.NEUTRAL;
    }

    private static String flowHint(Map<?, ?> flow, Map<?, ?> conviction) {
        String regime = flow == null ? null : asString(flow.get("regime"));
        if (regime != null && !regime.isEmpty()) {
            return regime;
        }
        if (conviction == null) {
            return "";
        }
        String trend = asString(conviction.get("trend"));
        if (trend != null && !trend.isEmpty()) {
            return trend;
        }
        String traj = asString(conviction.get("trajectory"));
        return traj == null ? "" : traj;
    }

    private static String reliabilityHint(Map<?, ?> reliab, String key) {
        if (reliab == null) {
            return "";
        }
        Double r = asDouble(reliab.get(key));
        if (r == null) {
            return "";
        }
        return "rel " + String.format(Locale.ROOT, "%.2f", r);
    }

    private static String cvdHint(Map<?, ?> sources) {
        Double cvd = sources == null ? null : asDouble(sources.get("flow_cvd"));
        if (cvd == null) {
            return "";
        }
        if (cvd > 0.20) {
            return "buy";
        }
        if (cvd < -0.20) {
            return "sell";
        }
        return "flat";
    }

    private static String absorbHint(Map<?, ?> sources, Map<?, ?> reliab) {
        Double s = sources == null ? null : asDouble(sources.get("level_reaction"));
        Double r = reliab == null ? null : asDouble(reliab.get("level_reaction"));
        if (s == null) {
            return "";
        }
        double mag = Math.abs(s);
        if (mag >= 0.40 && r != null && r >= 0.60) {
            return "hard";
        }
        if (mag >= 0.15) {
            return "soft";
        }
        return "none";
    }

    private static String vwapHint(Map<?, ?> vwapBias) {
        if (vwapBias == null) {
            return "";
        }
        Map<?, ?> components = asMap(vwapBias.get("components"));
        Double sigma = components == null ? null : asDouble(components.get("sigma_z"));
        if (sigma == null) {
            sigma = asDouble(vwapBias.get("sigma_z"));
        }
        String regime = components == null ? null : asString(components.get("regime"));
        if (regime == null || regime.isEmpty()) {
            regime = asString(vwapBias.get("regime"));
        }
        StringBuilder sb = new StringBuilder();
        if (sigma != null && !Double.isNaN(sigma)) {
            sb.append(String.format(Locale.ROOT, "%+.1fs", sigma));
        }
        if (regime != null && !regime.isEmpty()) {
            if (sb.length() > 0) {
                sb.append(' ');
            }
            sb.append(regime);
        }
        return sb.toString();
    }

    private static String vpHint(Map<?, ?> vpBias) {
        if (vpBias == null) {
            return "";
        }
        Map<?, ?> components = asMap(vpBias.get("components"));
        String va = components == null ? null : asString(components.get("va_state"));
        if (va == null || va.isEmpty()) {
            va = asString(vpBias.get("va_state"));
        }
        if (va != null && !va.isEmpty()) {
            return va;
        }
        String label = asString(vpBias.get("label"));
        return label == null ? "" : label;
    }

    private static String psHint(Map<?, ?> sources) {
        Double s = sources == null ? null : asDouble(sources.get("pull_stack"));
        if (s == null) {
            return "";
        }
        if (s > 0.20) {
            return "rot up";
        }
        if (s < -0.20) {
            return "rot down";
        }
        return "rot flat";
    }

    private static String tapeHint(Map<?, ?> tapeFlow) {
        if (tapeFlow == null) {
            return "";
        }
        String label = asString(tapeFlow.get("deltaLabel"));
        return label == null ? "" : label.toLowerCase(Locale.ROOT);
    }

    private static String bookHint(Map<?, ?> sources) {
        Double ob = sources == null ? null : asDouble(sources.get("orderbook"));
        Double lt = sources == null ? null : asDouble(sources.get("lt_liquidity"));
        double sum = 0.0;
        int n = 0;
        if (ob != null) { sum += ob; n++; }
        if (lt != null) { sum += lt; n++; }
        if (n == 0) {
            return "";
        }
        double avg = sum / n;
        if (avg > 0.15) {
            return "bid lean";
        }
        if (avg < -0.15) {
            return "ask lean";
        }
        return "balanced";
    }

    private static String microHint(Map<?, ?> root) {
        Object micro = root.get("micro_events");
        if (micro instanceof Map) {
            Map<?, ?> m = (Map<?, ?>) micro;
            String last = asString(m.get("lastEvent"));
            if (last != null && !last.isEmpty()) {
                return last.toLowerCase(Locale.ROOT);
            }
            Object events = m.get("events");
            if (events instanceof List && !((List<?>) events).isEmpty()) {
                Object first = ((List<?>) events).get(0);
                if (first instanceof Map) {
                    String kind = asString(((Map<?, ?>) first).get("kind"));
                    if (kind != null && !kind.isEmpty()) {
                        return kind.toLowerCase(Locale.ROOT);
                    }
                }
                return "active";
            }
        }
        return "none";
    }

    private static Map<?, ?> asMap(Object o) {
        return (o instanceof Map) ? (Map<?, ?>) o : null;
    }

    private static String asString(Object o) {
        if (o instanceof String) {
            return (String) o;
        }
        return null;
    }

    private static Double asDouble(Object o) {
        if (o instanceof Number) {
            double v = ((Number) o).doubleValue();
            if (Double.isNaN(v) || Double.isInfinite(v)) {
                return null;
            }
            return v;
        }
        if (o instanceof Boolean) {
            return ((Boolean) o) ? 1.0 : 0.0;
        }
        return null;
    }

    private PaxHeatwaveSnapshotParser() {
    }

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
            if (c == '{') {
                return readObject();
            }
            if (c == '[') {
                return readArray();
            }
            if (c == '"') {
                return readString();
            }
            if (c == 't' || c == 'f') {
                return readBool();
            }
            if (c == 'n') {
                return readNull();
            }
            if (c == '-' || (c >= '0' && c <= '9')) {
                return readNumber();
            }
            throw new RuntimeException("unexpected char '" + c + "' at position " + i);
        }

        private Map<String, Object> readObject() {
            expect('{');
            LinkedHashMap<String, Object> m = new LinkedHashMap<>();
            skipWs();
            if (peek() == '}') {
                i++;
                return m;
            }
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
                if (c == ',') {
                    i++;
                    continue;
                }
                if (c == '}') {
                    i++;
                    return m;
                }
                throw new RuntimeException("expected ',' or '}' at position " + i);
            }
        }

        private List<Object> readArray() {
            expect('[');
            ArrayList<Object> list = new ArrayList<>();
            skipWs();
            if (peek() == ']') {
                i++;
                return list;
            }
            while (true) {
                Object v = readValue();
                list.add(v);
                skipWs();
                char c = peek();
                if (c == ',') {
                    i++;
                    continue;
                }
                if (c == ']') {
                    i++;
                    return list;
                }
                throw new RuntimeException("expected ',' or ']' at position " + i);
            }
        }

        private String readString() {
            expect('"');
            StringBuilder sb = new StringBuilder();
            while (i < src.length()) {
                char c = src.charAt(i++);
                if (c == '"') {
                    return sb.toString();
                }
                if (c == '\\') {
                    if (i >= src.length()) {
                        throw new RuntimeException("bad escape at end of input");
                    }
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
                            if (i + 4 > src.length()) {
                                throw new RuntimeException("bad unicode escape at position " + i);
                            }
                            String hex = src.substring(i, i + 4);
                            i += 4;
                            try {
                                sb.append((char) Integer.parseInt(hex, 16));
                            } catch (NumberFormatException nfe) {
                                throw new RuntimeException("bad unicode escape \\u" + hex);
                            }
                            break;
                        default:
                            throw new RuntimeException("unknown escape \\" + e);
                    }
                } else {
                    sb.append(c);
                }
            }
            throw new RuntimeException("unterminated string starting at position " + i);
        }

        private Object readNumber() {
            int start = i;
            if (peek() == '-') {
                i++;
            }
            while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') {
                i++;
            }
            boolean isFloat = false;
            if (i < src.length() && src.charAt(i) == '.') {
                isFloat = true;
                i++;
                while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') {
                    i++;
                }
            }
            if (i < src.length() && (src.charAt(i) == 'e' || src.charAt(i) == 'E')) {
                isFloat = true;
                i++;
                if (i < src.length() && (src.charAt(i) == '+' || src.charAt(i) == '-')) {
                    i++;
                }
                while (i < src.length() && src.charAt(i) >= '0' && src.charAt(i) <= '9') {
                    i++;
                }
            }
            String s = src.substring(start, i);
            try {
                if (isFloat) {
                    return Double.parseDouble(s);
                }
                long l = Long.parseLong(s);
                return (double) l;
            } catch (NumberFormatException nfe) {
                throw new RuntimeException("bad number '" + s + "'");
            }
        }

        private Boolean readBool() {
            if (src.startsWith("true", i)) {
                i += 4;
                return Boolean.TRUE;
            }
            if (src.startsWith("false", i)) {
                i += 5;
                return Boolean.FALSE;
            }
            throw new RuntimeException("expected boolean at position " + i);
        }

        private Object readNull() {
            if (src.startsWith("null", i)) {
                i += 4;
                return null;
            }
            throw new RuntimeException("expected null at position " + i);
        }

        private void skipWs() {
            while (i < src.length()) {
                char c = src.charAt(i);
                if (c == ' ' || c == '\n' || c == '\r' || c == '\t') {
                    i++;
                } else {
                    return;
                }
            }
        }

        private char peek() {
            if (i >= src.length()) {
                throw new RuntimeException("unexpected end of input");
            }
            return src.charAt(i);
        }

        private void expect(char ch) {
            skipWs();
            if (i >= src.length() || src.charAt(i) != ch) {
                throw new RuntimeException("expected '" + ch + "' at position " + i);
            }
            i++;
        }
    }
}
