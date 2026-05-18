package com.openrange;

import java.nio.file.Path;

public class PaxOpeningRangeLogPath {
    private PaxOpeningRangeLogPath() {
    }

    public static Path signalLogPath(String directory, String symbol) {
        String safeDirectory = directory == null || directory.isBlank() ? "build\\logs" : directory.trim();
        return Path.of(safeDirectory, "openrange-signals-" + safeSymbol(symbol) + ".csv");
    }

    public static Path stateCachePath(String directory, String symbol) {
        String safeDirectory = directory == null || directory.isBlank() ? "build\\logs" : directory.trim();
        return Path.of(safeDirectory, "openrange-state-" + safeSymbol(symbol) + ".csv");
    }

    private static String safeSymbol(String symbol) {
        return symbol == null || symbol.isBlank() ? "unknown" : symbol.replaceAll("[^A-Za-z0-9._-]", "_");
    }
}
