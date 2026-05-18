package com.bookmapmcp;

import java.io.IOException;
import java.io.InputStream;
import java.net.BindException;
import java.net.HttpURLConnection;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.logging.Level;
import java.util.logging.Logger;

import com.sun.net.httpserver.HttpServer;

import com.bookmapmcp.handlers.BalanceHandler;
import com.bookmapmcp.handlers.BookDynamicsHandler;
import com.bookmapmcp.handlers.InstrumentsHandler;
import com.bookmapmcp.handlers.LtLiquidityHandler;
import com.bookmapmcp.handlers.MagnetLevelsHandler;
import com.bookmapmcp.handlers.MicrostructureEventsHandler;
import com.bookmapmcp.handlers.MomentumHandler;
import com.bookmapmcp.handlers.OrderbookHandler;
import com.bookmapmcp.handlers.PingHandler;
import com.bookmapmcp.handlers.PositionHandler;
import com.bookmapmcp.handlers.PullStackHandler;
import com.bookmapmcp.handlers.RecentFillsHandler;
import com.bookmapmcp.handlers.RecentTradesHandler;
import com.bookmapmcp.handlers.ScreenshotHandler;
import com.bookmapmcp.handlers.TapeBucketsHandler;
import com.bookmapmcp.handlers.TradingHandler;
import com.bookmapmcp.handlers.VolumeProfileHandler;
import com.bookmapmcp.handlers.VwapHandler;
import com.bookmapmcp.handlers.WorkingOrdersHandler;

public final class BridgeServer {

    private static final Logger LOG = Logger.getLogger(BridgeServer.class.getName());
    private static final AtomicBoolean STARTED = new AtomicBoolean(false);
    private static volatile HttpServer server;
    private static volatile BridgeConfig config;

    private BridgeServer() {}

    public static synchronized void ensureStarted() {
        if (STARTED.get()) return;
        BridgeConfig cfg;
        try {
            cfg = BridgeConfig.loadOrCreate();
        } catch (RuntimeException e) {
            LOG.log(Level.SEVERE, "Failed to load Bookmap MCP bridge config", e);
            throw e;
        }
        try {
            InetSocketAddress bind = new InetSocketAddress(InetAddress.getByName("127.0.0.1"), cfg.port());
            HttpServer http = HttpServer.create(bind, 0);
            BridgeAuth auth = new BridgeAuth(cfg.token());

            http.createContext("/ping",            auth.guard(new PingHandler()));
            http.createContext("/instruments",     auth.guard(new InstrumentsHandler()));
            http.createContext("/orderbook",       auth.guard(new OrderbookHandler()));
            http.createContext("/recent_trades",   auth.guard(new RecentTradesHandler()));
            http.createContext("/working_orders",  auth.guard(new WorkingOrdersHandler()));
            http.createContext("/position",        auth.guard(new PositionHandler()));
            http.createContext("/recent_fills",    auth.guard(new RecentFillsHandler()));
            http.createContext("/balance",         auth.guard(new BalanceHandler()));
            http.createContext("/vwap",            auth.guard(new VwapHandler()));
            http.createContext("/momentum",        auth.guard(new MomentumHandler()));
            http.createContext("/volume_profile",  auth.guard(new VolumeProfileHandler()));
            http.createContext("/tape_buckets",    auth.guard(new TapeBucketsHandler()));
            http.createContext("/lt_liquidity",    auth.guard(new LtLiquidityHandler()));
            http.createContext("/book_dynamics",   auth.guard(new BookDynamicsHandler()));
            http.createContext("/pull_stack",      auth.guard(new PullStackHandler()));
            http.createContext("/microstructure_events", auth.guard(new MicrostructureEventsHandler()));
            http.createContext("/magnet_levels",   auth.guard(new MagnetLevelsHandler()));
            http.createContext("/screenshot",      auth.guard(new ScreenshotHandler()));
            http.createContext("/place_limit_order", auth.guard(new TradingHandler(TradingHandler.Op.PLACE_LIMIT)));
            http.createContext("/cancel_order",    auth.guard(new TradingHandler(TradingHandler.Op.CANCEL)));

            http.setExecutor(Executors.newFixedThreadPool(4, r -> {
                Thread t = new Thread(r, "bookmap-mcp-bridge-http");
                t.setDaemon(true);
                return t;
            }));
            http.start();

            server = http;
            config = cfg;
            STARTED.set(true);
            LOG.info(() -> "Bookmap MCP bridge listening on http://127.0.0.1:" + cfg.port()
                    + " (token from " + cfg.source() + ")");
        } catch (BindException be) {
            if (probeOurBridge(cfg)) {
                config = cfg;
                STARTED.set(true);
                LOG.warning(() -> "Bookmap MCP bridge port " + cfg.port() + " already bound by our own bridge.");
                return;
            }
            LOG.log(Level.SEVERE, "Port " + cfg.port() + " bound by something that isn't our bridge", be);
            throw new IllegalStateException("Port " + cfg.port() + " is already in use by another process. "
                    + "Either change 'port=' in " + cfg.source() + " or kill whatever owns the port.", be);
        } catch (IOException e) {
            LOG.log(Level.SEVERE, "Failed to start Bookmap MCP bridge", e);
            throw new IllegalStateException("Failed to start Bookmap MCP bridge", e);
        }
    }

    private static boolean probeOurBridge(BridgeConfig cfg) {
        try {
            URL url = new URL("http://127.0.0.1:" + cfg.port() + "/ping");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            try {
                conn.setRequestMethod("GET");
                conn.setRequestProperty(BridgeAuth.HEADER, cfg.token());
                conn.setConnectTimeout(500);
                conn.setReadTimeout(500);
                int code = conn.getResponseCode();
                if (code != 200) return false;
                try (InputStream in = conn.getInputStream()) { in.readAllBytes(); }
                return true;
            } finally { conn.disconnect(); }
        } catch (IOException e) { return false; }
    }

    public static synchronized void stopForTests() {
        if (server != null) { server.stop(0); server = null; }
        config = null;
        STARTED.set(false);
    }

    public static BridgeConfig configOrNull() { return config; }
}
