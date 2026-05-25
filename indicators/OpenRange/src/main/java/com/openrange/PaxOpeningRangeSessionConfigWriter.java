package com.openrange;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

import velox.api.layer1.common.Log;

/**
 * Publishes the OpenRange indicator's effective session settings to a
 * well-known JSON file so the Python dashboard + Java bridge can use the
 * operator-controlled OR anchor as the single source of truth.
 *
 * <p>File schema (v1, ASCII only):
 * <pre>
 * {
 *   "version":       1,
 *   "updatedAtMs":   1779228000000,
 *   "timezone":      "America/Chicago",
 *   "startHour":     8,
 *   "startMinute":   30,
 *   "startSecond":   0,
 *   "rangeSeconds":  30,
 *   "endHour":       8,
 *   "endMinute":     30,
 *   "logDirectory":  "D:\\BookmapLogs",
 *   "logDirectoryAbsolute": "D:\\BookmapLogs",
 *   "labelPrefix":   "OpenRange",
 *   "daysToDisplay": 8,
 *   "source":        "openrange-indicator"
 * }
 * </pre>
 *
 * <p>Write paths (best-effort, all attempted):
 *   <ul>
 *     <li>{@code D:\BookmapLogs\or-session-config.json}  (production)</li>
 *     <li>{@code C:\BookmapLogs\or-session-config.json}  (dev fallback)</li>
 *     <li>{@code <logDirectory>\or-session-config.json}  (configured-log fallback)</li>
 *   </ul>
 *
 * <p>Atomic write: temp file in same directory, then rename. A reader
 * that opens the file mid-update will see either the prior version or
 * the new version, never a torn write.
 *
 * <p>Errors are logged via Bookmap's Log and swallowed — a write failure
 * must NEVER break the indicator. Readers fail safe to fallback values
 * when the file is missing or stale.
 */
public final class PaxOpeningRangeSessionConfigWriter {

    public static final String FILE_NAME = "or-session-config.json";
    public static final String TIMEZONE  = "America/Chicago";
    public static final int    SCHEMA_VERSION = 1;

    private PaxOpeningRangeSessionConfigWriter() { }

    /**
     * Publish the effective settings. Returns true if at least one target
     * was written successfully. Errors are logged but never thrown.
     */
    public static boolean publish(PaxOpeningRangeUiSettings ui) {
        if (ui == null) return false;
        long updatedAtMs = Instant.now().toEpochMilli();
        String json = renderJson(ui, updatedAtMs);
        List<Path> targets = collectTargetPaths(ui);
        boolean wrote = false;
        for (Path target : targets) {
            try {
                writeAtomic(target, json);
                wrote = true;
            } catch (IOException ioe) {
                try {
                    Log.warn("OpenRange: failed to write " + target + ": " + ioe.getMessage());
                } catch (Throwable ignored) { /* Log may be unavailable in tests */ }
            }
        }
        return wrote;
    }

    /** Render the OR config as ASCII-only JSON. No external dep — keeps
     *  the indicator jar small and survives offline builds. */
    static String renderJson(PaxOpeningRangeUiSettings ui, long updatedAtMs) {
        StringBuilder sb = new StringBuilder(384);
        sb.append("{\n");
        sb.append("  \"version\": ").append(SCHEMA_VERSION).append(",\n");
        sb.append("  \"updatedAtMs\": ").append(updatedAtMs).append(",\n");
        sb.append("  \"timezone\": ").append(jsonString(TIMEZONE)).append(",\n");
        sb.append("  \"startHour\": ").append(ui.startHour).append(",\n");
        sb.append("  \"startMinute\": ").append(ui.startMinute).append(",\n");
        sb.append("  \"startSecond\": ").append(ui.startSecond).append(",\n");
        sb.append("  \"rangeSeconds\": ").append(ui.rangeSeconds).append(",\n");
        sb.append("  \"endHour\": ").append(ui.endHour).append(",\n");
        sb.append("  \"endMinute\": ").append(ui.endMinute).append(",\n");
        String logDir = ui.logDirectory == null || ui.logDirectory.isBlank()
                ? "build\\logs" : ui.logDirectory;
        sb.append("  \"logDirectory\": ").append(jsonString(logDir)).append(",\n");
        sb.append("  \"logDirectoryAbsolute\": ")
                .append(jsonString(Path.of(logDir).toAbsolutePath().normalize().toString()))
                .append(",\n");
        sb.append("  \"labelPrefix\": ").append(jsonString(ui.labelPrefix == null ? "OpenRange" : ui.labelPrefix)).append(",\n");
        sb.append("  \"daysToDisplay\": ").append(ui.daysToDisplay).append(",\n");
        sb.append("  \"source\": \"openrange-indicator\"\n");
        sb.append("}\n");
        return sb.toString();
    }

    /** Build the list of write targets in priority order.
     * <p>OPERATOR PRIMARY first — whatever path the operator configured via
     * OpenRange's logDirectory setting. Then well-known production paths
     * (D:/BookmapLogs, C:/BookmapLogs) as backup so the dashboard reader
     * can still find a config even when logDirectory is set to an unusual
     * location. */
    static List<Path> collectTargetPaths(PaxOpeningRangeUiSettings ui) {
        List<Path> out = new ArrayList<>(4);
        String logDir = ui == null || ui.logDirectory == null || ui.logDirectory.isBlank()
                ? "build\\logs" : ui.logDirectory;
        out.add(Path.of(logDir, FILE_NAME));
        out.add(Path.of("D:\\BookmapLogs", FILE_NAME));
        out.add(Path.of("C:\\BookmapLogs", FILE_NAME));
        return out;
    }

    /** Atomic write: same-directory tempfile + ATOMIC_MOVE. Creates parent
     *  dirs as needed. */
    static void writeAtomic(Path target, String body) throws IOException {
        Path parent = target.getParent();
        if (parent != null) Files.createDirectories(parent);
        Path tmp = target.resolveSibling(target.getFileName().toString() + ".tmp");
        Files.write(tmp, body.getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING,
                StandardOpenOption.WRITE);
        try {
            Files.move(tmp, target,
                    java.nio.file.StandardCopyOption.ATOMIC_MOVE,
                    java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        } catch (java.nio.file.AtomicMoveNotSupportedException ame) {
            Files.move(tmp, target, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
        }
    }

    /** Minimal JSON-string escape (no external deps). ASCII-safe. */
    static String jsonString(String s) {
        StringBuilder sb = new StringBuilder(s.length() + 4);
        sb.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"':  sb.append("\\\""); break;
                case '\\': sb.append("\\\\"); break;
                case '\n': sb.append("\\n");  break;
                case '\r': sb.append("\\r");  break;
                case '\t': sb.append("\\t");  break;
                default:
                    if (c < 0x20) sb.append(String.format("\\u%04x", (int) c));
                    else          sb.append(c);
            }
        }
        sb.append('"');
        return sb.toString();
    }
}
