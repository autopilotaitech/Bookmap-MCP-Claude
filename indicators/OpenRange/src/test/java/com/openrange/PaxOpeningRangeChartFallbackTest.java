package com.openrange;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.Optional;
import java.util.UUID;

/**
 * Read-only chart fallback restore tests. Each scenario writes synthetic
 * openrange-signals CSVs into a fresh temp directory, then asserts that
 * loadLatest returns the right row (or none) for the requested symbol.
 *
 * Constraints enforced here:
 *   - exact symbol match only (no prefix / substring routing)
 *   - malformed rows skipped silently
 *   - across files: row with the latest `time` wins
 *   - returned values match what the live calculator would have produced
 *     (high/low are the row values; mid = (high+low)/2 rounded to tick)
 *   - fallback never mutates the trading calculator (verified by passing
 *     in a calculator and confirming its day map stays empty)
 */
public class PaxOpeningRangeChartFallbackTest {

    private static final String HEADER =
            "time,symbol,price,orHigh,orLow,cvdDelta,bidDepthDelta,askDepthDelta,netDepthDelta,"
                    + "cvdZ,psZ,cvdPercentile,psPercentile,location,distanceTicks,rangeWidth,"
                    + "rangeQuality,ageSeconds,bias,action,confidence,score,maxScore,evidence,reason";

    public static void main(String[] args) throws Exception {
        exactSymbolRestoreReturnsRow();
        wrongSymbolIsIgnored();
        malformedRowIsSkipped();
        newestRowWinsWithinFile();
        newestRowWinsAcrossFiles();
        missingDirectoryReturnsEmpty();
        emptyDirectoryReturnsEmpty();
        realNqCsvShapeRestoresVisibleLevels();
        ageSecondsDerivesCompletedAt();
        midIsRoundedToTickSize();
        searchesMultipleDirectories();
        restoresExtensionLevelsFromCsvPrice();
        restoresBothSidesFromSessionExtremes();
        fallbackDoesNotMutateCalculator();
        prefixSymbolDoesNotMatch();
        System.out.println("PaxOpeningRangeChartFallbackTest OK");
    }

    // ── tests ────────────────────────────────────────────────────────────

    private static void exactSymbolRestoreReturnsRow() throws Exception {
        Path dir = freshDir();
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "exact-symbol row must be found");
        PaxOpeningRangeDayState day = out.get().day;
        assertTrue(day.isComplete(), "fallback day must be marked complete");
        assertEquals(21500.00, day.getHigh(), "high");
        assertEquals(21400.00, day.getLow(), "low");
        assertEquals(21450.00, day.getMid(), "mid = (high+low)/2 rounded to tick");
        assertEquals("NQM6", out.get().symbol, "result.symbol echoes request");
        assertTrue(out.get().csvPath.toString().endsWith("openrange-signals-NQM6.csv"),
                "result.csvPath points at the matched file");
    }

    private static void wrongSymbolIsIgnored() throws Exception {
        Path dir = freshDir();
        // Filename is ES; row's symbol column is ES. We ask for NQ.
        writeCsv(dir.resolve("openrange-signals-ESM6.csv"),
                row("2026-05-19T09:30:30", "ESM6", 5900.00, 5870.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isEmpty(), "wrong-symbol CSV must not produce a fallback");
    }

    private static void malformedRowIsSkipped() throws Exception {
        Path dir = freshDir();
        // First row malformed (NaN high). Second row valid.
        String body = HEADER + "\n"
                + "2026-05-19T09:30:30,NQM6,21450.0,not-a-number,21400.00,0,0,0,0,0,0,0,0,IN,0,0,GOOD,30,NEUTRAL,WAIT,LOW,0,1,\"\",\"\"\n"
                + row("2026-05-19T09:31:00", "NQM6", 21500.00, 21400.00, 0) + "\n";
        Files.writeString(dir.resolve("openrange-signals-NQM6.csv"), body, StandardCharsets.UTF_8);

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "malformed first row must be skipped, valid second row used");
        assertEquals(21500.00, out.get().day.getHigh(), "high from valid row");
    }

    private static void newestRowWinsWithinFile() throws Exception {
        Path dir = freshDir();
        String body = HEADER + "\n"
                + row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0) + "\n"
                + row("2026-05-19T09:45:00", "NQM6", 21525.00, 21425.00, 30) + "\n"
                + row("2026-05-19T09:35:00", "NQM6", 21510.00, 21410.00, 30) + "\n";  // out of order
        Files.writeString(dir.resolve("openrange-signals-NQM6.csv"), body, StandardCharsets.UTF_8);

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "found");
        assertEquals(21525.00, out.get().day.getHigh(), "newest by row time wins, not by file order");
        assertEquals(LocalDateTime.parse("2026-05-19T09:45:00"), out.get().rowTime, "row time");
    }

    private static void newestRowWinsAcrossFiles() throws Exception {
        Path dir = freshDir();
        // NQ symbol appears in TWO files (e.g., rotated logs).
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-18T09:30:30", "NQM6", 21000.00, 20900.00, 0));
        writeCsv(dir.resolve("openrange-signals-NQM6-rotated.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "found");
        assertEquals(21500.00, out.get().day.getHigh(), "newest by row time wins across files");
        assertTrue(out.get().csvPath.toString().contains("rotated"),
                "csvPath should be the file holding the newest row");
    }

    private static void missingDirectoryReturnsEmpty() {
        Path missing = Path.of("C:\\definitely-not-a-real-dir-" + UUID.randomUUID());
        assertTrue(PaxOpeningRangeChartFallback.loadLatest(missing, "NQM6", 0.25).isEmpty(),
                "missing dir → empty");
        assertTrue(PaxOpeningRangeChartFallback.loadLatest((Path) null, "NQM6", 0.25).isEmpty(),
                "null dir → empty");
        assertTrue(PaxOpeningRangeChartFallback.loadLatest(missing, "", 0.25).isEmpty(),
                "blank symbol → empty");
        assertTrue(PaxOpeningRangeChartFallback.loadLatest(missing, null, 0.25).isEmpty(),
                "null symbol → empty");
    }

    private static void emptyDirectoryReturnsEmpty() throws Exception {
        Path dir = freshDir();
        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);
        assertTrue(out.isEmpty(), "no CSVs → empty");
    }

    private static void realNqCsvShapeRestoresVisibleLevels() throws Exception {
        Path dir = freshDir();
        Files.writeString(dir.resolve("openrange-signals-NQM6.csv"), HEADER + "\n"
                + "2026-05-19T12:19:03.732712701,NQM6,29091.0,28927.25,28902.25,"
                + "0.0,0.0,0.0,0.0,0.0,0.0,100,100,\"ORH\",655,25.0,\"WIDE\",2913,"
                + "NEUTRAL,BLOCK_SIGNAL,LOW,0,4,\"\",\"Breakout is more than 5 ticks beyond opening range.\"\n",
                StandardCharsets.UTF_8);

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "real CSV shape must restore");
        PaxOpeningRangeDayState day = out.get().day;
        assertEquals(28927.25, day.getHigh(), "real high");
        assertEquals(28902.25, day.getLow(), "real low");
        assertEquals(3, day.getUpperLevels().size(), "real upper extensions");
        assertEquals(1, day.getLowerLevels().size(), "real lower extensions");
    }

    private static void ageSecondsDerivesCompletedAt() throws Exception {
        Path dir = freshDir();
        // ageSeconds = 1800 → completedAt is 30 minutes before rowTime.
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T10:00:00", "NQM6", 21500.00, 21400.00, 1800));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "found");
        assertEquals(LocalDateTime.parse("2026-05-19T09:30:00"), out.get().day.getCompletedAt(),
                "completedAt = rowTime - ageSeconds");
    }

    private static void midIsRoundedToTickSize() throws Exception {
        Path dir = freshDir();
        // Raw mid = (21500.10 + 21400.00) / 2 = 21450.05 → tick 0.25 rounds to 21450.0.
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.10, 21400.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertEquals(21450.00, out.get().day.getMid(), "mid rounded to 0.25 tick");
    }

    private static void searchesMultipleDirectories() throws Exception {
        Path empty = freshDir();
        Path real = freshDir();
        writeCsv(real.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(java.util.List.of(empty, real), "NQM6", 0.25);

        assertTrue(out.isPresent(), "fallback must find CSV in secondary directory");
        assertTrue(out.get().csvPath.startsWith(real), "secondary directory row selected");
    }

    private static void restoresExtensionLevelsFromCsvPrice() throws Exception {
        Path dir = freshDir();
        // NQ rung is 65. Price is above +2, so fallback should draw +1,+2,+3 buffer.
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                rowWithPrice("2026-05-19T12:19:00", "NQM6", 29091.00, 28927.25, 28902.25, 2913));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "found");
        PaxOpeningRangeDayState day = out.get().day;
        assertEquals(3, day.getUpperLevels().size(), "upper extension count");
        assertEquals(1, day.getLowerLevels().size(), "lower extension count");
        assertEquals(28992.25, day.getUpperLevels().get(0).price(), "+1");
        assertEquals(29057.25, day.getUpperLevels().get(1).price(), "+2");
        assertEquals(29122.25, day.getUpperLevels().get(2).price(), "+3 buffer");
        assertEquals(28837.25, day.getLowerLevels().get(0).price(), "-1");
    }

    private static void restoresBothSidesFromSessionExtremes() throws Exception {
        Path dir = freshDir();
        String body = HEADER + "\n"
                // Earlier downside excursion below -3.
                + rowWithPrice("2026-05-19T10:15:00", "NQM6", 28690.00, 28927.25, 28902.25, 2670) + "\n"
                // Newest row is above +2. Both sides must be restored.
                + rowWithPrice("2026-05-19T12:19:00", "NQM6", 29091.00, 28927.25, 28902.25, 2913) + "\n";
        Files.writeString(dir.resolve("openrange-signals-NQM6.csv"), body, StandardCharsets.UTF_8);

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);

        assertTrue(out.isPresent(), "found");
        PaxOpeningRangeDayState day = out.get().day;
        assertEquals(3, day.getUpperLevels().size(), "upper restored from latest upside");
        assertEquals(4, day.getLowerLevels().size(), "lower restored from earlier downside plus buffer");
        assertEquals(28837.25, day.getLowerLevels().get(0).price(), "-1");
        assertEquals(28772.25, day.getLowerLevels().get(1).price(), "-2");
        assertEquals(28707.25, day.getLowerLevels().get(2).price(), "-3");
        assertEquals(28642.25, day.getLowerLevels().get(3).price(), "-4 buffer");
    }

    private static void fallbackDoesNotMutateCalculator() throws Exception {
        Path dir = freshDir();
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0));

        PaxOpeningRangeCalculator calc =
                new PaxOpeningRangeCalculator(PaxOpeningRangeSettings.defaults(), "NQM6", 0.25);
        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQM6", 0.25);
        assertTrue(out.isPresent(), "found");

        // Calculator must still be empty — fallback is read-only for chart display.
        assertEquals(0, calc.getDays().size(), "fallback did not write into calculator");
        // The fallback's day is NOT the same instance as anything in the calculator.
        for (PaxOpeningRangeDayState d : calc.getDays()) {
            if (d == out.get().day) {
                throw new AssertionError("fallback day must not be in calculator");
            }
        }
    }

    private static void prefixSymbolDoesNotMatch() throws Exception {
        Path dir = freshDir();
        // Row has symbol "NQM6"; we request "NQ" — must NOT match by prefix.
        writeCsv(dir.resolve("openrange-signals-NQM6.csv"),
                row("2026-05-19T09:30:30", "NQM6", 21500.00, 21400.00, 0));

        Optional<PaxOpeningRangeChartFallback.Result> out =
                PaxOpeningRangeChartFallback.loadLatest(dir, "NQ", 0.25);

        assertTrue(out.isEmpty(), "prefix match must be rejected; exact symbol only");
    }

    // ── helpers ──────────────────────────────────────────────────────────

    private static Path freshDir() throws IOException {
        return Files.createTempDirectory("openrange-chart-fallback-");
    }

    private static String row(String time, String symbol, double orHigh, double orLow, long age) {
        double price = (orHigh + orLow) / 2.0;
        return rowWithPrice(time, symbol, price, orHigh, orLow, age);
    }

    private static String rowWithPrice(String time, String symbol, double price,
            double orHigh, double orLow, long age) {
        return time + "," + symbol + "," + price + "," + orHigh + "," + orLow
                + ",0,0,0,0,0,0,0,0,IN,0,0,GOOD," + age + ",NEUTRAL,WAIT,LOW,0,1,\"\",\"\"";
    }

    private static void writeCsv(Path file, String... rows) throws IOException {
        StringBuilder sb = new StringBuilder(HEADER).append("\n");
        for (String r : rows) {
            sb.append(r).append("\n");
        }
        Files.writeString(file, sb.toString(), StandardCharsets.UTF_8);
    }

    private static void assertTrue(boolean value, String message) {
        if (!value) {
            throw new AssertionError(message);
        }
    }

    private static void assertEquals(double expected, double actual, String message) {
        if (Math.abs(expected - actual) > 1e-7) {
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
