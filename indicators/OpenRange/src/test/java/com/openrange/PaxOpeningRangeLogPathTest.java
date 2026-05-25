package com.openrange;

import java.nio.file.Path;

public class PaxOpeningRangeLogPathTest {
    public static void main(String[] args) {
        usesDefaultLogDirectoryWhenBlank();
        sanitizesSymbolInConfiguredDirectory();
        createsStateCachePath();
        sessionConfigPublishesLogDirectory();
    }

    private static void usesDefaultLogDirectoryWhenBlank() {
        Path path = PaxOpeningRangeLogPath.signalLogPath("", "ESM6");

        assertEquals(Path.of("build", "logs", "openrange-signals-ESM6.csv"), path, "path");
    }

    private static void sanitizesSymbolInConfiguredDirectory() {
        Path path = PaxOpeningRangeLogPath.signalLogPath("D:\\OpenRange Logs", "ES M6/TEST");

        assertEquals(Path.of("D:\\OpenRange Logs", "openrange-signals-ES_M6_TEST.csv"), path, "path");
    }

    private static void createsStateCachePath() {
        Path path = PaxOpeningRangeLogPath.stateCachePath("D:\\OpenRange Logs", "MNQM6.CME@RITHMIC");

        assertEquals(Path.of("D:\\OpenRange Logs", "openrange-state-MNQM6.CME_RITHMIC.csv"), path, "path");
    }

    private static void sessionConfigPublishesLogDirectory() {
        PaxOpeningRangeUiSettings ui = new PaxOpeningRangeUiSettings();
        ui.logDirectory = "D:\\OpenRange Logs";
        String json = PaxOpeningRangeSessionConfigWriter.renderJson(ui, 123L);

        if (!json.contains("\"logDirectory\": \"D:\\\\OpenRange Logs\"")) {
            throw new AssertionError("session config must publish operator CSV logDirectory: " + json);
        }
        if (!json.contains("\"logDirectoryAbsolute\"")) {
            throw new AssertionError("session config must publish resolved logDirectoryAbsolute: " + json);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
