package com.openrange;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;

public class PaxOpeningRangeSignalCsvLoggerTest {
    public static void main(String[] args) throws Exception {
        writesHeaderAndSignalRows();
        skipsDuplicateSignals();
    }

    private static void writesHeaderAndSignalRows() throws Exception {
        Path file = Files.createTempFile("paxor-signals", ".csv");
        PaxOpeningRangeSignalCsvLogger logger = new PaxOpeningRangeSignalCsvLogger(file);
        PaxOpeningRangeSignal signal = signal("CVD+ BID+ ASK PULL NET+");
        PaxOpeningRangeMarketState market = new PaxOpeningRangeMarketState(6401.25, 12, 500, -250, 750);

        logger.logIfChanged("ESM6", LocalDateTime.of(2026, 4, 28, 9, 31, 5),
                6400.00, 6390.00, market, signal);
        logger.close();

        String csv = Files.readString(file);
        assertContains(csv, "time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,netDepthDelta,cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,rangeWidth,rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,evidence,reason", "header");
        assertContains(csv, "2026-04-28T09:31:05,ESM6,6401.25,6400.0,6390.0,12.0,500.0,-250.0,750.0,0.0,0.0,0,0,\"\",0,0.0,\"\",0,LONG,ALLOW_SIGNAL,HIGH,4,4,\"CVD+ BID+ ASK PULL NET+\",\"Price is above ORH and order-flow confirmation is strong.\"", "row");
    }

    private static void skipsDuplicateSignals() throws Exception {
        Path file = Files.createTempFile("paxor-signals", ".csv");
        PaxOpeningRangeSignalCsvLogger logger = new PaxOpeningRangeSignalCsvLogger(file);
        PaxOpeningRangeSignal signal = signal("CVD+ BID+ ASK PULL NET+");
        PaxOpeningRangeMarketState market = new PaxOpeningRangeMarketState(6401.25, 12, 500, -250, 750);

        logger.logIfChanged("ESM6", LocalDateTime.of(2026, 4, 28, 9, 31, 5),
                6400.00, 6390.00, market, signal);
        logger.logIfChanged("ESM6", LocalDateTime.of(2026, 4, 28, 9, 31, 6),
                6400.00, 6390.00, market, signal);
        logger.close();

        long lineCount = Files.readAllLines(file).size();
        assertEquals(2, lineCount, "line count");
    }

    private static PaxOpeningRangeSignal signal(String evidence) {
        return new PaxOpeningRangeSignal(
                PaxOpeningRangeSignalAction.ALLOW_SIGNAL,
                PaxOpeningRangeSignalBias.LONG,
                PaxOpeningRangeSignalConfidence.HIGH,
                "Price is above ORH and order-flow confirmation is strong.",
                4,
                4,
                evidence);
    }

    private static void assertContains(String actual, String expected, String message) {
        if (!actual.contains(expected)) {
            throw new AssertionError(message + ": expected CSV to contain \"" + expected + "\" but got \"" + actual + "\"");
        }
    }

    private static void assertEquals(long expected, long actual, String message) {
        if (expected != actual) {
            throw new AssertionError(message + ": expected " + expected + " but got " + actual);
        }
    }
}
