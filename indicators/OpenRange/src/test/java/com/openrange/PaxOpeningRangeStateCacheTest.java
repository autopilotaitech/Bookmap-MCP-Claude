package com.openrange;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;

public class PaxOpeningRangeStateCacheTest {
    public static void main(String[] args) throws Exception {
        restoresCompletedOpeningRangeWhenHistoricalWindowIsUnavailable();
        restoresOpeningRangeFromSignalCsvFallback();
    }

    private static void restoresCompletedOpeningRangeWhenHistoricalWindowIsUnavailable() throws Exception {
        Path file = Files.createTempFile("openrange-state", ".csv");
        Files.deleteIfExists(file);

        PaxOpeningRangeCalculator source = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "MNQM6", 0.25);
        LocalDate date = LocalDate.of(2026, 4, 30);
        source.onInterval(LocalDateTime.of(date, LocalTime.of(9, 30, 1)), 19000.0, 18995.0);
        source.onInterval(LocalDateTime.of(date, LocalTime.of(9, 30, 30)), 19012.0, 18990.0);
        source.onInterval(LocalDateTime.of(date, LocalTime.of(10, 31, 0)), 19080.0, 19070.0);

        PaxOpeningRangeStateCache cache = new PaxOpeningRangeStateCache(file);
        cache.save(source.getDays());

        PaxOpeningRangeCalculator restored = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "MNQM6", 0.25);
        cache.restoreInto(restored);

        PaxOpeningRangeDayState day = restored.getDay(date);
        assertTrue(day.isComplete(), "restored day should be complete");
        assertEquals(19012.0, day.getHigh(), "high");
        assertEquals(18990.0, day.getLow(), "low");
        assertEquals(LocalDateTime.of(date, LocalTime.of(10, 31, 0)), day.getLastUpdateTime(), "last update");
        assertEquals(2, day.getUpperLevels().size(), "upper dynamic levels");
    }

    private static void restoresOpeningRangeFromSignalCsvFallback() throws Exception {
        Path signalFile = Files.createTempFile("openrange-signals", ".csv");
        Files.writeString(signalFile,
                "time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,evidence,reason\n"
                        + "2026-04-30T11:14:27.365868100,MNQM6,19020.0,19012.0,18990.0,0,0,0,0,0,0,0,0,\"ORH\",32,22.0,\"OK\",6267,LONG,WAIT,NONE,0,4,\"\",\"seed\"\n");

        PaxOpeningRangeCalculator restored = new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "MNQM6", 0.25);
        PaxOpeningRangeStateCache cache = new PaxOpeningRangeStateCache(Files.createTempFile("missing-state", ".csv"));
        cache.restoreFromSignalLog(restored, signalFile);

        PaxOpeningRangeDayState day = restored.getDay(LocalDate.of(2026, 4, 30));
        assertTrue(day.isComplete(), "csv fallback should restore complete day");
        assertEquals(19012.0, day.getHigh(), "high");
        assertEquals(18990.0, day.getLow(), "low");
        assertEquals(LocalDateTime.of(2026, 4, 30, 9, 30, 0, 365868100), day.getCompletedAt(), "completed at");
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(double expected, double actual, String message) {
        if (Math.abs(expected - actual) > 0.0000001) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertEquals(int expected, int actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }

    private static void assertEquals(Object expected, Object actual, String message) {
        if (!expected.equals(actual)) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
