package com.bookmapmcp.handlers;

import java.awt.AWTException;
import java.awt.GraphicsEnvironment;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;

import javax.imageio.ImageIO;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpHandler;

/**
 * GET /screenshot
 * Returns a PNG of the primary display. Bookmap is usually maximized so this
 * captures the chart Claude is asking about. UI control (instrument switch,
 * timeframe) is a separate, larger piece of work — not in this build.
 */
public final class ScreenshotHandler implements HttpHandler {

    @Override
    public void handle(HttpExchange exchange) throws IOException {
        if (!Http.requireGet(exchange)) return;
        try {
            Rectangle screen = GraphicsEnvironment.getLocalGraphicsEnvironment()
                    .getDefaultScreenDevice().getDefaultConfiguration().getBounds();
            BufferedImage img = new Robot().createScreenCapture(screen);
            ByteArrayOutputStream png = new ByteArrayOutputStream();
            ImageIO.write(img, "png", png);
            byte[] bytes = png.toByteArray();
            exchange.getResponseHeaders().add("Content-Type", "image/png");
            // Bust every caching layer: browser, MCP transport, intermediaries.
            // The screen state is live and must reflect the current Bookmap UI.
            exchange.getResponseHeaders().add("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0, private");
            exchange.getResponseHeaders().add("Pragma", "no-cache");
            exchange.getResponseHeaders().add("Expires", "0");
            exchange.getResponseHeaders().add("ETag", "\"" + System.nanoTime() + "\"");
            exchange.sendResponseHeaders(200, bytes.length);
            try (OutputStream os = exchange.getResponseBody()) {
                os.write(bytes);
            }
        } catch (AWTException e) {
            Http.writeJsonError(exchange, 500, "screenshot_failed", e.getMessage());
        }
    }
}
