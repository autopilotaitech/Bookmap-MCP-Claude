package com.openrange;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.util.Collection;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

public class PaxOpeningRangeCalculator {
    private final PaxOpeningRangeSettings settings;
    private final String symbol;
    private final double tickSize;
    private final double levelFactor;
    private final Map<LocalDate, PaxOpeningRangeDayState> days = new TreeMap<>();

    public PaxOpeningRangeCalculator(PaxOpeningRangeSettings settings, String symbol, double tickSize) {
        this.settings = settings;
        this.symbol = symbol == null ? "" : symbol.toUpperCase();
        this.tickSize = tickSize <= 0 ? 0.25 : tickSize;
        this.levelFactor = levelFactor(this.symbol);
    }

    public void onTrade(LocalDateTime time, double price) {
        onInterval(time, price, price);
    }

    public void onInterval(LocalDateTime time, double high, double low) {
        LocalDate date = time.toLocalDate();
        PaxOpeningRangeDayState day = days.computeIfAbsent(date, PaxOpeningRangeDayState::new);
        LocalTime localTime = time.toLocalTime();

        if (!day.isComplete()) {
            if (!localTime.isBefore(settings.rangeStart()) && !localTime.isAfter(settings.rangeEnd())) {
                day.observeRangePrice(high);
                day.observeRangePrice(low);
                day.setLastUpdateTime(time);
                if (localTime.isBefore(settings.rangeEnd())) {
                    return;
                }
            }
            if (!localTime.isBefore(settings.rangeEnd()) && !Double.isNaN(day.getHigh())) {
                double mid = roundToTick(day.getLow() + ((day.getHigh() - day.getLow()) * 0.5));
                day.setComplete(day.getHigh(), day.getLow(), mid, date.atTime(settings.rangeEnd()));
                if (levelFactor > 0) {
                    LocalDateTime levelStart = date.atTime(settings.rangeEnd());
                    day.addUpperLevel(roundToTick(day.getHigh() + levelFactor), levelStart);
                    day.addLowerLevel(roundToTick(day.getLow() - levelFactor), levelStart);
                }
            }
        }

        if (day.isComplete() && time.isAfter(settings.rangeEndDateTime(date))
                && !time.isAfter(settings.lineEndDateTime(date))) {
            day.setLastUpdateTime(time);
            checkDynamicLevels(day, time, high, low);
        }

        cleanup(date);
    }

    public PaxOpeningRangeDayState getDay(LocalDate date) {
        return days.computeIfAbsent(date, PaxOpeningRangeDayState::new);
    }

    public Collection<PaxOpeningRangeDayState> getDays() {
        return days.values();
    }

    public void reset() {
        days.clear();
    }

    void restoreCompletedDay(LocalDate date, double high, double low, double mid, LocalDateTime completedAt,
            LocalDateTime lastUpdateTime, List<PaxOpeningRangeLevel> upperLevels, List<PaxOpeningRangeLevel> lowerLevels) {
        PaxOpeningRangeDayState day = new PaxOpeningRangeDayState(date);
        day.setComplete(high, low, mid, completedAt);
        if (lastUpdateTime != null) {
            day.setLastUpdateTime(lastUpdateTime);
        }
        for (PaxOpeningRangeLevel level : upperLevels) {
            day.addUpperLevel(level.price(), level.startTime());
        }
        for (PaxOpeningRangeLevel level : lowerLevels) {
            day.addLowerLevel(level.price(), level.startTime());
        }
        days.put(date, day);
        cleanup(date);
    }

    double roundToTick(double value) {
        return Math.round(value / tickSize) * tickSize;
    }

    private void checkDynamicLevels(PaxOpeningRangeDayState day, LocalDateTime time, double high, double low) {
        if (levelFactor <= 0 || day.getUpperLevels().isEmpty() || day.getLowerLevels().isEmpty()) {
            return;
        }

        double highestUpper = day.getUpperLevels().get(day.getUpperLevels().size() - 1).price();
        if (high > highestUpper) {
            day.addUpperLevel(roundToTick(highestUpper + levelFactor), time);
        }

        double lowestLower = day.getLowerLevels().get(day.getLowerLevels().size() - 1).price();
        if (low < lowestLower) {
            day.addLowerLevel(roundToTick(lowestLower - levelFactor), time);
        }
    }

    private void cleanup(LocalDate currentDate) {
        while (!days.isEmpty() && currentDate.toEpochDay() - days.keySet().iterator().next().toEpochDay() >= settings.daysToDisplay()) {
            days.remove(days.keySet().iterator().next());
        }
    }

    private static double levelFactor(String symbol) {
        if (symbol.contains("ES") || symbol.contains("MES")) {
            return 15;
        }
        if (symbol.contains("NQ") || symbol.contains("MNQ")) {
            return 65;
        }
        return 0;
    }
}
