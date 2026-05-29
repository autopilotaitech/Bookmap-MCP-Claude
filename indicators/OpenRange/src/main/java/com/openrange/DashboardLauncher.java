package com.openrange;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.concurrent.TimeUnit;

import velox.api.layer1.common.Log;

/**
 * Spawns the Python live HUD dashboard ({@code python -m bookmap_mcp.dashboard
 * --port 18888}) when the OpenRange indicator is enabled, so the operator does
 * not have to run a terminal command every time. Mirrors the PaxAILauncher
 * lifecycle pattern.
 *
 * Read-only / presentation: the dashboard only polls the bridge and serves a
 * web HUD. No trading endpoints are touched here.
 *
 * Singleton: a static lock + static Process reference keep exactly one
 * dashboard alive even if the module is instantiated multiple times. If port
 * 18888 is already serving (operator started it manually), spawning is skipped
 * so we never fight over the port. stdout+stderr -> %LOCALAPPDATA%/pax-dashboard.
 */
final class DashboardLauncher {

    private static final Object LIFECYCLE_LOCK = new Object();
    private static volatile Process DASHBOARD_PROCESS = null;

    static final String DEFAULT_PYTHON =
        "C:\\Bookmap\\addons\\MCP\\Bookmap\\mcp-server\\.venv\\Scripts\\python.exe";
    static final String MCP_SERVER_DIR =
        "C:\\Bookmap\\addons\\MCP\\Bookmap\\mcp-server";
    static final int DASHBOARD_PORT = 18888;
    static final String[] PYTHON_ARGS =
        {"-B", "-u", "-m", "bookmap_mcp.dashboard", "--port", "18888"};

    private DashboardLauncher() {}

    static void start() {
        synchronized (LIFECYCLE_LOCK) {
            if (DASHBOARD_PROCESS != null && DASHBOARD_PROCESS.isAlive()) {
                logInfo("dashboard already running pid=" + DASHBOARD_PROCESS.pid());
                return;
            }
            if (portInUse(DASHBOARD_PORT)) {
                logInfo("dashboard port " + DASHBOARD_PORT
                          + " already in use; not spawning (assume operator-started)");
                return;
            }
            try {
                String python = resolvePython();
                if (python == null) {
                    logError("dashboard python interpreter not found; expected at "
                              + DEFAULT_PYTHON + " or via PAX_DASHBOARD_PYTHON env var");
                    return;
                }
                String[] cmd = new String[1 + PYTHON_ARGS.length];
                cmd[0] = python;
                System.arraycopy(PYTHON_ARGS, 0, cmd, 1, PYTHON_ARGS.length);
                ProcessBuilder pb = new ProcessBuilder(cmd);
                Path mcp = Paths.get(MCP_SERVER_DIR);
                if (Files.isDirectory(mcp)) {
                    pb.directory(mcp.toFile());   // so bookmap_mcp imports
                }
                pb.environment().put("PAX_DASHBOARD_LAUNCHED_BY", "openrange_addon");
                pb.redirectErrorStream(true);
                Path logDir = dashboardLogDir();
                Files.createDirectories(logDir);
                Path logFile = logDir.resolve("dashboard.log");
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile.toFile()));
                DASHBOARD_PROCESS = pb.start();
                logInfo("dashboard launched pid=" + DASHBOARD_PROCESS.pid()
                          + " log=" + logFile);
            } catch (Throwable t) {
                logError("dashboard launch failed", t);
            }
        }
    }

    static void stop() {
        synchronized (LIFECYCLE_LOCK) {
            Process p = DASHBOARD_PROCESS;
            if (p == null) {
                return;
            }
            try {
                if (p.isAlive()) {
                    p.destroy();
                    if (!p.waitFor(2, TimeUnit.SECONDS)) {
                        p.destroyForcibly();
                        logInfo("dashboard forcibly killed (did not exit in 2s)");
                    } else {
                        logInfo("dashboard stopped cleanly");
                    }
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                logError("dashboard stop interrupted", ie);
            } catch (Throwable t) {
                logError("dashboard stop failed", t);
            } finally {
                DASHBOARD_PROCESS = null;
            }
        }
    }

    /** Quick TCP probe: is something already listening on 127.0.0.1:port? */
    static boolean portInUse(int port) {
        try (Socket s = new Socket()) {
            s.connect(new InetSocketAddress("127.0.0.1", port), 250);
            return true;
        } catch (IOException e) {
            return false;
        }
    }

    /** Resolve python: PAX_DASHBOARD_PYTHON env, then DEFAULT_PYTHON. Null if none. */
    static String resolvePython() {
        String env = System.getenv("PAX_DASHBOARD_PYTHON");
        if (env != null && !env.isBlank()) {
            Path p = Paths.get(env);
            if (Files.exists(p)) {
                return p.toString();
            }
        }
        Path def = Paths.get(DEFAULT_PYTHON);
        if (Files.exists(def)) {
            return def.toString();
        }
        return null;
    }

    static Path dashboardLogDir() {
        String local = System.getenv("LOCALAPPDATA");
        if (local == null || local.isBlank()) {
            local = System.getProperty("user.home") + "\\AppData\\Local";
        }
        return Paths.get(local, "pax-dashboard");
    }

    private static void logInfo(String msg) {
        try { Log.info("[OpenRange/dashboard] " + msg); } catch (Throwable ignored) {}
    }

    private static void logError(String msg) {
        try { Log.error("[OpenRange/dashboard] " + msg); } catch (Throwable ignored) {}
    }

    private static void logError(String msg, Throwable t) {
        try { Log.error("[OpenRange/dashboard] " + msg, t); } catch (Throwable ignored) {}
    }
}
