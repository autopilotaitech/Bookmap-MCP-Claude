package com.openrange;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

public class PaxOpeningRangeDayState {
    private final LocalDate date;
    private final List<PaxOpeningRangeLevel> upperLevels = new ArrayList<>();
    private final List<PaxOpeningRangeLevel> lowerLevels = new ArrayList<>();
    private boolean complete;
    private double high = Double.NaN;
    private double low = Double.NaN;
    private double mid = Double.NaN;
    private LocalDateTime completedAt;
    private LocalDateTime lastUpdateTime;

    PaxOpeningRangeDayState(LocalDate date) {
        this.date = date;
    }

    public LocalDate getDate() {
        return date;
    }

    public boolean isComplete() {
        return complete;
    }

    void setComplete(double high, double low, double mid, LocalDateTime completedAt) {
        this.high = high;
        this.low = low;
        this.mid = mid;
        this.completedAt = completedAt;
        this.complete = true;
        this.lastUpdateTime = completedAt;
    }

    void observeRangePrice(double price) {
        if (Double.isNaN(high) || price > high) {
            high = price;
        }
        if (Double.isNaN(low) || price < low) {
            low = price;
        }
    }

    void setLastUpdateTime(LocalDateTime lastUpdateTime) {
        this.lastUpdateTime = lastUpdateTime;
    }

    void addUpperLevel(double price, LocalDateTime startTime) {
        upperLevels.add(new PaxOpeningRangeLevel(price, startTime));
    }

    void addLowerLevel(double price, LocalDateTime startTime) {
        lowerLevels.add(new PaxOpeningRangeLevel(price, startTime));
    }

    public double getHigh() {
        return high;
    }

    public double getLow() {
        return low;
    }

    public double getMid() {
        return mid;
    }

    public LocalDateTime getCompletedAt() {
        return completedAt;
    }

    public LocalDateTime getLastUpdateTime() {
        return lastUpdateTime;
    }

    public List<PaxOpeningRangeLevel> getUpperLevels() {
        return Collections.unmodifiableList(upperLevels);
    }

    public List<PaxOpeningRangeLevel> getLowerLevels() {
        return Collections.unmodifiableList(lowerLevels);
    }
}
