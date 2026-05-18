package com.bookmapmcp;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

class JsonWriterTest {

    @Test
    void writesObjectWithMixedTypes() {
        String body = new JsonWriter()
                .beginObject()
                .prop("ok", true)
                .prop("count", 3L)
                .prop("ratio", 1.5)
                .prop("name", "ESH6")
                .endObject()
                .build();
        assertEquals("{\"ok\":true,\"count\":3,\"ratio\":1.5,\"name\":\"ESH6\"}", body);
    }

    @Test
    void escapesQuotesAndBackslashes() {
        String body = new JsonWriter()
                .beginObject()
                .prop("alias", "a\"b\\c")
                .endObject()
                .build();
        assertEquals("{\"alias\":\"a\\\"b\\\\c\"}", body);
    }

    @Test
    void nonFiniteDoubleBecomesNull() {
        String body = new JsonWriter()
                .beginObject()
                .prop("p", Double.NaN)
                .endObject()
                .build();
        assertTrue(body.contains("\"p\":null"), body);
    }

    @Test
    void emptyObjectAndArray() {
        assertEquals("{}", new JsonWriter().beginObject().endObject().build());
        assertEquals("[]", new JsonWriter().beginArray().endArray().build());
    }
}
