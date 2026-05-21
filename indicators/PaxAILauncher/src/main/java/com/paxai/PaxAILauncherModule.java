package com.paxai;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

import velox.api.layer1.Layer1ApiAdminAdapter;
import velox.api.layer1.Layer1ApiFinishable;
import velox.api.layer1.Layer1ApiInstrumentSpecificEnabledStateProvider;
import velox.api.layer1.Layer1ApiProvider;
import velox.api.layer1.annotations.Layer1ApiVersion;
import velox.api.layer1.annotations.Layer1ApiVersionValue;
import velox.api.layer1.annotations.Layer1Attachable;
import velox.api.layer1.annotations.Layer1StrategyName;
import velox.api.layer1.common.ListenableHelper;
import velox.api.layer1.common.Log;

/**
 * Pax AI Launcher.
 *
 * Toggling this addon in Bookmap's Add-ons panel spawns the external
 * Python Pax AI process (which opens the floating dark-glass chat window
 * via pywebview). Detaching the addon (or closing Bookmap) terminates
 * the process.
 *
 * No market data is read. No trading endpoints are touched. The addon
 * only owns the lifecycle of one child process.
 *
 * Implementation notes:
 *   * Singleton process: a static lock + static Process reference ensures
 *     only one Pax AI is alive even if Bookmap instantiates the module
 *     multiple times.
 *   * stdout + stderr are redirected to %LOCALAPPDATA%/pax-ai/pax_ai.log
 *     (created on first run). The Bookmap log only sees lifecycle events.
 *   * Python path defaults to the project venv (matches CLAUDE.md). The
 *     PAX_AI_PYTHON env var overrides it.
 */
@Layer1Attachable
@Layer1StrategyName("Pax AI")
@Layer1ApiVersion(Layer1ApiVersionValue.VERSION2)
public class PaxAILauncherModule implements
        Layer1ApiAdminAdapter,
        Layer1ApiFinishable,
        Layer1ApiInstrumentSpecificEnabledStateProvider {

    private static final Object LIFECYCLE_LOCK = new Object();
    private static volatile Process PAX_AI_PROCESS = null;
    private static final Set<String> ENABLED_ALIASES = ConcurrentHashMap.newKeySet();

    static final String DEFAULT_PYTHON =
        "C:\\Bookmap\\addons\\MCP\\Bookmap\\mcp-server\\.venv\\Scripts\\python.exe";
    static final String[] PYTHON_ARGS = {"-B", "-u", "-m", "pax_ai", "--shell"};

    @SuppressWarnings("unused")
    private final Layer1ApiProvider provider;

    public PaxAILauncherModule(Layer1ApiProvider provider) {
        this.provider = provider;
        ListenableHelper.addListeners(provider, this);
    }

    @Override
    public void finish() {
        ENABLED_ALIASES.clear();
        stopPaxAi();
    }

    @Override
    public void onStrategyCheckboxEnabled(String alias, boolean isEnabled) {
        if (alias == null) {
            return;
        }
        if (isEnabled) {
            ENABLED_ALIASES.add(alias);
            startPaxAi();
        } else {
            ENABLED_ALIASES.remove(alias);
            if (ENABLED_ALIASES.isEmpty()) {
                stopPaxAi();
            } else {
                logInfo("Pax AI remains running for enabled aliases=" + ENABLED_ALIASES);
            }
        }
    }

    @Override
    public boolean isStrategyEnabled(String alias) {
        return alias != null && ENABLED_ALIASES.contains(alias);
    }

    // --------------------------------------------------------------------
    // Lifecycle (package-private for testability)
    // --------------------------------------------------------------------

    static void startPaxAi() {
        synchronized (LIFECYCLE_LOCK) {
            if (PAX_AI_PROCESS != null && PAX_AI_PROCESS.isAlive()) {
                logInfo("Pax AI already running pid=" + PAX_AI_PROCESS.pid());
                return;
            }
            try {
                String python = resolvePython();
                if (python == null) {
                    logError("Pax AI python interpreter not found; expected at "
                              + DEFAULT_PYTHON + " or via PAX_AI_PYTHON env var");
                    return;
                }
                String[] cmd = new String[1 + PYTHON_ARGS.length];
                cmd[0] = python;
                System.arraycopy(PYTHON_ARGS, 0, cmd, 1, PYTHON_ARGS.length);
                ProcessBuilder pb = new ProcessBuilder(cmd);
                pb.environment().put("PAX_AI_LAUNCHED_BY", "bookmap_addon");
                pb.redirectErrorStream(true);
                Path logDir = paxAiLogDir();
                Files.createDirectories(logDir);
                Path logFile = logDir.resolve("pax_ai.log");
                pb.redirectOutput(ProcessBuilder.Redirect.appendTo(logFile.toFile()));
                PAX_AI_PROCESS = pb.start();
                logInfo("Pax AI launched pid=" + PAX_AI_PROCESS.pid()
                          + " log=" + logFile);
            } catch (Throwable t) {
                logError("Pax AI launch failed", t);
            }
        }
    }

    static void stopPaxAi() {
        synchronized (LIFECYCLE_LOCK) {
            Process p = PAX_AI_PROCESS;
            if (p == null) {
                return;
            }
            try {
                if (p.isAlive()) {
                    p.destroy();
                    if (!p.waitFor(2, TimeUnit.SECONDS)) {
                        p.destroyForcibly();
                        logInfo("Pax AI forcibly killed (did not exit in 2s)");
                    } else {
                        logInfo("Pax AI stopped cleanly");
                    }
                }
            } catch (InterruptedException ie) {
                Thread.currentThread().interrupt();
                logError("Pax AI stop interrupted", ie);
            } catch (Throwable t) {
                logError("Pax AI stop failed", t);
            } finally {
                PAX_AI_PROCESS = null;
            }
        }
    }

    /** Resolve the python interpreter path. Order: PAX_AI_PYTHON env, DEFAULT_PYTHON. Returns null if none exist. */
    static String resolvePython() {
        String env = System.getenv("PAX_AI_PYTHON");
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

    /** %LOCALAPPDATA%/pax-ai/  (or user.home/AppData/Local/pax-ai/ on weird configs). */
    static Path paxAiLogDir() {
        String local = System.getenv("LOCALAPPDATA");
        if (local == null || local.isBlank()) {
            local = System.getProperty("user.home") + "\\AppData\\Local";
        }
        return Paths.get(local, "pax-ai");
    }

    // --------------------------------------------------------------------
    // Log helpers (tolerant of test environments where Log is unavailable)
    // --------------------------------------------------------------------

    private static void logInfo(String msg) {
        try { Log.info(msg); } catch (Throwable ignored) {}
    }

    private static void logError(String msg) {
        try { Log.error(msg); } catch (Throwable ignored) {}
    }

    private static void logError(String msg, Throwable t) {
        try { Log.error(msg, t); } catch (Throwable ignored) {}
    }
}
