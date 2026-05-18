package com.openrange;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Objects;

public class PaxOpeningRangeStateCache {
    private static final String HEADER = "date,high,low,mid,completedAt,lastUpdateTime,upperLevels,lowerLevels";

    private final Path file;

    public PaxOpeningRangeStateCache(Path file) {
        this.file = Objects.requireNonNull(file, "file");
    }

    public synchronized void save(Collection<PaxOpeningRangeDayState> days) {
        try {
            Path parent = file.getParent();
            if (parent != null) {
                Files.createDirectories(parent);
            }
            List<String> lines = new ArrayList<>();
            lines.add(HEADER);
            for (PaxOpeningRangeDayState day : days) {
                if (day.isComplete()) {
                    lines.add(row(day));
                }
            }
            Files.write(file, lines, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
        } catch (IOException e) {
            throw new IllegalStateException("Unable to write OpenRange state cache: " + file, e);
        }
    }

    public synchronized void restoreInto(PaxOpeningRangeCalculator calculator) {
        if (!Files.exists(file)) {
            return;
        }
        try {
            List<String> lines = Files.readAllLines(file, StandardCharsets.UTF_8);
            for (int i = 1; i < lines.size(); i++) {
                restoreLine(calculator, lines.get(i));
            }
        } catch (IOException e) {
            throw new IllegalStateException("Unable to read OpenRange state cache: " + file, e);
        }
    }

    public synchronized void restoreFromSignalLog(PaxOpeningRangeCalculator calculator, Path signalFile) {
        if (!Files.exists(signalFile)) {
            return;
        }
        try {
            List<String> lines = Files.readAllLines(signalFile, StandardCharsets.UTF_8);
            for (int i = 1; i < lines.size(); i++) {
                restoreSignalLine(calculator, lines.get(i));
            }
        } catch (IOException e) {
            throw new IllegalStateException("Unable to read OpenRange signal log for state restore: " + signalFile, e);
        }
    }

    private void restoreLine(PaxOpeningRangeCalculator calculator, String line) {
        if (line == null || line.isBlank()) {
            return;
        }
        String[] parts = line.split(",", -1);
        if (parts.length < 8) {
            return;
        }
        calculator.restoreCompletedDay(
                LocalDate.parse(parts[0]),
                Double.parseDouble(parts[1]),
                Double.parseDouble(parts[2]),
                Double.parseDouble(parts[3]),
                LocalDateTime.parse(parts[4]),
                parseTime(parts[5]),
                parseLevels(parts[6]),
                parseLevels(parts[7]));
    }

    private void restoreSignalLine(PaxOpeningRangeCalculator calculator, String line) {
        List<String> parts = csvParts(line);
        if (parts.size() < 18) {
            return;
        }
        LocalDateTime time = LocalDateTime.parse(parts.get(0));
        double high = Double.parseDouble(parts.get(3));
        double low = Double.parseDouble(parts.get(4));
        long ageSeconds = Long.parseLong(parts.get(17));
        LocalDateTime completedAt = time.minusSeconds(ageSeconds);
        double mid = calculator.roundToTick(low + ((high - low) * 0.5));
        calculator.restoreCompletedDay(time.toLocalDate(), high, low, mid, completedAt, time, List.of(), List.of());
    }

    private static String row(PaxOpeningRangeDayState day) {
        return day.getDate() + "," + day.getHigh() + "," + day.getLow() + "," + day.getMid() + ","
                + day.getCompletedAt() + "," + value(day.getLastUpdateTime()) + ","
                + levels(day.getUpperLevels()) + "," + levels(day.getLowerLevels());
    }

    private static String levels(List<PaxOpeningRangeLevel> levels) {
        List<String> values = new ArrayList<>();
        for (PaxOpeningRangeLevel level : levels) {
            values.add(level.price() + "@" + level.startTime());
        }
        return String.join("|", values);
    }

    private static List<PaxOpeningRangeLevel> parseLevels(String text) {
        List<PaxOpeningRangeLevel> levels = new ArrayList<>();
        if (text == null || text.isBlank()) {
            return levels;
        }
        for (String part : text.split("\\|")) {
            String[] fields = part.split("@", 2);
            if (fields.length == 2) {
                levels.add(new PaxOpeningRangeLevel(Double.parseDouble(fields[0]), LocalDateTime.parse(fields[1])));
            }
        }
        return levels;
    }

    private static String value(LocalDateTime time) {
        return time == null ? "" : time.toString();
    }

    private static LocalDateTime parseTime(String text) {
        return text == null || text.isBlank() ? null : LocalDateTime.parse(text);
    }

    private static List<String> csvParts(String line) {
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
}
