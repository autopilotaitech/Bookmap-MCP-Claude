package com.openrange;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * Stateless read-only reader that hydrates a chart-only OR DayState from the
 * signals CSV when the live calculator has no completed day (typical cold-
 * start after a Bookmap restart with a stale trade cache).
 *
 * Never writes into the trading calculator. The returned DayState is a free-
 * standing object meant solely for the painter to draw OR high/low/mid lines.
 *
 * Row selection rule: pick the row across all openrange-signals-*.csv files
 * whose `symbol` column exactly matches the requested symbol AND which has
 * the maximum parsed `time`. Malformed rows are skipped silently.
 */
public final class PaxOpeningRangeChartFallback {

    private PaxOpeningRangeChartFallback() {
    }

    /** Wraps the free-standing DayState plus diagnostic metadata. */
    public static final class Result {
        public final PaxOpeningRangeDayState day;
        public final Path csvPath;
        public final LocalDateTime rowTime;
        public final String symbol;

        Result(PaxOpeningRangeDayState day, Path csvPath, LocalDateTime rowTime, String symbol) {
            this.day = day;
            this.csvPath = csvPath;
            this.rowTime = rowTime;
            this.symbol = symbol;
        }
    }

    /**
     * Scan the directory for openrange-signals-*.csv files, return the newest
     * row whose `symbol` column exactly matches the requested value.
     *
     * @param logDirectory  the directory holding openrange-signals-*.csv files
     * @param symbol        exact symbol match (case-sensitive)
     * @param tickSize      used only for rounding the derived mid to a tick
     * @return Optional.empty() if no file, no readable row, or no matching row
     */
    public static Optional<Result> loadLatest(Path logDirectory, String symbol, double tickSize) {
        if (logDirectory == null) {
            return Optional.empty();
        }
        return loadLatest(List.of(logDirectory), symbol, tickSize);
    }

    /**
     * Scan directories in order for openrange-signals-*.csv files, returning
     * the newest valid exact-symbol row across all readable directories.
     */
    public static Optional<Result> loadLatest(List<Path> logDirectories, String symbol, double tickSize) {
        if (symbol == null || symbol.isBlank()) {
            return Optional.empty();
        }
        if (logDirectories == null || logDirectories.isEmpty()) {
            return Optional.empty();
        }
        double tick = tickSize <= 0 ? 0.25 : tickSize;

        Candidate best = null;
        List<Candidate> candidates = new ArrayList<>();
        Set<Path> uniqueDirs = new LinkedHashSet<>(logDirectories);
        for (Path logDirectory : uniqueDirs) {
            if (logDirectory == null || !Files.isDirectory(logDirectory)) {
                continue;
            }
            try (DirectoryStream<Path> stream =
                         Files.newDirectoryStream(logDirectory, "openrange-signals-*.csv")) {
                for (Path file : stream) {
                    for (Candidate c : scanFile(file, symbol)) {
                        candidates.add(c);
                        if (best == null || c.rowTime.isAfter(best.rowTime)) {
                            best = c;
                        }
                    }
                }
            } catch (IOException e) {
                continue;
            }
        }

        if (best == null) {
            return Optional.empty();
        }

        double mid = roundToTick(best.low + ((best.high - best.low) * 0.5), tick);
        LocalDateTime completedAt = best.rowTime.minusSeconds(Math.max(0L, best.ageSeconds));
        Candidate session = withSessionExtremes(best, candidates);

        PaxOpeningRangeDayState day = new PaxOpeningRangeDayState(best.rowTime.toLocalDate());
        day.setComplete(best.high, best.low, mid, completedAt);
        day.setLastUpdateTime(best.rowTime);
        addRestoredLevels(day, session, tick);
        return Optional.of(new Result(day, best.file, best.rowTime, symbol));
    }

    private static List<Candidate> scanFile(Path file, String symbol) {
        List<String> lines;
        try {
            lines = Files.readAllLines(file, StandardCharsets.UTF_8);
        } catch (IOException e) {
            return List.of();
        }
        List<Candidate> out = new ArrayList<>();
        // Skip header (i = 0); scan every row, keep newest matching by parsed time.
        for (int i = 1; i < lines.size(); i++) {
            Candidate c = parseRow(lines.get(i), symbol, file);
            if (c == null) {
                continue;
            }
            out.add(c);
        }
        return out;
    }

    private static Candidate parseRow(String line, String symbol, Path file) {
        if (line == null || line.isBlank()) {
            return null;
        }
        List<String> parts = csvParts(line);
        // Minimum fields: time(0), symbol(1), price(2), orHigh(3), orLow(4), ..., ageSeconds(17).
        if (parts.size() < 18) {
            return null;
        }
        String rowSymbol = parts.get(1);
        if (rowSymbol == null || !rowSymbol.equals(symbol)) {
            return null;
        }
        LocalDateTime rowTime;
        double price;
        double high;
        double low;
        long ageSeconds;
        try {
            rowTime = LocalDateTime.parse(parts.get(0));
            price = Double.parseDouble(parts.get(2));
            high = Double.parseDouble(parts.get(3));
            low = Double.parseDouble(parts.get(4));
            ageSeconds = Long.parseLong(parts.get(17).trim());
        } catch (RuntimeException e) {
            return null;
        }
        if (Double.isNaN(price) || Double.isNaN(high) || Double.isNaN(low) || high <= low) {
            return null;
        }
        return new Candidate(file, rowTime, rowSymbol, price, price, price, high, low, ageSeconds);
    }

    private static Candidate withSessionExtremes(Candidate best, List<Candidate> candidates) {
        double maxPrice = best.maxPrice;
        double minPrice = best.minPrice;
        for (Candidate c : candidates) {
            if (!c.symbol.equals(best.symbol)) {
                continue;
            }
            if (!c.rowTime.toLocalDate().equals(best.rowTime.toLocalDate())) {
                continue;
            }
            maxPrice = Math.max(maxPrice, c.price);
            minPrice = Math.min(minPrice, c.price);
        }
        return new Candidate(best.file, best.rowTime, best.symbol, best.price,
                maxPrice, minPrice, best.high, best.low, best.ageSeconds);
    }

    private static void addRestoredLevels(PaxOpeningRangeDayState day, Candidate c, double tickSize) {
        double rung = levelFactor(c.symbol);
        if (rung <= 0) {
            return;
        }
        LocalDateTime start = c.rowTime.minusSeconds(Math.max(0L, c.ageSeconds));
        int upperCount = Math.max(1, Math.min(12, (int) Math.floor(Math.max(0.0, c.maxPrice - c.high) / rung) + 1));
        int lowerCount = Math.max(1, Math.min(12, (int) Math.floor(Math.max(0.0, c.low - c.minPrice) / rung) + 1));
        for (int i = 1; i <= upperCount; i++) {
            day.addUpperLevel(roundToTick(c.high + rung * i, tickSize), start);
        }
        for (int i = 1; i <= lowerCount; i++) {
            day.addLowerLevel(roundToTick(c.low - rung * i, tickSize), start);
        }
    }

    private static double levelFactor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase();
        if (s.contains("ES") || s.contains("MES")) {
            return 15.0;
        }
        if (s.contains("NQ") || s.contains("MNQ")) {
            return 65.0;
        }
        return 0.0;
    }

    private static double roundToTick(double value, double tickSize) {
        return Math.round(value / tickSize) * tickSize;
    }

    /** Quoted-aware CSV splitter — matches the writer's `csv()` quoting rules. */
    static List<String> csvParts(String line) {
        List<String> parts = new ArrayList<>();
        StringBuilder current = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"') {
                quoted = !quoted;
            } else if (c == ',' && !quoted) {
                parts.add(current.toString());
                current.setLength(0);
            } else {
                current.append(c);
            }
        }
        parts.add(current.toString());
        return parts;
    }

    private static final class Candidate {
        final Path file;
        final LocalDateTime rowTime;
        final String symbol;
        final double price;
        final double maxPrice;
        final double minPrice;
        final double high;
        final double low;
        final long ageSeconds;

        Candidate(Path file, LocalDateTime rowTime, String symbol, double price,
                  double maxPrice, double minPrice, double high, double low, long ageSeconds) {
            this.file = file;
            this.rowTime = rowTime;
            this.symbol = symbol;
            this.price = price;
            this.maxPrice = maxPrice;
            this.minPrice = minPrice;
            this.high = high;
            this.low = low;
            this.ageSeconds = ageSeconds;
        }
    }
}
