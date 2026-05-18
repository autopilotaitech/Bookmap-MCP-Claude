package com.bookmapmcp;

import static org.junit.jupiter.api.Assertions.assertEquals;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.net.URI;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;

import com.sun.net.httpserver.Headers;
import com.sun.net.httpserver.HttpContext;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpPrincipal;

import org.junit.jupiter.api.Test;

class BridgeAuthTest {

    @Test
    void rejectsMissingToken() throws IOException {
        AtomicBoolean delegated = new AtomicBoolean(false);
        FakeExchange ex = new FakeExchange(null);
        new BridgeAuth("good-token").guard(e -> delegated.set(true)).handle(ex);
        assertEquals(401, ex.status);
        assertEquals(false, delegated.get());
    }

    @Test
    void rejectsWrongToken() throws IOException {
        AtomicBoolean delegated = new AtomicBoolean(false);
        FakeExchange ex = new FakeExchange("bad-token");
        new BridgeAuth("good-token").guard(e -> delegated.set(true)).handle(ex);
        assertEquals(401, ex.status);
        assertEquals(false, delegated.get());
    }

    @Test
    void allowsCorrectToken() throws IOException {
        AtomicBoolean delegated = new AtomicBoolean(false);
        FakeExchange ex = new FakeExchange("good-token");
        new BridgeAuth("good-token").guard(e -> delegated.set(true)).handle(ex);
        assertEquals(true, delegated.get());
    }

    /** Minimal HttpExchange stand-in for testing the auth wrapper in isolation. */
    private static final class FakeExchange extends HttpExchange {
        final Headers requestHeaders = new Headers();
        final Headers responseHeaders = new Headers();
        final ByteArrayOutputStream responseBody = new ByteArrayOutputStream();
        int status = -1;

        FakeExchange(String token) {
            if (token != null) {
                requestHeaders.put(BridgeAuth.HEADER, List.of(token));
            }
        }

        @Override public Headers getRequestHeaders() { return requestHeaders; }
        @Override public Headers getResponseHeaders() { return responseHeaders; }
        @Override public URI getRequestURI() { return URI.create("/test"); }
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
