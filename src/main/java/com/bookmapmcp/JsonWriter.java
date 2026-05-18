package com.bookmapmcp;

/**
 * Minimal hand-rolled JSON encoder. Stays dep-free; we'll upgrade to Gson or
 * Jackson when the response shapes get richer (orderbook, trades, etc.).
 */
public final class JsonWriter {

    private final StringBuilder buf = new StringBuilder(128);

    public JsonWriter beginObject() {
        buf.append('{');
        return this;
    }

    public JsonWriter endObject() {
        trimTrailingComma();
        buf.append('}');
        buf.append(',');
        return this;
    }

    public JsonWriter beginArray() {
        buf.append('[');
        return this;
    }

    public JsonWriter endArray() {
        trimTrailingComma();
        buf.append(']');
        buf.append(',');
        return this;
    }

    public JsonWriter prop(String name, String value) {
        appendKey(name);
        appendString(value);
        buf.append(',');
        return this;
    }

    public JsonWriter prop(String name, boolean value) {
        appendKey(name);
        buf.append(value);
        buf.append(',');
        return this;
    }

    public JsonWriter prop(String name, long value) {
        appendKey(name);
        buf.append(value);
        buf.append(',');
        return this;
    }

    public JsonWriter prop(String name, double value) {
        appendKey(name);
        if (Double.isFinite(value)) {
            buf.append(value);
        } else {
            // JSON has no NaN/Infinity; emit null and let callers handle it.
            buf.append("null");
        }
        buf.append(',');
        return this;
    }

    public JsonWriter rawProp(String name, String rawJson) {
        appendKey(name);
        buf.append(rawJson);
        buf.append(',');
        return this;
    }

    public String build() {
        trimTrailingComma();
        return buf.toString();
    }

    private void appendKey(String name) {
        appendString(name);
        buf.append(':');
    }

    private void appendString(String value) {
        if (value == null) {
            buf.append("null");
            return;
        }
        buf.append('"');
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i);
            switch (c) {
                case '"' -> buf.append("\\\"");
                case '\\' -> buf.append("\\\\");
                case '\n' -> buf.append("\\n");
                case '\r' -> buf.append("\\r");
                case '\t' -> buf.append("\\t");
                case '\b' -> buf.append("\\b");
                case '\f' -> buf.append("\\f");
                default -> {
                    if (c < 0x20) {
                        buf.append(String.format("\\u%04x", (int) c));
                    } else {
                        buf.append(c);
                    }
                }
            }
        }
        buf.append('"');
    }

    private void trimTrailingComma() {
        int len = buf.length();
        if (len > 0 && buf.charAt(len - 1) == ',') {
            buf.setLength(len - 1);
        }
    }
}
