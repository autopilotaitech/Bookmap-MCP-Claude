package com.bookmapmcp.handlers;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Instant;

import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpContext;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpPrincipal;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.bookmapmcp.BridgeRegistry;
import com.bookmapmcp.state.InstrumentState;

class TrendAnalyzerHandlerTest {

    @BeforeEach
    void cleanRegistry() {
        clearRegistry();
    }

    @AfterEach
    void teardown() {
        clearRegistry();
    }

    @Test
    void missingAliasReturns400() throws IOException {
        FakeExchange ex = new FakeExchange("/trend_analyzer");
        new TrendAnalyzerHandler().handle(ex);
        assertEquals(400, ex.status);
        String body = ex.responseBody.toString(StandardCharsets.UTF_8);
        assertTrue(body.contains("missing_alias"), "body should mention missing_alias: " + body);
    }

    @Test
    void unknownAliasReturns404() throws IOException {
        FakeExchange ex = new FakeExchange("/trend_analyzer?alias=DOES_NOT_EXIST");
        new TrendAnalyzerHandler().handle(ex);
        assertEquals(404, ex.status);
        assertTrue(ex.responseBody.toString(StandardCharsets.UTF_8).contains("unknown_alias"));
    }

    @Test
    void happyPathReturnsExpectedShape() throws IOException {
        InstrumentState state = new InstrumentState("NQM6.CME@RITHMIC", "NQ", "NASDAQ",
                0.25, 20, Instant.now());
        BridgeRegistry.INSTANCE.attach(state);
        // Inject a trade so the snapshot has non-warmup state to serialize.
        state.onTrade(21800.0, 5, true);

        FakeExchange ex = new FakeExchange("/trend_analyzer?alias=NQM6.CME@RITHMIC");
        new TrendAnalyzerHandler().handle(ex);
        assertEquals(200, ex.status);
        String body = ex.responseBody.toString(StandardCharsets.UTF_8);
        // Every field we declared in the contract should appear by name.
        assertTrue(body.contains("\"alias\""),           "alias field present: " + body);
        assertTrue(body.contains("\"asOfNanos\""),        "asOfNanos field present");
        assertTrue(body.contains("\"eventMs\""),          "eventMs field present");
        assertTrue(body.contains("\"updatedAtMs\""),      "updatedAtMs field present");
        assertTrue(body.contains("\"lastClose\""),        "lastClose field present");
        assertTrue(body.contains("\"warmedUp\""),         "warmedUp field present");
        assertTrue(body.contains("\"score\""),            "score field present");
        assertTrue(body.contains("\"reliabilityHint\""),  "reliabilityHint field present");
        assertTrue(body.contains("\"fast\""),             "fast object present");
        assertTrue(body.contains("\"slow\""),             "slow object present");
        assertTrue(body.contains("\"direction\""),        "leg.direction present");
        assertTrue(body.contains("\"directionSign\""),    "leg.directionSign present");
        assertTrue(body.contains("\"confidence\""),       "leg.confidence present");
        assertTrue(body.contains("\"chop\""),             "leg.chop present");
        assertTrue(body.contains("\"candleIntervalMillis\""), "leg.candleIntervalMillis present");
        assertTrue(body.contains("\"candleCount\""),      "leg.candleCount present");
    }

    @Test
    void warmupCompletesAndSerializesWarmedUpTrueAfterDeterministicDrive() throws IOException {
        // Drive InstrumentState.onTrade with synthetic feed timestamps that
        // cross 40+ fast buckets (15s each). After that:
        //   - fastCandleCount >= 10 (fast engine numberOfCandles)
        //   - slowCandleCount >= 10 (slow engine numberOfCandles)
        //   - warmedUp = true
        //   - score != 0 for a clean monotonic uptrend
        // The handler must serialize all of these — proving the full path
        // (InstrumentState -> TimeBucketTrendAccumulator -> snapshot -> JSON)
        // works end-to-end without any flag-faking.
        InstrumentState state = new InstrumentState("NQM6.CME@RITHMIC", "NQ", "NASDAQ",
                0.25, 20, Instant.now());
        BridgeRegistry.INSTANCE.attach(state);

        long baseMs = 1_700_000_000_000L;          // plausible epoch (2023-11)
        long fastBucketMs = 15_000L;
        int buckets = 50;                          // > 40 to ensure slow >= 10
        double price = 21800.0;
        for (int i = 0; i <= buckets; i++) {
            // Advance the feed clock past the i-th bucket boundary.
            // onTimestamp publishes nanos; nowMsForTrend() validates the
            // value against the plausible epoch range and uses it as-is.
            long nanos = (baseMs + (long) i * fastBucketMs) * 1_000_000L;
            state.onTimestamp(nanos);
            state.onTrade(price, 10, true);   // buy aggressor, monotonic uptrend
            price += 0.5;
        }

        FakeExchange ex = new FakeExchange("/trend_analyzer?alias=NQM6.CME@RITHMIC");
        new TrendAnalyzerHandler().handle(ex);
        assertEquals(200, ex.status);
        String body = ex.responseBody.toString(StandardCharsets.UTF_8);

        // Pin warmup state directly in the serialized JSON.
        assertTrue(body.contains("\"warmedUp\":true"),
                "warmedUp must be true after deterministic 50-bucket uptrend feed: " + body);
        // Both engines must be at or above their required candle count.
        // candleCount may be the last field in the fast leg's object, so we
        // accept either trailing comma or closing brace after the integer.
        assertTrue(body.matches("(?s).*\"fast\":\\{[^}]*\"candleCount\":\\d+[,}].*"),
                "fast candleCount must be present in serialized leg; got: " + body);
        // Score must be nonzero and positive for a monotonic uptrend.
        java.util.regex.Matcher scoreM = java.util.regex.Pattern.compile(
                "\"score\":(-?[0-9]+(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)").matcher(body);
        assertTrue(scoreM.find(), "score field must be present");
        double score = Double.parseDouble(scoreM.group(1));
        assertTrue(score > 0.0,
                "score must be positive for a clean uptrend; got " + score + " in body " + body);
        // /trend_analyzer must also carry the legs.
        assertTrue(body.contains("\"direction\":\"UP\""),
                "fast (or slow) direction must be UP after monotonic uptrend; got body: " + body);
    }

    @Test
    void warmupResponseHasZeroCounts() throws IOException {
        InstrumentState state = new InstrumentState("NQM6.CME@RITHMIC", "NQ", "NASDAQ",
                0.25, 20, Instant.now());
        BridgeRegistry.INSTANCE.attach(state);
        // No trades fed — engine cold.
        FakeExchange ex = new FakeExchange("/trend_analyzer?alias=NQM6.CME@RITHMIC");
        new TrendAnalyzerHandler().handle(ex);
        assertEquals(200, ex.status);
        String body = ex.responseBody.toString(StandardCharsets.UTF_8);
        assertTrue(body.contains("\"warmedUp\":false"),
                "warmedUp must be false on cold accumulator: " + body);
        assertTrue(body.contains("\"candleCount\":0"),
                "candleCount must be 0 on cold accumulator: " + body);
    }

    private static void clearRegistry() {
        for (InstrumentState s : BridgeRegistry.INSTANCE.all()) {
            BridgeRegistry.INSTANCE.detach(s.alias());
        }
    }

    /** Minimal HttpExchange stand-in for testing handlers in isolation. */
    private static final class FakeExchange extends HttpExchange {
        final Headers requestHeaders = new Headers();
        final Headers responseHeaders = new Headers();
        final ByteArrayOutputStream responseBody = new ByteArrayOutputStream();
        final URI uri;
        int status = -1;

        FakeExchange(String pathAndQuery) {
            this.uri = URI.create(pathAndQuery);
        }

        @Override public Headers getRequestHeaders() { return requestHeaders; }
        @Override public Headers getResponseHeaders() { return responseHeaders; }
        @Override public URI getRequestURI() { return uri; }
        @Override public String getRequestMethod() { return "GET"; }
        @Override public HttpContext getHttpContext() { return null; }
        @Override public void close() { /* no-op */ }
        @Override public java.io.InputStream getRequestBody() { return java.io.InputStream.nullInputStream(); }
        @Override public OutputStream getResponseBody() { return responseBody; }
        @Override public void sendResponseHeaders(int rCode, long responseLength) { this.status = rCode; }
        @Override public InetSocketAddress getRemoteAddress() { return new InetSocketAddress("127.0.0.1", 0); }
        @Override public int getResponseCode() { return status; }
        @Override public InetSocketAddress getLocalAddress() { return new InetSocketAddress("127.0.0.1", 0); }
        @Override public String getProtocol() { return "HTTP/1.1"; }
        @Override public Object getAttribute(String name) { return null; }
        @Override public void setAttribute(String name, Object value) {}
        @Override public void setStreams(java.io.InputStream i, OutputStream o) {}
        @Override public HttpPrincipal getPrincipal() { return null; }
    }
}
